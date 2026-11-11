"""The four Odoo-context tools, served from a Knowledge workspace.

The logic is in ``context_core`` (shared byte-for-byte with the independent
server); this file is the in-Odoo backend and the tool registrations.

* ``odoo_context_for`` and ``odoo_context_lookup`` read as the calling user.
* ``odoo_context_propose`` checks the contributor group, then creates the one
  proposal article with elevated rights (``sudo``), so contributors only need
  read access to the workspace. ``create_uid`` stays the calling user.
* ``odoo_context_apply_proposal`` checks the reviewer group and writes as the
  reviewer, so Knowledge's history shows who changed what.
"""
from markupsafe import Markup

from odoo import fields

from . import context_core
from .mcp_registry import register_tool

ROOT_PARAM = 'odoo_module_mcp_server.context_root_article_id'
STALE_PARAM = 'odoo_module_mcp_server.context_stale_days'


def configured_root_id(env):
    raw = env['ir.config_parameter'].sudo().get_param(ROOT_PARAM) or ''
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value or None


def paths_of(env, model, ids):
    """``{id: parent_path}`` for articles, or members' articles, seen by sudo."""
    records = env[model].sudo().with_context(active_test=False).browse(ids).exists()
    if model == context_core.MODEL:
        return {r.id: r.parent_path for r in records}
    return {r.id: r.article_id.parent_path for r in records}


def check_workspace_records(env, model, ids=None, values=None):
    """Workspace write guard for the generic record tools."""
    if model not in context_core.GUARDED_MODELS or model not in env:
        return
    context_core.guard_records(model, ids, values, lambda: configured_root_id(env),
                               lambda m, i: paths_of(env, m, i))


def check_workspace_execute(env, model, method, args, kwargs):
    """Workspace write guard for odoo_execute."""
    if model not in context_core.GUARDED_MODELS or model not in env:
        return
    context_core.guard_execute(model, method, args, kwargs, lambda: configured_root_id(env),
                               lambda m, i: paths_of(env, m, i))


class OdooBackend(context_core.Backend):
    """context_core.Backend over the ORM, as the request's user."""

    def __init__(self, env):
        self.env = env

    def _model(self, model, all_companies=False):
        records = self.env[model]
        if all_companies:
            records = records.with_context(
                allowed_company_ids=self.env.user.company_ids.ids)
        return records

    def search_read(self, model, domain, fields_, order=None, limit=None,
                    all_companies=False):
        return self._model(model, all_companies).search_read(
            domain, fields_, order=order, limit=limit)

    def create(self, model, values, elevated=False):
        records = self.env[model].sudo() if elevated else self.env[model]
        return records.create(values).id

    def write(self, model, ids, values):
        return self.env[model].browse(ids).write(values)

    def post_message(self, article_id, body_html, partner_ids=()):
        article = self.env[context_core.MODEL].browse(article_id)
        # A contributor can only read the Proposals folder; posting on the
        # proposal they just filed is part of filing it.
        article.sudo().message_post(
            body=Markup(body_html),
            partner_ids=list(partner_ids), message_type='comment',
            subtype_xmlid='mail.mt_comment', author_id=self.env.user.partner_id.id)

    def user(self):
        user = self.env.user
        return {'id': user.id, 'name': user.name, 'company_ids': user.company_ids.ids}

    def has_group(self, xmlid):
        return self.env.user.has_group(xmlid)

    def group_partner_ids(self, xmlid):
        group = self.env.ref(xmlid, raise_if_not_found=False)
        if not group:
            return []
        return group.sudo().users.partner_id.ids

    def today(self):
        return fields.Date.context_today(self.env.user)

    def base_url(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''

    def root_article_id(self):
        return configured_root_id(self.env)

    def stale_days(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(STALE_PARAM)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return context_core.DEFAULT_STALE_DAYS
        return value if value > 0 else context_core.DEFAULT_STALE_DAYS


def _backend(env):
    return OdooBackend(env)


_READ_HINTS = {'readOnlyHint': True, 'destructiveHint': False,
               'idempotentHint': True, 'openWorldHint': True}


@register_tool(
    name='odoo_context_for',
    description=context_core.TOOL_DOCS['odoo_context_for'],
    input_schema=context_core.TOOL_SCHEMAS['odoo_context_for'],
    annotations=_READ_HINTS,
)
def odoo_context_for(env, args):
    return context_core.context_for(_backend(env), args)


@register_tool(
    name='odoo_context_lookup',
    description=context_core.TOOL_DOCS['odoo_context_lookup'],
    input_schema=context_core.TOOL_SCHEMAS['odoo_context_lookup'],
    annotations=_READ_HINTS,
)
def odoo_context_lookup(env, args):
    return context_core.lookup(_backend(env), args)


@register_tool(
    name='odoo_context_propose',
    description=context_core.TOOL_DOCS['odoo_context_propose'],
    input_schema=context_core.TOOL_SCHEMAS['odoo_context_propose'],
    annotations={'readOnlyHint': False, 'destructiveHint': False,
                 'idempotentHint': False, 'openWorldHint': True},
)
def odoo_context_propose(env, args):
    return context_core.propose(_backend(env), args)


@register_tool(
    name='odoo_context_apply_proposal',
    description=context_core.TOOL_DOCS['odoo_context_apply_proposal'],
    input_schema=context_core.TOOL_SCHEMAS['odoo_context_apply_proposal'],
    annotations={'readOnlyHint': False, 'destructiveHint': True,
                 'idempotentHint': False, 'openWorldHint': True},
)
def odoo_context_apply_proposal(env, args):
    return context_core.apply_proposal(_backend(env), args)
