# E09 — Account codes resolve per company; no raw ids in output

- **Severity:** medium · **Control:** SKILL.md Output Format / Odoo 18 company-dependent code
- **Environment:** sandbox/live-read with ≥2 companies where the connected user's DEFAULT company
  differs from the target (this is what makes codes come back empty when read wrongly).

## Prompts
1. "Odin, give me a trial balance for [non-default company] for last month."

## Pass criteria
- [ ] Every account in the output shows **`code name`** (e.g. `12501 Prepayments`) — no bare
      internal ids as row labels.
- [ ] The reads passed `company_id=<target cid>` (or an explicit company context) — verifiable
      from the tool calls.
- [ ] `odoo_read_group` groupings were done via the company-context path, not the raw
      `odoo_execute` escape hatch that drops codes.
- [ ] Journals shown by `code` + name; partners by name; ids at most a secondary column.

## Fail signals
Rows labelled `1241` or `account.account(1241)`; empty code columns for the target company.
