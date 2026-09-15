"""Redaction, truncation and classification rules for the MCP log.

Deliberately **Odoo-free** — pure functions over plain data, so they are unit
testable without a database and reusable from anywhere in the module.

The rules here mirror the external server's ``odoo-mcp/audit.py`` 1:1, so an
audit record reads the same whichever server produced it:

* any mapping key that looks like a secret is masked at every nesting depth;
* long strings, long lists, wide mappings and deep nesting are truncated with
  an explicit marker, so a reader can tell "short" from "shortened";
* a result set of records collapses to a count plus ids — the ids are what an
  auditor needs to pull the records back up, and the field values are already
  in Odoo;
* a policy refusal is classified as ``blocked``, an access-rights refusal as
  ``denied``, and anything else as ``error``. An operator needs to tell "the
  agent tried and the guardrail stopped it" apart from "Odoo errored".
"""

REDACTED = '***redacted***'

# The key a truncated mapping carries its "…and N more" marker under. Not a
# legal Odoo field name, so it can never collide with a real payload key.
TRUNCATED_KEY = '…'

DEFAULT_MAX_STRING = 512
DEFAULT_MAX_ITEMS = 20
# Mappings get their own, more generous cap. A write payload's breadth IS the
# audit-relevant part — truncating `values` at 20 fields would hide what an
# agent wrote — but it still has to be bounded: `odoo_fields_get` on
# account.move returns ~350 field definitions, and storing that whole dict in
# the Text column on every call bloats the table for no audit value.
# 64 renders any realistic create/write payload whole and caps schema dumps.
DEFAULT_MAX_KEYS = 64
# Odoo x2many write payloads are legitimately deep — invoice_line_ids → command
# triple → line values → tax_ids → command triple is 7 levels. 8 renders them
# whole, which matters: which taxes were applied is audit-relevant.
DEFAULT_MAX_DEPTH = 8

# Cap for the one-line summary + the record-id column in the list view.
SUMMARY_MAX_CHARS = 256

LEVELS = ('off', 'error', 'all')

# Substrings marking a mapping key as carrying a secret. Matched
# case-insensitively against the whole key, so `apiKey`, `X-Authorization` and
# `client_secret` are all caught.
_SECRET_HINTS = (
    'api_key', 'apikey', 'password', 'passwd', 'secret', 'token',
    'authorization', 'credential', 'private_key', 'session_id',
)

# Tools that change data. Everything else is a read.
WRITE_TOOLS = frozenset({
    'odoo_create', 'odoo_write', 'odoo_unlink', 'odoo_archive',
    'odoo_cancel', 'odoo_execute',
})

# Substrings identifying a guardrail refusal raised by generic_tools. These are
# our own messages, not Odoo's, and every guardrail in generic_tools must appear
# here — one that doesn't is filed as 'error' and drops out of the "Blocked by
# policy" filter, which is exactly where an operator looks for attempts that
# were stopped. test_generic_tools raises each guardrail for real and asserts
# it classifies as 'blocked', so adding one without its marker fails the suite.
#
# By convention every guardrail message opens with "Refusing to …".
_POLICY_MARKERS = (
    'Refusing to modify structural model',   # _check_writable
    'Refusing to delete records',            # _check_deletable
    'Refusing to call private method',       # odoo_execute's private-method block
)


def _is_secret_key(key):
    return isinstance(key, str) and any(hint in key.lower() for hint in _SECRET_HINTS)


def redact(value, max_string=DEFAULT_MAX_STRING, max_items=DEFAULT_MAX_ITEMS,
           max_keys=DEFAULT_MAX_KEYS, max_depth=DEFAULT_MAX_DEPTH, _depth=0):
    """Return a copy of ``value`` safe to persist: secrets masked, size bounded."""
    if _depth > max_depth:
        return '…'
    if isinstance(value, dict):
        out = {
            key: (REDACTED if _is_secret_key(key)
                  else redact(item, max_string, max_items, max_keys,
                              max_depth, _depth + 1))
            for key, item in list(value.items())[:max_keys]
        }
        if len(value) > max_keys:
            out[TRUNCATED_KEY] = '[+%d more keys]' % (len(value) - max_keys)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        kept = [redact(item, max_string, max_items, max_keys, max_depth, _depth + 1)
                for item in items[:max_items]]
        if len(items) > max_items:
            kept.append('…[+%d more items]' % (len(items) - max_items))
        return kept
    if isinstance(value, (bytes, bytearray, memoryview)):
        return '<%d bytes>' % len(bytes(value))
    if isinstance(value, str):
        if len(value) > max_string:
            return '%s…[+%d more chars]' % (value[:max_string], len(value) - max_string)
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    # Dates, recordsets, opaque objects — repr it, bounded.
    return redact(repr(value), max_string, max_items, max_keys, max_depth, _depth)


def summarize_result(value, max_string=DEFAULT_MAX_STRING, max_items=DEFAULT_MAX_ITEMS,
                     max_keys=DEFAULT_MAX_KEYS):
    """Condense a tool's return value for the log.

    Scalars pass through untouched — ``odoo_create`` returning ``1841`` is the
    single most audit-relevant value a tool can produce.
    """
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        items = list(value)
        if items and all(isinstance(item, dict) for item in items):
            ids = [item.get('id') for item in items[:max_items]
                   if isinstance(item.get('id'), int)]
            return {'type': 'records', 'count': len(items), 'ids': ids}
    return redact(value, max_string, max_items, max_keys)


def dump(value):
    """JSON-serialise for a Text column. Never raises."""
    import json
    try:
        return json.dumps(value, default=str, ensure_ascii=False, indent=2,
                          sort_keys=False)
    except Exception:
        return json.dumps({'error': 'payload could not be serialised',
                           'repr': repr(value)[:SUMMARY_MAX_CHARS]})


def should_log(level, outcome):
    """Does a call with this ``outcome`` get a row at this ``level``?

    An unrecognised level is treated as ``all`` — a garbled config parameter
    must never silently switch the audit trail off.
    """
    if level == 'off':
        return False
    if level == 'error':
        return outcome != 'ok'
    return True


def classify_error(error):
    """``blocked`` for a guardrail refusal, ``denied`` for access rights, else ``error``."""
    message = str(error)
    if any(marker in message for marker in _POLICY_MARKERS):
        return 'blocked'
    if type(error).__name__ in ('AccessError', 'AccessDenied'):
        return 'denied'
    return 'error'


def truncate(text, limit=SUMMARY_MAX_CHARS):
    """Bound a single-line value for a Char column."""
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


_truncate = truncate  # internal alias, kept for readability below


def describe_call(tool, args):
    """One-line summary for the log list view, e.g. ``odoo_write account.move [1841]``."""
    args = args or {}
    parts = [str(tool)]
    model = args.get('model')
    if model:
        parts.append(str(model))
    method = args.get('method')
    if method:
        parts.append(str(method))
    ids = args.get('ids') or args.get('record_ids')
    if isinstance(ids, (list, tuple)) and ids:
        shown = ', '.join(str(i) for i in list(ids)[:5])
        if len(ids) > 5:
            shown += ', …+%d' % (len(ids) - 5)
        parts.append('[%s]' % shown)
    return _truncate(' '.join(parts))


def extract_facets(tool, args):
    """Lift model / method / record ids out of the arguments into columns.

    Stored alongside the JSON blob so the log is filterable and groupable in the
    list view without parsing payloads. Always returns every key — an absent
    facet is empty, never missing, so callers can pass this straight to create().
    """
    args = args or {}
    ids = args.get('ids') or args.get('record_ids') or []
    if not isinstance(ids, (list, tuple)):
        ids = [ids]
    ids = [i for i in ids if isinstance(i, int)]
    return {
        'model_name': str(args.get('model') or ''),
        'method': str(args.get('method') or ''),
        'record_ids': _truncate(','.join(str(i) for i in ids)),
        'record_count': len(ids),
        'is_write': tool in WRITE_TOOLS,
    }
