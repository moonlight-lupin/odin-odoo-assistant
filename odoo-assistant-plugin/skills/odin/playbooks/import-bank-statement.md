# Playbook: import-bank-statement

Import a **manual bank statement** (CSV or PDF the user provides) into Odoo as bank statement lines
for a specific bank journal — for when transactions are **not auto-synced** (e.g. no SaltEdge / bank
feed). Produces the statement + lines so they can then be reconciled (→ bank-reconciliation). The
**closing balance must tie back to the statement**. Detailed runbook — written so a competent model
(e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "import a bank statement", "upload the bank statement", "load this CSV/PDF
statement", "enter the bank transactions", "no bank feed — add the statement manually".

> **Settlement model (SKILL.md → Cash & settlement model):** the bank statement is how cash movements
> *enter* the books; this playbook gets them in, and **bank-reconciliation then settles** the open
> items against them. Importing ≠ settling.

---

## Scope & guardrails
- **One company + one bank/cash journal + one statement at a time.**
- **This WRITES.** Creating statement lines records bank transactions (each posts a move to the bank
  + suspense account). So: **parse → map → confirm the data AND verify the closing-balance tie-out on
  paper BEFORE creating anything.** Create only after the user confirms.
- **Don't double-import.** Check existing lines for the journal over the statement's date range first;
  skip/flag overlaps.
- **Always output the Excel report + a link to the statement** (Step 6). Hand off to
  **bank-reconciliation** afterwards (importing ≠ reconciling).
- JSON line commands are arrays `[0,0,{…}]`. Verify field names with `odoo_fields_get` (they vary by version).

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder known. 3. The user has the statement file (CSV/PDF).

## Step 1 — Confirm entity & bank account
1. Confirm the **company** (`cid`, name) and the **bank/cash account = the journal**:
   ```
   odoo_search_read("account.journal", domain=[["company_id","=",cid],["type","in",["bank","cash"]]],
     fields=["id","name","code","currency_id","default_account_id","suspense_account_id"])
   ```
   Confirm which journal the statement belongs to (`jid`), and its currency.
2. Establish the **opening balance** for the new statement = the **closing balance of the last
   statement** for this journal (or the current book balance if none):
   ```
   odoo_search_read("account.bank.statement", domain=[["journal_id","=",jid]],
     fields=["name","date","balance_end_real","balance_start"], order="date desc", limit=1)
   ```

## Step 2 — Analyse past statement entries (learn format + avoid overlap)
- Read recent statement lines for this journal to learn the **label/description style** and the **last
  date already imported** (so you don't re-import):
  ```
  odoo_search_read("account.bank.statement.line", domain=[["journal_id","=",jid]],
    fields=["date","payment_ref","amount","partner_id"], order="date desc", limit=20)
  ```
- Note the last imported date; the new statement should start after it (flag any overlap).

## Step 3 — Analyse the provided CSV/PDF
Read the file the user provides and identify its structure:
- **CSV:** parse the header row; identify columns for **date**, **description/narrative**, and
  **amount** (either a single signed `Amount`, or separate `Money In`/`Money Out` or `Debit`/`Credit`),
  plus any **running balance** and the **opening/closing balance** rows.
- **PDF:** extract the transaction table and the opening/closing balances printed on the statement.
- Capture the statement's **opening balance** and **closing balance** as printed (for the tie-out).

## Step 4 — Map columns (date, description, amount — minimum) & confirm
Map source → Odoo `account.bank.statement.line` fields, and **confirm the mapping with the user**:
- **date** → `date` (**confirm the date format** — DD/MM/YYYY vs MM/DD/YYYY — this is a common error source).
- **description** → `payment_ref` (the bank narrative); keep extra detail in `narration` if useful.
- **amount** → `amount`, as a **single signed number**: **money IN = positive, money OUT = negative**.
  If the source has separate Debit/Credit (or Money In/Out) columns, combine: `amount = credit − debit`
  (confirm the sign convention with the user).
- Optional: `partner_id` (only if confidently identifiable), `ref`.
- Present a **preview table** (date | description | amount) of the parsed rows and the row count;
  confirm it matches the statement before proceeding.

## Step 5 — Verify the tie-out ON PAPER, then extract/format/upload
1. **Paper tie-out first:** `opening balance + Σ(line amounts)` must equal the statement's **closing
   balance**. If it doesn't, **stop** — a line is missing/extra/mis-keyed (re-check Step 3/4). Do not
   create a statement that doesn't tie.
2. **Create the statement with its lines** (one `account.bank.statement`, opening & closing balances
   set so Odoo re-checks the tie-out):
   ```
   odoo_create("account.bank.statement", values={
     "name": "<journal code> — <period/label>",
     "journal_id": <jid>,
     "date": "<statement end date>",
     "balance_start": <opening balance>,
     "balance_end_real": <closing balance per the bank statement>,
     "line_ids": [
       [0, 0, {"date": "2026-03-01", "payment_ref": "<narrative>", "amount": -120.50}],
       [0, 0, {"date": "2026-03-02", "payment_ref": "<narrative>", "amount": 5000.00}]
       # … one per transaction, signed
     ]
   })
   ```
   (If the instance wants `journal_id` on each line too, add it — verify with `fields_get`. For large
   statements, create in batches and keep one statement.)

## Step 6 — Check closing balance ties back (Odoo's own check)
Read the created statement and confirm Odoo's computed end balance equals the bank's closing balance:
```
odoo_read("account.bank.statement", [stmt_id], ["balance_start","balance_end","balance_end_real"])
```
- `balance_end` (computed = `balance_start` + Σ line amounts) **must equal `balance_end_real`**
  (the figure from the bank statement). **If not, the import is wrong** — report the difference,
  identify the offending line(s), fix (or delete and re-import), and re-check. **Do not leave a
  statement that doesn't tie.**

## Step 7 — Report (+ Excel) & hand off
- **Always produce the Excel action report** (skill's *Action report convention*):
  `<working folder>/bookkeeping/<period>/bank-statement-import_<journal>_<period>.xlsx` — one row per
  imported line (Date | Description | Amount | Running balance), a header block (company, journal,
  opening/closing balance, tie-out OK?), and a **Link** to the statement
  (`<url>/web#id=<stmt_id>&model=account.bank.statement&view_type=form`).
- State the tie-out result clearly (✅ ties / ❌ off by X).
- **Hand off to bank-reconciliation** to match the now-imported lines to open items/payments.

## Do-NOT list
- ❌ Create the statement before the **paper tie-out** passes and the user confirms the parsed data.
- ❌ Leave a statement where `balance_end ≠ balance_end_real` (must tie back).
- ❌ Re-import dates already present for the journal (duplicate transactions).
- ❌ Guess the date format or sign convention — confirm both.
- ❌ Treat import as reconciliation — reconciling is the separate bank-reconciliation playbook.

## To refine with the user
- The usual file format per bank (column layout, date format, sign convention) — record per company
  in the bookkeeping rulebook so future imports are faster.
- Whether to attach the source CSV/PDF to the statement (via `ir.attachment`).
