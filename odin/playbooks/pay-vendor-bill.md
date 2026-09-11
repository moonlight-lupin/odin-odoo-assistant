# Playbook: pay-vendor-bill

An AP-payments assistant for vendor bills, with **three functions**:
- **A · Advise outstanding unpaid bills** — what's owed and due (read-only); give links on request.
- **B · Create the payment entries** — prepare **draft** `account.payment` records for bills the user
  picks (mirrors Odoo's "Pay" dialog inputs). **Draft only — Odin never posts or pays.**
- **C · Summarise & print payments** — a list/report of payments (draft/posted) with links, and/or
  **print a payment document** (e.g. a custom "Payment Request Form") for selected payments, on request.

Use them together (advise → create → summarise) or individually. Detailed runbook — written so a
competent model (e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "what bills are outstanding / due", "pay vendor bill", "pay this supplier",
"prepare a payment for bill <X>", "settle bill <X>", "summarise the payments", "list payments made",
"print the payment request form", "print a payment report for these payments".

> ⛔ **Not part of monthly-book-keeping; never invoked automatically.** Paying is a deliberate act.

> **Settlement model (SKILL.md → Cash & settlement model):** a draft payment here is an *optional
> pre-step*, **not** settlement. A bill is settled when its **bank statement line** is **reconciled**
> against it (import-bank-statement → bank-reconciliation). Don't treat a draft payment as "paid".

---

## Scope & guardrails
- **Functions A & C are read-only.** **Function B writes DRAFT only** — `account.payment` in draft;
  **never `action_post`**, never reconciles, never moves money. The user posts in Odoo.
- **One company at a time** for B (a payment belongs to one company); A & C can span the chosen scope.
- **Function B pays only bills the user names/picks** (e.g. from the Function-A list) — no auto
  "pay everything"; **posted bills only**; amount ≤ outstanding residual.
- **Always give the record link**; for C (and after B) **always produce the Excel report**.

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder known. 3. The company/scope is confirmed.

## Step 0 — Field reference (Odoo 18; verify with `odoo_fields_get`)
- Vendor bill (`account.move`): `state` (need `posted`), `move_type` (`in_invoice`), `partner_id`,
  `invoice_date`, `invoice_date_due`, `amount_total`, `amount_residual` (outstanding),
  `payment_state` (`not_paid`/`partial`/`paid`/…), `currency_id`, `company_id`, `name` (Odoo entry
  no.), `ref` (supplier invoice no.), `payment_reference` (the bill's payment reference → the memo).
- **Payment (`account.payment`) — the six inputs that mirror Odoo's "Pay" dialog
  (`account.payment.register`):**
  | Pay-dialog field | `account.payment` field | Source |
  |------------------|--------------------------|--------|
  | **Journal** | `journal_id` | the company's **own** bank/cash journal (NOT a parent's) |
  | **Payment Method** | `payment_method_line_id` | a line of that journal's **outbound** methods (e.g. "Manual Payment") |
  | **Recipient Bank Account** | `partner_bank_id` | the **vendor's** bank account (`res.partner.bank`) |
  | **Amount** | `amount` (+ `currency_id`) | ≤ bill `amount_residual`, in the bill currency |
  | **Payment Date** | `date` | the payment date |
  | **Memo** | `memo` | bill `payment_reference`, else `ref` — never the bill `name` |
  Always also set: `payment_type="outbound"`, `partner_type="supplier"`, `partner_id`, `company_id`.
  Record stays `state="draft"`.
- Helpers: `account.payment.method.line` (journal's method lines), `res.partner.bank` (vendor banks).
- Links: bill `<url>/web#id=<id>&model=account.move&view_type=form`;
  payment `<url>/web#id=<id>&model=account.payment&view_type=form`.

---

## Function A — Advise outstanding unpaid bills (read-only)
Tell the user what's owed so they can decide what to pay.
1. Confirm scope (a company, several, or a vendor) and any filter (due by a date / overdue only).
2. Query posted, unpaid bills:
   ```
   odoo_search_read("account.move",
     domain=[["company_id","in",<scope>],["move_type","=","in_invoice"],["state","=","posted"],
             ["payment_state","in",["not_paid","partial"]],["amount_residual",">",0]],
     fields=["id","name","ref","partner_id","invoice_date","invoice_date_due","amount_residual","currency_id","payment_state"],
     order="invoice_date_due asc, invoice_date asc")
   ```
   (`odoo_search_count` first if the scope is large.)
3. Present a clear table — vendor, supplier ref, bill, due date, **outstanding** amount, currency,
   overdue? — sorted by due date; total outstanding per vendor/scope. **Give the bill links on request.**
   > For a full **aged payables** analysis, use month-end-review (Check B) or generate-reports (Aged AP).
   > This function is the quick "what should I pay?" view that feeds Function B.

---

## Function B — Create the payment entries (draft)
### B1 — Resolve the named/picked bill(s)
The user names bills (or picks from Function A):
```
odoo_search_read("account.move",
  domain=[["move_type","=","in_invoice"],["name","=","<bill name>"]],   # or by partner+ref+amount, or ids from A
  fields=["id","name","ref","payment_reference","state","partner_id","amount_total","amount_residual","payment_state","currency_id","company_id"])
```
Confirm the right bill(s); note `company_id` (one company per run).

### B2 — Validate each bill (posted-only gate)
Require ALL of: `state=="posted"` (**if draft → REFUSE**: post it in Odoo first); `move_type=="in_invoice"`;
`payment_state` in (`not_paid`,`partial`) and `amount_residual>0` (else already paid → flag/skip, no
overpayment). Capture vendor, residual, currency, company.

### B3 — Define the payment inputs (mirror Odoo's "Pay" dialog)
1. **Journal** — **default to the company's OWN bank journal** (`company_id==cid`; prefer the bill
   currency):
   ```
   odoo_search_read("account.journal", domain=[["company_id","=",cid],["type","in",["bank","cash"]]],
     fields=["id","name","code","currency_id","outbound_payment_method_line_ids"])
   ```
   > A parent/holding company's bank journal may show up as a selectable option — **do NOT default to
   > it**; only use it if the user explicitly says so.
2. **Payment Method** (`payment_method_line_id`) — an **outbound** method line of that journal (e.g.
   "Manual Payment"); rulebook default or confirm:
   ```
   odoo_search_read("account.payment.method.line",
     domain=[["journal_id","=",<journal_id>],["payment_type","=","outbound"]], fields=["id","name","payment_method_id"])
   ```
3. **Recipient Bank Account** (`partner_bank_id`) — the **vendor's** bank account:
   ```
   odoo_search_read("res.partner.bank", domain=[["partner_id","=",<vendor_id>]], fields=["id","acc_number","bank_id","allow_out_payment"])
   ```
   One → use; several → confirm; **none → offer to add one (optional `res.partner.bank` create, with
   confirmation) but don't press** — payment can proceed without it (e.g. Manual Payment). Never invent one.
4. **Amount** (+ `currency_id`) — default full `amount_residual`; partial only if specified and ≤ residual.
5. **Payment Date** (`date`) — confirm.
6. **Memo** (`memo`) — bill **`payment_reference`**; **if empty, the bill `ref` (supplier invoice no.)**;
   **never the bill `name`**.
- **Duplicate guard:** if a draft/posted payment already exists for this vendor+bill+amount, flag (don't duplicate).
- Present all six fields and **get explicit confirmation** per payment before creating.

### B4 — Create the DRAFT payment (do NOT post)
```
odoo_create("account.payment", values={
  "payment_type": "outbound", "partner_type": "supplier", "partner_id": <vendor_id>,
  "amount": <residual or partial>, "currency_id": <bill currency_id>,
  "journal_id": <own bank journal>, "payment_method_line_id": <outbound method line>,
  "partner_bank_id": <vendor bank acct or omit>, "date": "<payment date>",
  "memo": "<payment_reference, else ref — never name>", "company_id": <cid>
})
```
- Stays **draft**. **Do NOT `action_post`.** Read back to confirm `state="draft"` + the six inputs.
- **Return the payment link.** Tell the user: review & post in Odoo (or use the bill's "Pay"/Register
  Payment, which applies these same inputs and links + posts in one step). A standalone draft payment
  isn't matched to the bill until they post/reconcile.

---

## Function C — Summarise payments & print payment documents

### C1 — Summarise (read-only + Excel)
1. Scope: the payments **just created** this session, or a query — outbound supplier payments for the
   company/scope (+ optional date range, state):
   ```
   odoo_search_read("account.payment",
     domain=[["company_id","in",<scope>],["payment_type","=","outbound"],["partner_type","=","supplier"],
             ["date",">=",start],["date","<=",end]],
     fields=["id","name","partner_id","amount","currency_id","date","journal_id","payment_method_line_id","state","memo"],
     order="date desc")
   ```
2. **Always produce the Excel action report** (skill's *Action report convention*):
   `<working folder>/bookkeeping/<period>/vendor-payments_<date>.xlsx` — one row per payment, columns:
   Vendor | Bill (if known) | Amount | Currency | Journal | Payment method | Recipient bank | Memo |
   Date | State (draft/posted) | **Payment link** | **Bill link**. Header block (company, period,
   generated date). Tell the user the path + reproduce links in chat.
3. Summarise: total by state (draft vs posted), by vendor, by journal.

### C2 — Print a payment document (e.g. a custom "Payment Request Form")
Replicates the Odoo **Print → <report>** action on selected payments. The report is an
`ir.actions.report` bound to `account.payment`. **Report names are instance-specific — discover at
runtime, never hardcode.**
1. **Discover the available payment reports** and pick the one the user means (e.g. "Payment Request
   Form", "Payment Receipt"):
   ```
   odoo_search_read("ir.actions.report", domain=[["model","=","account.payment"]],
     fields=["id","name","report_name","report_type"])
   ```
   Match by `name` (e.g. ilike "payment request"); if several/ambiguous, **confirm which** with the
   user. Note the `report_name` (what the render tool needs) and `report_type` (expect `qweb-pdf`).
2. **Render it for the selected payment ids** (one PDF covering all selected, like the UI's multi-select Print):
   ```
   res = odoo_render_report(report_ref="<discovered report_name>", ids=[<payment ids>], converter="pdf")
   ```
   (Read-only; renders, doesn't change data. If `report_type` isn't a QWeb pdf/html/text, see
   generate-reports for alternatives.)
3. **Save the file** — decode `res["content_base64"]` to
   `<working folder>/bookkeeping/<period>/<report-slug>_<date>.pdf`:
   ```python
   import base64, pathlib
   pathlib.Path("<…path…>").write_bytes(base64.b64decode(res["content_base64"]))
   ```
   Report the saved path + the payment links. **If `odoo_render_report` errors** (e.g. **HTTP login
   403** — common on Cloudflare-fronted Odoo Online / `run-odoo.com`, or web login disabled), **give
   the user the direct report URL to open while logged into Odoo** — it renders the same PDF
   client-side: `<url>/report/pdf/<report_name>/<comma-separated ids>` (also works for `out_invoice`
   etc.). Optionally also suggest Print in Odoo.
> The generic native-report mechanism lives in **generate-reports** (Path A); this is the
> payment-specific shortcut.

---

## Do-NOT list
- ❌ **Post a payment / move money** (Function B is draft only); the user posts in Odoo.
- ❌ Pay a **draft** bill (posted-only); pay more than the outstanding residual.
- ❌ Pay bills the user didn't name/pick (no auto payment runs).
- ❌ Run as part of monthly-book-keeping or any automatic flow.
- ❌ Default to a **parent/holding** company's bank journal — use the bill company's own.
- ❌ Set the memo to the bill's `name` — use `payment_reference`, else `ref` (supplier invoice no.).
- ❌ Invent a recipient bank account (offer to add one, optional; never fabricate).
- ❌ Create duplicate payments; skip the link / Excel report.

## To refine with the user
- **Per-company payment defaults (rulebook):** the company's **own** bank/cash journal, default
  **payment method line** (e.g. Manual Payment), and how to pick the **recipient bank account** when a
  vendor has several. (Memo is bill-derived: `payment_reference` → `ref`.)
- Partial-payment policy and any approval threshold (flag payments above an amount for sign-off).
