# Playbook: migrate-entity-books

Bring an entity's **existing books into Odoo** at a confirmed **cutover date**: master data
(partners, optionally their bank accounts), the **opening trial balance**, and the **open AR/AP
items** (so ageing and future bank reconciliation work from day one). Full historical periods are
an **explicit opt-in**, never the default — history migration is where take-ons blow up.

Detailed runbook — written so a competent model (e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "migrate this entity's books into Odoo", "opening balances / opening TB",
"take-on balances", "load the trial balance", "bring the open invoices/bills over",
"migrate from <old system / spreadsheet / third party>".

**Entry modes** (ask which; default = Mode A):
- **Mode A — standard take-on:** master data + opening TB + open AR/AP items as at cutover.
- **Mode B — history opt-in:** after Mode A, monthly movement journals for named past periods
  (uses the third-party-conversion Part-B mechanics per period).
- **Mode C — master data only:** partners (+bank accounts) without balances.
- **Mode V — verify:** after the user has posted the migration drafts, re-derive the Odoo TB at
  cutover and diff it against the source TB (read-only).

---

## Scope & guardrails
- **Explicit-only; one entity per run; never part of monthly-book-keeping.**
- **DRAFT ONLY.** Every move (opening entry, open bills/invoices, history journals) is created in
  draft; **never post** without explicit instruction. Partners/bank accounts are master data (no
  draft state) — confirm before creating, with a **duplicate guard** (below).
- **Empty-books check first.** Migration assumes the entity has no accounting history at/before
  cutover: `odoo_search_count("account.move", domain=[["company_id","=",cid],["date","<=",cutover]])`
  — if > 0, **STOP** and show what exists; the user decides (wrong entity? partial migration?
  re-run?). Never layer a take-on onto existing balances silently.
- **No unmapped accounts.** If the source CoA differs from Odoo's, every source account must map
  (rulebook mapping — design-bookkeeping-rules / third-party-conversion Part A pattern). Unmapped →
  stop and ask; never guess.
- **Everything must tie** (see Tie-outs). Never create drafts from data that doesn't tie.
- **Duplicate guards:** partners by name/VAT against the whole database (partners are often shared
  across the group — reuse, don't re-create); bills by vendor+ref; invoices by name/number. Re-running
  the playbook must never double-load — check for existing migration drafts (by ref prefix) first.
- **Shared/parent-journal workaround** applies (misc journals only; bank journals never).
- **Always Excel report + links.** Outputs under `<working folder>/entity-migration/<slug>/`.

## Preconditions
1. Connected; working folder; context loaded. The entity **exists with its CoA finished** —
   if not, run **setup-new-entity** first.
2. Source documents: **TB as at cutover** (Excel/CSV/PDF), **open AR and AP item listings**
   (invoice-level: partner, ref/number, dates, currency, original + outstanding amount), and a
   **partner list** if master data is in scope. A CoA mapping if source codes ≠ Odoo codes.
3. Ideally the entity's rulebook (`bookkeeping-rules/<company>.md`) for naming conventions.

---

## Step 1 — Frame the migration (confirm before touching anything)
Confirm: entity + **cutover date** (usually the last day of the period before Odoo goes live) ·
functional currency · scope (Mode A/B/C) · source system · the **control totals** (TB total,
AR total, AP total per the source) · target journal for the opening entry (a dedicated misc
"Opening/Migration" journal, or the entity's misc journal — shared-journal workaround if needed) ·
ref convention (default: `MIG <entity> — Opening as at <cutover>` and `MIG` prefix on every record).
Then run the **empty-books check** (guardrail above).

## Step 2 — Ingest & map (paper only)
1. Read the TB and the open-item listings. Parse defensively (confirm date format and sign
   convention; debits positive / credits negative or two columns — say which you inferred).
2. Apply the CoA mapping if any (every account must resolve to an Odoo `code name` + id, read with
   `company_id=cid` so codes resolve — Odoo 18 company-dependent codes).
3. **FX flag:** any open item or TB line not in the functional currency → list them, confirm the
   treatment (foreign-currency lines carry `currency_id` + `amount_currency`; the TB itself must be
   in functional currency). If the source TB embeds unrealised FX, note it — don't invent rates.

## Step 3 — Tie-outs (paper, before any create — all four must pass)
1. **TB balances:** Σ debits = Σ credits (to the cent).
2. **Open AR list ties:** Σ outstanding AR items = the TB's AR control balance.
3. **Open AP list ties:** Σ outstanding AP items = the TB's AP control balance.
4. **Control totals match the source's own stated totals** (the figure printed on the report).
Show the four results (✅/❌ with the difference). Any ❌ → stop, reconcile with the user, re-run.

## Step 4 — Master data (Modes A & C)
1. For each partner: **search before create** — `odoo_search_read("res.partner",
   domain=["|",["name","ilike","<name>"],["vat","=","<vat>"]], fields=["name","vat","company_type"])`.
   Existing → reuse (note it). New → confirm, then `odoo_create("res.partner", {name, vat,
   country_id, company_type, …})` — minimal fields; enrich later if wanted.
2. **Vendor bank accounts (optional, sensitive):** only from the provided source data, confirm each:
   `odoo_create("res.partner.bank", {"acc_number":…, "partner_id":…})`. Never invent bank details.

## Step 5 — Open AR/AP items (Mode A) — choose the approach (recommend 1)
**Approach 1 — individual draft documents (default, recommended):** each open item becomes its own
draft `account.move` (`out_invoice` / `in_invoice`; credit notes as `out_refund`/`in_refund`):
- `invoice_date` = the **original** invoice date (correct ageing), `invoice_date_due` = original due
  date, `date` (accounting date) = **cutover** (so nothing posts into pre-migration periods).
- Vendor bills: supplier's number → **`ref`** (never `name`); customer invoices: set `name` only if
  preserving the old numbering is wanted (confirm — else let Odoo number them).
- One line per document: the **outstanding** amount to a **migration clearing account** — ask the
  user which (a dedicated `MIG clearing` account is cleanest; it must net to zero in Step 6) — **no
  taxes** (tax was accounted for in the old system; migrating net-of-tax open balances only).
- Foreign-currency items: set `currency_id`; Odoo derives `amount_currency`.
**Approach 2 — partner-split lines inside the opening entry (lean):** no separate documents; the
opening JE carries one AR/AP line **per open item** (`partner_id`, `name` = original ref,
`date_maturity` = due date). Fewer records, no printable documents, ageing from `date_maturity`.
Confirm the approach with the user before building.

## Step 6 — Opening trial balance entry (Mode A)
One draft `account.move` (`move_type:"entry"`, the migration journal, `date` = cutover,
`ref` = the convention from Step 1), lines = the mapped TB:
- **Approach 1:** replace the AR/AP control lines with the **migration clearing account** (the open
  items from Step 5 post the AR/AP side; clearing nets to **zero** across the whole set — verify).
- **Approach 2:** the AR/AP control amounts appear as the partner-split lines directly in this entry.
- Retained earnings/current-year result per the source TB (map to the CoA's retained-earnings
  account; flag if the localization auto-computes it).
- Large entries: batch into several drafts if needed (note the split; the set must still tie).
Read the draft(s) back: balanced, right company, right journal — then **paper-verify the whole
package**: opening entry + open items together reproduce the source TB **exactly**, and the
clearing account nets to zero. Show this reconciliation.

## Step 7 — History (Mode B only, explicit opt-in)
Per named period, post one **movement journal** (period P&L + balance-sheet movements) using the
third-party-conversion Part-B mechanics: extract → map → balance + tie to the period's control
total → draft entry dated the period end, ref `MIG <entity> — <period> movements`. Never overlaps
the cutover TB (history periods must end **before** cutover; re-derive that the summed history +
opening TB remain consistent).

## Step 8 — Handoff
- **Do NOT post anything.** The user reviews and posts in Odoo (or instructs Odin explicitly);
  suggest posting order: open items first, then the opening entry, then verify clearing = 0.
- Offer **Mode V** after posting: rebuild the Odoo TB as at cutover
  (`odoo_read_group("account.move.line", domain=[["company_id","=",cid],["date","<=",cutover],
  ["parent_state","=","posted"]], groupby=["account_id"], fields=["debit","credit"], company_id=cid)`)
  and diff against the source TB → ✅ per account or the exact differences.
- Point to **bank-reconciliation** (post-cutover statements will settle the migrated open items)
  and **design-bookkeeping-rules** (record the migration + conventions in the rulebook, Change Log).

## Output (always: Excel + links)
`entity-migration/<slug>/migration_<entity>_<cutover>.xlsx`:
- **Sheet "Tie-outs"** — the Step-3 (and Step-6) reconciliations, source vs loaded, ✅/❌.
- **Sheet "Opening TB"** — source account → Odoo account (code name) → debit/credit → entry link.
- **Sheet "Open items"** — one row per migrated AR/AP item: partner, ref, dates, currency,
  outstanding, draft link.
- **Sheet "Master data"** — partners created vs reused (+bank accounts), links.
Header block: entity, cutover, source, approach (1/2), mode, control totals, generated date.
Run-metadata line with the token estimate (skill convention). Reproduce the key links in chat.

## Do-NOT list
- ❌ Proceed past a failed tie-out, an unmapped account, or a non-empty-books check.
- ❌ Post anything; create duplicates (partners, bills by vendor+ref, a second take-on run).
- ❌ Put taxes on migrated open items, or accounting dates before cutover.
- ❌ Invent FX rates, bank details, or a clearing account without confirming.
- ❌ Migrate history without explicit opt-in (Mode B).
- ❌ Skip the tie-out sheet, the Excel action report, or the links.
