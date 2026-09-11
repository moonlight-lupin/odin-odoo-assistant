# Playbook: month-end-review

Run a defined month-end **review** over already-entered books (this is a reviewer's checklist,
not the close process itself) and produce a per-entity exceptions report plus a group summary.
Deliver in **both markdown and Excel**.

**Trigger phrases:** "month-end review", "quarter-end / quarter close review", "half-year ended
review", "year ended review", "close checklist", "review the close", "check the books for
<period>", "period-end review", "MEC review".

Scope of checks: **A. Completeness & period integrity · B. Subledger tie-outs & ageing ·
C. Intercompany & consolidation · D. Material & unusual entries · E. Analytical review (BS & P&L
vs prior comparable).** The review **window scales to the close type** (month / quarter / half-year
/ year — see Step 0). Comparison context = the **equivalent prior window** (and, for recurring-item
completeness, the last 2–3 comparable windows). (Tax/FX technical detail is out of scope unless the
user asks.)

---

## Principles
- **ALWAYS ASK THE PERIOD.** Never assume or infer the period from today's date, the last
  closed month, or context — explicitly ask the user which period to review and wait for the
  answer before running anything. Same for the entities (see Scope).
- **100% read-only against Odoo.** Only reads (`odoo_search_read`, `odoo_read`,
  `odoo_search_count`, `odoo_read_group` — pass `company_id` so account labels carry their code).
  The only writes are local files.
- **Confirm before running:** the **period** and the **entities** (see Scope). Don't guess.
- **Posted basis** for tie-outs/ageing/IC unless stated; **drafts are themselves an exception**
  in check A. Always state the basis in the output.
- **Token economy — fetch cheap, batch hard (the dominant cost is fetching + round-trips, not the
  analysis).** None of this changes the checks or the output contract — only *how* data is fetched:
  - **Batch independent reads into ONE step.** The harness allows many tool calls per message, and
    every round-trip re-sends the whole transcript — so N sequential calls pay for earlier results N×.
    Issue all of a check's independent `read_group`s — and across checks, wherever a query doesn't
    depend on an earlier result — **in a single batched step**. Most of A–E is independent once Step-2
    lookups are cached, so it fits in 1–2 big batches. **Target: a ≤3-entity review in ≤6 tool steps.**
  - **One multi-company call, not one per company.** Filter `["company_id","in",<scope>]` and add
    `company_id` to `groupby` instead of looping per `cid`. One `read_group` then returns every entity
    keyed by `[company_id, account_id]` — N× fewer calls and N× less re-send. (Drop to a per-`cid`
    `company_id=` context only when you must resolve `account_id` codes on a **mixed-ownership** CoA;
    on a shared group CoA the single call is fine — resolve codes once from the cached CoA map.)
  - **Aggregate first, drill only on exception.** Default every check to a summary `read_group`; pull
    row-level `search_read` detail **only** for the specific partner/account/entity that breaches a
    threshold. Never pull a full ledger to compute a headline number.
  - **Mind the `read_group` payload.** Each row echoes the full filter in `__domain` (can't be
    stripped client-side), so a high-cardinality `groupby` is mostly boilerplate. Restrict to material
    accounts / the related-party set / adjustment journals before grouping; avoid grouping by
    `partner_id` across hundreds of one-off suppliers.
  - **Stable prefix for caching.** Load the skill, this playbook, and the `odoo-context/` files
    **once, early**; don't re-read large reference files mid-run.
- **Lite (default) vs full mode.** Default to **lite** — aggregate queries only, with row-level detail
  fetched on demand for anything over materiality (right for recurring monthly runs). Switch to
  **full** — pull supporting detail up front (full ageing ledger, line-by-line no-partner list,
  largest-entry drills) — for a hard-close / audit period or when the user asks. **State the mode** in
  the output. The checks and output contract are identical; only the detail-fetch timing differs.
- **Label accounts by code + name** (e.g. `12501 Prepayments`), never the raw id. **The account
  `code` is company-dependent (Odoo 18)**, so it's blank unless you read in the company's context.
  **Always pass `company_id=cid`** on account reads **and** on every `odoo_read_group` grouped by
  `account_id` — the result then comes back **with** the code: `account_id: [1241, "12501
  Prepayments"]` (verified). **Use `odoo_read_group(..., company_id=cid)`, NOT
  `odoo_execute(... "read_group" ...)`** — the escape hatch runs in your default company and drops the
  code, which is exactly what leaks the raw id into reports. Journals by `code`. (See SKILL.md →
  Human-recognisable identifiers & Common Gotchas.)
- **Materiality** drives Checks D & E. **Ask the user for a materiality threshold** at the start
  (absolute amount and/or % of the period's activity); if they don't give one, default to the
  greater of a fixed floor (e.g. 1,000 company-currency) or **1% of total period debits**, and
  **state the threshold used** in the output.
- **Reuse context:** if `odoo-context/` exists, use it for company ids, hierarchy, receivable/
  payable account ids, and company→partner mapping instead of re-discovering.
- If a field/model named here doesn't exist on the instance, `odoo_fields_get` to find the right
  one and note the substitution — don't error out.

---

## Step 0 — Connect & set the review window
1. Connect (Session Setup). 2. **Always ask the period** and note the **close type** — the
   review window scales to it. The period **end** is the close date; the **journals you review
   span the whole period** (not just the closing month):

   | Close type (from the request) | Window length | Example for "… ended Mar 2026" |
   |-------------------------------|---------------|-------------------------------|
   | Month-end | 1 month | Mar 2026 only |
   | **Quarter ended / quarter close** | **3 months** | **Jan–Mar 2026** |
   | Half-year ended | 6 months | Oct 2025–Mar 2026 |
   | Year ended | 12 months | Apr 2025–Mar 2026 |

   Derive:
   - `end` — last day of the close month; `start` — first day of the window per the table above.
   - `prior_start`/`prior_end` — the equivalent prior window (same length, immediately before) for
     movement context.
   - **Completeness look-back** (`comp_start`): the *later* coverage of either the review window
     OR "3 months before the period" — i.e. for a **month-end** review still look back 3 months
     (to catch quarterly adjustments); for quarter/half/year the window already covers ≥3 months,
     so `comp_start = start`. (`comp_end = end`.)
   If the fiscal year doesn't end in December, confirm the quarter/half/year month boundaries with
   the user.

## Step 1 — Resolve scope (prompt + expand to subsidiaries)
1. Ask which **entities** to review (by name/id), or "all".
2. **Auto-expand to subsidiaries:** for each selected company, include all descendants. Read the
   hierarchy and walk it:
   ```
   odoo_search_read("res.company", fields=["id","name","parent_id","currency_id","partner_id"], order="id")
   ```
   Build parent→children from `parent_id`; for each selected id, add its full descendant subtree.
3. Show the **expanded entity list** and confirm before proceeding. Cache, per company:
   `currency_id`, `partner_id` (its commercial partner — needed for intercompany).

## Step 2 — Cache common lookups (once)
- Receivable/payable account ids per company — pass **`company_id=cid`** so `code` populates
  (it's company-dependent; a plain read returns blank codes for non-default companies):
  ```
  odoo_search_read("account.account",
    domain=[["company_ids","in",[cid]],["account_type","in",["asset_receivable","liability_payable"]]],
    fields=["code","name","account_type"], company_id=cid)
  ```
  (Recall: `account.account` uses the **`company_ids` m2m**; `code` is company-dependent — Gotchas.)
  Keep the resulting `{id → "code name"}` map; reuse it to label every account in this entity's
  output (and as the decorator for `read_group` `account_id` values, which are `[id, name]` only).
- **Subgroup partners** — `partner_id` of the in-scope companies (selected + subsidiaries).
- **Group company partners** — `partner_id` of **every accessible** `res.company` (from Step 1).
  Keep a `partner → company` map (resolve line partners via `commercial_partner_id`).
- **Related parties (for the wider-group / IC checks) — go beyond the company list.** Related
  parties include other group entities that may exist only as `res.partner` records (not as Odoo
  companies). This instance flags them with a **custom boolean on `res.partner`** (something like
  `x_is_relatedparty` / `x_studio_related_party`). **Discover the exact field, don't hardcode:**
  ```
  odoo_search_read("ir.model.fields",
    domain=["&",["model","=","res.partner"],"|",["name","ilike","relat"],["field_description","ilike","related part"]],
    fields=["name","field_description","ttype"])
  ```
  Pick the boolean whose label means "related party"; if there are several or none, **confirm with
  the user** which field to use. Then build the **related-party partner set**:
  ```
  odoo_search_read("res.partner", domain=[["<related_party_field>","=",true]], fields=["id","name","commercial_partner_id"])
  ```
  Use **(group company partners) ∪ (flagged related-party partners)** as the "related/intragroup"
  set in Checks B and C.
- **Elimination entities — identify correctly, not just by name.** Name-matching `ilike
  "elimination"` is a starting hint but may miss some. Check for a reliable marker first
  (`odoo_fields_get("res.company")` / `ir.model.fields` for an elimination/consolidation flag or
  category); combine with the name hint; then **show the user the candidate list and confirm**
  before relying on it. Cache the confirmed elimination-entity ids.
- **Suspense / clearing accounts** (for the mandatory Check A3) — bank/cash journal suspense
  accounts plus name-pattern accounts:
  ```
  odoo_search_read("account.journal", domain=[["company_id","in",<scope>],["type","in",["bank","cash"]]],
    fields=["company_id","suspense_account_id"])
  odoo_search_read("account.account",
    domain=["&",["company_ids","in",<scope>],"|","|","|",["name","ilike","suspense"],
            ["name","ilike","clearing"],["name","ilike","undeposited"],["name","ilike","to allocate"]],
    fields=["id","code","name"])
  ```
  **If unsure which accounts are truly suspense/clearing, confirm the list with the user** before
  judging balances (see Check A3).

---

## Execution plan — run the checks in 1–2 batched steps (don't drip one call per step)
Once Step 1–2 lookups are cached, **most of A–E is independent** and should be issued **together**:
- **Batch 1 (aggregates — fire all at once, multi-company `["company_id","in",scope]`):** A1 drafts,
  A2 future-dated counts, A3 suspense balances, A4 recurring-adjustment group, B1 no-partner counts +
  control balances, B2 partner totals + overdue slice, B3 wrong-sign, C1 IC matrix, D1 largest
  entries, E1 P&L-by-month, E2 BS snapshots (×2). These don't depend on each other — one message.
- **Batch 2 (drill-downs — only what Batch 1 flagged):** line detail for a no-partner entity, an
  external partner over materiality (B2 buckets), an IC break (C2), D2/D3 unusual-entry detail. Skip
  any with no flag.
That's ~2 steps of reads for a small review (plus Step 0–2). Avoid a third round unless an exception
genuinely needs it. (Lite mode rarely needs Batch 2 at all.)

---

## Check A — Completeness & period integrity

> Completeness is reviewed over the window from Step 0 (`comp_start`→`comp_end`): the full
> close period (1/3/6/12 months), and for a *month-end* review at least the 3 preceding months,
> since quarterly adjustments may still be in draft from up to a quarter earlier.

**A1. Draft (unposted) entries across the completeness window** — should they be posted? One
multi-company call (drafts are exceptions, so list them):
```
odoo_search_read("account.move",
  domain=[["company_id","in",scope],["state","=","draft"],["date",">=",comp_start],["date","<=",comp_end]],
  fields=["company_id","name","date","move_type","ref","amount_total","journal_id","partner_id"],
  order="company_id, date")
```
**Group by month** so lingering quarterly items stand out; count per month per entity. Drafts anywhere
in the window are exceptions.

**A2. Future-dated entries** landing beyond the period — count per entity in one call:
```
odoo_read_group("account.move",
  domain=[["company_id","in",scope],["state","=","posted"],["date",">",end]],
  fields=["id:count"], groupby=["company_id"])
```
Any entity with a count > 0 ⇒ drill a sample there — entries dated after period end may be mis-dated.

**A3. Suspense / clearing balances at period end. — MANDATORY (never skip).**
1. Use the suspense/clearing accounts identified in Step 2 (bank/cash journal
   `suspense_account_id` + name-pattern accounts). **If you are unsure which accounts qualify as
   suspense/clearing, present the candidates and confirm with the user before judging balances** —
   do not silently skip this check.
2. Balance at period end for those accounts — one multi-company call (`<suspense_ids>` = the union
   across scope), grouped by `[company_id, account_id]`; resolve the account `code` from the cached
   CoA map (shared CoA) or a one-off context read (mixed-ownership):
   ```
   odoo_read_group("account.move.line",
     domain=[["company_id","in",scope],["parent_state","=","posted"],["date","<=",end],
             ["account_id","in",<suspense_ids>]],
     fields=["balance:sum"], groupby=["company_id","account_id"])
   ```
   **Non-zero = exception** (clearing accounts should net to ~0 at month-end). Add a prior-month
   snapshot (second call, `date<=prior_end`) in the same batched step for movement context.

**A4. Recurring-adjustment completeness — did this period book the adjustments prior periods did?**
The core completeness test a reviewer runs: period-end adjustments **recur** (accruals, prepayment
amortisation, depreciation, provisions, interest, FX revaluation, management/recharge fees). One
that appeared in the prior comparable period(s) but is **absent (or materially lower) this period**
is a likely omission. Compare the **nature** of adjusting entries this window vs the equivalent
prior window(s).
1. **Cadence first (preferred):** if the company's `bookkeeping-rules/<company>.md` exists, read its
   monthly/quarterly/yearly adjustment list and **tick off each adjustment that is due this period**
   against what's actually posted. A due item with nothing posted ⇒ flag.
2. **Data-driven comparison** (always, and the only route when no rulebook): pull the adjusting
   postings — the misc/general + closing journals (identify from context/rulebook) — grouped by
   account, for the **current** window and each of the **prior 2–3 comparable** windows:
   ```
   odoo_read_group("account.move.line",
     domain=[["company_id","in",scope],["parent_state","=","posted"],
             ["date",">=",<oldest comparable window start>],["date","<=",end],
             ["journal_id","in",<misc/general/closing journal ids>]],
     fields=["balance:sum","debit:sum","credit:sum"], groupby=["company_id","account_id","date:month"])
   # ONE call covers every entity AND all look-back windows; split current vs prior windows by month
   # in-memory per [company_id, account_id]
   ```
   Also bucket each move's narration (`account.move` `ref`/`name`) by theme keywords: *accrual,
   prepay/amortis, deprec, provision, interest, FX/revaluation, mgmt/management/recharge fee*.
3. **Compare & flag:** for every account (or narration theme) **present in a prior comparable window
   but missing/near-zero in the current window**, flag **"recurring adjustment may be missing this
   period (present in <prior period>, amount <x>)."** Use the **2–3 period** look-back so a true
   one-off isn't mistaken for recurring, and a **quarterly** item isn't wrongly flagged in a single
   month (cross-check against the cadence and the Step-0 `comp_start` look-back).
4. Conversely, note any **new adjustment account this period with no prior precedent** as an
   informational item (could be a new recurring item or a misposting — worth a glance).

---

## Check B — Subledger tie-outs & ageing

**B1. Control-account integrity — lines without a partner.** The AR/AP control accounts only tie to
the partner ledger if every line carries a partner. **Count-first, multi-company** (one call covers
every entity; `<rec_pay_ids>` = the union of all in-scope entities' receivable/payable account ids):
```
odoo_read_group("account.move.line",
  domain=[["company_id","in",scope],["parent_state","=","posted"],["date","<=",end],
          ["account_id","in",<rec_pay_ids>],["partner_id","=",false]],
  fields=["balance:sum"], groupby=["company_id"])
```
Any entity with rows = exception — **drill only there** (`search_read` the offending lines:
`move_id`, `account_id`, `date`, `name`, `balance`). Also report each control account's end balance
in the same batched step: one `read_group` grouped by `["company_id","account_id"]` over the rec/pay
accounts (vs prior month if wanted).

**B2. Aged receivables / payables — aggregate first, drill only on exception (the single biggest
cost if done naively; default LITE).** The headline ("any *external* partner overdue beyond
materiality?") needs ~summary numbers, not the hundreds of underlying open lines. So **do not** pull
the open-items ledger up front. Instead, in one batched step, get partner-level totals **and** the
overdue slice across all in-scope entities:
```
# (1) total open AR per [entity, partner]
odoo_read_group("account.move.line",
  domain=[["company_id","in",scope],["parent_state","=","posted"],
          ["account_id.account_type","=","asset_receivable"],   # then liability_payable for AP
          ["reconciled","=",false],["amount_residual","!=",0]],
  fields=["amount_residual:sum"], groupby=["company_id","partner_id"])
# (2) the OVERDUE slice — same call + ["date_maturity","<",end]  → overdue residual per [entity, partner]
```
- **Classify each partner related vs external** (its `commercial_partner_id` ∈ the **related/intragroup
  set** from Step 2). **Overdue alarm = EXTERNAL only.** Related parties are listed and marked
  "related party — see Check C"; never alarmed (intragroup timing isn't a collection risk).
- **Headline** = external partners whose **overdue** residual (from call 2) exceeds materiality. That's
  it for lite mode — no line pulls.
- **Drill only on exception:** for each external partner over materiality, `search_read` *that
  partner's* open lines (`partner_id`, `date_maturity`, `amount_residual`, `move_id`) to produce the
  Current / 1–30 / 31–60 / 61–90 / 90+ buckets for the report. (full mode: pull the line ledger up
  front as before and bucket client-side.)
> Caveat: `amount_residual`/`reconciled` reflect **current** state, not strictly as-of `end`
> (a reconciliation dated after `end` won't be unwound). State this in the report.

**B3. Wrong-sign partner balances.** Multi-company, partner-level (one call all entities):
```
odoo_read_group(model="account.move.line",
  domain=[["company_id","in",scope],["parent_state","=","posted"],["reconciled","=",false],
          ["account_id.account_type","=","asset_receivable"]],
  fields=["amount_residual:sum"], groupby=["company_id","partner_id"])
```
Receivable with **negative** total (customer in credit) or payable with **positive** total
(supplier in debit) = exception **for external counterparties**. For **related parties** (the
Step-2 set), a wrong sign is expected (it mirrors the counterparty) — note it, don't alarm; it's
covered by the Check C reconciliation.

---

## Check C — Intercompany & consolidation

**Definitions** (from Steps 1–2):
- **subgroup** = selected entities + their subsidiaries (in-scope).
- **related/intragroup set** = group company partners **∪ flagged related-party partners** (the
  related-party `res.partner` flag discovered in Step 2). Wider-group counterparties may be
  partners that are **not** Odoo companies — include them.
- **elimination entities** = the **confirmed** elimination-entity list from Step 2 (a flag/category
  where available + name hint, confirmed with the user) — *not* just name-matching.
- Map line `partner_id` → company/partner via `commercial_partner_id`.

**C1. Build the IC balance matrix — ONE multi-company call.** Net receivable/payable per
`[entity, counterparty]` in the **related/intragroup set** as of `end`, across all in-scope entities
at once (don't loop per company A):
```
odoo_read_group("account.move.line",
  domain=[["company_id","in",scope],["parent_state","=","posted"],["date","<=",end],
          ["account_id.account_type","in",["asset_receivable","liability_payable"]],
          ["partner_id","in",<related/intragroup partner ids>]],
  fields=["balance:sum"], groupby=["company_id","partner_id"])
```
One result gives the whole matrix: each row is `(company A, counterparty)` → `M[A][B]`. Resolve each
counterparty → company B (or a non-company related party). The `partner_id` filter keeps cardinality
to the related-party set (cheap), per the payload rule.

**C2. Within-subgroup IC must FULLY reconcile.** For every pair (A, B) where **both are in the
subgroup**, `M[A][B] + M[B][A]` must be ≈ 0 (tolerance 1.00, same currency).
- Not zero ⇒ **EXCEPTION — "intra-subgroup IC not reconciled"**. List both sides + the difference;
  offer to drill into the underlying lines.
- Different reporting currencies ⇒ flag **"review – cross-currency"**.

**C3. IC with the wider group / related parties — REPORT FOR CONFIRMATION.** Where A is in the
subgroup and the counterparty is a **related party outside the subgroup** and **not an elimination
entity** (a parent, sister company, OR a flagged related-party partner that isn't an Odoo company),
report the balance **for confirmation** with the counterparty. This is a *confirmation* list,
distinct from the C2 reconciliation breaks — list counterparty, balance, and which side holds it.

**C4. Is an elimination entry required? (per related pair that transacted in the period).**
Eliminations are booked **in the elimination entities**. For each pair of related entities (A, B)
that **have partner records of each other and transacted during the period**:
1. Confirm they transacted in-period:
   ```
   odoo_search_count("account.move.line",
     domain=[["company_id","=",A],["parent_state","=","posted"],["date",">=",start],["date","<=",end],
             ["partner_id","=",<B's partner>]])   # and the mirror for B vs A
   ```
2. If yes, compare **which accounts each side used** for that IC activity (`company_id=A` so the
   `account_id` labels carry their codes):
   ```
   odoo_read_group(model="account.move.line",
     domain=[["company_id","=",A],["parent_state","=","posted"],["date",">=",start],["date","<=",end],
             ["partner_id","=",<B's partner>]],
     fields=["balance:sum"], groupby=["account_id"], company_id=A)   # mirror for B
   ```
3. **Decision:**
   - **Both sides hit the SAME account (same CoA — shared group CoA) → NO elimination entry
     required** (the two legs already net to zero in that account on consolidation).
   - **Sides hit DIFFERENT accounts → an elimination entry is typically REQUIRED.** Check whether
     a matching entry exists in the relevant **elimination entity** for the period; if not, flag
     **"elimination entry appears to be missing for A↔B"**.
4. Also confirm each elimination entity carries period activity where eliminations are expected:
   ```
   odoo_search_count("account.move",
     domain=[["company_id","=",elim_cid],["state","=","posted"],["date",">=",start],["date","<=",end]])
   ```
   Zero activity while step 3 shows pairs needing elimination ⇒ flag "elimination entries may be
   missing for the period". (Balances *with* an elimination entity are the elimination mechanism
   itself, not a confirmation item.)

---

## Check D — Material & unusual entries
> Surface the entries a reviewer would want to eyeball: the biggest, and anything that looks off.
> Uses the **materiality threshold** from Principles (state it). Cross-reference `audit-trail-review`
> for who/when on anything flagged. Present every account as **code + name**.

**D1. Largest entries in the window** (posted) — the "top movers" list. Filter by materiality
**server-side** and across all entities in one call (don't pull everything then sort):
```
odoo_search_read("account.move",
  domain=[["company_id","in",scope],["state","=","posted"],["date",">=",start],["date","<=",end],
          ["amount_total",">=",materiality]],
  fields=["company_id","name","date","journal_id","ref","move_type","amount_total","partner_id","create_uid"],
  order="amount_total desc", limit=50)
```
The `amount_total >= materiality` filter returns only the material entries (usually few); page with
`offset` only if 50 isn't enough.

**D2. Unusual postings** — run these heuristics and list any hits (each is a *review* item, not an
automatic error):
- **Manual JEs into sensitive accounts** — general-journal lines hitting AR/AP control, bank/cash
  GL, revenue, equity, tax or suspense (these are normally driven by sub-ledgers, not hand-posted):
  ```
  odoo_search_read("account.move.line",
    domain=[["company_id","=",cid],["parent_state","=","posted"],["date",">=",start],["date","<=",end],
            ["journal_id.type","=","general"],["account_id","in",<sensitive_account_ids>]],
    fields=["move_id","account_id","name","debit","credit","partner_id"])
  ```
  (`<sensitive_account_ids>` = the receivable/payable ids from Step 2 + bank/cash, revenue, equity,
  tax-payable and suspense accounts.)
- **Round-number large amounts** (e.g. exact thousands at/above materiality) — often estimates/manual.
- **Back-dated / late-posted** — large gap between accounting `date` and `create_date`, or posted
  after period end into the period; read both and diff:
  ```
  odoo_search_read("account.move",
    domain=[["company_id","=",cid],["state","=","posted"],["date",">=",start],["date","<=",end]],
    fields=["name","date","create_date","write_date","create_uid","journal_id","amount_total"])
  ```
- **Weekend/after-hours postings**, **entries with no `ref`/narration**, and **unexpected user**
  (a poster who doesn't normally book here) — derive from the same `create_date`/`create_uid` read.
- **Possible duplicates** — same partner + same absolute amount appearing more than once in the
  window (group and eyeball).

**D3. Reversals & cancelled** — entries reversed in-period, or moves in a cancelled state that still
look like they need attention. `account.move.reversed_entry_id` (the entry a move reverses) is
reliable; the reverse side may be `reversal_move_id`/`reversal_move_ids` depending on version —
`odoo_fields_get("account.move")` to confirm before adding it to the domain:
```
odoo_search_read("account.move",
  domain=[["company_id","=",cid],["date",">=",start],["date","<=",end],["reversed_entry_id","!=",false]],
  fields=["name","date","reversed_entry_id","amount_total","state"])
```
Note reversal pairs (legitimate) vs an unreversed accrual that should have reversed. Report D as a
ranked list (by amount) with a one-line "why flagged" per item.

---

## Check E — Analytical review (BS & P&L vs prior comparable)
> A **high-level commentary**, not a line-by-line audit: compare this period's P&L and Balance Sheet
> to the **equivalent prior window** and explain the big movements. Group by account (roll up to
> account group/report line where it's cleaner). Present accounts as **code + name**.

**E1. P&L movement (flows: this window vs prior window).** **Both periods in ONE multi-company call**
(same trick as A4 — group by month over `[prior_start, end]`, then sum each window in-memory; don't
issue two full-CoA calls per entity):
```
odoo_read_group("account.move.line",
  domain=[["company_id","in",scope],["parent_state","=","posted"],
          ["date",">=",prior_start],["date","<=",end],
          ["account_id.account_type","in",["income","income_other","expense","expense_depreciation","expense_direct_cost"]]],
  fields=["balance:sum"], groupby=["company_id","account_id","date:month"])
```
Per `[company_id, account_id]`, sum the current-window months vs the prior-window months → Δ and Δ%.
**Comment on the largest movers** (one-line driver each) and on expected lines that are flat/zero.
**Cap the payload: drop accounts with ~zero movement before formatting** (a manager reads the movers,
not the flat lines). If month-cardinality makes the single call heavy, fall back to two `read_group`s
(current, prior) **issued in one batched step**.

**E2. Balance Sheet movement (cumulative as of `end` vs `prior_end`).** BS balances are cumulative,
so they're two snapshots — but make them **multi-company and batch both in ONE step** (not two calls
per entity):
```
# both snapshots, same batched step, grouped by [company_id, account_id]:
odoo_read_group("account.move.line",
  domain=[["company_id","in",scope],["parent_state","=","posted"],["date","<=",end],
          ["account_id.account_type","in",["asset_receivable","asset_cash","asset_current","asset_non_current",
            "asset_prepayments","asset_fixed","liability_payable","liability_current","liability_non_current",
            "liability_credit_card","equity","equity_unaffected"]]],
  fields=["balance:sum"], groupby=["company_id","account_id"])
# second call: identical but ["date","<=",prior_end]
```
Diff per `[company_id, account_id]`; comment on the largest movers, **new or vanished balances**, and
**unexpected signs**. **Cap the payload: drop near-zero-Δ accounts before formatting.**

**E3. Cross-checks & sanity (light).** Flag movements that look internally inconsistent — e.g.
revenue down but receivables up; payables up with no matching expense/asset; cash moved materially
with no corresponding P&L or balance change. Keep it to a few sharp observations.

Output E as a short **commentary** (bullets) + two variance tables (P&L, BS). This is the narrative
a manager reads first — lead with the 3–5 things that actually moved and why.

---

## Step 3 — Build the deliverables (FIXED output contract — identical every run)
The format must be **the same on every run** — never vary structure by what was found. Create
`<working folder>/month-end-review/<period>/` and **always** write all of:
1. `summary.md` — the group view / index: the entity × A–E matrix + headline exceptions (template below).
2. `report.md` — **one combined document**: a short header, then **one `# <entity>` section per
   in-scope entity** in fixed section order A→E (template below). *(Default packaging. If the user asks
   to "split per entity", emit `<NN>-<entity-slug>.md` files instead — same per-entity template.)*
3. `month-end-review_<period>.xlsx` — the workbook (fixed sheets + columns below; all entities stacked
   per sheet with an `entity` column).
4. (offer) PDF — combined (`report.md` → one PDF) or per-entity, per SKILL.md → *Offer PDF output*.

**Determinism rules — this is what stops two runs differing:**
- **Every check A–E appears for every entity, in order, even when clean** — show the status line and
  then either the detail table or the literal `_None._`. Never omit a section because it was empty.
- **Every Excel sheet below is always created** (same names, same order, header row) — a sheet with no
  data gets a single `(none)` row, not omission.
- **Fixed status tokens** (use verbatim in the matrix and per-entity status lines): `✅ Pass` ·
  `⚠️ N to action` · `⚠️ N to review` · `ℹ️ N confirm` · `📝 commentary` (Check E only).
- **Accounts always shown `code name`** (id only in a trailing `id` column); amounts right-aligned to
  **2 dp** with the entity currency; dates `YYYY-MM-DD`.
- **Produce BOTH markdown and Excel every run** — not one or the other.

### Excel (`month-end-review_<period>.xlsx`)
Build with the **xlsx skill** (or `openpyxl`). Sheets:
- `Summary` (same matrix as summary.md, with a header block)
- `A_Drafts` (completeness window, with a `month` column), `A_Suspense` (mandatory),
  `A_RecurringGaps` (A4 — recurring adjustment present prior / missing now, with the prior amount &
  period), `B_NoPartner`, `B_AgedAR`, `B_AgedAP` (each with a `related party?` column;
  external-overdue is the headline), `B_WrongSign`, `C_IC_Subgroup_Breaks` (C2), `C_IC_Group_Confirm`
  (C3 wider-group/related-party confirmation), `C_Elimination` (C4 — pairs needing elimination +
  missing-entry flags), `D_Material` (D1 largest entries), `D_Unusual` (D2/D3 heuristic hits with a
  `why flagged` column), `E_PL_Variance` (E1 — account, current, prior, Δ, Δ%), `E_BS_Variance`
  (E2 — account, as-of-end, as-of-prior-end, Δ) — each with an `entity` column so all in-scope
  entities share a sheet.
- Accounts in every sheet are shown as **code + name** (a `code` and `name` column, not the id).
- **Fixed columns per sheet**: `entity` first, then the columns named above for that sheet, then a
  trailing `id`. Same order every run. A sheet with no data still gets its header row + one `(none)` row.
Freeze header rows; right-align amounts (2 dp); include a totals row where amounts are summed.

### Summary templates
`summary.md`:
```markdown
# Month-End Review — <period>
_Generated <date> · Basis: posted entries (drafts flagged) · Reviewer uid <uid>_
_Run metadata — estimated tokens: input ~<N>k · output ~<M>k (approx.) · materiality: <threshold>_
Entities (<n>, incl. subsidiaries auto-added): <list>

| Entity | A. Completeness | B. Subledger (external) | C. Intercompany | D. Material/unusual | E. Analytical |
|--------|-----------------|-------------------------|-----------------|---------------------|---------------|
| Co A | ✅ | ⚠️ 3 | ⚠️ 1 break · ℹ️ 2 confirm | ⚠️ 4 to review | 📝 5 movers |
| … | | | | | |

_Legend: ✅ pass · ⚠️ exception/item to action or review · ℹ️ confirmation/informational (incl.
related-party AR/AP and C3 wider-group balances) · 📝 commentary (Check E narrative, not pass/fail).
A. Completeness includes A4 recurring-adjustment gaps. Basis: posted; completeness window per Step 0;
materiality per Principles._

## Headline exceptions
- <entity> — <check> — <one line>
```

### Per-entity section (fixed template — same sections every run)
Used as one `# <entity>` section inside `report.md` (default), or as a standalone
`<NN>-<entity-slug>.md` file if the user asked to split. `report.md` opens with a one-line header
(`# Month-End Review — <period>` · basis · entities · generated date) then these sections per entity:
```markdown
# <entity name> (id <cid>) — Month-End Review <period>
_Basis: posted (drafts flagged) · currency <ccy> · materiality <threshold> · generated <date>_

## A. Completeness & period integrity — <status>
**A1 · Drafts in window** (<count>): <table | _None._>
**A2 · Future-dated posted** (<count>): <table | _None._>
**A3 · Suspense / clearing at period end** (MANDATORY): <balances table — never omit>
**A4 · Recurring-adjustment gaps vs prior periods** (<count>): <table | _None._>

## B. Subledger tie-outs & ageing — <status>
**B1 · Control lines without partner** (<count>): <table | _None._>
**B2 · Aged AR / AP** — external overdue is the headline; related parties listed separately: <tables>
**B3 · Wrong-sign balances (external)** (<count>): <table | _None._>

## C. Intercompany & consolidation — <status>
**C2 · Intra-subgroup breaks** (<count>): <table | _None._>
**C3 · Wider-group / related-party confirmations** (<count>): <table | _None._>
**C4 · Elimination required / missing** (<count>): <table | _None._>

## D. Material & unusual entries — <status>
**D1 · Largest entries (≥ materiality)** (<count>): <ranked table>
**D2 · Unusual heuristics** (<count>): <table with a `why flagged` column | _None._>
**D3 · Reversals / cancelled** (<count>): <table | _None._>

## E. Analytical review — 📝 commentary
**Commentary:** <3–5 biggest BS & P&L movers vs the prior comparable window, and why>
**E1 · P&L variance:** <table: account · current · prior · Δ · Δ%>
**E2 · BS variance:** <table: account · as-of-end · as-of-prior-end · Δ>
```
Every detail table uses the fixed columns from the matching Excel sheet (accounts as `code name`,
trailing `id`). When a sub-check has no items, write `_None._` — do not drop the line.

## Step 4 — Report to the user (fixed shape — same order every run)
Output these, in this order:
1. **Output paths** — the `month-end-review/<period>/` folder, `summary.md`, `report.md`, and the `.xlsx`.
2. **The matrix** — the entity × A–E table from `summary.md` (flagging which entities were auto-added
   as subsidiaries), using the fixed status tokens.
3. **Headline items to action** — the top exceptions across A–D (one line each), then the Check E
   commentary headline (the 3–5 biggest movers).
4. **Run metadata line** — generated date · period · basis · materiality used · **estimated tokens
   input/output (approx.)** (SKILL.md → *Run metadata & token estimate*).
5. **Offers** — PDF output (combined or per-entity); drill into any exception (e.g. the specific IC
   mismatch lines); or a supporting report via `generate-reports`.

---

## Thresholds & limits (defaults — confirm/adjust per run)
- Rounding tolerance for IC symmetry & wrong-sign: **1.00** company-currency unit.
- **Window scales to the close type** (month=1, quarter=3, half=6, year=12 months ending at the
  close date); the journals reviewed span the whole window. Balances are as-of the close date.
- **A3 suspense is mandatory** — identify suspense/clearing accounts from the CoA (confirm with the
  user if unsure) and judge their period-end balances; never skip.
- Drafts, no-partner control lines, non-zero suspense, and **intra-subgroup IC breaks (C2)** are
  always flagged. **C3** balances are a **confirmation** list, not failures.
- **A4 recurring-adjustment completeness** compares the **last 2–3 comparable** windows (and the
  rulebook cadence) — flag adjustments present before but missing now; respect monthly vs quarterly
  vs yearly cadence so periodic items aren't false-flagged.
- **D (material/unusual)** uses the materiality threshold (Principles) — state it; its heuristics are
  **review prompts**, not automatic errors. **E (analytical)** is **commentary**, not pass/fail —
  lead with the 3–5 biggest BS & P&L movers vs the prior comparable window and why.
- **Accounts are always shown as code + name**, never the raw id (read `account.account.code`).
- **Related-party AR/AP is never an overdue/wrong-sign alarm** (Check B) — only noted; the
  related-party set = group companies ∪ flagged related parties; reconciliation lives in Check C.
- `amount_residual`/`reconciled` are **current**, not as-of-`end` — note in the report.
- IC matching assumes counterparties are modelled as the companies' partners (matched via
  `commercial_partner_id`); cross-currency pairs are flagged for review, not auto-failed.
- This playbook never posts/edits/reconciles. Acting on an exception is a separate task under the
  normal Safety Protocol.
```
