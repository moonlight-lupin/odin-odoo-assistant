# Playbook: bank-reconciliation

Reconcile a bank/cash journal for a period: match **bank statement lines** to open
receivables/payables/payments so the journal's book balance ties to the bank, and clear the bank
**suspense** account. Detailed runbook — written so a competent model (e.g. Sonnet) can execute
step-by-step.

**Trigger phrases:** "bank reconciliation", "reconcile the bank", "match the bank statement",
"clear the suspense account", "reconcile <bank journal>".

> Odoo's bank reconciliation is largely a UI widget; over the MCP we work at the
> `account.bank.statement.line` / `account.move.line` level and the reconcile wizards. **Verify the
> exact method on the instance before batching, and fall back to flagging for the UI if uncertain.**

> **This is the settlement step** (SKILL.md → Cash & settlement model): cash is recognised from the
> bank statement, and reconciling each line here is what **settles** the customer invoice / vendor
> bill / payment it relates to. If statements aren't synced, run **import-bank-statement** first.

---

## Scope & guardrails
- **One company + one bank/cash journal + one period at a time.** **Always ask the period** (the
  statement window). Confirm the company (`cid`) and journal (`jid`).
- **Reconcile scope = apply clear 1:1 matches on confirmation.** Odin applies an unambiguous
  statement-line ↔ open-item match **after the user confirms**; everything uncertain is **flagged for
  manual handling**, not forced. Odin does **not** hand-create clearing entries (beyond what existing
  reconcile-model rules produce) in this playbook.
- **Matching waterfall (in this order)** — see Step 3:
  1. **Existing `account.reconcile.model` rules** — apply where they match. *No need to confirm* (these
     are the user's pre-set rules; they may auto-code charges/interest etc.).
  2. **Infer from past months' reconciliations** — learn how similar lines were reconciled before,
     propose the match, **confirm with the user**.
  3. **Manual 1:1 matching** — match remaining lines to open items, **confirm with the user**.
  4. **Combination (N-to-1) matching** — where a statement line has no single match, **try summing
     combinations of outstanding payments / open items** to see if a mix equals the line amount (e.g.
     one deposit clearing several invoices). Propose the combination, **confirm with the user**.
  5. **Propose new rules FYI** — where a recurring pattern has no rule, *suggest* an
     `account.reconcile.model` as a recommendation. **Do NOT create it** (read-only suggestion).
- **Verify the reconcile mechanism** on the instance (Step 0/4) and **test on one line** before
  applying to many. If the programmatic reconcile is uncertain/errors, **flag for the UI** — the
  proposed matches still have value.
- **Always produce the Excel action report + links** (Step 6). Never bank journals via the
  shared/parent-journal workaround — bank journals are genuinely per-company.

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder + the company's **rulebook** loaded (for how
   recurring items like charges/interest/FX should be coded, and which accounts are suspense/clearing).

## Step 0 — Model & field reference (verify on the instance)
The instance has: `account.bank.statement`, `account.bank.statement.line`, `account.reconcile.model`
(+ `.line`, `.partner.mapping`), `account.full.reconcile`, `account.partial.reconcile`, and reconcile
wizards (`account.reconcile.wizard`, `account.auto.reconcile.wizard`). **Field names vary by version —
confirm with `odoo_fields_get` before relying on them.** Likely-relevant `account.bank.statement.line`
fields: `journal_id`, `date`, `amount`, `payment_ref`, `partner_id`, `narration`, `is_reconciled`,
`move_id`. Each statement line posts a move whose counterpart sits in the bank journal's
**suspense account** until reconciled.

## Step 1 — Pick company, bank journal, period
1. Confirm `cid` + the bank/cash **journal** (`account.journal`, `type` in `bank`/`cash`); note its
   `suspense_account_id` and `default_account_id`.
2. **Ask the period** (statement window) → `start`/`end`.

## Step 2 — Get the starting point (unreconciled lines) — handles manual/mixed
- **If statement lines already exist** (imported/fed): pull the unreconciled ones:
  ```
  odoo_search_read("account.bank.statement.line",
    domain=[["journal_id","=",jid],["is_reconciled","=",false],["date",">=",start],["date","<=",end]],
    fields=["id","date","payment_ref","partner_id","amount","narration","move_id"], order="date,id")
  ```
  (Verify `is_reconciled` exists via `fields_get`; if not, find the equivalent unreconciled filter.)
- **If statements aren't synced (manual CSV/PDF):** run **import-bank-statement** first to parse,
  map, tie-out, and upload the statement + lines — then come back here to reconcile. (Don't hand-create
  ad-hoc lines here; the import playbook does it properly with the closing-balance tie-out.)
- Also pull the **open counterpart items** for matching:
  ```
  odoo_search_read("account.move.line",
    domain=[["company_id","=",cid],["parent_state","=","posted"],["reconciled","=",false],["amount_residual","!=",0],
            ["account_id.account_type","in",["asset_receivable","liability_payable"]]],
    fields=["id","move_id","partner_id","date","date_maturity","amount_residual","name"])
  ```
  and unmatched payments (`account.payment`) if used.

## Step 3 — Matching waterfall (apply the order above)
For each unreconciled statement line, resolve a match in this priority:

**3a. Existing reconcile-model rules (auto, no confirmation).**
```
odoo_search_read("account.reconcile.model", domain=[["company_id","=",cid]], fields=["id","name","rule_type","match_label"])
```
Where a rule clearly matches the line (e.g. a "Bank charges" rule by label), apply it — these are the
user's pre-set rules. (Confirm the apply mechanism on the instance; some rules auto-create the coding entry.)

**3b. Infer from past months' reconciliations (confirm).** Learn the house pattern: look at how this
journal's **already-reconciled** lines were matched/coded in prior months, so recurring items
(standing orders, rent, the same vendor) map the same way:
```
odoo_search_read("account.bank.statement.line",
  domain=[["journal_id","=",jid],["is_reconciled","=",true]],
  fields=["payment_ref","partner_id","amount","move_id"], order="date desc", limit=50)
```
(then inspect what those lines reconciled to — partner / counterpart account). Propose the matching
open item or coding for the current line **and confirm with the user**.

**3c. Manual 1:1 matching (confirm).** For the rest, match each line to a single open item by
**amount + partner + reference** (exact amount first, then close/fuzzy). Present a proposal table;
**confirm** each before applying.

**3d. Combination (N-to-1) matching (confirm).** For a statement line with no single match, try to
match it to a **sum of several outstanding items** — typically unreconciled `account.payment`
lines, or several open invoices (e.g. one deposit clearing multiple receipts).
- **Candidate set:** outstanding/open items of the **same sign** as the line, ideally the **same
  partner** (if the line names one) and within a **date window** around the line's date.
- **Bound the search** (combinatorial blow-up): cap the candidate set to ≈ ≤ 15 items; try
  combinations of **size 2–5** whose residuals **sum to the line amount within a small tolerance**
  (e.g. ≤ 0.02 of currency). If there are too many candidates or several combinations tie, **ask the
  user to narrow** (by partner/date) rather than guessing.
- Present each viable combination ("line £1,500 = Payment A £500 + Payment B £1,000") and **confirm**
  before applying. Apply by reconciling the **set** together (Step 4, N-to-1).

**3e. Still unmatched → flag + propose-rule-FYI.** Lines with no open item, rule, or combination:
**flag for manual handling**. Where it's a clear recurring pattern (e.g. monthly bank charge with no
rule), **suggest** a new `account.reconcile.model` (rule_type, match label, target account) as an FYI
recommendation — **do not create it**.

## Step 4 — Apply confirmed matches: concrete Odoo mechanics (verify → test-one → UI fallback)

### The reconcile primitive — and the method that does NOT exist
> ⛔ **Never call `account.bank.statement.line.reconcile(...)`.** It does **not exist in Odoo 18** —
> it was the ≤15 API, removed when bank rec was rewritten, and errors with *"The method
> 'account.bank.statement.line.reconcile' does not exist"*. The `reconcile([{ "id": … }, …])`
> vals-list form (passing statement-line ids + dicts) is the same legacy call — **do not use it.**
> In 16–18 reconciliation happens **only at the move-line level.**

`account.move.line.reconcile()` reconciles a **recordset of move lines on the SAME account whose
balances net to ~0**, creating an `account.full.reconcile` (or a partial). Call it on the **counterpart
move lines** — never on statement-line ids, never with dicts:
```
odoo_execute("account.move.line", "reconcile", [[<line_id_a>, <line_id_b>, ...]])
```

### Anatomy + which matches the primitive can / can't do (Odoo 16–18) — CHECK ACCOUNTS FIRST
- Each `account.bank.statement.line` posts an `account.move` (`move_id`) with two lines: a **liquidity**
  line on the bank GL, and a **counterpart** line on the journal's **suspense account**
  (`journal.suspense_account_id`) — a placeholder until matched.
- **`reconcile()` works ONLY when the counterpart and the open items are already on the SAME account.**
  Read both sides' `account_id` before doing anything.
- **Counterpart on SUSPENSE while the open items are on a *different* account → the primitive CANNOT
  join them.** This is the usual case for:
  - **registered / outstanding payments** — they sit on the journal's *outstanding payments/receipts*
    account (e.g. "Payment clearance account"), **not** the suspense account; and
  - **matching an invoice** — the open line is on receivable/payable.
  Re-pointing the suspense leg to the open item's account and *then* reconciling is what Odoo's
  **bank-reconciliation widget** (`account.bank.rec.widget`, Enterprise) does in one click — and since
  that widget isn't callable over RPC, you replicate it with the **account-substitution procedure**
  below. Do it deliberately and **verify state after every step**; if anything looks off, stop and
  finish the match in the Odoo bank-rec UI (it auto-suggests the items by partner + amount).
- The primitive **does** fit when both sides already share an account — e.g. you've coded a charge so
  the counterpart lands on the expense account, or the journal's outstanding account *is* the
  statement counterpart account. Verify with a `fields_get`/read, then test on one line.

### Procedure
1. **Get the statement line's counterpart move line:** read its `move_id` → `line_ids` → the
   non-liquidity (suspense/outstanding) line. Note that line's `account_id`.
2. **Gather the counterpart line(s)** to reconcile — the single open item (1:1), or the **set** of
   outstanding lines for an N-to-1 combination. Confirm they are on the **same account** as the
   statement counterpart and **net to ~0** together.
3. **Test on ONE confirmed match** first:
   ```
   odoo_execute("account.move.line", "reconcile", [[<stmt_counterpart_line_id>, <open_line_id(s)...>]])
   ```
   Re-read to verify: statement line `is_reconciled=true`; open item `reconciled=true` /
   `amount_residual=0`; a `full_reconcile_id` is set.
4. If it works, apply the remaining **confirmed** matches the same way (one line for 1:1, the set for
   3d combinations). **If the statement counterpart is on suspense while the open items are on a
   different (clearing/receivable) account**, use the **account-substitution procedure** below first.
   If a method errors in an unexpected way (not the benign `None`-marshal case), stop and flag for the
   UI. Don't guess.
- Apply only **confirmed** matches (3b/3c/3d). Existing-rule matches (3a) use the rule's own apply
  mechanism. Never go beyond matching into hand-built clearing entries.

### Account-substitution procedure (suspense → clearing/receivable) — the programmatic path
When the statement counterpart sits on **suspense** but the open items are on a different account
(the **payment-clearance / outstanding-payments** flow, or a direct invoice match), you replicate what
the bank-rec widget does: swap the suspense leg onto the open items' account, then reconcile. Verified
on Odoo 18. Do it **per statement line, on confirmed matches only**, verifying after each step.

> ⚠️ Several of these methods return `None`; on the XML-RPC server that may surface as **`cannot
> marshal None …`** — **benign, the call succeeded**. **Re-read the record after every step** to
> confirm state (SKILL.md → `None`-returning methods). Never retry a step blindly on that error.

1. **Read the statement line's move + its two lines** — the liquidity (bank GL) line and the suspense
   counterpart line; note `move_id`, the suspense `line_id`, the partner, and the **target account**
   (the account the open items sit on, e.g. the *Payment clearance* / receivable account).
2. **Confirm the match nets to zero** — the suspense line balance == −Σ(open item balances) on the
   target account.
3. **A — reset the statement move to draft:** `odoo_execute("account.move","button_draft",[[move_id]])`
   → re-read `state` == `"draft"`.
4. **B — substitute the account on the suspense line** (and set the partner) so it lands on the open
   items' account:
   ```
   odoo_execute("account.move","write",
     [[move_id], {"line_ids": [[1, <suspense_line_id>, {"account_id": <target_acct>, "partner_id": <pid>}]]}])
   ```
   (Returns `true`.) Re-read the line → `account_id` is now the target account.
5. **C — re-post:** `odoo_execute("account.move","action_post",[[move_id]])` → re-read `state` ==
   `"posted"`.
6. **D — reconcile on the now-shared account:** the edited counterpart line + the open item lines (all
   on the target account, netting to ~0):
   ```
   odoo_execute("account.move.line","reconcile",[[<edited_counterpart_line_id>, <open_line_ids...>]])
   ```
   Re-read → statement line `is_reconciled=true`, each open item `reconciled=true` /
   `amount_residual=0`, a `full_reconcile_id` is set.
7. **E — (if applicable) onward reconciliation** — e.g. for registered payments, also reconcile the
   payment's **AP/AR** debit line against the original bill/invoice's AP/AR credit line (same account,
   net to ~0). Same `reconcile()` call.

**Guardrails:** confirmed matches only; one line at a time; verify state after every step; if any step's
state doesn't change as expected, **stop and flag for the UI** — do not improvise account changes.

## Step 5 — Suspense check
After matching, confirm the bank journal's `suspense_account_id` **nets to ~0** at period end (any
residual = unmatched lines still sitting in suspense). This ties into month-end-review Check A3.
```
odoo_read_group(model="account.move.line",
  domain=[["company_id","=",cid],["account_id","=",<suspense_acct>],["parent_state","=","posted"],["date","<=",end]],
  fields=["balance:sum"], groupby=["account_id"], company_id=cid)   # company_id → account label carries the code
```

## Step 6 — Report (+ ALWAYS an Excel action report)
- On-screen summary: # statement lines, # matched (by rule / inferred / manual), # flagged unmatched,
  residual suspense balance.
- **Always produce the Excel action report** (skill's *Action report convention*):
  `<working folder>/bookkeeping/<period>/bank-recon_<journal>_<period>.xlsx` — one row per statement
  line, columns: Date | Description (payment_ref) | Amount | Match type (rule / inferred / manual /
  combination / unmatched) | Counterpart (partner / open item(s) / account) | State | **Link** (clickable to the
  statement line's move: `<url>/web#id=<id>&model=account.move&view_type=form`). Note the residual
  suspense balance and any proposed new rules (FYI). Tell the user the path + reproduce links in chat.
- New recurring coding not in the rulebook → feed back to design-bookkeeping-rules (living doc).

## Do-NOT list
- ❌ Call `account.bank.statement.line.reconcile(...)` or pass `[{"id": …}, …]` vals — Odoo ≤15 legacy,
  **removed in 18** ("method does not exist"). Reconcile **only** via
  `account.move.line.reconcile([<same-account line ids netting to ~0>])` on the counterpart lines.
- ❌ Call `account.move.line.reconcile()` across **different accounts** — first run the
  **account-substitution procedure** (swap the suspense line onto the open items' account, verifying
  each step) so both sides share an account; only then reconcile. Never substitute accounts without the
  per-step state verification, and stop/flag for the UI if a step doesn't take.
- ❌ Treat the **`cannot marshal None`** error as a failure or retry on it — the method committed;
  re-read to verify state.
- ❌ Apply 3b/3c/3d matches without confirmation; never bulk-auto-reconcile beyond existing rules.
- ❌ Create `account.reconcile.model` rules (Step 3e is FYI/suggest only).
- ❌ Hand-create clearing entries beyond what existing rules produce.
- ❌ Reconcile lines that aren't on the **same account** or don't **net to ~0** (`reconcile()` needs both).
- ❌ Let the combination search blow up — bound candidates/size; if ambiguous, ask the user to narrow.
- ❌ Batch-reconcile before testing the mechanism on one line; if uncertain, flag for the UI.
- ❌ Skip the suspense check or the Excel action report.

## To refine with the user
- The exact reconcile method that works over the MCP on this instance (wizard vs `reconcile()`),
  recorded once confirmed.
- Auto-match tolerance for 1:1 (exact only vs small rounding).
- Which recurring patterns should become reconcile-model rules (then created separately/in UI).
