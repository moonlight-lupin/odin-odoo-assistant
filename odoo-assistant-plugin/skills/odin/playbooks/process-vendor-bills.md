# Playbook: process-vendor-bills

Record vendor bills (supplier invoices) into Odoo as `account.move` with `move_type="in_invoice"`
(`in_refund` for vendor credit notes), in **draft** for review. Two ingestion sources:
**(1) a bill document you provide** (Odin reads it and extracts the data), or **(2) draft bills
already in Odoo** (e.g. from email/OCR digitisation) that Odin **codes and completes**. Detailed
runbook — written so a competent model (e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "process vendor bills", "enter supplier invoices", "record bills", "book the
AP", "code these draft bills", "here's an invoice to enter".

---

## Scope & guardrails
- **DRAFT ONLY.** Odin **never posts and never pays** vendor bills. It creates/completes them in
  draft; the user posts and pays in Odoo. (No `action_post`, no payment registration — ever, in
  this playbook.)
- **Process one company at a time.** Load that company's **rulebook**
  (`<working folder>/bookkeeping-rules/<NN>-<slug>.md`) for default expense accounts, purchase
  taxes, analytic, and the purchase journal.
- **The bill number is the SUPPLIER's reference** → put it in **`ref`** (Bill Reference). **Do NOT
  set `name`** — Odoo assigns its own internal number (e.g. `BILL/2026/0001`) on post. (Contrast
  with customer invoices, where we control the rolling number.)
- **Duplicate check is mandatory** (AP risk = paying twice): key on **vendor + their `ref`** (+ amount).
- **Shared/parent journal:** if the company has no own **purchase** journal, use the
  shared/parent-journal workaround (SKILL.md): create under the parent company + journal, then
  `odoo_write` `company_id` to the subsidiary while draft.
- **Always return the draft link** and **always produce the Excel action report** (Step 6 / Step 7).
- **PO matching: sometimes** — when a purchase order plausibly matches, surface it and confirm
  linking; otherwise record directly (Step 4).
- JSON line commands are arrays `[0,0,{…}]` / updates `[1,line_id,{…}]`. Don't guess vendor/account/
  tax — ask.

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder + the company's rulebook loaded (else offer
   design-bookkeeping-rules, or proceed with explicit per-line coding the user gives).

## Step 0 — Field reference (Odoo 18 vendor bill)
Header (`account.move`): `move_type="in_invoice"`, `partner_id` (vendor), `company_id`,
`journal_id` (**purchase**), `invoice_date` (the **supplier's** invoice date), **either**
`invoice_date_due` **or** `invoice_payment_term_id`, `currency_id` (set if foreign), **`ref`**
(supplier's bill number), `narration`, `invoice_origin` (source doc / PO name if matched).
Line (`invoice_line_ids`): `product_id` (preferred) or free-text `name`, `quantity`, `price_unit`,
`account_id` (expense), `tax_ids: [[6,0,[id,…]]]` (purchase taxes, `type_tax_use="purchase"`),
`analytic_distribution` (dict `{"<analytic_account_id>": <percent>}`).
Record link to return: `<url>/web#id=<id>&model=account.move&view_type=form` (`<url>` from `odoo_connect`).

## Step 1 — On invocation: ask company + which source
1. **"Which company are we booking bills for?"** → resolve `cid`, confirm name+id. One company at a time.
2. **"Is this a bill document you'll give me, or draft bills already sitting in Odoo to complete?"**
   → branch to Step 2A (document provided) or Step 2B (existing Odoo drafts). Both can be used in a session.

## Step 2A — Source: a bill document you provide
1. **Read the document** the user shares (PDF/image/text in the conversation) and extract: vendor
   name, the supplier's invoice number, invoice date, due date/terms, currency, line items
   (description, qty, unit price), tax, and the total.
2. **Confirm the extraction** back to the user (especially vendor, ref, date, total) before creating —
   you read it; they verify.
3. **Resolve the vendor:**
   ```
   odoo_search_read("res.partner", domain=["&",["supplier_rank",">",0],["name","ilike","<text>"]],
     fields=["id","name","property_account_payable_id","property_payment_term_id","property_account_position_id","vat","currency_id"])
   ```
   Disambiguate; create a new vendor only with explicit confirmation.
4. **Study the vendor's past bills — match their house style & coding.** Before coding, look at how
   this vendor's previous bills were recorded, so the new bill mirrors the same **line description /
   narrative style** and uses the **same accounts/taxes**:
   ```
   odoo_search_read("account.move",
     domain=[["company_id","=",cid],["partner_id","=",<vendor_id>],["move_type","=","in_invoice"]],
     fields=["id","name","ref","invoice_date","invoice_line_ids","amount_total"], order="invoice_date desc", limit=5)
   odoo_read("account.move.line", <their invoice_line_ids>, ["name","account_id","tax_ids","quantity","price_unit"])
   ```
   - From those lines, learn the **description wording/format** the vendor's bills usually use and the
     **expense account(s)/tax** they're coded to — reuse that style/coding for the bill-under-processing
     (overriding the rulebook default only where the history is more specific/consistent).
   - **Also read the prior actual invoice documents** where available — fetch their attachments and
     view them to match the narrative precisely:
     ```
     odoo_search_read("ir.attachment",
       domain=[["res_model","=","account.move"],["res_id","in",<prior move ids>]],
       fields=["id","name","mimetype","res_id"])
     odoo_read("ir.attachment", [<attachment id>], ["datas"])   # base64; view/extract where you can
     ```
     (If a PDF blob can't be read directly, rely on the prior line-item text, which already captures
     the wording.) If there are **no prior bills** for this vendor, fall back to the rulebook + the
     document's own wording.
5. **Code the lines** — apply the style/coding learned in step 4, else the rulebook default
   (expense account + purchase tax + analytic per category).
6. Continue to Step 3 (PO check) → Step 4 (duplicate) → Step 5 (create draft, **attach the document**,
   return link).

## Step 2B — Source: complete existing Odoo draft bills
1. **List the draft bills** awaiting coding:
   ```
   odoo_search_read("account.move", domain=[["company_id","=",cid],["move_type","=","in_invoice"],["state","=","draft"]],
     fields=["id","partner_id","invoice_date","ref","amount_total","invoice_origin","invoice_line_ids"], order="create_date desc")
   ```
2. For each, **read its lines** to see what's missing/uncoded:
   ```
   odoo_read("account.move.line", <invoice_line_ids>, ["name","product_id","account_id","tax_ids","price_unit","quantity"])
   ```
   (If a source attachment exists and you can view it, use it to verify; otherwise rely on the
   existing data + ask the user where unclear.)
3. **Complete & code** the bill with `odoo_write` (still draft) — update existing lines and/or add
   missing fields:
   ```
   odoo_write("account.move", [move_id], {
     "ref": "<supplier ref if missing>", "invoice_date": "<if missing>",
     "invoice_line_ids": [[1, <line_id>, {"account_id": <exp>, "tax_ids": [[6,0,[<tax>]]],
                                          "analytic_distribution": {"<analytic>": 100}}]]
   })
   ```
   Use rulebook defaults; ask when a line's coding is ambiguous.
4. Continue to Step 3 (PO check) → Step 4 (duplicate) → Step 5 (verify + link).

## Step 3 — PO matching (when one exists)
Check for a plausible purchase order for this vendor/company:
```
odoo_search_read("purchase.order",
  domain=[["company_id","=",cid],["partner_id","=",<vendor_id>],["state","in",["purchase","done"]],["invoice_status","!=","invoiced"]],
  fields=["id","name","amount_total","invoice_status"])
```
- If a likely match (by amount / reference), **tell the user and confirm linking.** Set
  `invoice_origin` to the PO name, and where possible link lines to the PO. **Proper 3-way matching
  (PO ↔ receipt ↔ bill) is cleanest done from the PO in the Odoo UI ("Create Bill")** — if the user
  prefers that, flag it and stop short of forcing a link.
- If no PO (or none matches), record the bill directly.

## Step 4 — Duplicate check (mandatory)
Before creating, search for an existing bill with the same vendor + supplier ref:
```
odoo_search_count("account.move",
  domain=[["company_id","=",cid],["move_type","=","in_invoice"],["partner_id","=",<vendor_id>],["ref","=","<supplier ref>"]])
```
If > 0 (or a close amount match on the same date), **warn and confirm with the user** before creating —
do not create silently. (Odoo also warns natively on duplicate vendor+ref.)

## Step 5 — Create / complete the draft, verify, RETURN THE LINK
For a new bill (Step 2A):
```
odoo_create("account.move", values={
  "move_type": "in_invoice",
  "partner_id": <vendor_id>,
  "company_id": <cid>,                       # parent for the workaround, then reassign
  "journal_id": <purchase_journal_id>,
  "invoice_date": "<supplier date>",
  "invoice_payment_term_id": <term_id>,      # OR "invoice_date_due"
  "currency_id": <ccy_id>,                    # if foreign
  "ref": "<supplier bill number>",
  "invoice_origin": "<PO name if matched>",
  "invoice_line_ids": [
    [0, 0, {"name": "Consulting — Mar 2026", "quantity": 1, "price_unit": 2500.0,
            "account_id": <expense_acct>, "tax_ids": [[6,0,[<purchase_tax>]]],
            "analytic_distribution": {"<analytic>": 100}}]
  ]
})
```
- **Show the payload, confirm, create (draft).** Read it back to verify totals and `company_id`:
  `odoo_read("account.move",[id],["partner_id","ref","invoice_date","amount_untaxed","amount_tax","amount_total","state","company_id"])`.
- **Attach the source document to the bill (standard for Step 2A — the file goes INTO Odoo, not just
  the data).** Upload the invoice the user provided as an `ir.attachment` linked to the bill so it's
  stored on the record:
  ```
  odoo_create("ir.attachment", values={
    "name": "<original filename>.pdf", "res_model": "account.move", "res_id": <move_id>,
    "type": "binary", "mimetype": "application/pdf", "datas": "<base64 of the file>"})
  ```
  (base64-encode the document the user gave you.) Confirm it appears on the bill.
- **Return the link:** `[<vendor> — <ref> <total>](<url>/web#id=<id>&model=account.move&view_type=form)`.
- Repeat per bill; keep a running table with a **link column**.

## Step 6 — Do NOT post or pay
Stop at draft. Tell the user the bills are drafted and ready for **their** review → posting → payment
in Odoo. (This playbook never posts or registers payment.)

## Step 7 — Report (+ ALWAYS an Excel action report)
- On-screen table: vendor, supplier ref, date, untaxed/tax/total, currency, journal, company, state,
  **draft link**.
- **Always produce the Excel action report** (skill's *Action report convention*):
  `<working folder>/bookkeeping/<period>/vendor-bills_<period>.xlsx` — one row per bill, columns:
  Vendor | Supplier ref | Date | Untaxed | Tax | Total | Currency | Journal | Company | PO matched? |
  State | **Link** (clickable hyperlink). Header block (company, period, generated date). Tell the
  user the path + reproduce links in chat.
- Items needing attention: unresolved vendor, missing/ambiguous account or tax, suspected duplicates,
  PO matches to confirm in the UI.
- New vendor/expense type/coding not in the rulebook → feed back to design-bookkeeping-rules (living doc).

## Worked example (document provided)
> User attaches an electricity bill for company 25.
1. Read it → vendor "Total Energies", ref `G00012345`, date 2026-03-10, £1,200 + 20% VAT.
2. Confirm extraction. 3. Resolve vendor; purchase journal for co 25 (own, else workaround).
4. Rulebook: utilities → `6201 Utilities`, `Std 20%` purchase tax, analytic = property.
5. PO check (none) → 6. Duplicate check (vendor + `G00012345`) → 7. Create draft, read back £1,440
gross, return link. 8. Leave draft; add to the Excel report.

## Do-NOT list
- ❌ Post or pay a bill (draft only — always).
- ❌ Set `name` (that's Odoo's internal sequence); put the supplier's number in `ref`.
- ❌ Create without the duplicate check (vendor + ref).
- ❌ Skip the draft link or the Excel action report.
- ❌ Invent vendor/account/tax ids, or reuse another company's coding.
- ❌ Post a subsidiary bill on a parent-owned purchase journal — use the shared/parent-journal workaround.

## To refine with the user
- Default expense accounts / purchase-tax codes per vendor or category (→ design-bookkeeping-rules).
- Whether to attach source documents to bills; new-vendor setup policy.
- How strict PO matching should be, and which bills must go via the PO "Create Bill" UI flow.
