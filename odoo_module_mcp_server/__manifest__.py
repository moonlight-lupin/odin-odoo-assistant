{
    'name': 'MCP Server',
    'version': '18.0.5.0.0',
    'category': 'Productivity',
    'summary': 'Expose Odoo data and tools over the Model Context Protocol.',
    'description': """
MCP Server.

Adds a Model Context Protocol (MCP) HTTP endpoint to Odoo so external clients
(Claude Desktop, Claude.ai custom connectors, Cline, …) can call into the
database as an authenticated user. The endpoint speaks JSON-RPC 2.0 over POST at
/mcp/v1 and accepts either:

* a standard Odoo API key (Authorization: Bearer …, scope 'rpc') — always on; or
* an OAuth 2.1 access token issued by this server — opt-in (needed by clients
  like Claude Desktop / Claude.ai whose custom-connector flow requires an OAuth
  Authorization Server). Enable under Settings → General Settings → MCP Server.

Every tool call runs as the token / key owner — record rules and ir.model.access
enforce what the user can see and do. Tool names match the external MCP server
1:1 so the same client skill drives either. Fifteen generic tools are shipped:

* odoo_whoami (confirm session)
* odoo_models_list / odoo_fields_get
* odoo_search_read / odoo_search_count / odoo_read / odoo_read_group / odoo_name_search
* odoo_create / odoo_write / odoo_archive / odoo_cancel / odoo_unlink
* odoo_render_report (native, in-process) / odoo_execute (call_kw escape hatch)

The read tools take an optional company_id to read in that company's context
(needed so Odoo-18 company-dependent fields like account.account.code resolve).

OAuth 2.1 (opt-in): when enabled + an Issuer URL is set, the server exposes
/.well-known/oauth-protected-resource, /.well-known/oauth-authorization-server,
/oauth/register (Dynamic Client Registration), /oauth/authorize (PKCE S256) and
/oauth/token (authorization_code + refresh_token). When off, those return 404
and /mcp/v1 accepts only the Odoo API-key bearer flow.

Activity log: every tool call is recorded in custom.mcp.log (Settings →
Technical → MCP Activity Log) — who called it, which tool, against which model
and record ids, the outcome (OK / blocked by policy / access denied / error)
and the duration, plus the redacted call arguments and a summary of the result.
Rejected bearer tokens are recorded too. Rows are written on their own cursor,
so a failed call still leaves a trail; secrets are masked and payloads
truncated before storage; and logging can never break a request. Level,
argument logging and retention (default 90 days, trimmed by a daily cron) are
settings. The redaction and truncation rules match the external MCP server's,
so a record reads the same whichever server produced it.

Every tool is advertised with all four MCP behavioural hints (readOnlyHint /
destructiveHint / idempotentHint / openWorldHint), so a client can tell the user
whether a call only reads the ledger or can overwrite it before it runs.

Bridge modules can register more specialised tools via ``register_tool`` from
``odoo_module_mcp_server.mcp_registry``, which requires those four hints.
""",
    'author': 'Odoo Expansions',
    'website': 'https://example.com',
    'license': 'LGPL-3',  # links Odoo (LGPL-3); the rest of the repo is Apache-2.0
    'depends': ['base', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'data/mcp_log_cron.xml',
        'views/oauth_consent_templates.xml',
        'views/mcp_log_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
}
