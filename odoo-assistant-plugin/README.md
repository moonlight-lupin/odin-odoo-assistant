# odoo-assistant plugin

Bundles **Odin** — an Odoo finance assistant — together with its **Odoo MCP connector** in one
installable Claude Code plugin. Install once and you get both the skill and a live, OAuth-authenticated
connection to the Odoo instance.

## What's inside

```
odoo-assistant/
├── .claude-plugin/plugin.json   # plugin manifest
├── .mcp.json                    # bundled Odoo MCP server (OAuth, HTTP)
├── hooks/                       # skill/plugin hygiene hook (SKILL.md + plugin.json checks, non-blocking)
└── skills/odin/                 # the Odin skill
    ├── SKILL.md                 # assistant identity + session setup + safety protocol
    ├── playbooks/               # 15 procedures (build-context, month-end-review, bookkeeping, setup-new-entity, migrate-entity-books, …)
    └── references/              # Odoo 18 model/API reference docs (loaded on demand)
```

The skill is invoked as **`/odoo-assistant:odin`**, or just greet it with **"hi Odin"** / hand it any
Odoo task — the model routes in from the skill description.

## The bundled MCP server

`.mcp.json` points at the **in-Odoo module** server (JSON-RPC at `/mcp/v1`) which uses **OAuth**:

```json
{
  "mcpServers": {
    "odoo": { "type": "http", "url": "https://odoo.example.com/mcp/v1" }
  }
}
```

No credentials or secrets live in the plugin — OAuth is negotiated by Claude Code's MCP client.

## Install & first run

1. Install the plugin (from a marketplace or a local path), e.g.
   `claude plugin install odoo-assistant@<marketplace>` — or load the `.plugin` archive.
2. Run **`/mcp`** and authenticate the **odoo** server in the browser (one-time OAuth handshake).
3. Confirm the `odoo_*` tools are present, then say **"hi Odin"**. Odin runs Session Setup
   (working folder → `odoo_whoami` → company selection) and you're ready.

Because the bundled server is the OAuth in-Odoo module, there is **no `odoo_connect`** step and no
credentials file — the OAuth token authenticates you. (The credentials-file flow in SKILL.md is the
fallback only when pointing Odin at the self-hosted external `odoo-mcp/` server instead.)

## What Odin does

Read-first and **draft-only** by default (it never posts, pays, or deletes without an explicit
instruction for the specific record). Playbooks cover the full cycle: **set up** (build-context,
setup-new-entity, migrate-entity-books, design-bookkeeping-rules) → **record** (vendor bills, customer invoices, import bank statement,
third-party conversion) → **settle & adjust** (bank reconciliation, closing journals, pay vendor
bill) → **run the cycle** (monthly bookkeeping) → **review & report** (month-end review, audit-trail
review, generate reports).

## Versioning & packaging

Package with **`python build_plugin.py`** from the repo root — it validates before it zips
(manifest + SKILL.md limits, config parses, playbook table ↔ files cross-check, odin/ ↔ plugin
drift check, and the `odoo-mcp/tests` policy-control suite) and refuses to package on any failure.
Batch changes and repackage once per batch — don't bump the version on every edit.

Behavioural evals for the prompt-level controls live in `odin/evals/` (repo-only, not packaged) —
run the relevant scenarios after changing a playbook.
