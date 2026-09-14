"""Generic Odoo data tools registered with the MCP registry.

Every tool runs as the authenticated user — Odoo's record rules and
ir.model.access enforce what the caller can read/write/delete. Methods
prefixed with ``_`` are refused (consistent with Odoo's standard external
RPC).

Structural protection
=====================

Tools are named with the ``odoo_*`` scheme to match the external MCP server
1:1, so the same Odin skill/playbooks drive either server unchanged:
``odoo_whoami``, ``odoo_search_read`` / ``odoo_search_count`` / ``odoo_read`` /
``odoo_read_group`` / ``odoo_name_search`` / ``odoo_fields_get`` /
``odoo_models_list``, ``odoo_create`` / ``odoo_write`` / ``odoo_archive`` /
``odoo_cancel`` / ``odoo_unlink``, ``odoo_render_report``, and ``odoo_execute``
(the call_kw escape hatch). (No ``odoo_connect`` — auth is the MCP client's API
key, so a bare ``odoo_whoami`` confirms the session.)

When ``odoo_module_mcp_server.protect_structural`` is on (the default), the
write tools (``odoo_create`` / ``odoo_write`` / ``odoo_unlink`` /
``odoo_execute``) refuse to operate on a denylist of *structural* models —
schema, views, actions, security, auth, modules, settings, mail templates,
automation rules, and reference data. Reads are always allowed (introspection
is useful for agents). Turning the toggle off restores the original behaviour
(Odoo's per-user access rights are the only gate).
"""
import base64

from odoo.api import call_kw

from .mcp_registry import register_tool

_MODEL_SCHEMA = {
    'type': 'string',
    'description': 'Technical model name, e.g. res.partner or account.move.line.',
}

_COMPANY_ID_SCHEMA = {
    'type': 'integer',
    'description': "Optional company id to read in the context of. Some Odoo 18 "
                   "fields are company-dependent — notably account.account.code "
                   "(and display_name). Without this, reads run in the API user's "
                   "default company and those fields come back blank for any other "
                   "company; set it to the company that owns the records to get the "
                   "code/display_name populated (e.g. '12501 Prepayments').",
}


def _model(env, name):
    """Resolve a model by name. Raises ValueError if unknown."""
    if not name or not isinstance(name, str):
        raise ValueError('Missing model name')
    if name not in env:
        raise ValueError("Unknown model: '%s'" % name)
    return env[name]


def _scoped(env, args):
    """Resolve the model, optionally pinned to a company so COMPANY-DEPENDENT
    fields (e.g. account.account.code in Odoo 18) compute for that company.

    Without a company context the read runs in the API user's default company,
    so company-dependent fields come back blank for other companies (and only
    the internal id is left to show). ``with_company`` fixes the context."""
    model = _model(env, args.get('model'))
    company_id = args.get('company_id')
    if company_id:
        model = model.with_company(company_id)
    return model


# ---------------------------------------------------------------------------
# Structural-model denylist
# ---------------------------------------------------------------------------

# Models matched by any of these prefixes are off-limits for writes when
# ``odoo_module_mcp_server.protect_structural`` is on.
_STRUCTURAL_PREFIXES = (
    'ir.',                  # schema, views, actions, cron, modules, settings, …
    'bus.',                 # internal pub/sub
    'base_automation.',     # server-side automation (executes code)
    'custom.mcp.oauth.',    # our own OAuth tables — never let the agent mint or
                            # revoke its own credentials
    # Auth subtrees. The exact names below cover Odoo core's own models, but
    # third-party addons extend these namespaces (res.users.role from a roles
    # addon, res.users.apikeys.description in core) and every one of them is
    # part of the privilege surface. Match the whole subtree, as the external
    # server does — a guardrail that depends on enumerating models loses to the
    # next addon installed.
    'res.users.',
    'res.groups.',
    # Odoo Studio customisations are schema changes by another name.
    'studio.',
)

# Exact model names that are off-limits even though they don't match a
# prefix above (or that need to be called out explicitly).
_STRUCTURAL_EXACT = frozenset({
    # Auth / permissions
    'res.groups',
    'res.groups.privilege',
    'res.users',
    'res.users.apikeys',
    'res.users.log',
    'res.users.identitycheck',
    'res.users.settings',
    # Reference data — agents shouldn't be inventing currencies
    'res.lang',
    'res.currency',
    'res.currency.rate',
    'res.country',
    'res.country.group',
    'res.country.state',
    # Mail templates can embed Python expressions; aliases route inbound mail
    'mail.template',
    'mail.alias',
    # Automation rules execute server-side Python. The MODEL is 'base.automation'
    # in Odoo 16+ (the MODULE is 'base_automation') — the dotted name is the one
    # that actually exists on a live instance, so it must be listed explicitly:
    # neither the 'base_automation.' prefix nor the bare module name matches it.
    'base.automation',
    'base_automation',      # not all instances put automations under the prefix
    'studio',               # bare name, alongside the 'studio.' prefix above
})

# Carve-outs: models matched by a prefix in ``_STRUCTURAL_PREFIXES`` that
# are explicitly transactional in practice and stay writable.
_STRUCTURAL_OVERRIDE_ALLOW = frozenset({
    'ir.attachment',        # uploading PDFs / files to records is everyday
    'ir.attachment.url',
})


def _is_structural_model(model_name):
    """Return True when writes to ``model_name`` should be refused."""
    if model_name in _STRUCTURAL_OVERRIDE_ALLOW:
        return False
    if model_name in _STRUCTURAL_EXACT:
        return True
    return any(model_name.startswith(prefix)
               for prefix in _STRUCTURAL_PREFIXES)


def _check_writable(env, model_name):
    """Raise ValueError if ``model_name`` is structural and protection is on.

    Called at the top of every write tool. When
    ``odoo_module_mcp_server.protect_structural`` is off, this is a no-op and
    only Odoo's per-user access rights gate writes.
    """
    params = env['ir.config_parameter'].sudo()
    # Default to protected when unset, so a fresh install is safe.
    if params.get_param('odoo_module_mcp_server.protect_structural',
                         default='True') == 'False':
        return
    if _is_structural_model(model_name):
        raise ValueError(
            "Refusing to modify structural model '%s' — MCP is in "
            "transactional mode (Settings → General Settings → MCP "
            "Server → 'Protect structural models'). Toggle that off "
            "if you intentionally need structural access." % model_name)


def _check_deletable(env):
    """Raise unless record deletion has been explicitly enabled.

    Mirrors the external MCP server's "no record deletion" policy: hard
    deletes are refused by default on *every* model (not just structural
    ones) — archive the record (set ``active=False``) or cancel it
    instead. An administrator can flip
    ``odoo_module_mcp_server.allow_record_deletion`` to 'True' (Settings →
    General Settings → MCP Server → 'Allow record deletion') to permit
    ``odoo_unlink``. Off by default so a fresh install never destroys data.
    """
    params = env['ir.config_parameter'].sudo()
    if params.get_param('odoo_module_mcp_server.allow_record_deletion',
                         default='False') == 'True':
        return
    raise ValueError(
        "Refusing to delete records — MCP record deletion is disabled "
        "(the default). Archive the record (set active=False) or cancel "
        "it instead. An administrator can allow hard deletes under "
        "Settings → General Settings → MCP Server → 'Allow record "
        "deletion'.")


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

@register_tool(
    name='odoo_models_list',
    description="List models the authenticated user can read. Optionally "
                "filter by a substring on the technical model name.",
    input_schema={
        'type': 'object',
        'properties': {
            'name_substring': {
                'type': 'string',
                'description': "Case-insensitive substring of the technical "
                               "model name to filter by.",
            },
            'limit': {'type': 'integer', 'default': 200},
        },
        'additionalProperties': False,
    },
)
def odoo_models_list(env, args):
    domain = []
    if args.get('name_substring'):
        domain.append(('model', 'ilike', args['name_substring']))
    limit = args.get('limit') or 200
    rows = env['ir.model'].search_read(
        domain, ['model', 'name'], limit=limit, order='model')
    # Filter to models the user can actually read.
    readable = []
    for row in rows:
        try:
            env[row['model']].check_access('read')
            readable.append(row)
        except Exception:
            continue
    return readable


@register_tool(
    name='odoo_fields_get',
    description="Introspect a model's fields. Returns a dict keyed by field "
                "name with the requested attributes.",
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'attributes': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': "Field-definition attributes to return. "
                               "Defaults to ['string','type','required','readonly',"
                               "'help','relation','selection'].",
            },
        },
        'required': ['model'],
        'additionalProperties': False,
    },
)
def odoo_fields_get(env, args):
    model = _model(env, args.get('model'))
    attributes = args.get('attributes') or [
        'string', 'type', 'required', 'readonly', 'help',
        'relation', 'selection',
    ]
    return model.fields_get(attributes=attributes)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@register_tool(
    name='odoo_search_read',
    description="Search a model with a domain and return matching records' "
                "fields. Returns up to `limit` records (default 100). The "
                "domain follows Odoo's standard syntax, e.g. "
                "[('state','=','posted'),('amount_total','>',1000)].",
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'domain': {'type': 'array', 'default': []},
            'fields': {
                'type': 'array', 'items': {'type': 'string'},
                'description': "Fields to read. Defaults to a small set; "
                               "pass an empty list to get every field.",
            },
            'limit': {'type': 'integer', 'default': 100},
            'offset': {'type': 'integer', 'default': 0},
            'order': {'type': 'string'},
            'company_id': _COMPANY_ID_SCHEMA,
        },
        'required': ['model'],
        'additionalProperties': False,
    },
)
def odoo_search_read(env, args):
    model = _scoped(env, args)
    return model.search_read(
        args.get('domain') or [],
        args.get('fields') or None,
        offset=args.get('offset') or 0,
        limit=args.get('limit') or 100,
        order=args.get('order') or None,
    )


@register_tool(
    name='odoo_search_count',
    description='Count records matching a domain on a model.',
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'domain': {'type': 'array', 'default': []},
            'company_id': _COMPANY_ID_SCHEMA,
        },
        'required': ['model'],
        'additionalProperties': False,
    },
)
def odoo_search_count(env, args):
    model = _scoped(env, args)
    return model.search_count(args.get('domain') or [])


@register_tool(
    name='odoo_read',
    description='Read specific records by id from a model.',
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'ids': {'type': 'array', 'items': {'type': 'integer'}},
            'fields': {'type': 'array', 'items': {'type': 'string'}},
            'company_id': _COMPANY_ID_SCHEMA,
        },
        'required': ['model', 'ids'],
        'additionalProperties': False,
    },
)
def odoo_read(env, args):
    model = _scoped(env, args)
    return model.browse(args['ids']).read(args.get('fields') or None)


@register_tool(
    name='odoo_read_group',
    description="Aggregate-read on a model — Odoo's classic `read_group`. "
                "Returns one entry per group with the requested aggregate "
                "fields. Useful for charting / summarising.",
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'domain': {'type': 'array', 'default': []},
            'fields': {
                'type': 'array', 'items': {'type': 'string'},
                'description': "Fields to aggregate. Each as 'field' "
                               "(default agg) or 'field:agg'.",
            },
            'groupby': {
                'type': 'array', 'items': {'type': 'string'},
                'description': "Group-by fields. Date fields can carry a "
                               "granularity, e.g. 'date:month'.",
            },
            'offset': {'type': 'integer', 'default': 0},
            'limit': {'type': 'integer'},
            'orderby': {'type': 'string'},
            'company_id': _COMPANY_ID_SCHEMA,
        },
        'required': ['model', 'fields', 'groupby'],
        'additionalProperties': False,
    },
)
def odoo_read_group(env, args):
    model = _scoped(env, args)
    return model.read_group(
        args.get('domain') or [],
        args.get('fields') or [],
        args.get('groupby') or [],
        offset=args.get('offset') or 0,
        limit=args.get('limit'),
        orderby=args.get('orderby') or False,
        lazy=False,
    )


@register_tool(
    name='odoo_name_search',
    description="Autocomplete-style lookup: returns records whose display "
                "name matches. Useful for resolving 'the Acme customer' to "
                "a partner id.",
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'name': {'type': 'string', 'default': ''},
            'args': {'type': 'array', 'default': []},
            'operator': {'type': 'string', 'default': 'ilike'},
            'limit': {'type': 'integer', 'default': 10},
            'company_id': _COMPANY_ID_SCHEMA,
        },
        'required': ['model'],
        'additionalProperties': False,
    },
)
def odoo_name_search(env, args):
    model = _scoped(env, args)
    # Call positionally: the 2nd positional arg is the search domain in
    # every supported series, but its keyword name differs (``args`` in
    # <=16, ``domain`` in 17+). Positional keeps this 18- and 19-safe.
    results = model.name_search(
        args.get('name') or '',
        args.get('args') or [],
        args.get('operator') or 'ilike',
        args.get('limit') or 10,
    )
    return [{'id': rid, 'display_name': name} for rid, name in results]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

@register_tool(
    name='odoo_create',
    description="Create one or many records on a model. `values` is either a "
                "dict (creates one record) or a list of dicts (creates many). "
                "Returns the created id(s).",
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'values': {
                'oneOf': [
                    {'type': 'object'},
                    {'type': 'array', 'items': {'type': 'object'}},
                ],
            },
        },
        'required': ['model', 'values'],
        'additionalProperties': False,
    },
)
def odoo_create(env, args):
    _check_writable(env, args.get('model'))
    model = _model(env, args.get('model'))
    records = model.create(args['values'])
    if isinstance(args['values'], list):
        return records.ids
    return records.id


@register_tool(
    name='odoo_write',
    description='Update records on a model. Returns true on success.',
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'ids': {'type': 'array', 'items': {'type': 'integer'}},
            'values': {'type': 'object'},
        },
        'required': ['model', 'ids', 'values'],
        'additionalProperties': False,
    },
)
def odoo_write(env, args):
    _check_writable(env, args.get('model'))
    model = _model(env, args.get('model'))
    return bool(model.browse(args['ids']).write(args['values']))


@register_tool(
    name='odoo_unlink',
    description='Delete records by id from a model. Returns true on success.',
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'ids': {'type': 'array', 'items': {'type': 'integer'}},
        },
        'required': ['model', 'ids'],
        'additionalProperties': False,
    },
)
def odoo_unlink(env, args):
    # Two gates: (1) no-delete policy applies to every model by default;
    # (2) structural protection still applies when deletion is enabled.
    _check_deletable(env)
    _check_writable(env, args.get('model'))
    model = _model(env, args.get('model'))
    return bool(model.browse(args['ids']).unlink())


@register_tool(
    name='odoo_execute',
    description="Escape hatch — call any PUBLIC model method via Odoo's call_kw "
                "(identical semantics to the external server's execute_kw). `args` "
                "are the positional args; for an instance method the FIRST element "
                "is the list of record ids, e.g. action_post → args=[[move_id]]; for "
                "an @api.model method pass plain args. Private ('_'-prefixed) methods "
                "and structural-model calls are refused; `unlink` obeys the no-delete "
                "policy (archive/cancel instead).",
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'method': {'type': 'string'},
            'args': {'type': 'array', 'default': []},
            'kwargs': {'type': 'object', 'default': {}},
        },
        'required': ['model', 'method'],
        'additionalProperties': False,
    },
)
def odoo_execute(env, args):
    # The wild card: we don't know in advance whether the method writes, so when
    # protection is on, refuse any call on a structural model.
    _check_writable(env, args.get('model'))
    model = _model(env, args.get('model'))
    method = args.get('method')
    if not method or method.startswith('_'):
        raise ValueError("Refusing to call private method: '%s'" % method)
    # Don't let execute be a back door around the no-delete policy.
    if method == 'unlink':
        _check_deletable(env)
    if not hasattr(model, method):
        raise ValueError("Model '%s' has no method '%s'" % (model._name, method))
    # call_kw mirrors execute_kw: for an instance method it browses args[0] as the
    # ids and calls the method on that recordset with the remaining args.
    return call_kw(model, method, args.get('args') or [], args.get('kwargs') or {})


# ---------------------------------------------------------------------------
# Session / connection (parity with the external server's session tools)
# ---------------------------------------------------------------------------

@register_tool(
    name='odoo_whoami',
    description="Confirm the connection: returns the authenticated user, the db, "
                "the instance base URL, and the companies the user can access. On "
                "this (in-Odoo) server the MCP client's API key already "
                "authenticated you — there is NO separate odoo_connect step.",
    input_schema={'type': 'object', 'properties': {}, 'additionalProperties': False},
)
def odoo_whoami(env, args):
    user = env.user
    companies = env['res.company'].search_read(
        [('id', 'in', user.company_ids.ids)], ['id', 'name', 'currency_id'], order='id')
    base_url = env['ir.config_parameter'].sudo().get_param('web.base.url')
    return {
        'uid': user.id, 'username': user.login, 'name': user.name,
        'db': env.cr.dbname, 'url': base_url, 'companies': companies,
    }


# ---------------------------------------------------------------------------
# Non-destructive "remove" + native report render (parity with external server)
# ---------------------------------------------------------------------------

@register_tool(
    name='odoo_archive',
    description="Archive (active=False) or unarchive (active=True) records — the "
                "non-destructive alternative to delete. The model must have an "
                "`active` field.",
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'ids': {'type': 'array', 'items': {'type': 'integer'}},
            'archive': {'type': 'boolean', 'default': True},
        },
        'required': ['model', 'ids'],
        'additionalProperties': False,
    },
)
def odoo_archive(env, args):
    _check_writable(env, args.get('model'))
    model = _model(env, args.get('model'))
    archive = args.get('archive', True)
    return bool(model.browse(args['ids']).write({'active': not archive}))


@register_tool(
    name='odoo_cancel',
    description="Cancel workflow records via their cancel action (tries "
                "action_cancel, then button_cancel). Non-destructive void for "
                "invoices/bills, sale/purchase orders, pickings, payments, etc.",
    input_schema={
        'type': 'object',
        'properties': {
            'model': _MODEL_SCHEMA,
            'ids': {'type': 'array', 'items': {'type': 'integer'}},
        },
        'required': ['model', 'ids'],
        'additionalProperties': False,
    },
)
def odoo_cancel(env, args):
    _check_writable(env, args.get('model'))
    model = _model(env, args.get('model'))
    records = model.browse(args['ids'])
    for method in ('action_cancel', 'button_cancel'):
        if hasattr(records, method):
            return {'method': method, 'result': getattr(records, method)()}
    raise ValueError(
        "No cancel method (action_cancel / button_cancel) on '%s'" % model._name)


@register_tool(
    name='odoo_render_report',
    description="Render a QWeb report (ir.actions.report) NATIVELY in-process "
                "(no HTTP — sidesteps the external server's Cloudflare/web-session "
                "issues). Returns the file as base64. `report_ref` is the report's "
                "`report_name` (e.g. 'account.report_invoice') or its xmlid.",
    input_schema={
        'type': 'object',
        'properties': {
            'report_ref': {'type': 'string',
                           'description': "report_name or the report's xmlid"},
            'ids': {'type': 'array', 'items': {'type': 'integer'}},
            'converter': {'type': 'string', 'default': 'pdf',
                          'description': "'pdf', 'html', or 'text'"},
        },
        'required': ['report_ref', 'ids'],
        'additionalProperties': False,
    },
)
def odoo_render_report(env, args):
    report_ref = args.get('report_ref')
    ids = args.get('ids') or []
    converter = args.get('converter') or 'pdf'
    Report = env['ir.actions.report']
    report = Report._get_report_from_name(report_ref) if report_ref else None
    if not report:
        report = env.ref(report_ref, raise_if_not_found=False)
    if not report:
        raise ValueError("Report not found: %s" % report_ref)
    render = getattr(report, '_render_qweb_%s' % converter, None)
    if render is None:
        raise ValueError("Unsupported converter '%s'" % converter)
    content, content_type = render(report.report_name, ids)
    return {
        'report_name': report.report_name,
        'converter': converter,
        'ids': ids,
        'mimetype': content_type,
        'size': len(content or b''),
        'content_base64': base64.b64encode(content or b'').decode('ascii'),
    }
