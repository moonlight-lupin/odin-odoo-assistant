# Playbook: posting-closing-journal-entries (and book-any-journal-entry)

Prepare journal entries (`account.move`, `move_type="entry"`) as **drafts** for review. Two uses:
**(A) a period-end closing run** (the cadence-due accruals, prepayments, depreciation, interest, FX,
tax, intercompany) and **(B) an ad-hoc single entry for a specific event** (day-to-day — e.g. record
a loan drawdown, a provision, a one-off adjustment). Detailed runbook — written so a competent model
(e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "post closing entries", "month/quarter/year-end journals", "post the
accruals/depreciation/prepayments", "book a journal for <event>", "record this <accrual/provision/
drawdown>", "make an adjusting entry".

---

## Two sources of truth (consult in this order)
1. **The bookkeeping guide — FIRST source of truth. Ask for it before anything else.** This is the
   company's rulebook `<working folder>/bookkeeping-rules/<NN>-<slug>.md` (from design-bookkeeping-rules)
   plus any accounting policy document. It governs the accounts, journal, tax, analytic, **cadence**,
   reversal policy, and naming. If it doesn't exist, offer to run design-bookkeeping-rules first; or
   proceed only on coding the user explicitly gives (no guessing).
2. **The past 3/6/12-month trend — SECOND source of truth.** How similar entries were actually booked
   before: the recurring entries, their accounts/amounts, the **naming convention**, and — crucially —
   **what's potentially MISSING** this period (an entry posted every prior month but not yet this one).

## Scope & guardrails
- **One company at a time.** **Draft-only by default**; **post only on explicit instruction**
  (`action_post`). Never pay.
- **Confirm before creating** each proposed entry — UNLESS the user explicitly says to proceed
  without confirmation. **If unsure about an amount/account, ask.** If the user wants to **breeze
  through**, create the draft journal **with the right accounts/description but amounts left at 0**
  (a skeleton) for the user to complete/confirm in Odoo (see Step 6).
- **Balanced** when amounts are known (Σdebit = Σcredit); skeleton entries may be 0/0 (draft tolerates it).
- **Strict naming from past trend** (Step 5).
- **Shared/parent journal workaround** for the misc/adjustments journal if the company has none of its
  own (SKILL.md): create under parent + journal, then `odoo_write` `company_id` to the subsidiary.
- **Always output the Excel action report with clickable links** (Step 9).
- JSON line commands are arrays `[0,0,{…}]`; `analytic_distribution` is a dict. Always set `company_id`+`date`.

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder known.

## Step 1 — Mode, company, and (A) period / (B) event
1. **Ask the bookkeeping guide question first:** "Do you have a bookkeeping guide/rulebook for this
   company (or an accounting policy doc)? I'll use it as the primary source of truth." Load it.
2. Confirm the **company** (`cid`, name, currency).
3. **Which mode?**
   - **(A) Period-end run** → **ask the period + close type** (month/quarter/year). The trend lookback
     and cadence-due set scale to it: month → look back ≥3 months; quarter → 3; half → 6; year → 12.
   - **(B) Specific event** → get the event description, date, amounts/parties. (Day-to-day use.)

## Step 2 — Load & apply the bookkeeping guide (source of truth #1)
Read the rulebook. For **Mode A**, note the adjusting entries due for the period's cadence (the
Monthly/Quarterly/Yearly/Ad-hoc sections) and their accounts/journal/reverse policy. For **Mode B**,
find the rule covering this event type; if none, this is a *new* pattern → propose coding from the
policy doc + past trend and (after) feed it back to design-bookkeeping-rules (living document).

## Step 3 — Past-trend view (source of truth #2) + find missing entries
Pull the relevant prior journals to learn the pattern and the naming, and to spot gaps:
```
odoo_search_read("account.move",
  domain=[["company_id","=",cid],["move_type","=","entry"],["state","=","posted"],
          ["date",">=","<lookback start>"],["date","<=","<period end or today>"]],
  fields=["id","name","ref","date","journal_id","amount_total"], order="date,id")
```
- For recurring types, read their lines (`account.move.line`: `account_id`, `name`, `debit`, `credit`,
  `analytic_distribution`) to learn the **exact accounts and the description wording**.
- **Missing-entry detection (Mode A):** for each recurring entry seen in prior periods (e.g.
  "Depreciation" every month, "Bank interest accrual" monthly, a quarterly VAT entry), check whether
  the **equivalent entry exists for the current period**:
  ```
  odoo_search_count("account.move",
    domain=[["company_id","=",cid],["move_type","=","entry"],["ref","ilike","<recurring ref stem>"],
            ["date",">=",period_start],["date","<=",period_end]])
  ```
  If 0 → it's a candidate **missing** entry to propose. List these prominently.
- **Mode B:** find prior entries for the same event type (by `ref`/accounts) to mirror coding & wording.

## Step 4 — Build the proposed entry list
- **Mode A:** cadence-due entries from the guide **+** the missing entries found in Step 3.
- **Mode B:** the single event's entry (with its reversal if it's an accrual-type).
For each: the lines (Dr/Cr accounts + descriptions), journal, date, whether it reverses, and the
amount basis (from the guide's basis, computed from Odoo, or "ask the user").

## Step 5 — Naming convention (strictly follow past trend)
Study how prior entries were named/labelled and **replicate it exactly**:
- Look at recent posted entries' `name` (the journal sequence, e.g. `MISC/2026/03/0007`) and `ref`
  (the human label, e.g. `Depreciation — Mar 2026`). The `name` is normally assigned by the journal's
  sequence on post; the **`ref`/narrative is where you mirror the house style** — match the wording,
  date format, and any code the user uses. If the user manually numbers entries (a consistent `name`
  pattern on past drafts), follow that and set `name` explicitly (confirm).
- Apply the same convention to every entry you create this run.

## Step 6 — Confirm, or breeze-through (skeleton drafts)
- **Default:** present the proposed entries (accounts, amounts, journal, ref) and **get confirmation**
  per entry (or as a batch). If an amount/account is **unclear, ask** — don't fabricate.
- **Proceed-without-confirmation:** only if the user explicitly said so — then create the drafts directly.
- **Breeze-through (amounts unknown / user wants speed):** create each draft with the **correct
  accounts, description, journal, date, and ref**, but **amounts left at 0** (skeleton), so the user
  fills/confirms them in Odoo. Tell the user clearly these are zero-amount skeletons to complete.

## Step 7 — Create the draft entry (concrete Odoo mechanics)
Resolve the misc/adjustments journal (own, else shared/parent workaround). Manual JE:
```
odoo_create("account.move", values={
  "move_type": "entry",
  "journal_id": <misc_journal_id>,
  "date": "<period end / event date>",
  "company_id": <cid>,                       # parent for the workaround, then reassign
  "ref": "<name per Step 5, e.g. 'Depreciation — Mar 2026'>",
  "line_ids": [
    [0, 0, {"account_id": <dr>, "name": "<desc>", "debit": <amt or 0.0>, "credit": 0.0,
            "partner_id": <if intercompany>, "analytic_distribution": {"<analytic>": 100}}],
    [0, 0, {"account_id": <cr>, "name": "<desc>", "debit": 0.0, "credit": <amt or 0.0>}]
  ]
})
```
- **Verify Σdebit == Σcredit** when amounts are known (skip for skeletons — 0/0 is fine in draft).
- Read back: `odoo_read("account.move",[id],["name","ref","date","amount_total","state","company_id"])`
  (confirm `company_id` after any workaround).
- **Return the link** immediately: `[<ref> — <amount>](<url>/web#id=<id>&model=account.move&view_type=form)`.

### Worked examples (entry types)
- **Accrual (reverses next period):** Dr expense, Cr accruals (liability). Mark reverse=yes (Step 8).
- **Prepayment amortisation:** Dr expense, Cr prepayment (asset). Monthly per schedule.
- **Depreciation:** Dr depreciation expense, Cr accumulated depreciation. Monthly.
- **Interest accrual:** Dr interest expense, Cr interest payable/loan. Reverses or unwinds per policy.
- **VAT/tax provision:** Dr tax expense / Cr tax payable, per the guide.
- **Intercompany recharge:** Dr IC receivable (partner = counterparty company), Cr income/recharge —
  set `partner_id` on the IC line; ties to month-end-review Check C.
- **Ad-hoc event (Mode B), e.g. loan drawdown:** Dr bank/loan-receivable, Cr loan payable — amounts &
  parties from the user; coding from the guide.

## Step 8 — Reversals (accrual-type entries flagged "reverse")
Two mechanisms — verify field names on the instance (`odoo_fields_get`) and prefer whichever works:
- **Reverse wizard** (`account.move.reversal`): create it referencing the move, then run it —
  ```
  wiz = odoo_create("account.move.reversal", {"move_ids": [[6,0,[move_id]]],
                    "date": "<first day next period>", "journal_id": <misc_journal_id>})
  odoo_execute("account.move.reversal", "reverse_moves", [[wiz]])
  ```
  (Field names like `date`/`reversal_date` vary — confirm; the reversal is created in draft.)
- **Manual mirror:** create the opposite entry dated the first day of next period (draft).
Record which entries reverse so they aren't double-counted, and include them in the report.

## Step 9 — Report (+ ALWAYS Excel with links)
- On-screen: entries (ref, journal, Dr/Cr, amount or "skeleton", state, **link**), the **missing
  entries** flagged, and which reverse.
- **Always produce the Excel action report** (skill's *Action report convention*):
  `<working folder>/bookkeeping/<period>/closing-entries_<period>.xlsx` — one row per entry, columns:
  Ref/Name | Type | Cadence | Journal | Debit acct | Credit acct | Amount | Reverses? | Company |
  State | **Link** (clickable hyperlink `<url>/web#id=<id>&model=account.move&view_type=form`). Header
  block (company, mode, period, generated date). Tell the user the path + reproduce links in chat.

## Step 10 — Post & hand off
- **Post only on explicit instruction:** `odoo_execute("account.move","action_post",[[id]])` →
  re-read `state`. Otherwise leave draft.
- **Mode A:** offer to run **month-end-review** for the company/period to confirm the books tie out.
- Feed any new/uncovered entry type back into **design-bookkeeping-rules** (living document).

## FX revaluation (when the guide requires it at period end)
- Find foreign-currency monetary balances: `account.move.line` where `company_id=cid`, monetary
  account types (receivable/payable/bank/liquidity), `currency_id` ≠ company currency, not fully
  reconciled; group by `currency_id`/`account_id` summing `amount_currency` and `balance`.
- Get the period-end rate (`res.currency` / `res.currency.rate`), compute revalued company-currency =
  `amount_currency × rate`, and the **difference vs current `balance`**.
- Post the difference: Dr/Cr **FX gain/loss** (P&L) and the monetary/revaluation account per the guide.
- *(Enterprise has a built-in "Unrealized Currency Gains/Losses" report that can post this — prefer it
  if the user uses it; the manual entry above is the fallback.)*

## Idempotency / duplicate guard
Before creating a period entry, check it doesn't already exist (Step 3's missing-entry search in
reverse): if an entry with the same `ref` stem already exists for the period, **don't duplicate** —
report it instead.

## Do-NOT list
- ❌ Skip the bookkeeping-guide question (source of truth #1) or the past-trend review (#2).
- ❌ Fabricate an amount/account — ask, or create a zero-amount skeleton for the user to complete.
- ❌ Post (or pay) without explicit instruction; create an unbalanced non-skeleton entry.
- ❌ Deviate from the past naming convention.
- ❌ Post a subsidiary entry on a parent-owned journal — use the shared/parent-journal workaround.
- ❌ Skip the Excel action report / links.
