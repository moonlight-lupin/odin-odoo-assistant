# MCP Server

Exposes the running Odoo database to **Model Context Protocol** clients (Claude Desktop, Claude.ai
custom connectors, Cline, …) over HTTP at `POST /mcp/v1` (JSON-RPC 2.0). Every tool call runs as the
authenticated user, so record rules and `ir.model.access` enforce what the caller can see and do.

**Two authentication methods:**

* **Odoo API key** (`Authorization: Bearer <key>`, scope `rpc`) — always on. Best for personal
  scripts, Cline, CI, and Claude Desktop via the `mcp-remote` bridge.
* **OAuth 2.1** (opt-in) — required by the **Claude Desktop / claude.ai "custom connector"** flow,
  which authenticates via an OAuth Authorization Server (no static-key field). Per-user browser login,
  nothing shared. **→ Full team setup guide: [`OAUTH_SETUP.md`](OAUTH_SETUP.md).**

## Endpoint

`POST /mcp/v1`

Headers:

* `Authorization: Bearer <api-key-or-oauth-token>` — required.
* `Content-Type: application/json`

The body is a JSON-RPC 2.0 envelope, e.g.

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
```

Batched requests (a JSON array of envelopes) are also accepted.

## Authentication — Odoo API key

1. Top-right user menu → **My Profile** → **Account Security**.
2. Click **New API Key**, set a name (e.g. `MCP`) and an expiration date.
3. Copy the generated key — Odoo only shows it once.
4. Send it as `Authorization: Bearer <api-key>` to `/mcp/v1`. Done.

Right for personal scripts, local agents (Cline, Claude Desktop via `mcp-remote`) and CI jobs. For the
**Claude Desktop / claude.ai custom-connector** experience (per-user login, no key sharing), use
**OAuth** instead — see **[`OAUTH_SETUP.md`](OAUTH_SETUP.md)**.

## Structural-write protection (on by default)

The four write tools (`odoo_create` / `odoo_write` / `odoo_unlink` /
`odoo_execute`) refuse to operate on a denylist of *structural*
models — anything an agent doing transactional business work has no
business touching. Reads remain unrestricted (introspecting `ir.model`
or `ir.ui.view` is useful for agents).

Configured under **Settings → General Settings → MCP Server → Protect
structural models**. The toggle is **on by default**; turn it off only
if you trust the agent to do anything its Odoo user can do.

**The denylist:**

| Pattern | Examples | Why off-limits |
|---|---|---|
| `ir.*` (prefix) | `ir.model`, `ir.ui.view`, `ir.actions.server`, `ir.cron`, `ir.config_parameter`, `ir.module.module`, `ir.rule`, `ir.model.access`, `ir.translation`, `ir.mail_server` | Schema, views, server actions (can execute code), cron, modules, settings |
| `bus.*` (prefix) | `bus.bus`, `bus.presence` | Internal pub/sub |
| `base_automation.*` (prefix) | `base_automation` | Automation rules — can execute code |
| `res.groups`, `res.groups.privilege` | — | Permission structure |
| `res.users`, `res.users.apikeys`, `res.users.log`, `res.users.identitycheck`, `res.users.settings` | — | Auth (incl. minting new API keys) |
| `res.lang`, `res.currency`, `res.currency.rate`, `res.country`, `res.country.group`, `res.country.state` | — | Reference data |
| `mail.template`, `mail.alias` | — | Templates can embed Python; aliases route inbound mail |

**Explicit carve-outs** (matched by a prefix above but still writable):

* `ir.attachment` / `ir.attachment.url` — uploading files / PDFs to
  records is everyday transactional work.

**What still gates everything else**: Odoo's normal `ir.model.access` +
record rules. The denylist is *in addition to*, not *instead of*, the
per-user permissions. So even with protection off you'd still need a
user with the right groups to mutate `ir.model.fields`.

**What it refuses to do exactly**: any call to `odoo_create`,
`odoo_write`, `odoo_unlink`, or `odoo_execute` with a denylisted
model. The error message is explicit:

> `Refusing to modify structural model 'ir.config_parameter' — MCP is in
> transactional mode (Settings → General Settings → MCP Server →
> 'Protect structural models'). Toggle that off if you intentionally
> need structural access.`

## Tools shipped

Fifteen generic Odoo tools, each running as the authenticated user. The names match the **external
MCP server 1:1** so the same Odin skill/playbooks drive either (there's no `odoo_connect` here — the
API key authenticates you; `odoo_whoami` confirms the session):

| Tool | Purpose |
|---|---|
| `odoo_whoami` | Current user + db + base URL + accessible companies (confirm the connection). |
| `odoo_models_list` | List models the user can read (optionally filtered by name). |
| `odoo_fields_get` | Introspect a model's fields. |
| `odoo_search_read` | Search + read in one call. |
| `odoo_search_count` | Count records matching a domain. |
| `odoo_read` | Read specific records by id. |
| `odoo_read_group` | Aggregate-read (sums, counts, grouped by). |
| `odoo_name_search` | Autocomplete-style lookup by display name. |
| `odoo_create` | Create one or many records. |
| `odoo_write` | Update records by id. |
| `odoo_archive` | Archive / unarchive (`active`) — the non-destructive "remove". |
| `odoo_cancel` | Cancel a workflow record (`action_cancel` / `button_cancel`). |
| `odoo_unlink` | Delete records by id (refused by default — see record-deletion control). |
| `odoo_render_report` | Render a QWeb report natively in-process → base64 (no HTTP). |
| `odoo_execute` | Call any **public** model method via call_kw (private `_`-prefixed refused). |

The read tools (`search_read` / `search_count` / `read` / `read_group` / `name_search`) accept an
optional **`company_id`** — when set, the model is read with `with_company(company_id)` so
**company-dependent fields resolve for that company**. This matters in Odoo 18 for
`account.account.code` (and `display_name`), which are computed per company and otherwise come back
blank when read in a different company's context.

Bridge modules (e.g. `custom_reporting_engine_mcp`) register higher-level tools on
top — discoverable via `tools/list`.

## Quick test — three curl calls

```bash
KEY="paste-your-api-key-here"
URL="http://localhost:8069/mcp/v1"

# 1. Handshake
curl -s -X POST "$URL" -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
          "params":{"protocolVersion":"2024-11-05","capabilities":{},
                    "clientInfo":{"name":"curl","version":"0"}}}'

# 2. List the tools
curl -s -X POST "$URL" -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# 3. Count the partners the API user can see
curl -s -X POST "$URL" -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
          "params":{"name":"odoo_search_count",
                    "arguments":{"model":"res.partner","domain":[]}}}'
```

## Wiring an MCP client

Most clients (Claude Desktop, Cline, …) take a JSON config block of MCP
servers. For an HTTP server like this one:

```json
{
  "mcpServers": {
    "odoo": {
      "url": "https://your-odoo/mcp/v1",
      "headers": {
        "Authorization": "Bearer <YOUR_API_KEY>"
      }
    }
  }
}
```

(Some clients want `transport: "http"` or similar — consult the client docs.)

## Security notes

* **The API key is a credential.** Treat it like a password — anyone holding
  the key can do whatever its owner can do in Odoo.
* **Per-user permissions still apply.** Give your API user the minimum groups
  needed — e.g. *Reporting User* alone for read-only reporting access.
* **`odoo_unlink`, `odoo_write`, `odoo_execute` are full-fat.** Methods
  that post invoices, send emails, etc. can be called if the user has access.
  Scope users appropriately for your trust model.
* **Always run behind HTTPS** in production — a Bearer key sent over plain HTTP
  is exposed in transit.
* The endpoint uses `csrf=False` (it's an API, not a form) and runs without
  saving an Odoo session — each request is independent.

## Adding your own tools

In any module that depends on `odoo_module_mcp_server`, register a tool at import time:

```python
from odoo.addons.odoo_module_mcp_server.mcp_registry import register_tool

@register_tool(
    name='my_module.do_something',
    description='What this tool does, written for an LLM to read.',
    input_schema={
        'type': 'object',
        'properties': {'foo': {'type': 'string'}},
        'required': ['foo'],
    },
)
def do_something(env, args):
    # `env` is the request env (already as the API user)
    return {'result': 'ok', 'echo': args['foo']}
```

The tool is advertised by `tools/list` and dispatched by `tools/call`. The
function should return a JSON-serialisable value (recordsets and dates are
auto-serialised).
