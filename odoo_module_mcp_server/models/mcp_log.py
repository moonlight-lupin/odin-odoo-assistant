"""Append-only audit trail of MCP activity, stored in Odoo.

One row per tool call: who called it, which tool, the (redacted, truncated)
arguments, the outcome and how long it took. This is the "what did the agent
actually do in our books" record — and, because the write guardrails refuse
things, it is also the record of what it *tried* to do and was stopped from
doing.

Three design decisions worth knowing about:

**Rows are written on their own cursor.** A tool that raises leaves the request
cursor dirty (and Odoo rolls it back), so a log row created on it would vanish
with the failure — exactly the row an auditor most wants. Each row is committed
through a fresh cursor instead, so the trail survives the rollback.

**Logging never breaks a request.** Every entry point is wrapped: if the log
cannot be written, the failure is reported to the Odoo log and the tool call
proceeds. An audit trail that can take the server down is worse than no audit
trail.

**Credentials never land here.** Arguments pass through
``log_utils.redact`` before storage — any key that looks like a secret is
masked at every nesting depth — and payloads are truncated. The rules are
shared with the external server so a record reads the same either way.

Retention is a setting (default 90 days), enforced by a daily cron.
"""
import logging
from contextlib import contextmanager

from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError

from .. import log_utils

_logger = logging.getLogger(__name__)

# Config parameters, mirrored by res_config_settings.
LEVEL_PARAM = 'odoo_module_mcp_server.log_level'
PAYLOADS_PARAM = 'odoo_module_mcp_server.log_payloads'
RETENTION_PARAM = 'odoo_module_mcp_server.log_retention_days'

DEFAULT_LEVEL = 'all'
DEFAULT_RETENTION_DAYS = 90

# Retention deletes in committed batches — see _gc_logs.
GC_BATCH_SIZE = 1000
GC_MAX_BATCHES_PER_RUN = 500


class McpLog(models.Model):
    _name = 'custom.mcp.log'
    _description = 'MCP Activity Log'
    _order = 'create_date desc, id desc'
    _rec_name = 'summary'

    # -- who / when ------------------------------------------------------
    user_id = fields.Many2one(
        'res.users', string='User', index=True, ondelete='set null',
        help="The Odoo user the call ran as — the owner of the API key or "
             "OAuth token it was authenticated with.")
    auth_method = fields.Selection(
        [('api_key', 'API key'), ('oauth', 'OAuth 2.1'), ('none', 'Unauthenticated')],
        string='Authentication')
    remote_addr = fields.Char(string='Client IP')

    # -- what ------------------------------------------------------------
    kind = fields.Selection(
        [('tool_call', 'Tool call'),
         ('auth_failure', 'Authentication failure'),
         ('protocol_error', 'Protocol error')],
        string='Kind', required=True, default='tool_call', index=True)
    tool = fields.Char(string='Tool', index=True)
    summary = fields.Char(string='Summary', required=True)
    model_name = fields.Char(string='Model', index=True)
    method = fields.Char(string='Method')
    record_ids = fields.Char(
        string='Record IDs',
        help="The record ids the call targeted — follow these back into the "
             "model to see what changed.")
    record_count = fields.Integer(string='Records', default=0)
    is_write = fields.Boolean(
        string='Write', index=True, default=False,
        help="True for the tools that change data (create / write / archive / "
             "cancel / unlink / execute). Filter on it to see only what "
             "actually touched the books.")

    # -- outcome ---------------------------------------------------------
    outcome = fields.Selection(
        [('ok', 'OK'),
         ('blocked', 'Blocked by policy'),
         ('denied', 'Access denied'),
         ('error', 'Error')],
        string='Outcome', required=True, default='ok', index=True,
        help="'Blocked by policy' means an MCP guardrail refused the call "
             "before it reached the data — not that Odoo errored.")
    duration_ms = fields.Float(string='Duration (ms)', digits=(12, 3))
    error_type = fields.Char(string='Error type')
    error_message = fields.Text(string='Error message')

    # -- payloads --------------------------------------------------------
    arguments = fields.Text(
        string='Arguments',
        help="The call arguments as JSON, with secrets redacted and long "
             "values truncated. Empty when payload logging is off.")
    result = fields.Text(
        string='Result',
        help="A summary of what the call returned. Record sets collapse to a "
             "count plus ids; large payloads are truncated.")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @api.model
    def _log_level(self):
        """'off' | 'error' | 'all'. Unknown values are treated as 'all' —
        a garbled parameter must not silently switch the audit trail off."""
        level = self.env['ir.config_parameter'].sudo().get_param(
            LEVEL_PARAM, default=DEFAULT_LEVEL)
        return level if level in log_utils.LEVELS else DEFAULT_LEVEL

    @api.model
    def _log_payloads(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            PAYLOADS_PARAM, default='True') != 'False'

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    @api.model
    def log_call(self, tool, args=None, outcome='ok', duration_ms=0.0,
                 result=None, error=None):
        """Record one MCP tool invocation. Never raises."""
        try:
            context = self._request_context()   # no DB access — see below
            with self._log_env() as log:
                if not log_utils.should_log(log._log_level(), outcome):
                    return
                values = dict(
                    log_utils.extract_facets(tool, args),
                    kind='tool_call',
                    tool=tool,
                    summary=log_utils.describe_call(tool, args),
                    outcome=outcome,
                    duration_ms=round(duration_ms or 0.0, 3),
                    **context
                )
                if error is not None:
                    values['error_type'] = type(error).__name__
                    values['error_message'] = log_utils.redact(str(error))
                if log._log_payloads():
                    if args is not None:
                        values['arguments'] = log_utils.dump(log_utils.redact(args))
                    if error is None:
                        values['result'] = log_utils.dump(
                            log_utils.summarize_result(result))
                log.create(values)
        except Exception:
            # An audit trail that can take the server down is worse than none.
            _logger.exception("MCP: could not log tool call %s", tool)

    @api.model
    def log_event(self, kind, summary, outcome='error', error=None, detail=None):
        """Record a non-tool event: a rejected bearer token, a protocol fault."""
        try:
            context = self._request_context()
            with self._log_env() as log:
                if not log_utils.should_log(log._log_level(), outcome):
                    return
                values = dict(
                    kind=kind,
                    summary=log_utils.truncate(summary),
                    outcome=outcome,
                    model_name='', method='', record_ids='',
                    record_count=0, is_write=False,
                    **context
                )
                if error is not None:
                    values['error_type'] = type(error).__name__
                    values['error_message'] = log_utils.redact(str(error))
                if detail is not None and log._log_payloads():
                    values['arguments'] = log_utils.dump(log_utils.redact(detail))
                log.create(values)
        except Exception:
            _logger.exception("MCP: could not log event %s", kind)

    @api.model
    def _request_context(self):
        """Who and where, pulled off the live request. Returns {} off-request.

        Reads no database: ``request.env.user`` is already-loaded and only its
        ``id`` is taken. That matters — this runs before the fresh cursor is
        opened, while the request cursor may be aborted.
        """
        try:
            from odoo.http import request
            if not request:
                return {}
            # Set by ir_http once a credential is accepted; absent means the
            # request never got that far (e.g. a rejected token).
            auth_method = getattr(request, 'mcp_auth_method', None)
            context = {
                'auth_method': auth_method or 'none',
                'remote_addr': request.httprequest.remote_addr,
            }
            # Only attribute the row to a user when a credential was actually
            # accepted. Before that, request.env.user is still the public user,
            # and stamping it would make every rejected bearer token — the
            # signal that a key has leaked or been revoked — read in the list
            # view as if that account had made the call.
            if auth_method:
                user = getattr(request.env, 'user', None)
                if user and user.id:
                    context['user_id'] = user.id
            return context
        except Exception:
            return {}

    @contextmanager
    def _log_env(self):
        """Yield ``custom.mcp.log`` bound to a FRESH, committing cursor.

        The request cursor is the wrong place for any of this. A tool that
        raised a database error has left it aborted: every subsequent query on
        it fails, so even *reading the log level* would throw — and a row
        created on it would be rolled back with the failure anyway. That is
        precisely the row an auditor wants. So the settings reads, the create,
        and the commit all happen here instead, and a broken log can never
        poison a working transaction.
        """
        with self.env.registry.cursor() as cr:
            yield api.Environment(cr, SUPERUSER_ID, {})['custom.mcp.log']

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------
    @api.model
    def _gc_logs(self, days=None):
        """Delete rows older than the retention window. Called daily by cron.

        Returns the number of rows removed. A retention of 0 (or less) keeps
        everything — for an instance whose auditors require an unbroken trail.
        """
        if days is None:
            raw = self.env['ir.config_parameter'].sudo().get_param(
                RETENTION_PARAM, default=DEFAULT_RETENTION_DAYS)
            try:
                days = int(raw)
            except (TypeError, ValueError):
                _logger.warning(
                    "MCP: %s=%r is not a number — keeping the default of %d days.",
                    RETENTION_PARAM, raw, DEFAULT_RETENTION_DAYS)
                days = DEFAULT_RETENTION_DAYS
        if days <= 0:
            return 0
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        domain = [('create_date', '<', cutoff)]

        # Delete in committed batches rather than one transaction. Turning
        # retention back on after a long spell of "keep everything" can leave
        # millions of stale rows; a single unlink of that would hold locks for
        # minutes and risk the worker being killed mid-way, achieving nothing.
        # Batches make progress durable and bound the per-run cost — the cron
        # is daily, so a large backlog drains over a few runs.
        removed = 0
        for _batch in range(GC_MAX_BATCHES_PER_RUN):
            stale = self.sudo().search(domain, limit=GC_BATCH_SIZE)
            if not stale:
                break
            removed += len(stale)
            stale.unlink()
            self.env.cr.commit()
        if removed:
            _logger.info("MCP: removed %d log rows older than %d days.", removed, days)
            if self.sudo().search_count(domain):
                _logger.info(
                    "MCP: more rows remain past the retention window — the next "
                    "daily run will continue.")
        return removed

    # ------------------------------------------------------------------
    # Append-only
    # ------------------------------------------------------------------
    def write(self, values):
        """The trail is append-only — an editable audit log is not an audit log."""
        raise UserError(_(
            "MCP log entries cannot be edited — the trail is append-only. "
            "Old entries are removed by the retention setting "
            "(Settings → General Settings → MCP Server)."))
