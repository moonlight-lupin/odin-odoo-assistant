# Odoo Assistant — MCP + **Odin** skill

> **Status:** runs against a live instance and the policy controls are pinned by 86 offline tests —
> but treat it as early software. Review what it drafts before you post anything to your books.
> **Licence:** Apache-2.0 · **Requires:** Python 3.11+ · Not affiliated with or endorsed by Odoo S.A.
>
> **Tested on Odoo 18 only.** Nothing here deliberately targets a single version, but the field
> names and model behaviour were verified against 18 and nowhere else. Reports and PRs covering
> **Odoo 17 or 19** are very welcome — see [Compatibility](#compatibility).

A finance assistant for **Odoo**, in two components:

1. **The MCP** — exposes Odoo to an AI client as safe `odoo_*` tools (with built-in policy controls).
   It ships in **two interchangeable forms** — pick one:
   - **`odoo-mcp/`** — an **independent server** that wraps Odoo's XML-RPC API (local stdio or hosted
     HTTP/Docker). Runs *outside* Odoo; nothing to install in the database.
   - **`odoo_module_mcp_server/`** — an **Odoo module** you install *inside* the database; it serves
     MCP over JSON-RPC at `/mcp/v1` from within Odoo itself.
2. **`odin/`** — **Odin**, the skill that drives those tools: a set of detailed, finance-ops
   **playbooks** (bookkeeping, reconciliation, close, review, reporting) an AI executes on your behalf.

The two MCP forms expose equivalent tools and enforce the **same policy controls**, so Odin works
against either without changes.

```
                          ┌─ Option A: odoo-mcp/  (independent server, XML-RPC) ─┐
   Odoo 18  ⇄  …  ⇄       │                                                      │  ⇄  MCP client  ⇄  Odin skill
                          └─ Option B: odoo_module_mcp_server/ (in-Odoo, /mcp/v1)┘                  (playbooks)
```

---

## 1. The MCP — choose a form

Both expose the same kind of generic `odoo_*` data tools and enforce the same controls. Choose by where
you can run code:

| | **`odoo-mcp/` — independent server** | **`odoo_module_mcp_server/` — Odoo module** |
|---|---|---|
| **Runs** | Outside Odoo (local stdio **or** hosted HTTP/Docker) | Inside Odoo (in-process) |
| **Talks to Odoo via** | XML-RPC (`execute_kw`) | Direct ORM (no RPC hop) |
| **Auth** | Per-user `username` + `api_key` at runtime (`odoo_connect`); not stored | Odoo **API key** (`Authorization: Bearer`) — always on; **OAuth 2.1 + PKCE** opt-in for Claude.ai connectors |
| **Install** | Host the process; `url`+`db` in `odoo_config.json` | Drop the folder in an addons path and install the app |
| **Report rendering** | HTTP render — *limited on Odoo Online* (use deep links) | **Native** — shares the logged-in session |
| **Audit trail** | Rotating JSONL file + stderr; read back with `odoo_audit_tail` or `jq` | `custom.mcp.log` table with list/form views, filters and a retention cron |
| **Best when** | You **can't install modules** (e.g. Odoo Online/SaaS) or want MCP kept outside Odoo | You **control the instance** (self-hosted / Odoo.sh) and want native, no separate hosting |

**Shared policy controls (both ON by default):**
- **No record deletion** — `unlink` blocked everywhere (use archive/cancel).
- **No model tampering** — writes to technical/auth models blocked (schema, modules, views, actions,
  cron, automation, **`res.users`/`res.groups`/API keys**, mail templates/aliases). `ir.attachment`
  stays writable so file uploads work.
- **Audit trail** — every tool call recorded: who, which tool, which records, the outcome
  (**ok** / **blocked by policy** / **error**) and the duration. Secrets are redacted and payloads
  truncated before anything is written, and logging can never break a call. Both forms use the same
  redaction and truncation rules, so a record reads the same either way — see
  **[Logging](#logging-audit-trail)**.

**Option A — independent server (`odoo-mcp/`):** **16 tools** — connect/disconnect/whoami,
search_read/search_count/read/**read_group**/**name_search**/fields_get, create/write/archive/cancel,
render-report, execute, **audit-tail**. Config is split: `url`+`db` in `odoo_config.json` (set by the host via
`odoo-mcp/config_gui.py`); each user supplies **their own** credentials at runtime. See
**[`odoo-mcp/README.md`](odoo-mcp/README.md)** for local install, the config GUI, Docker/Compose, the
`.tar` distribution, and connecting a client.

**Option B — Odoo module (`odoo_module_mcp_server/`):** generic tools
(models_list/fields_get/search_read/search_count/read/read_group/name_search/create/write/unlink/call_method)
served at `POST /mcp/v1`. Toggle the controls and OAuth under **Settings → General Settings → MCP
Server**. See **[`odoo_module_mcp_server/README.md`](odoo_module_mcp_server/README.md)**.

## 2. `odin/` — the skill
Odin (triggered by "hi Odin" or any Odoo task) drives the `odoo_*` tools through **playbooks** —
multi-step procedures written to be executed reliably (concrete tool calls, confirmed Odoo-18 field
names, draft-only writes, links + Excel reports). See **[`odin/SKILL.md`](odin/SKILL.md)**.

**Playbooks follow a finance-ops lifecycle (15):**
| Stage | Playbooks |
|-------|-----------|
| **1 · Set up & understand** | build-context · **setup-new-entity** (company + CoA + default-account clean-up) · **migrate-entity-books** (opening balances take-on) · design-bookkeeping-rules |
| **2 · Record (data in)** | process-vendor-bills · create-customer-invoices · import-bank-statement · third-party-conversion |
| **3 · Settle & adjust** | bank-reconciliation · posting-closing-journal-entries · pay-vendor-bill |
| **4 · Run the cycle** | monthly-book-keeping (orchestrator) |
| **5 · Review, report & assure** | month-end-review · audit-trail-review · generate-reports |

**Key principles baked in:**
- **Draft-only & Safety Protocol** — bookkeeping playbooks create drafts, confirm, and **never post or
  pay** without explicit instruction.
- **Per-company rulebook** — `design-bookkeeping-rules` produces `bookkeeping-rules/<company>.md` (coding,
  cadence, naming, CoA & third-party mappings) that the other playbooks consult. One company per file.
- **Cash & settlement model** — cash is recognised from the **bank statement**; **bank-reconciliation
  settles** the open AR/AP/payments. A bill/invoice is settled when its bank line is reconciled.
- **Always output** the record link + a simple **Excel action report** for anything created/changed.

---

## Quick start
1. **Stand up the MCP** — *either* host the **independent server** (configure `odoo_config.json` via
   `odoo-mcp/config_gui.py`, then run local stdio or `docker compose up`), *or* install the **Odoo
   module** (`odoo_module_mcp_server/`) and generate an API key. See the respective README.
2. **Connect a client** to the MCP (the `odoo_*` tools appear) and install/enable the **Odin** skill.
3. **Talk to Odin** — e.g. *"hi Odin"*, then *"build context"*, *"month-end review for March"*,
   *"process these vendor bills"*. Odin asks for your working folder and reads your credentials from a
   file in it (`odin_credentials.txt`) to connect.

---

## Installation & connection guides

Pick **one** MCP form (A or B), wire it into your Claude client, then enable the Odin skill (C).
Full detail lives in each component's README — this is the practical path.

### A · Independent server (`odoo-mcp/`)

Wraps Odoo XML-RPC. Best when you **can't install Odoo modules** (e.g. Odoo Online/SaaS) or want MCP
kept outside Odoo. → full guide: **[`odoo-mcp/README.md`](odoo-mcp/README.md)**.

**Install & run** — two ways:

*Local (stdio, simplest):*
```powershell
cd odoo-mcp
python -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt
python config_gui.py            # set url + db → writes odoo_config.json
```

*Hosted (HTTP via Docker — multi-user, no per-client Python):*
```powershell
cd odoo-mcp
docker compose up -d --build
# edit .\config\odoo_config.json → set real url + db, then:
docker compose restart          # endpoint live at http://localhost:8000/mcp
```
(Offline/another host: `docker load -i odoo-mcp.tar` then `docker run -d -p 8000:8000 -v "$PWD/config:/config" odoo-mcp:latest`.)

**Connect in Claude:**

- **Claude Desktop — local stdio.** Edit `%APPDATA%\Claude\claude_desktop_config.json`:
  ```json
  {
    "mcpServers": {
      "odoo": {
        "command": "C:\\path\\to\\odoo-mcp\\.venv\\Scripts\\python.exe",
        "args": ["C:\\path\\to\\odoo-mcp\\server.py"]
      }
    }
  }
  ```
  Restart Claude Desktop. The `odoo_*` tools appear.

- **Claude Desktop / Claude Code — hosted HTTP.** Point the client at the URL instead of a command:
  ```powershell
  claude mcp add --transport http odoo http://localhost:8000/mcp     # Claude Code
  ```
  ```json
  { "mcpServers": { "odoo": { "type": "http", "url": "http://localhost:8000/mcp" } } }
  ```

> **Credentials:** the server only stores `url`+`db`. Each user supplies their **own** `username`+`api_key`
> at runtime via the `odoo_connect` tool (Odin reads them from `odin_credentials.txt` in your working
> folder). Generate an API key in Odoo: **Settings → Users → My Profile → Account Security → New API Key**
> (developer mode on).

### B · Odoo module (`odoo_module_mcp_server/`)

Runs **inside** Odoo, serving JSON-RPC at `POST /mcp/v1`. Best when you **control the instance**
(self-hosted / Odoo.sh) — gives native report rendering and OAuth for Claude.ai.
→ full guide: **[`odoo_module_mcp_server/README.md`](odoo_module_mcp_server/README.md)**.

**Install:**
1. Copy the `odoo_module_mcp_server/` folder into one of your Odoo **addons paths** (or mount it on Odoo.sh).
2. Restart Odoo, enable **developer mode**, then **Apps → Update Apps List**.
3. Search **“MCP Server”** and **Install**.
4. Generate an API key: **top-right user menu → My Profile → Account Security → New API Key**.
5. (Optional) review **Settings → General Settings → MCP Server** — the *Allow record deletion* and
   *Protect structural models* toggles, plus OAuth.

**Connect in Claude:**

- **Claude Desktop / Cline — API key (Bearer header).**
  ```json
  {
    "mcpServers": {
      "odoo": {
        "url": "https://your-odoo/mcp/v1",
        "headers": { "Authorization": "Bearer <YOUR_API_KEY>" }
      }
    }
  }
  ```

- **Claude.ai — custom connector (OAuth 2.1).** First enable it in Odoo: **Settings → General Settings →
  MCP Server → OAuth 2.1** on, set **Issuer URL** to the public base URL (must match the host clients use),
  Save. Then in **Claude.ai → Settings → Connectors → Add custom connector**: Server URL
  `https://your-odoo/mcp/v1`, Authentication **OAuth** (leave client id blank — Claude self-registers),
  **Connect** → log in to Odoo → **Approve**. Tool calls now run as your Odoo user.

> **Smoke test** (proves the endpoint without a client):
> ```bash
> curl -s -X POST https://your-odoo/mcp/v1 -H "Authorization: Bearer <KEY>" \
>   -H 'Content-Type: application/json' \
>   -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
> ```

### C · Enable the Odin skill (both forms)

1. Make the `odin/` skill available to your Claude client (it ships as a skill with `SKILL.md` +
   `playbooks/`). With an MCP connected, the `odoo_*` tools are already present.
2. Say **“hi Odin”** (or give it any Odoo task). On first use Odin asks for your **working folder**;
   put your `odin_credentials.txt` (`username` + `api_key`) there and Odin connects for you.
3. Drive it: *“build context”*, *“month-end review for March”*, *“process these vendor bills”*.

> Odin is MCP-form-agnostic — it calls the same `odoo_*` tools whether they come from the independent
> server or the Odoo module.

## Logging (audit trail)

Both forms record every tool call. For a bookkeeping agent this is the point: it is the record of
what was done in your books, by whom, and — because the controls above refuse things — what was
attempted and stopped.

| | **`odoo-mcp/` — independent server** | **`odoo_module_mcp_server/` — Odoo module** |
|---|---|---|
| **Where** | Rotating JSONL file + stderr | `custom.mcp.log` table |
| **Read it** | `odoo_audit_tail` tool, or `jq` over the file | Settings → Technical → **MCP Activity Log** (filter, group, drill in) |
| **Configure** | `MCP_AUDIT_LEVEL` / `MCP_AUDIT_LOG` env vars, or `audit_*` keys in `odoo_config.json` | Settings → General Settings → MCP Server → **Activity log** |
| **Retention** | Size-based rotation (default 5 MiB × 4 files = 20 MiB ceiling) | Age-based: daily cron, default 90 days (0 = keep everything) |
| **Also records** | Connection + authentication events | Rejected bearer tokens, protocol errors |

Shared by both, and the reason a record reads the same either way:

- **Credentials never land in the log.** Any key that looks like a secret (`api_key`, `password`,
  `token`, `authorization`, …) is masked at every nesting depth.
- **Payloads stay bounded.** Long strings and lists are truncated with explicit markers; a rendered
  PDF becomes `<N bytes>`; a result set of records collapses to `{count, ids}`. A 400 KB report
  download is a ~900-byte log record.
- **Logging never breaks a call.** A broken sink degrades the record, not the operation.
- **`blocked` ≠ `error`.** A guardrail refusal is filed separately from an Odoo fault, so you can
  see what an agent tried to do and was stopped from doing.

**Sizing:** a typical record is ~650 bytes, so 500 tool calls a day is ~10 MB a month. Read traffic
dominates and changes nothing in your books — the module's **Writes only** filter, or `audit_level`
/ `Failures and policy blocks only`, cuts that down sharply for review.

> The trail names users, models and record ids. Treat it as a record of financial activity: the
> file is gitignored, and the Odoo table is `base.group_system` **read-only** (append-only — the
> model refuses `write()`; rows leave only via the retention cron).

## Hosting pitfalls — lessons from running this against real Odoo hosting

Everything below was hit in practice (mostly on **Odoo Online / SaaS** behind Cloudflare, and when
hosting the independent server behind a tunnel). They're already handled in the code/playbooks —
this section exists so nobody re-debugs them.

### Odoo Online & managed Odoo hosting (`*.run-odoo.com`)

> Know which you're on: `*.odoo.com` = Odoo's own SaaS (no addons, no shell). `*.run-odoo.com`-style
> domains = a **hosting provider's managed Odoo on AWS EC2 behind their nginx** — custom modules CAN
> be deployed (via the provider, to the addons path — not the in-app "Import Module" uploader), but
> any proxy/nginx change is a **support request to the provider**, not something you configure.

1. **API keys are XML-RPC-only — there is no web session.** Odoo Online rejects an API key at
   `/web/session/authenticate`, and the `/report/*` controllers need a web session. Consequence:
   the independent server's `odoo_render_report` **cannot complete on Odoo Online** even though
   every data tool works. Workarounds, in order: use the **in-Odoo module** (renders natively,
   in-process), or fall back to the **deep link** `<url>/report/pdf/<report_name>/<ids>` opened in
   a browser that's already logged in (the playbooks do this automatically). This one cost real
   debugging time because it *looks* like an auth bug — it isn't; the key genuinely has no web
   power. (Self-hosted instances that accept API-key web login are unaffected.)
2. **Cloudflare 403s header-less HTTP requests.** Python's default `urllib` requests (no
   `User-Agent`) get a Cloudflare 403 *before reaching Odoo*. The server now always sends a
   browser User-Agent (`MCP_HTTP_USER_AGENT` to override). Verified: header-less → 403, with UA
   → 200. If report/HTTP calls suddenly 403, check this before blaming credentials.
3. **Module installs depend on the host.** True Odoo Online (`*.odoo.com`) takes no custom addons
   at all — only the independent server applies there. Managed hosts deploy addons **through the
   provider** to the addons path; the in-app "Import Module" uploader does **not** run custom
   Python on managed hosts (bit us — the module *looked* installed but its controllers were dead).
   Either way, schema changes / module ops via the MCP are blocked by the model-tampering control.
4. **UI behaviour ≠ RPC behaviour.** Example: creating a company in the UI auto-loads the
   country's chart of accounts; over RPC it often does not. Playbooks are written
   *verify-then-act* for this reason (`setup-new-entity` checks the chart before loading it).
5. **Stale API keys fail confusingly.** An expired/rotated key in a credentials file produces
   auth failures that read like server misconfiguration. Check the key first; keys also **must
   be minted with developer mode on** (Settings → My Profile → Account Security).

6. **The provider's nginx blocks OAuth discovery (`/.well-known/…` → 404), breaking the Claude
   connector.** The in-Odoo module's OAuth needs two discovery URLs to reach Odoo:
   `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server`. On the
   managed host, nginx answers `/.well-known/` itself (an ACME/certbot webroot block or a dot-path
   deny rule), so Claude's discovery 404s and the login falls back to a broken `/authorize` URL.
   Diagnosis: on the instance, `curl http://127.0.0.1:8069/.well-known/oauth-authorization-server`
   returns JSON (Odoo serves it) while the public URL returns nginx's 404 — nginx is the layer.
   Fix = a **support request to the hosting provider**: one nginx `location` forwarding exactly
   those two GET paths to the Odoo upstream. Full diagnostic, the ready-to-send request text and
   the location block are in **[`odoo_module_mcp_server/OAUTH_SETUP.md`](odoo_module_mcp_server/OAUTH_SETUP.md)**
   (Step 2). Until it's done, the API-key `mcp-remote` fallback in that guide still connects.

### Hosting the independent server (Docker + tunnel)

7. **`421 Misdirected Request` behind a tunnel/proxy.** The MCP SDK's DNS-rebinding protection
   rejects any non-localhost `Host` header — which is every request arriving through a tunnel
   (Cloudflare Tunnel, reverse proxy). Fix: `MCP_ALLOWED_HOSTS` — unset or `*` disables the check
   (fine when the tunnel is the trust boundary), or set a comma-separated allowlist of exact
   hostnames to lock it down.
8. **OAuth-probe 404s are noise.** MCP clients probe `/.well-known/oauth-authorization-server`
   and `/register` on connect; the independent server is no-auth (the tunnel/Cloudflare Access is
   the boundary), so those 404s are expected and harmless. Don't chase them.
9. **Keep the image tarball out of the build context.** `docker save` produces `odoo-mcp.tar`
   next to the Dockerfile; without `.dockerignore` excluding `*.tar` the next build bakes the
   previous image into the new one (~251 MB → ~308 MB and growing). `.dockerignore` also keeps
   `config/` and `odoo_config.json` out of the image — config is bind-mounted, never baked.

### XML-RPC quirks (any hosting)

10. **`cannot marshal None unless allow_none is enabled` is a SUCCESS, not an error.** Many Odoo
   methods return `None` (`action_post`, `button_draft`, `account.move.line.reconcile`,
   `message_post`…). Some endpoints can't marshal a `None` result — but marshalling happens
   *after* the commit, so the method already ran. The server swallows this fault and returns
   `None`; regardless, always **re-read the record** to confirm the new state instead of retrying
   (a blind retry double-posts).
11. **`has_access(operation)` breaks over XML-RPC** ("missing argument"). Use
    `check_access_rights(op, {"raise_exception": False})` for access probing, or infer scope from
    `res.groups` membership.
12. **Odoo command tuples must be JSON arrays.** MCP arguments are JSON — write `[0, 0, {...}]`,
    never Python tuple syntax, for `line_ids`/`tax_ids`/any x2many command.

### Odoo 18 data-model traps (verified live)

13. **`account.account.code` is company-dependent.** It's a non-stored computed field over a
    per-company `code_store`: read it outside the owning company's context and it comes back
    **empty** (raw ids then leak into reports). Always read with the company context (the server's
    `company_id` parameter on the read tools). You can't `order` by `code`, and
    `account.code.mapping` is **not searchable**. Ownership is `company_ids` (many2many), not
    `company_id`.
14. **Journal/company mismatch in multi-company groups.** A move's `company_id` must match its
    journal's company, and subsidiaries often have no journals of their own. Workaround: create
    the draft under the parent company + journal, then reassign `company_id` while draft
    (misc/sale/purchase journals only — **never** bank journals). Documented in the skill.
15. **Two budget models on Enterprise 18** — `budget.analytic`/`budget.line` (analytic) and
    `account.report.budget(.item)` (financial); `crossovered.budget` does **not** exist. Bonus
    trap: the field is literally spelled `theoritical_amount`.

---

## Development — tests, controls & packaging

- **Hard controls** (enforced server-side, both ON by default): no `unlink` anywhere; no writes to
  technical/auth models (`ir.model*`, `ir.module*`, views, actions, cron, automation,
  `res.users`/`res.groups`, mail templates/aliases). Pinned by **`odoo-mcp/tests/`**, together with
  the logging interface and the tool plumbing — fully offline (the MCP SDK is stubbed, Odoo is a
  fake recorder). The module's suite lives in **`odoo_module_mcp_server/tests/`** and runs its real
  code against a fake `odoo`: the same guardrails, the JSON-RPC dispatch, the OAuth code exchange
  (PKCE, replay, expiry, redirect-URI matching) and the activity log.
- **Parity is tested, not assumed.** `odoo_module_mcp_server/tests/test_parity.py` compares the two
  servers' write denylists and tool names directly, and fails if the module ever permits something
  the independent server refuses — the safety posture must not depend on which form you deployed.
  Intentional differences are listed in that file with their reasons.
- Run everything from the repo root: `python -m pytest -q` (or one suite at a time:
  `python -m pytest odoo-mcp/tests -q`). The module's suite does **not** cover view archs, xmlids or
  ORM behaviour — install against a real Odoo 18 before trusting a release.
- **Soft controls** (prompt-level: draft-only, confirm gates, tie-outs, credential hygiene) have
  behavioural evals in **`odin/evals/`** (E01–E10) — run the affected scenarios against a
  **sandbox** after changing a playbook; log outcomes in `odin/evals/results.md`.
- **Packaging:** `python build_plugin.py` — validates (SKILL.md ≤1024-char / plugin.json
  ≤500-char description limits, config parses, playbook table ↔ files, source ↔ plugin drift,
  the test suite) and only then writes `odoo-assistant.plugin` + `odin.zip`. A PostToolUse hook
  (`odoo-assistant-plugin/hooks/`) warns on the same hygiene issues at edit time.

## Folder layout
**Core (the reusable product — share/version these):**
- `odoo-mcp/` — the **independent** MCP server (+ Dockerfile, compose, config GUI, README, tests).
- `odoo_module_mcp_server/` — the **in-Odoo** MCP module (OAuth opt-in).
- `odin/` — the skill: `SKILL.md`, `playbooks/`, `references/` (Odoo 18 model/API docs),
  `evals/` (behavioural eval scenarios — repo-only).
- `odoo-assistant-plugin/` — the installable Claude Code **plugin** (Odin + bundled OAuth
  connector + hygiene hook); packaged to `odoo-assistant.plugin` by `build_plugin.py`.

**Instance context — generated, SHAREABLE across the team (same for every user → share to save tokens):**
- `odoo-context/` — entities, chart of accounts, journals (own vs shared), taxes/analytic, **custom
  reports**, per-entity profiles (from `build-context`).
- `bookkeeping-rules/<company>.md` — per-company rulebooks (from `design-bookkeeping-rules`).
- → Put these in a shared repo/drive so teammates' Odin reads them instead of re-deriving.

**User-private (per person — never share):**
- `creds.txt` — your `username` + `api_key` (gitignored).
- `access/<user>.md` — your access scope (gitignored).
- `odoo_config.json` — instance `url` + `db` (gitignored; independent-server only).

**Deliverables (work products):** `month-end-review/`, `bookkeeping/`, `reports/`, `audit/`.
**Meta (local, gitignored):** `CLAUDE.md` + `.claude/memory/` — agent build rules and instance memory.

> **Security:** see [Security](#security) below before exposing either form.

## Compatibility

Everything here was built and verified against **Odoo 18** — that is the only version it has been
run against, so it is the only one the docs claim. The field names, model behaviour and the traps
catalogued above were all confirmed live on 18.

Nothing is deliberately version-locked, so **Odoo 17 and 19 may well work**. If you try one:

- Say which version and how you host it when you open an issue — "works on 17" is as useful a
  report as a bug.
- Where a field or model differs, a PR that handles both versions beats one that switches the
  target. `odin/references/odoo18_*.md` is where verified model facts live; add the version-specific
  ones alongside rather than overwriting.
- The offline test suite does not need an Odoo instance, so it stays green either way — the real
  signal is a playbook run against a **sandbox** database on your version.

## Contributing

Issues and pull requests are welcome. A few things that make review quick:

- **Run the suite before you open a PR** — `python -m pytest odoo-mcp/tests -q` (offline, no Odoo
  needed). Anything touching the two policy controls should arrive with a test that pins it.
- **Changed a playbook?** Run the affected `odin/evals/` scenarios against a **sandbox database**,
  never production, and log the outcome in `odin/evals/results.md`.
- **Changed the skill or the plugin?** Package with `python build_plugin.py` rather than zipping by
  hand — it runs the validation gates (description-length limits, playbook table ↔ files, source ↔
  plugin drift, the test suite) and only then writes the artifacts.
- **`odin/` and `odoo-assistant-plugin/skills/odin/` are kept in sync** — edit the former; the build
  script checks for drift.
- Keep the house style of the docs: concrete tool calls, verified Odoo 18 field names, no invented
  model names.

## Security

Both MCP forms enforce two server-side controls, **on by default**: no `unlink` anywhere (archive or
cancel instead), and no writes to technical/auth models (schema, modules, views, actions, cron,
automation, `res.users` / `res.groups` / API keys, mail templates and aliases). `ir.attachment` is
carved out so file uploads keep working. Both can be switched off by an administrator — do that
deliberately, for a change window, and switch them back.

The independent server's HTTP endpoint has **no built-in auth**: front it with a trusted proxy or
tunnel providing TLS and access control. The Odoo module authenticates every call by API key or
OAuth and runs as that user, so record rules and access rights apply normally.

Never commit credentials. `creds.txt`, `odin_credentials.txt`, `odoo_config.json` and `access/` are
gitignored for that reason. To report a vulnerability, please open a security advisory on the
repository rather than a public issue.

## Configure before you build

The plugin's MCP connector is instance-specific, so only **`odoo-assistant-plugin/.mcp.json.example`**
is tracked; the real `.mcp.json` is gitignored. `build_plugin.py` seeds it from the example on first
run — edit the URL to your own instance, or the packaged plugin connects nowhere:

```json
{ "mcpServers": { "odoo": { "type": "http", "url": "https://odoo.example.com/mcp/v1" } } }
```

The same placeholder host appears throughout `odoo_module_mcp_server/OAUTH_SETUP.md`.

## Support

If this saved you an afternoon, you can
[buy me a coffee](https://buymeacoffee.com/moonlightlupin). Entirely optional — bug reports and
version-compatibility findings are worth more.

## Licence

Copyright 2026 **[Phronesis Applied](https://www.phronesis-applied.com)**.

- **Apache-2.0** for the repository — the independent server (`odoo-mcp/`), the Odin skill
  (`odin/`), the plugin (`odoo-assistant-plugin/`) and the docs. See [LICENSE](LICENSE) and
  [NOTICE](NOTICE).
- **LGPL-3.0** for the in-Odoo module (`odoo_module_mcp_server/`) alone, because it links Odoo,
  which is itself LGPL-3. Its texts sit beside it in
  [`odoo_module_mcp_server/LICENSE`](odoo_module_mcp_server/LICENSE) (LGPL-3, which incorporates
  the [GPL-3](odoo_module_mcp_server/LICENSE.GPL-3) by reference).

The split keeps each part under a licence that fits what it links. Odoo is a trademark of Odoo
S.A.; this project is an independent integration and carries no affiliation or endorsement.

## Disclaimer

This software drafts bookkeeping entries; it does not give accounting, tax or audit advice, and it
is not a substitute for a qualified person reviewing the books. Everything it produces is a draft
for review. No warranty, express or implied — see the licence.
