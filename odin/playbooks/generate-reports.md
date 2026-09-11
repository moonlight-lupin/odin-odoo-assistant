# Playbook: generate-reports

Produce a report the user wants — either Odoo's **own rendered file** (native) or a **clean
Excel built from the data** — and save it to `<working folder>/reports/`. There are two delivery paths and you
pick per report type:

| Report kind | Path | How |
|-------------|------|-----|
| **Document / QWeb** — invoices, vendor bills, customer/partner statements, payment receipts, any `ir.actions.report` (pdf/html/text) | **Native** | `odoo_render_report` → save the bytes |
| **Financial / analytical tables** — Trial Balance, P&L, Balance Sheet, General Ledger, Aged AR/AP, any aggregation | **Data-build** | pull via `read_group`/`search_read` → build `.xlsx` |

**Trigger phrases:** "generate a report", "export … to Excel/PDF", "print the invoice",
"trial balance", "P&L / income statement", "balance sheet", "general ledger", "aged
receivables/payables", "customer statement", "give me the … as a file".

> **Why two paths:** the MCP reaches Odoo over XML-RPC, which serves **data**, while Odoo's
> *rendered* files come from HTTP controllers. `odoo_render_report` bridges that for QWeb
> reports. The Enterprise **financial report engine** (`account.report`) exports through a
> different controller that isn't exposed here — so financial statements are **built from
> data**, which is also more controllable.

---

## Principles
- **ALWAYS ASK THE PERIOD / DATE RANGE** for any report covering a time span (financials, GL,
  ageing, etc.). Never assume or infer it from today's date — ask and wait for the answer.
- **Reading + rendering only.** Never post/modify Odoo data to produce a report.
- **Confirm scope first**: which report, which **entity (`company_id`)**, **period/date range**,
  which **records** (for document reports), and the **format**. Don't guess the company on a
  multi-company instance.
- **Mind size.** GL / line-level exports can be huge — `odoo_search_count` first; if large,
  confirm the user really wants every line, or aggregate.
- **Reuse context.** If `odoo-context/` exists (from build-context), use it to resolve company
  ids, chart of accounts, and account types instead of re-discovering.
- Save outputs to `<working folder>/reports/` (create it). Name files `<report>_<entity>_<period>_<date>.<ext>`.

---

## Step 1 — Discover what's available (when unsure)
- QWeb/document reports — **incl. custom reports on ANY model** (invoices/bills, payments, journal
  entries, statements, partners, …). Filter `ir.actions.report` by the relevant model:
  ```
  odoo_search_read("ir.actions.report",
    fields=["name","report_name","model","report_type"],
    domain=[["model","=","<model, e.g. account.move / account.payment>"]])
  ```
  Match by `name` (e.g. a custom "Payment Request Form"); if several, confirm which. **`report_name`
  is instance-specific — discover it, never hardcode**; it's what `odoo_render_report` needs.
  `report_type` is usually `qweb-pdf`. This is how Odin replicates any Odoo **Print → \<report\>** action.
- Financial reports (Enterprise): `odoo_search_count("account.report")` to confirm the engine
  exists, then `odoo_search_read("account.report", fields=["name"])` to list them (Balance
  Sheet, P&L, GL, Trial Balance, Aged…). These go the **data-build** route.

---

## Path A — Native document report (PDF/HTML)
For invoices, bills, statements, receipts, etc.

1. Resolve the records (e.g. the `account.move` ids the user means) with `odoo_search_read`.
2. Find the report_name (Step 1) — common ones: `account.report_invoice_with_payments`,
   `account.report_invoice`. When in doubt, list `ir.actions.report` for the model and ask.
3. Render and save:
   ```
   res = odoo_render_report(report_ref="account.report_invoice_with_payments",
                            ids=[<move_ids>], converter="pdf")
   ```
4. **Save the bytes** — `res["content_base64"]` is the file. The Write tool can't emit binary,
   so decode with a short Python step:
   ```python
   import base64, pathlib
   pathlib.Path("reports").mkdir(exist_ok=True)
   pathlib.Path("reports/" + res["filename"]).write_bytes(base64.b64decode(res["content_base64"]))
   ```
   Report the saved path and size to the user.
5. **If `odoo_render_report` errors** (e.g. **HTTP login 403** — common on Cloudflare-fronted Odoo
   Online / `run-odoo.com`, or HTML error page): **give the user the direct report URL to open while
   logged into Odoo** (renders the same PDF client-side):
   `<url>/report/pdf/<report_name>/<comma-separated ids>` — e.g.
   `https://…/report/pdf/account.report_invoice_with_payments/123,124`. Also offer Print in Odoo or
   (for tabular reports) the data-build equivalent.

---

## Path B — Data-build Excel (financial / analytical)
Pull aggregates over XML-RPC, then construct the `.xlsx`. Build the workbook with the **xlsx
skill** if available, otherwise `openpyxl`/`pandas` in a short script. Always label columns,
include a header block (entity, period, basis = posted entries, generated date), and a totals row.

All queries: filter `["company_id","=",cid]` and (for actuals) `["parent_state","=","posted"]`,
and a date window `["date",">=",start],["date","<=",end]`.

### Trial Balance
```
odoo_read_group(model="account.move.line",
  domain=[["company_id","=",cid],["parent_state","=","posted"],
          ["date",">=",start],["date","<=",end]],
  fields=["debit:sum","credit:sum","balance:sum"], groupby=["account_id"], company_id=cid)
```
**Pass `company_id=cid`** — the account `code` is company-dependent in Odoo 18, so this makes
`account_id` come back as `[id, "12501 Prepayments"]` (code + name). Without it (or via the
`odoo_execute(... "read_group" ...)` escape hatch, which runs in your default company) the code is
blank and the raw id leaks into the report.
Columns: Account code | Name | Debit | Credit | Balance. Sort by code; total debit must equal
total credit. Split the `account_id` label into code + name (it's `"CODE NAME"`).

### Profit & Loss / Balance Sheet
Same `read_group`, grouped by `account_id`, then **map each account to its `account_type`**
(read `account.account` `account_type`, or reuse `odoo-context/chart-of-accounts.md`):
- **P&L**: income types (`income`, `income_other`) and expense types (`expense`,
  `expense_depreciation`, `expense_direct_cost`); net = income − expense.
- **Balance Sheet**: asset / liability / equity types; assets = liabilities + equity.
Group lines under their statement headings with subtotals.

### General Ledger (line level)
`odoo_search_count` first. If acceptable, page through:
```
odoo_search_read("account.move.line",
  domain=[["company_id","=",cid],["parent_state","=","posted"],
          ["date",">=",start],["date","<=",end]],
  fields=["date","move_id","account_id","partner_id","name","debit","credit","balance"],
  order="account_id, date, id", limit=2000, offset=...)
```
One sheet, or a sheet per account for big sets. Warn and confirm before pulling tens of
thousands of lines.

### Aged Receivable / Payable
Open items with maturity buckets:
```
odoo_search_read("account.move.line",
  domain=[["company_id","=",cid],["parent_state","=","posted"],
          ["account_id.account_type","=","asset_receivable"],   # liability_payable for AP
          ["reconciled","=",False],["amount_residual","!=",0]],
  fields=["partner_id","date_maturity","amount_residual","move_id"])
```
Bucket `amount_residual` by `date_maturity` vs the report date (Current, 1–30, 31–60, 61–90, 90+),
pivot by partner, total each bucket.

---

## Step 3 — Deliver
Tell the user the file path, the path taken (native vs built), the entity + period covered, and
the basis (posted only). For built reports, note any caveats (e.g. excludes draft entries; GL
truncated to N rows). Offer the alternate format if useful (e.g. "want the invoice PDF too?").

## Limits
- Native path covers **QWeb pdf/html/text** `ir.actions.report` only. Enterprise financial
  statements are **built from data** (the `account.report` XLSX controller isn't exposed via
  the MCP). Say so plainly if the user expected Odoo's exact financial-report layout.
- `odoo_render_report` needs the server to allow API-key HTTP login; if disabled, only the
  data-build path works.
- Multi-currency: amounts are in company currency unless you explicitly handle `amount_currency`.
