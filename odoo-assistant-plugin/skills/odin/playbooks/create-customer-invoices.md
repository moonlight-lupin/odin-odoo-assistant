# Playbook: create-customer-invoices

Create customer invoices (and credit notes) in Odoo as `account.move`
(`move_type="out_invoice"`; `out_refund` for credit notes), in **draft** for the user to review.
Two modes: **(A) new invoices** built with the user, or **(B) repeat** of past invoices for a new
period. **Always return a clickable link to each draft** so the user can open, review and confirm.
Detailed runbook — written so a competent model (e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "create customer invoice(s)", "raise an invoice", "bill the customer",
"issue invoices", "same invoices as last month/quarter", "book the AR", "raise a credit note".

---

## Scope & guardrails
- **Process one company at a time.** Each invoice belongs to one `res.company`; load that company's
  **rulebook** (`<working folder>/bookkeeping-rules/<NN>-<slug>.md`) for default income accounts,
  taxes, analytic, the sale journal, and the **invoice naming convention**. If the user names several
  companies, confirm the list and handle them one company at a time.
- **Writes — Safety Protocol:** state what will be created; **show the payload as JSON the first
  time**; create in **draft**; confirm. **Never `action_post` or send/email** without an explicit
  per-invoice instruction.
- **ALWAYS return the draft link** after every `odoo_create` (record URL — Step 0), **and always
  produce a simple Excel action report** of everything created with clickable links (Step 7). Both
  are mandatory.
- **Shared/parent journal:** if the company has no own **sale** journal, use the shared/parent-journal
  workaround (SKILL.md): create under the parent company + journal, then `odoo_write` `company_id`
  to the subsidiary while draft. The rulebook records which journals are own vs shared.
- **JSON arrays for line commands** `[0, 0, {…}]`, never tuples. Always set `company_id`. Don't guess
  customers/products/accounts/taxes — ask.

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder + target company's rulebook loaded (else offer
   design-bookkeeping-rules, or proceed with explicit per-line coding the user gives).

## Step 0 — Field & link reference (Odoo 18)
Header (`account.move`): `move_type="out_invoice"`, `partner_id`, `company_id`, `journal_id` (sale),
`invoice_date`, **either** `invoice_date_due` **or** `invoice_payment_term_id`, `currency_id` (set
for foreign currency), `ref` (customer ref), `narration`, `fiscal_position_id` (optional), and
**`name`** (the invoice number — see Step 3 on the naming convention).
Line (`invoice_line_ids`, each `[0,0,{…}]`): `product_id` (preferred) or free-text `name`,
`quantity`, `price_unit`, `discount`, `account_id` (income), `tax_ids: [[6,0,[id,…]]]`,
`analytic_distribution` (a **dict** `{"<analytic_account_id>": <percent>}` — not `analytic_account_id`).

**Draft record link to return (mandatory):**
```
<url>/web#id=<move_id>&model=account.move&view_type=form
```
where `<url>` is the instance URL from `odoo_connect` (e.g. `https://your-odoo`). (Odoo 18 also
accepts `<url>/odoo/account.move/<move_id>`; the `/web#...` form is the safe universal.) Present it
as a Markdown link, e.g. `[INV draft #<id>](<url>/web#id=<id>&model=account.move&view_type=form)`.

> **Naming caveat:** if you do **not** set `name`, a draft shows `name="/"` and Odoo assigns the
> sequence on post. This playbook **sets `name` explicitly** per the confirmed convention (Step 3).

## Step 1 — On invocation: ask company(ies) + mode
Ask two things up front (don't proceed until answered):
1. **"Which company (or companies) are we invoicing for?"** → resolve to `res.company` id(s); confirm
   names + ids. Process one company at a time.
2. **"Do you want to (A) create new customer invoices, or (B) raise invoices like past ones (repeat
   a period's billing)?"**
Then branch to Mode A (Step 2A) or Mode B (Step 2B). Steps 3–6 are common.

## Step 2A — NEW invoices (build with the user)
Work through each invoice with the user:
1. **Customer:** resolve via
   `odoo_search_read("res.partner", domain=["&",["customer_rank",">",0],["name","ilike","<text>"]], fields=["id","name","property_account_receivable_id","property_payment_term_id","property_account_position_id","currency_id"])`.
   Disambiguate; create a new partner only with explicit confirmation.
2. **Details:** confirm lines (description or product, qty, unit price, discount), income account +
   tax + analytic (defaults from the rulebook; confirm), invoice date, payment term/due date,
   currency, customer `ref`.
3. Proceed to Step 3 (naming) then Step 4 (create draft) → return the link.

## Step 2B — REPEAT past invoices (re-bill a period)
1. **Confirm the source period** to copy from and the **target period** to create for (ask both;
   "always ask the period").
2. **Review past invoices** for the company over the source period to establish the billing pattern:
   ```
   odoo_search_read("account.move",
     domain=[["company_id","=",cid],["move_type","=","out_invoice"],["state","=","posted"],
             ["invoice_date",">=","<src start>"],["invoice_date","<=","<src end>"]],
     fields=["name","partner_id","invoice_date","amount_untaxed","amount_tax","amount_total","invoice_line_ids","ref"],
     order="invoice_date,id")
   ```
   For the recurring ones, read their lines (`account.move.line` / invoice lines) to capture
   description, account, tax, analytic, qty, price.
3. **Establish & present the pattern:** "Last [period] you billed: Customer A — rent £X; Customer B —
   service charge £Y; … Shall I re-create the same set for [target period], updating the line text
   to 'for the month of <target>' and keeping the same accounts/taxes?" **Confirm the list, the
   per-invoice amounts (flag any that should change), and the wording update.**
4. For each confirmed invoice, build it (Step 4) with: same customer/coding, line descriptions
   updated for the target period, `invoice_date` in the target period, and the next name per the
   convention (Step 3). Return each draft link.
> Don't blindly clone — confirm amounts and which customers still apply (some may have ended).

## Step 3 — Invoice naming / rolling number (confirm, then set `name`)
The user controls invoice numbers. Determine the convention and the next number, **confirm**, and set
the `name` field on each draft.
1. Look at recent **posted** invoices for this company+journal to infer the format & last number:
   ```
   odoo_search_read("account.move",
     domain=[["company_id","=",cid],["move_type","=","out_invoice"],["state","=","posted"]],
     fields=["name","invoice_date"], order="invoice_date desc, id desc", limit=5)
   ```
   (Also check the rulebook for a documented convention.)
2. Parse the format (e.g. `INV/2026/0123` → prefix `INV/2026/`, number `0123`, width 4) and propose
   the next (`INV/2026/0124`). Handle year/period resets if the convention uses them.
3. **Confirm with the user**: *"Next invoice number will be `INV/2026/0124`, then `…0125`… — correct?"*
   For a batch, assign sequential numbers and list them.
4. Set `"name": "<confirmed number>"` in each create payload. Ensure **uniqueness** (no two drafts
   share a name, or posting will clash). If the rulebook lacks a convention, record the agreed one there.

## Step 4 — Create the draft, verify, RETURN THE LINK
```
odoo_create("account.move", values={
  "move_type": "out_invoice",
  "name": "INV/2026/0124",                 # confirmed number from Step 3
  "partner_id": <customer_id>,
  "company_id": <cid>,                       # parent for the workaround, then reassign
  "journal_id": <sale_journal_id>,
  "invoice_date": "2026-03-01",
  "invoice_payment_term_id": <term_id>,      # OR "invoice_date_due": "2026-03-31"
  "currency_id": <ccy_id>,                    # only if foreign
  "ref": "<customer ref>",
  "invoice_line_ids": [
    [0, 0, {"name": "Rent — for the month of Mar 2026", "quantity": 1, "price_unit": 5000.0,
            "account_id": <income_acct_id>, "tax_ids": [[6, 0, [<sale_tax_id>]]],
            "analytic_distribution": {"<analytic_account_id>": 100}}]
  ]
})
```
- **Show the payload, confirm, create (draft).** Then **read back** to verify and to confirm the
  `company_id` (after any workaround) and totals:
  `odoo_read("account.move", [id], ["name","partner_id","invoice_date","amount_untaxed","amount_tax","amount_total","state","company_id"])`.
- **Return the link** for the user to review:
  `[<name> — <customer> <total>](<url>/web#id=<id>&model=account.move&view_type=form)`.
- Repeat per invoice; keep a running table **with a link column**.

## Step 5 — Duplicate check
Before creating, warn if a like-for-like invoice already exists (customer + period + amount), and in
Repeat mode skip customers already billed for the target period.

## Step 6 — Post & send (only on explicit instruction)
- Post: `odoo_execute("account.move","action_post",[[id]])` → re-read `state`/`name`. Sending the
  PDF/email is a **further** explicit step.

## Step 7 — Report (+ ALWAYS an Excel action report)
- On-screen: table of name, customer, date, untaxed/tax/total, currency, journal, **draft link**, state.
- **Always produce a simple Excel report** (per the skill's *Action report convention*):
  `<working folder>/bookkeeping/<period>/customer-invoices_<period>.xlsx` with one row per invoice —
  **columns:** Invoice name | Customer | Invoice date | Untaxed | Tax | Total | Currency | Journal |
  Company | State | **Link**. The **Link** cell is a real hyperlink to
  `<url>/web#id=<id>&model=account.move&view_type=form` (openpyxl: `cell.value=name`,
  `cell.hyperlink=<url>`). Header block: company, mode (New/Repeat), period, generated date. Freeze
  the header row; right-align amounts. Tell the user the file path and reproduce the links in chat.
- Open items (unresolved customer/account/tax, amounts to confirm, ended customers in Repeat mode).
- Also write a short markdown run log alongside the xlsx (optional). New revenue type/coding not in
  the rulebook → feed back to design-bookkeeping-rules (living doc).

## Do-NOT list
- ❌ Skip returning the draft link — always provide it after creating.
- ❌ Post/send without explicit instruction (draft only).
- ❌ Reuse the same `name` on two drafts; don't leave numbering unconfirmed.
- ❌ Set both `invoice_date_due` and `invoice_payment_term_id`; don't use `analytic_account_id` on lines.
- ❌ Blindly clone past invoices without confirming customers/amounts/period wording.
- ❌ Post a subsidiary invoice on a parent-owned sale journal — use the shared/parent-journal workaround.

## To refine with the user
- The exact naming convention & whether it resets yearly/monthly (record in the rulebook).
- Default income accounts & sale-tax codes per revenue type; payment terms per customer.
- Posting/sending policy (who approves, when emailed).
