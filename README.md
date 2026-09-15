# Odoo Assistant (Odin)

An AI finance assistant for **Odoo**. It runs month-end and day-to-day work through playbooks — bills, invoices, bank import, reconciliation, close, review, and reports — under rules you can observe. Draft-only by default. A person confirms and posts.

Built by Phronesis Applied. Finance practitioner who builds, not a tech vendor.

**Licence:** Apache-2.0 (repository); the in-Odoo module alone is LGPL-3.0. **Tested on Odoo 18.** Not affiliated with or endorsed by Odoo S.A.

---

## What the finance team gets

- **Playbook-driven Odoo work.** Month-end and day-to-day: vendor bills, customer invoices, bank statement import, bank reconciliation, journal entries, monthly bookkeeping, month-end review, and reports.
- **Draft-only by default.** The assistant prepares. Confirm and post stay with a person.
- **Your practice, not a generic guess.** A per-company rulebook holds coding, cadence, naming, and chart mappings.
- **Your Odoo, your hosting.** Same playbooks on Odoo Online (external connector) or self-hosted / Odoo.sh (in-Odoo module).

Today’s playbooks focus on accounting. They can be extended to other Odoo apps and workflows — for example CRM or inventory — because the connector can already reach those models. What is narrow today is the playbook coverage, not the connector.

## Your first 15 minutes

Once a connector is up and Odin is enabled (see [Getting connected](#getting-connected) below), try a short path on a **sandbox** company — not production.

1. Say **“hi Odin”** (or any Odoo task). On first use it asks for a working folder and credentials.
2. Ask **“build context”** so it learns the companies, chart, and journals you work in.
3. Then try one of these:

> **“Process these vendor bills”** — attach or point at bill PDFs/files, and let it draft.  
> **“Month-end review for March”** — a review pass on a closed or near-closed month.  
> **“Import this bank statement”** — bring a statement file into the books as drafts for recon.

You should get drafts, links into Odoo, and a simple action report — not silent posts. If something looks wrong, stop and review before you confirm anything.

Deep install and client wiring stay in the component READMEs, not on this page.

## How the AI agent works

**Odin** drives Odoo through playbooks: multi-step procedures with concrete steps — not free-form “figure it out.”

An accounting professional stays in the loop. They see what is pending, what was drafted, and what the audit trail recorded. Sign-off stays with the person who owns the books. Odin does not replace that person; it does the work under rules they can observe.

## Trust — graduated controls

Controls stack from soft to hard. Start here; dig into the admin docs only if you need to change them.

1. **Draft-only (practice).** Bookkeeping playbooks create drafts. They do not post or pay without clear instruction. Review before you confirm.
2. **Policy rails (server).** By default: no record deletion (archive or cancel instead); no tampering with models, users, groups, or API keys. Administrators can change these — do that deliberately and for a reason.
3. **Audit trail (evidence).** Every tool call is recorded: who, which tool, which records, and whether it succeeded, was blocked by policy, or failed. That is how you see what the agent tried and what was stopped.

Use a sandbox first, and have a person own the close.

## What’s in the product

Playbooks follow a finance-ops lifecycle:

- **Set up and understand** — company context, new entity setup, opening balances, bookkeeping rules
- **Record** — vendor bills, customer invoices, bank statement import, third-party conversion
- **Settle and adjust** — bank reconciliation, posting and closing journals, paying vendor bills
- **Run the cycle** — monthly bookkeeping as an orchestrated run
- **Review, report, and assure** — month-end review, audit-trail review, reports

The skill detail lives in [odin/SKILL.md](odin/SKILL.md).

## Getting connected

Pick **one** way to connect Odoo. Same playbooks either way. Install and hosting detail are in those READMEs — not here.

- **Odoo Online / cannot install modules** — external connector: [odoo-mcp/README.md](odoo-mcp/README.md)
- **Self-hosted / Odoo.sh** — in-Odoo module: [odoo_module_mcp_server/README.md](odoo_module_mcp_server/README.md)

Then enable the Odin skill and use the [first 15 minutes](#your-first-15-minutes) path above.

## Licence and disclaimer

Copyright 2026 Phronesis Applied. Apache-2.0 for the repository (see [LICENSE](LICENSE) and [NOTICE](NOTICE)). LGPL-3.0 for the in-Odoo module alone (see that folder). Odoo is a trademark of Odoo S.A.; this project is an independent integration and carries no affiliation or endorsement.

This software drafts bookkeeping entries. It does not give accounting, tax, or audit advice, and it is not a substitute for a qualified person reviewing the books. Everything it produces is a draft for review. No warranty, express or implied — see the licence.
