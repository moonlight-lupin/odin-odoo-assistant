# Odoo 18 Accounting Models Reference

## Table of Contents
1. [account.move](#accountmove) — Journal Entries, Invoices, Bills
2. [account.move.line](#accountmoveline) — Entry Lines
3. [account.account](#accountaccount) — Chart of Accounts
4. [account.journal](#accountjournal) — Journals
5. [account.payment](#accountpayment) — Payments
6. [account.bank.statement](#accountbankstatement) — Bank Statements
7. [account.bank.statement.line](#accountbankstatementline) — Bank Statement Lines
8. [account.analytic.account](#accountanalyticaccount) — Analytic Accounts
9. [account.analytic.plan](#accountanalyticplan) — Analytic Plans
10. [crossovered.budget](#crossoveredbudget) — Financial Budgets
11. [crossovered.budget.lines](#crossoveredbudgetlines) — Budget Lines
12. [account.account.budget](#accountaccountbudget) — Budget Positions
13. [account.tax](#accounttax) — Taxes
14. [account.payment.term](#accountpaymentterm) — Payment Terms
15. [res.partner](#respartner) — Partners (AP/AR context)
16. [Common Domain Patterns](#common-domain-patterns)
17. [move_type Values](#move_type-values)
18. [State Values](#state-values)

---

## account.move

Primary model for **all** accounting transactions: manual journal entries, customer invoices,
vendor bills, credit notes, receipts.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Document number e.g. `INV/2025/0001`, `/` if draft |
| `move_type` | selection | See [move_type Values](#move_type-values) |
| `state` | selection | `draft`, `posted`, `cancel` |
| `journal_id` | many2one → account.journal | Required |
| `company_id` | many2one → res.company | Required — always filter by this |
| `date` | date | Accounting date (manual JEs) |
| `invoice_date` | date | Invoice date (invoices/bills) |
| `invoice_date_due` | date | Due date |
| `partner_id` | many2one → res.partner | Customer or vendor |
| `ref` | char | Internal reference / memo |
| `payment_reference` | char | Communication on payment |
| `currency_id` | many2one → res.currency | Transaction currency |
| `amount_untaxed` | monetary | Subtotal before tax (computed) |
| `amount_tax` | monetary | Tax amount (computed) |
| `amount_total` | monetary | Total incl. tax (computed) |
| `amount_residual` | monetary | Outstanding balance (computed) |
| `invoice_line_ids` | one2many → account.move.line | Invoice lines (filtered) |
| `line_ids` | one2many → account.move.line | All lines incl. tax/receivable |
| `payment_state` | selection | `not_paid`, `in_payment`, `paid`, `partial`, `reversed`, `invoicing_legacy` |
| `reversed_entry_id` | many2one → account.move | Source entry if this is a reversal |
| `reversal_move_id` | one2many → account.move | Reversal entries |
| `invoice_origin` | char | Source document reference |
| `narration` | html | Internal notes |
| `fiscal_position_id` | many2one → account.fiscal.position | Tax mapping |

### Create Payload — Manual Journal Entry

```python
entry = {
    'move_type': 'entry',
    'journal_id': <journal_id>,
    'date': '2025-05-01',
    'company_id': <company_id>,
    'ref': 'Manual accrual - May 2025',
    'line_ids': [
        (0, 0, {
            'account_id': <debit_account_id>,
            'name': 'Description of debit line',
            'debit': 5000.00,
            'credit': 0.00,
            'partner_id': <partner_id>,  # optional
            'analytic_distribution': {'<analytic_account_id>': 100},  # optional
        }),
        (0, 0, {
            'account_id': <credit_account_id>,
            'name': 'Description of credit line',
            'debit': 0.00,
            'credit': 5000.00,
        }),
    ]
}
move_id = models.execute_kw(db, uid, api_key, 'account.move', 'create', [entry])
```

### Create Payload — Vendor Bill

```python
bill = {
    'move_type': 'in_invoice',
    'journal_id': <purchase_journal_id>,
    'partner_id': <vendor_partner_id>,
    'invoice_date': '2025-05-01',
    'invoice_date_due': '2025-05-31',
    'company_id': <company_id>,
    'ref': 'Vendor Ref 12345',
    'invoice_line_ids': [
        (0, 0, {
            'account_id': <expense_account_id>,
            'name': 'Service fee',
            'quantity': 1,
            'price_unit': 5000.00,
            'tax_ids': [(6, 0, [<tax_id>])],  # optional
            'analytic_distribution': {'<analytic_account_id>': 100},  # optional
        }),
    ]
}
```

### Post (Validate) — Only on explicit user instruction

```python
models.execute_kw(db, uid, api_key, 'account.move', 'action_post', [[move_id]])
```

---

## account.move.line

Individual debit/credit lines within a journal entry or invoice.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Line ID |
| `move_id` | many2one → account.move | Parent entry |
| `move_name` | char | Parent entry name (computed) |
| `account_id` | many2one → account.account | G/L account |
| `name` | char | Line description |
| `date` | date | Accounting date (from move) |
| `company_id` | many2one → res.company | Company |
| `partner_id` | many2one → res.partner | Partner |
| `debit` | float | Debit amount (0.00 if credit) |
| `credit` | float | Credit amount (0.00 if debit) |
| `balance` | float | debit - credit (computed) |
| `amount_currency` | float | Amount in transaction currency |
| `currency_id` | many2one → res.currency | Transaction currency |
| `journal_id` | many2one → account.journal | Journal (from move) |
| `analytic_distribution` | json | `{"analytic_account_id": pct, ...}` |
| `tax_ids` | many2many → account.tax | Taxes applied |
| `tax_line_id` | many2one → account.tax | Tax this line represents |
| `reconciled` | bool | Whether this line is reconciled |
| `full_reconcile_id` | many2one | Reconcile group |
| `move_type` | selection | From parent move (useful for filtering) |
| `display_type` | selection | `product`, `cogs`, `tax`, `rounding`, `payment_term`, `line_section`, `line_note` |
| `parent_state` | selection | State of parent move (`draft`, `posted`, `cancel`) |

### GL Query Example

```python
# Posted GL lines for a specific account + date range + company
lines = models.execute_kw(db, uid, api_key, 'account.move.line', 'search_read',
    [[
        ('company_id', '=', company_id),
        ('account_id', '=', account_id),
        ('parent_state', '=', 'posted'),
        ('date', '>=', '2025-01-01'),
        ('date', '<=', '2025-03-31'),
    ]],
    {'fields': ['move_name', 'date', 'name', 'partner_id', 'debit', 'credit', 'balance'],
     'order': 'date asc'})
```

---

## account.account

Chart of accounts.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `code` | char | Account code e.g. `1000` |
| `name` | char | Account name |
| `account_type` | selection | See below |
| `company_ids` | many2many → res.company | Odoo 18: accounts can be shared |
| `currency_id` | many2one → res.currency | Force currency (optional) |
| `reconcile` | bool | Whether lines can be reconciled |
| `deprecated` | bool | If true, cannot be used |
| `tag_ids` | many2many → account.account.tag | Tax grid tags |
| `group_id` | many2one → account.group | Account group |

### account_type Values

| Value | Meaning |
|-------|---------|
| `asset_receivable` | Accounts receivable |
| `asset_cash` | Cash & bank |
| `asset_current` | Current assets |
| `asset_non_current` | Non-current assets |
| `asset_prepayments` | Prepayments |
| `asset_fixed` | Fixed assets |
| `liability_payable` | Accounts payable |
| `liability_credit_card` | Credit card |
| `liability_current` | Current liabilities |
| `liability_non_current` | Non-current liabilities |
| `equity` | Equity |
| `equity_unaffected` | Retained earnings / current year |
| `income` | Revenue |
| `income_other` | Other income |
| `expense` | Expenses |
| `expense_depreciation` | Depreciation |
| `expense_direct_cost` | Cost of goods sold |
| `off_balance` | Off-balance accounts |

---

## account.journal

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Journal name |
| `code` | char | Short code e.g. `INV`, `MISC`, `BNK1` |
| `type` | selection | `sale`, `purchase`, `cash`, `bank`, `general` |
| `company_id` | many2one → res.company | Company |
| `currency_id` | many2one → res.currency | Journal currency |
| `default_account_id` | many2one → account.account | Default posting account |
| `suspense_account_id` | many2one → account.account | Bank suspense account |

---

## account.payment

Standalone payments (not matched to an invoice via reconciliation).

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Payment reference |
| `state` | selection | `draft`, `posted`, `sent`, `reconciled`, `cancelled` |
| `payment_type` | selection | `outbound` (pay), `inbound` (receive) |
| `partner_type` | selection | `customer`, `supplier` |
| `partner_id` | many2one → res.partner | Partner |
| `amount` | monetary | Payment amount |
| `currency_id` | many2one → res.currency | Currency |
| `date` | date | Payment date |
| `journal_id` | many2one → account.journal | Bank/cash journal |
| `company_id` | many2one → res.company | Company |
| `memo` | char | Internal note / communication |
| `move_id` | many2one → account.move | Underlying journal entry |

---

## account.bank.statement

Bank statement header (one per import batch or manual statement).

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Statement name |
| `date` | date | Statement date |
| `journal_id` | many2one → account.journal | Bank journal |
| `company_id` | many2one → res.company | Company |
| `balance_start` | float | Opening balance |
| `balance_end_real` | float | Closing balance |
| `state` | selection | `open`, `posted` |

---

## account.bank.statement.line

Individual transactions on a bank statement.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `statement_id` | many2one → account.bank.statement | Parent |
| `date` | date | Transaction date |
| `payment_ref` | char | Bank description / reference |
| `partner_id` | many2one → res.partner | Matched partner |
| `amount` | float | Amount (signed: positive = credit to bank) |
| `currency_id` | many2one | Currency |
| `is_reconciled` | bool | Whether matched to GL |
| `move_id` | many2one → account.move | Underlying move |
| `journal_id` | many2one → account.journal | Bank journal |
| `company_id` | many2one → res.company | Company |

---

## account.analytic.account

Individual analytic accounts. In Odoo 18 these belong to a **plan**.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Account name |
| `code` | char | Short code |
| `plan_id` | many2one → account.analytic.plan | Parent plan |
| `company_id` | many2one → res.company | Company (optional — can be shared) |
| `active` | bool | Active status |
| `partner_id` | many2one → res.partner | Optional linked partner |
| `balance` | float | Current balance (computed) |

### Usage in move lines

```python
# Set 100% allocation to one analytic account
'analytic_distribution': {'<analytic_account_id>': 100}

# Split across two analytic accounts
'analytic_distribution': {'<id_1>': 60, '<id_2>': 40}
```

---

## account.analytic.plan

Analytic plans group analytic accounts. Odoo 18 enforces plan-level distribution rules.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Plan name |
| `code` | char | Short code |
| `account_ids` | one2many → account.analytic.account | Accounts in this plan |
| `default_applicability` | selection | `optional`, `mandatory`, `unavailable` |
| `applicability_ids` | one2many | Per-account-type rules |
| `company_id` | many2one → res.company | Optional (plans can be cross-company) |

---

## crossovered.budget

Financial budget header. Each budget covers a period and set of budget lines.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Budget name |
| `state` | selection | `draft`, `cancel`, `confirm`, `validate`, `done` |
| `date_from` | date | Budget period start |
| `date_to` | date | Budget period end |
| `user_id` | many2one → res.users | Responsible user |
| `company_id` | many2one → res.company | Company |
| `crossovered_budget_line` | one2many → crossovered.budget.lines | Budget lines |

---

## crossovered.budget.lines

Individual budget line: one account position + period + analytic account.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `crossovered_budget_id` | many2one → crossovered.budget | Parent budget |
| `general_budget_id` | many2one → account.budget.post | Budget position (group of accounts) |
| `analytic_account_id` | many2one → account.analytic.account | Optional analytic filter |
| `date_from` | date | Line period start |
| `date_to` | date | Line period end |
| `planned_amount` | float | Budgeted amount |
| `practical_amount` | float | Actual amount from GL (computed) |
| `theoritical_amount` | float | Pro-rated budget to date (computed) |
| `percentage` | float | practical / planned % (computed) |
| `company_id` | many2one → res.company | Company |

### Budget vs Actual Query

```python
budget_lines = models.execute_kw(db, uid, api_key, 'crossovered.budget.lines', 'search_read',
    [[
        ('crossovered_budget_id.company_id', '=', company_id),
        ('crossovered_budget_id.state', 'in', ['confirm', 'validate']),
        ('date_from', '>=', '2025-01-01'),
        ('date_to', '<=', '2025-12-31'),
    ]],
    {'fields': ['general_budget_id', 'analytic_account_id', 'date_from', 'date_to',
                'planned_amount', 'practical_amount', 'percentage']})
```

---

## account.budget.post

Budget positions — named groups of G/L accounts used in budgets.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Position name e.g. "Operating Expenses" |
| `account_ids` | many2many → account.account | Accounts in this position |
| `company_id` | many2one → res.company | Company |

---

## account.tax

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Tax name |
| `type_tax_use` | selection | `sale`, `purchase`, `none` |
| `amount_type` | selection | `percent`, `fixed`, `group`, `division` |
| `amount` | float | Rate (e.g. `9.0` for 9%) |
| `company_id` | many2one → res.company | Company |
| `active` | bool | Active |

---

## account.payment.term

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | e.g. "Net 30" |
| `note` | char | Description |
| `line_ids` | one2many → account.payment.term.line | Payment term lines |
| `company_id` | many2one → res.company | Company (optional) |

---

## res.partner

Partners relevant to AP/AR.

### Key Fields (accounting-relevant)

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Partner name |
| `ref` | char | Internal reference |
| `vat` | char | Tax ID / GST number |
| `customer_rank` | int | > 0 means is a customer |
| `supplier_rank` | int | > 0 means is a vendor |
| `property_account_receivable_id` | many2one → account.account | AR account |
| `property_account_payable_id` | many2one → account.account | AP account |
| `property_payment_term_id` | many2one → account.payment.term | Default payment terms |
| `property_supplier_payment_term_id` | many2one | Default vendor payment terms |
| `company_id` | many2one → res.company | Company (null = shared) |

---

## Common Domain Patterns

```python
# Posted entries only
('parent_state', '=', 'posted')           # on move.line
('state', '=', 'posted')                  # on move

# Date range (accounting date)
('date', '>=', '2025-01-01')
('date', '<=', '2025-03-31')

# Company filter — always include
('company_id', '=', company_id)

# Account type filter
('account_id.account_type', '=', 'expense')

# Invoices only (exclude manual JEs)
('move_type', 'in', ['out_invoice', 'in_invoice', 'out_refund', 'in_refund'])

# Unpaid invoices
('payment_state', 'in', ['not_paid', 'partial'])

# Exclude archived
('active', '=', True)

# Analytic account filter on move lines
('analytic_distribution', 'ilike', str(analytic_account_id))
```

---

## move_type Values

| Value | Meaning |
|-------|---------|
| `entry` | Manual journal entry |
| `out_invoice` | Customer invoice |
| `out_refund` | Customer credit note |
| `in_invoice` | Vendor bill |
| `in_refund` | Vendor credit note |
| `out_receipt` | Customer receipt (POS/simplified) |
| `in_receipt` | Vendor receipt |

---

## State Values

### account.move
| Value | Meaning |
|-------|---------|
| `draft` | Unposted — editable |
| `posted` | Confirmed and in GL — locked |
| `cancel` | Cancelled |

### account.payment
| Value | Meaning |
|-------|---------|
| `draft` | Unposted |
| `posted` | Posted to bank/cash journal |
| `sent` | Payment sent |
| `reconciled` | Fully matched to invoice |
| `cancelled` | Cancelled |

### crossovered.budget
| Value | Meaning |
|-------|---------|
| `draft` | Work in progress |
| `confirm` | Submitted for approval |
| `validate` | Approved — actuals computed |
| `done` | Closed |
| `cancel` | Cancelled |
