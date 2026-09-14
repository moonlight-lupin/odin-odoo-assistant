"""MCP settings — OAuth, structural-protection, no-delete and logging slots.

Surfaced under Settings → General Settings → MCP Server. Stored as
ir.config_parameter so they persist across restarts.

* ``/mcp/v1`` accepts a standard Odoo API key (Bearer) always, and — when the
  OAuth toggle is on and an Issuer URL is set — OAuth 2.1 access tokens. With
  OAuth off, the ``.well-known`` / ``/oauth/*`` endpoints return 404.
* When the structural-protection toggle is on (the default), the write
  tools refuse to operate on a denylist of structural models (``ir.*``,
  security tables, …) — see ``generic_tools._is_structural_model``. Reads
  are always allowed.
* When record-deletion is off (the default), ``odoo_unlink`` is refused on
  every model — archive/cancel instead.
* The activity log (``custom.mcp.log``) records every tool call. Its level,
  whether argument payloads are stored, and how long rows are kept are all
  settings here.
"""
from odoo import api, fields, models


# Short-lived access tokens limit damage on compromise; the per-hour refresh is
# fine for agents.
DEFAULT_ACCESS_TTL_MINUTES = 60
DEFAULT_REFRESH_TTL_DAYS = 30

# Ninety days covers a quarter-end review without the table growing unbounded.
DEFAULT_LOG_RETENTION_DAYS = 90


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    mcp_oauth_enabled = fields.Boolean(
        string='Enable MCP OAuth 2.1',
        config_parameter='odoo_module_mcp_server.oauth_enabled',
        help="When on, the MCP endpoint advertises an OAuth 2.1 Authorization "
             "Server at this Odoo instance and accepts both OAuth bearer tokens "
             "and Odoo API keys (needed for clients like Claude Desktop / "
             "Claude.ai custom connectors, which authenticate via OAuth). When "
             "off, only Odoo API keys are accepted and the .well-known and "
             "/oauth/* endpoints return 404.")
    mcp_oauth_issuer_url = fields.Char(
        string='OAuth Issuer URL',
        config_parameter='odoo_module_mcp_server.oauth_issuer_url',
        help="Public base URL of this Odoo instance, e.g. "
             "https://odoo.example.com — must include the scheme and match the "
             "URL clients use to reach /mcp/v1.")
    mcp_oauth_access_ttl = fields.Integer(
        string='Access Token TTL (minutes)',
        config_parameter='odoo_module_mcp_server.oauth_access_ttl_min',
        default=DEFAULT_ACCESS_TTL_MINUTES)
    mcp_oauth_refresh_ttl = fields.Integer(
        string='Refresh Token TTL (days)',
        config_parameter='odoo_module_mcp_server.oauth_refresh_ttl_day',
        default=DEFAULT_REFRESH_TTL_DAYS)

    mcp_allow_record_deletion = fields.Boolean(
        string='Allow record deletion',
        config_parameter='odoo_module_mcp_server.allow_record_deletion',
        default=False,
        help="Off by default. When off, the odoo_unlink tool (and any "
             "call_method targeting unlink) refuses to hard-delete records "
             "on EVERY model — archive the record (active=False) or cancel "
             "it instead. This mirrors a no-destructive-delete policy. Turn "
             "on only if agents must permanently delete records.")

    mcp_protect_structural = fields.Boolean(
        string='Protect structural models',
        default=True,
        help="When on (the default), the MCP write tools "
             "(odoo_create / odoo_write / odoo_unlink / odoo_execute) "
             "refuse to operate on a denylist of structural models — "
             "ir.* (schema, views, actions, cron, modules, config "
             "parameters, …), res.users / res.groups / res.users.apikeys "
             "(auth), mail.template / mail.alias, base_automation.*, "
             "bus.*, and reference data (res.lang / res.currency / "
             "res.country). "
             "ir.attachment is explicitly carved out so file uploads "
             "still work. Reads are always allowed. Turn this off only "
             "if you trust the agent to do anything its Odoo user can do.")

    # -- Activity log ---------------------------------------------------
    mcp_log_level = fields.Selection(
        [('all', 'All calls'),
         ('error', 'Failures and policy blocks only'),
         ('off', 'Off')],
        string='Activity logging',
        config_parameter='odoo_module_mcp_server.log_level',
        default='all',
        help="What the MCP activity log records. 'All calls' is the full audit "
             "trail — who ran which tool, against which records, and what "
             "happened. 'Failures and policy blocks only' keeps just the calls "
             "that errored or were refused by a guardrail, which is a small "
             "fraction of the traffic. 'Off' records nothing.")

    mcp_log_retention_days = fields.Integer(
        string='Keep log entries for (days)',
        config_parameter='odoo_module_mcp_server.log_retention_days',
        default=DEFAULT_LOG_RETENTION_DAYS,
        help="A daily job deletes entries older than this. Set 0 to keep the "
             "trail forever — appropriate where auditors require an unbroken "
             "record, but the table then grows without bound.")

    mcp_log_payloads = fields.Boolean(
        string='Log call arguments',
        default=True,
        help="On by default. Stores each call's arguments and a summary of its "
             "result, with secrets redacted and long values truncated — this is "
             "what lets you see WHAT an agent wrote, not just that it wrote. "
             "Turn off to keep only the tool name, user, outcome and duration.")

    # ``config_parameter=`` cannot round-trip a Boolean whose default is True:
    # core ``set_values`` hands the Python False to ``set_param``, which unlinks
    # the row, and ``get_values`` then falls back to the field default — so the
    # switch springs back on and ``_check_writable`` never sees the 'False' it
    # tests for. Store the flag as an explicit string instead.
    PROTECT_STRUCTURAL_PARAM = 'odoo_module_mcp_server.protect_structural'
    LOG_PAYLOADS_PARAM = 'odoo_module_mcp_server.log_payloads'

    @api.model
    def get_values(self):
        res = super().get_values()
        params = self.env['ir.config_parameter'].sudo()
        res['mcp_protect_structural'] = params.get_param(
            self.PROTECT_STRUCTURAL_PARAM, default='True') != 'False'
        res['mcp_log_payloads'] = params.get_param(
            self.LOG_PAYLOADS_PARAM, default='True') != 'False'
        return res

    def set_values(self):
        super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        params.set_param(
            self.PROTECT_STRUCTURAL_PARAM,
            'True' if self.mcp_protect_structural else 'False')
        params.set_param(
            self.LOG_PAYLOADS_PARAM,
            'True' if self.mcp_log_payloads else 'False')

    def action_mcp_open_log(self):
        """Open the activity log from the settings page."""
        self.ensure_one()
        return self.env['ir.actions.act_window']._for_xml_id(
            'odoo_module_mcp_server.action_mcp_log')
