# Odoo MCP Server

MCP server that wraps your Odoo XML-RPC API and exposes generic CRUD + escape-hatch tools to Claude. Tested against Odoo 18 only — 17 and 19 are untested and reports are welcome. Run it locally as a stdio subprocess, or host it as a long-running HTTP service in Docker (see [Docker](#docker-hosted-http-service)).

## Tools exposed

- `odoo_connect` — authenticate with **your own** username + api_key; starts the session (url/db come from config)
- `odoo_disconnect` — clear the cached session so another user can connect
- `odoo_whoami` — current connection check + list of companies
- `odoo_search_read` — query any model with domain, fields, limit, offset, order
- `odoo_search_count` — count matches before fetching
- `odoo_read` — fetch records by ID
- `odoo_fields_get` — discover model schema
- `odoo_create` — create a record (defaults to draft state for workflow models)
- `odoo_write` — update records
- `odoo_archive` — archive/unarchive records (`active=False`) — the non-destructive "remove"
- `odoo_cancel` — cancel workflow records (`action_cancel`/`button_cancel`)
- `odoo_render_report` — render a QWeb report (pdf/html/text) over HTTP and return it as base64
- `odoo_execute` — generic escape hatch for any model.method (policy controls still apply)
- `odoo_audit_tail` — read back the server's own audit trail (local file only; touches no Odoo data)

> There is **no delete tool** — see Controls below.

The read tools (`odoo_search_read` / `odoo_search_count` / `odoo_read` / `odoo_read_group` /
`odoo_name_search`) take an optional **`company_id`** — when set, the read runs in that company's
context so **company-dependent fields resolve**. In Odoo 18 `account.account.code` (and
`display_name`) is computed per company, so without `company_id` it comes back blank for any
non-default company. Pass the owning company id to get `code`/`display_name` like `12501 Prepayments`.

## Folder layout

```
odoo-assistant/
├── odoo_config.json         ← url + db (edited by the GUI); credentials are per-user at runtime
├── odin/                    ← the skill
│   ├── SKILL.md             ← the "odin" skill (drives these MCP tools)
│   ├── playbooks/           ← the 13 playbooks
│   └── references/          ← Odoo 18 model/API reference docs the skill loads
└── odoo-mcp/
    ├── server.py                 ← the MCP server (stdio or HTTP)
    ├── audit.py                   ← logging interface (audit trail + diagnostics)
    ├── config_gui.py             ← GUI to edit odoo_config.json
    ├── requirements.txt
    ├── Dockerfile                ← builds the HTTP service image
    ├── entrypoint.sh             ← seeds an example config on first run
    ├── docker-compose.yml        ← runs the hosted service + config volume
    ├── .dockerignore
    ├── odoo_config.example.json  ← committed template (no secrets)
    └── README.md                 ← this file
```

The server reads `odoo_config.json` from its parent folder by default. Override with the `ODOO_CONFIG_PATH` environment variable if you keep the config elsewhere (the Docker image sets it to `/config/odoo_config.json`).

## Config GUI

Run the GUI to set the **shared instance settings** — the server **URL** and **database**:

```powershell
# from the odoo-mcp folder — no venv or `mcp` package needed (stdlib Tkinter only)
python config_gui.py
```

By design the GUI manages **only `url` and `db`**. The **`username` and `api_key` are personal credentials that each user supplies themselves** — add them to `odoo_config.json` directly (or have the skill prompt you). The GUI never displays or edits them, and **Save preserves** whatever credentials are already in the file.

- **Fetch DBs** — queries the server (URL only) for its database list and drops it into a dropdown, so you don't have to type the db name. Gracefully reports if the server has the database list disabled.
- **Test Connection** — authenticates using the username/api_key already present in the config file (read-only — never shown). If those aren't set yet, it tells you to add them.
- **Save** — writes `url` + `db` into `odoo_config.json`, merging into the existing file so credentials survive.

Use `File → Open` to point it at a config in a different location, or set `ODOO_CONFIG_PATH` before launching. After saving, restart the MCP server so it picks up the changes.

`odoo_config.json` ends up with all four keys — the first two from the GUI, the last two from you:

```json
{
  "url": "https://your-odoo-server",   ← set via GUI
  "db": "your_database_name",          ← set via GUI
  "username": "you@company.com",       ← you provide
  "api_key": "your_odoo_api_key"       ← you provide
}
```

## The skill — Odin

- **`odin`** (`../odin/SKILL.md`) — drives the `odoo_*` MCP tools below, plus playbooks
  (build-context, generate-reports, month-end-review, and more). Triggered by "hi Odin" or any
  Odoo task. Requires this MCP server to be connected.

## Docker (hosted HTTP service)

Run the server as a long-running HTTP service. Clients connect to it over the network at `http://<host>:8000/mcp` (streamable-http transport) — no per-client Python install.

### Config: gitignored, auto-seeded

The live config is **never committed** — `.gitignore` excludes `odoo_config.json` everywhere and the `odoo-mcp/config/` bind-mount dir. Only `odoo_config.example.json` (no secrets) is tracked.

Compose mounts `./config` into the container at `/config`. **On a fresh run, if `config/odoo_config.json` doesn't exist yet, the entrypoint seeds it from the example template** and logs a notice. You then edit it with your real `url`/`db` and restart. Credentials (`username`/`api_key`) are **not** in this file — each user supplies their own at runtime via the `odoo_connect` tool.

### Build & run with Compose

```powershell
# from the odoo-mcp folder
docker compose up -d --build
docker compose logs -f          # watch for the "created an example" notice on first run
# edit .\config\odoo_config.json  → set your real url + db
docker compose restart
```

The endpoint is then live at `http://localhost:8000/mcp`. Override the port by editing `docker-compose.yml` (`ports:` and `MCP_PORT`).

### Distribute as a `.tar` (offline / another host)

Build and export the image to a tarball:

```powershell
docker build -t odoo-mcp:latest .
docker save -o odoo-mcp.tar odoo-mcp:latest      # ~57 MB; gitignored (*.tar)
```

On the target machine, load and run it:

```powershell
docker load -i odoo-mcp.tar
docker run -d --name odoo-mcp -p 8000:8000 -v "$PWD/config:/config" odoo-mcp:latest
```

(Or copy `docker-compose.yml` alongside and run `docker compose up -d` — Compose will use the loaded `odoo-mcp:latest` image since the `image:` tag matches.)

### Environment variables

| Var | Default | Meaning |
|-----|---------|---------|
| `MCP_TRANSPORT` | `http` (in image) | `http`/`streamable-http`, `sse`, or `stdio` |
| `MCP_HOST` | `0.0.0.0` | bind address |
| `MCP_PORT` | `8000` | listen port |
| `MCP_PATH` | `/mcp` | HTTP endpoint path |
| `ODOO_CONFIG_PATH` | `/config/odoo_config.json` | config location inside the container |
| `MCP_HTTP_USER_AGENT` | a Chrome UA string | User-Agent sent on `odoo_render_report` HTTP login/download (avoids Cloudflare 403 on Odoo Online); override if needed |
| `MCP_AUDIT_LOG` | `/config/mcp-audit.jsonl` (in image) | audit-trail file; `off` disables the file sink (stderr only) |
| `MCP_AUDIT_LEVEL` | `all` | `off`, `error` (failures + policy blocks only), or `all` |
| `MCP_AUDIT_PAYLOADS` | `true` | `false` logs only tool/user/outcome/duration — no argument values |
| `MCP_AUDIT_MAX_BYTES` | `5242880` | rotate the trail at this size |
| `MCP_AUDIT_BACKUPS` | `3` | how many rotated files to keep |
| `MCP_AUDIT_STDERR` | `true` | mirror each record to stderr (`docker logs`) |
| `MCP_LOG_LEVEL` | `INFO` | level for the diagnostic logger (connection/auth/transport) |

### Register the hosted server with a client

Point your MCP client at the URL instead of a local command. For Claude Code:

```powershell
claude mcp add --transport http odoo http://localhost:8000/mcp
```

Or in a `claude_desktop_config.json`-style config that supports HTTP servers:

```json
{
  "mcpServers": {
    "odoo": { "type": "http", "url": "http://localhost:8000/mcp" }
  }
}
```

> **Smoke test the running service** (proves transport + tools without a client):
> ```bash
> curl -s -X POST http://localhost:8000/mcp \
>   -H "Content-Type: application/json" \
>   -H "Accept: application/json, text/event-stream" \
>   -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
> ```
> You should get an SSE `result` with `"serverInfo":{"name":"odoo-assistant"...}`.

> **Security note:** the HTTP endpoint has no built-in auth, and it binds `0.0.0.0` in the container. Keep the published port on a trusted network / localhost, or front it with a reverse proxy (TLS + auth) before exposing it. Auth still happens at the Odoo layer — each user must `odoo_connect` with their own API key — but anyone who can reach the port can *attempt* calls.

## Install (local, without Docker)

You need Python 3.10+ on your machine. Two options:

### Option A — `uv` (cleanest)

```powershell
# from the odoo-mcp folder
uv venv
uv pip install -r requirements.txt
```

### Option B — `pip` + venv

```powershell
cd "C:\Users\user\Documents\Claude\Projects\Useful tools\odoo-mcp"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Credential model

The server splits configuration in two:

- **`url` + `db`** come from `odoo_config.json` (set with `config_gui.py`). These are the shared instance settings.
- **`username` + `api_key`** are supplied **by each user at runtime**: the user (via the skill) calls the `odoo_connect` tool with their own credentials, and the server uses them to authenticate to the Odoo database. The session is cached for the life of the server process. Use `odoo_disconnect` to switch users.

> **Backward compatibility:** if `odoo_config.json` still contains `username`/`api_key`, the other tools auto-connect from the config when no explicit `odoo_connect` has been made — so older single-user setups keep working.

## Controls (safety policy)

The server enforces two guardrails, **both ON by default**, on every operation (including `odoo_execute`):

1. **No model tampering** (`allow_model_changes`, default `false`) — writes to technical/structural models are blocked, so the Odoo data model can't be altered. Reads are still allowed. Protected: `ir.model*`, `ir.module*`, `ir.ui.view`, `ir.ui.menu`, `ir.actions*`, `ir.rule`, `ir.cron`, `ir.config_parameter`, `base.automation`, `studio*`. This blocks adding/altering fields & models, installing/upgrading modules, editing views/actions/cron/automation.
2. **No deletion** (`allow_record_deletion`, default `false`) — `unlink` is blocked on **every** model. There is no delete tool. To "remove" data, use:
   - **`odoo_archive`** — sets `active=False` (hidden, reversible), or
   - **`odoo_cancel`** — runs the record's workflow cancel (`action_cancel`/`button_cancel`).

Each control can be lifted per instance by setting the flag to `true` in `odoo_config.json`:

```json
{
  "url": "...",
  "db": "...",
  "allow_record_deletion": false,
  "allow_model_changes": false
}
```

> Note: these controls live in the **MCP server**, so they apply to everything that goes through it (including the `odin` skill).

## Logging (audit trail + diagnostics)

Every tool call is recorded. This is the "what did the agent actually do in our books"
record — and, because the guardrails below refuse things, it is also the record of what
it *tried* to do and was stopped from doing.

Two streams, both written to **stderr-safe** paths (on stdio, stdout is the MCP protocol
channel and must stay byte-clean):

1. **Audit trail** — one JSON object per tool call, appended to a rotating JSONL file and
   mirrored to stderr.
2. **Diagnostics** — the `odoo_mcp` logger: connection, authentication, transport and
   configuration events.

A record looks like this (one line, pretty-printed here):

```json
{
  "ts": "2026-09-14T09:12:33.481920+00:00",
  "kind": "tool_call",
  "seq": 42,
  "pid": 1,
  "session": {"url": "https://odoo.example.com", "db": "prod", "uid": 7,
              "username": "you@company.com"},
  "tool": "odoo_write",
  "outcome": "ok",
  "duration_ms": 61.4,
  "args": {"model": "account.move", "ids": [1841], "values": {"ref": "INV-2026-0031"}},
  "result": true
}
```

`outcome` is one of:

| Outcome | Meaning |
|---------|---------|
| `ok` | the call succeeded |
| `blocked` | refused by a policy guardrail (deletion / model tampering) before reaching Odoo |
| `error` | Odoo (or the transport) returned a fault |

### What is and isn't written

- **Credentials are never written.** Any key that looks like a secret — `api_key`,
  `password`, `token`, `authorization`, `client_secret`, … at any nesting depth — is
  replaced with `***redacted***` before serialisation. The `session` block is a
  whitelist (url, db, uid, username): the API key is structurally absent from it.
- **Payloads are bounded.** Strings over 512 chars and lists over 20 items are truncated
  with an explicit marker, nesting deeper than 8 levels collapses, and byte payloads
  (e.g. a rendered PDF) become `<N bytes>`. A result set of records is summarised to
  `{"type": "records", "count": N, "ids": [...]}` — the ids are what you need to pull the
  records back up; the field values are already in Odoo.
- **Logging never breaks a tool call.** An unwritable path, a full disk or an
  unserialisable payload degrades the record, warns once, and the call proceeds.

### Configuration

Env vars (see the table above) win over the equivalent `odoo_config.json` keys:

```json
{
  "url": "...",
  "db": "...",
  "audit_log": "mcp-audit.jsonl",
  "audit_level": "all",
  "audit_payloads": true,
  "audit_max_bytes": 5242880,
  "audit_backups": 3,
  "log_level": "INFO"
}
```

A **relative** `audit_log` resolves against the folder holding `odoo_config.json` (not the
process cwd, which on stdio is whatever the MCP client launched from). With nothing set,
the trail lands next to the config as `mcp-audit.jsonl`. Set `"audit_log": false` or
`MCP_AUDIT_LOG=off` to keep stderr only.

> The trail is gitignored (`mcp-audit.jsonl*`) — it is operational data about your books.
> Treat it like a log of financial activity: it names users, models and record ids.

### Reading it back

Ask Claude, via the `odoo_audit_tail` tool:

```
odoo_audit_tail(limit=20, outcome="blocked")   → everything a guardrail refused
odoo_audit_tail(tool="odoo_write")             → every write this server made
```

Or straight from the file:

```bash
# last 20 calls, newest last
tail -n 20 config/mcp-audit.jsonl | jq -c '{ts, tool, outcome, user: .session.username}'

# everything a guardrail refused
jq -c 'select(.outcome == "blocked") | {ts, tool, error: .error.message}' config/mcp-audit.jsonl

# every write to account.move
jq -c 'select(.args.model == "account.move" and .tool == "odoo_write")' config/mcp-audit.jsonl
```

In Docker the trail is inside the `/config` volume, so it survives rebuilds, and
`docker logs odoo-mcp` shows the same records on stderr.

## Smoke test

Before wiring it into Claude, confirm the config is readable and the server can reach + authenticate to Odoo. Pass your own credentials:

```powershell
# from the odoo-mcp folder, with the venv activated
python -c "from server import _authenticate; print(_authenticate('you@company.com', 'YOUR_API_KEY')['uid'])"
```

You should see a numeric UID. An authentication error means wrong credentials or db; a network error means your machine can't reach the Odoo host. To just check the config file parses (url + db present) without credentials:

```powershell
python -c "from server import _load_cfg; c=_load_cfg(); print('url+db OK:', c['url'], c['db'])"
```

## Register with Claude

Find your Claude MCP config file. On Windows it's usually:

```
%APPDATA%\Claude\claude_desktop_config.json
```

Add an entry under `mcpServers`. Replace the venv python path with whatever you actually used.

```json
{
  "mcpServers": {
    "odoo": {
      "command": "C:\\Users\\user\\Documents\\Claude\\Projects\\Useful tools\\odoo-mcp\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\user\\Documents\\Claude\\Projects\\Useful tools\\odoo-mcp\\server.py"
      ]
    }
  }
}
```

If you used `uv`:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\Users\\user\\Documents\\Claude\\Projects\\Useful tools\\odoo-mcp",
        "run",
        "python",
        "server.py"
      ]
    }
  }
}
```

Restart Claude. The new tools appear with the prefix `odoo_*`. Run "list my Odoo companies" to confirm.

## Why this works (and the sandbox didn't)

Claude's built-in shell runs in a network-restricted sandbox that can't reach arbitrary external hosts. An MCP server runs as a normal local process on your machine, so it has the same network access you do — including your Odoo instance. The MCP protocol just hands tool calls back and forth between Claude and the local process.

## Hardening (optional)

The server currently disables TLS hostname verification (`CERT_NONE`) to support self-hosted Odoo instances with self-signed certs. If your instance has a valid cert (e.g. Odoo Online / `*.run-odoo.com`), for production use you can flip the SSL block to:

```python
ctx = ssl.create_default_context()
transport = xmlrpc.client.SafeTransport(context=ctx)
```

That enforces real cert validation.

## Troubleshooting

- **"ModuleNotFoundError: No module named 'mcp'"** — venv isn't active or `pip install` didn't run.
- **"Authentication failed"** — wrong db name or expired API key. Generate a new one in Odoo: Settings → Users → My Profile → Account Security → New API Key (developer mode required).
- **"Connection refused" / DNS error** — your machine can't reach the Odoo URL; not an MCP problem.
- **Claude doesn't see the tools** — check the config JSON is valid (use a linter), restart Claude fully, and check logs at `%APPDATA%\Claude\logs\`.
