# Odoo 18 — Sales, CRM, Purchasing, Projects & Contacts Models Reference

## Table of Contents
1. [Contacts / Partners (res.partner)](#respartner)
2. [Sales — sale.order](#saleorder)
3. [Sales — sale.order.line](#saleorderline)
4. [CRM — crm.lead](#crmlead)
5. [CRM — crm.stage](#crmstage)
6. [CRM — crm.team](#crmteam)
7. [Purchasing — purchase.order](#purchaseorder)
8. [Purchasing — purchase.order.line](#purchaseorderline)
9. [Projects — project.project](#projectproject)
10. [Projects — project.task](#projecttask)
11. [Projects — project.task.type](#projecttasktype)
12. [Common Cross-App Patterns](#common-cross-app-patterns)

---

## res.partner

Full partner model — customers, vendors, contacts, companies. Shared across all apps.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Display name |
| `ref` | char | Internal reference code |
| `type` | selection | `contact`, `invoice`, `delivery`, `other`, `private` |
| `is_company` | bool | True if this is a company record |
| `parent_id` | many2one → res.partner | Parent company (for contacts under a company) |
| `child_ids` | one2many → res.partner | Contacts under this company |
| `company_id` | many2one → res.company | Odoo company this partner belongs to (null = shared) |
| `company_name` | char | Company name if `is_company=False` and no parent |
| `email` | char | Email address |
| `phone` | char | Phone number |
| `mobile` | char | Mobile number |
| `website` | char | Website URL |
| `vat` | char | Tax ID / GST / VAT number |
| `street` | char | Address line 1 |
| `street2` | char | Address line 2 |
| `city` | char | City |
| `zip` | char | Postal code |
| `state_id` | many2one → res.country.state | State / Province |
| `country_id` | many2one → res.country | Country |
| `lang` | selection | Language code e.g. `en_US` |
| `customer_rank` | int | > 0 = is a customer |
| `supplier_rank` | int | > 0 = is a vendor/supplier |
| `active` | bool | False = archived |
| `category_id` | many2many → res.partner.category | Partner tags |
| `user_id` | many2one → res.users | Salesperson / responsible |
| `team_id` | many2one → crm.team | Sales team |
| `property_account_receivable_id` | many2one → account.account | AR account |
| `property_account_payable_id` | many2one → account.account | AP account |
| `property_payment_term_id` | many2one → account.payment.term | Customer payment terms |
| `property_supplier_payment_term_id` | many2one | Vendor payment terms |
| `comment` | html | Internal notes |
| `sale_order_count` | int | Number of sales orders (computed) |
| `purchase_order_count` | int | Number of purchase orders (computed) |

### Common Queries

```python
# Find customers by name
customers = models.execute_kw(db, uid, api_key, 'res.partner', 'search_read',
    [[('customer_rank', '>', 0), ('name', 'ilike', 'Acme')]],
    {'fields': ['id', 'name', 'email', 'phone', 'vat', 'customer_rank']})

# Find vendors
vendors = models.execute_kw(db, uid, api_key, 'res.partner', 'search_read',
    [[('supplier_rank', '>', 0), ('active', '=', True)]],
    {'fields': ['id', 'name', 'vat', 'country_id', 'supplier_rank']})

# Get all contacts under a company
contacts = models.execute_kw(db, uid, api_key, 'res.partner', 'search_read',
    [[('parent_id', '=', company_partner_id)]],
    {'fields': ['id', 'name', 'type', 'email', 'phone']})
```

### res.partner.category (Partner Tags)

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Tag ID |
| `name` | char | Tag name |
| `parent_id` | many2one → res.partner.category | Parent tag (hierarchical) |
| `active` | bool | Active status |

---

## sale.order

Sales order (quotation or confirmed order).

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Order number e.g. `S00042` |
| `state` | selection | `draft`, `sent`, `sale`, `done`, `cancel` |
| `partner_id` | many2one → res.partner | Customer |
| `partner_invoice_id` | many2one → res.partner | Invoice address |
| `partner_shipping_id` | many2one → res.partner | Delivery address |
| `date_order` | datetime | Order / quotation date |
| `validity_date` | date | Quotation expiry date |
| `user_id` | many2one → res.users | Salesperson |
| `team_id` | many2one → crm.team | Sales team |
| `company_id` | many2one → res.company | Company |
| `currency_id` | many2one → res.currency | Currency |
| `pricelist_id` | many2one → product.pricelist | Pricelist |
| `payment_term_id` | many2one → account.payment.term | Payment terms |
| `order_line` | one2many → sale.order.line | Order lines |
| `amount_untaxed` | monetary | Subtotal (computed) |
| `amount_tax` | monetary | Tax (computed) |
| `amount_total` | monetary | Total incl. tax (computed) |
| `invoice_status` | selection | `upselling`, `invoiced`, `to invoice`, `nothing` |
| `invoice_ids` | many2many → account.move | Linked invoices |
| `opportunity_id` | many2one → crm.lead | Linked CRM opportunity |
| `note` | html | Terms and conditions |
| `client_order_ref` | char | Customer reference |
| `commitment_date` | datetime | Committed delivery date |

### State Values

| Value | Meaning |
|-------|---------|
| `draft` | Quotation |
| `sent` | Quotation sent to customer |
| `sale` | Sales order (confirmed) |
| `done` | Locked (fully invoiced/delivered) |
| `cancel` | Cancelled |

### Common Queries

```python
# Open sales orders for a company
orders = models.execute_kw(db, uid, api_key, 'sale.order', 'search_read',
    [[('company_id', '=', company_id), ('state', 'in', ['draft', 'sent', 'sale'])]],
    {'fields': ['name', 'partner_id', 'date_order', 'amount_total', 'state',
                'invoice_status', 'user_id'],
     'order': 'date_order desc'})

# Orders pending invoicing
to_invoice = models.execute_kw(db, uid, api_key, 'sale.order', 'search_read',
    [[('company_id', '=', company_id), ('state', '=', 'sale'),
      ('invoice_status', '=', 'to invoice')]],
    {'fields': ['name', 'partner_id', 'amount_total', 'date_order']})
```

---

## sale.order.line

Individual lines on a sales order.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Line ID |
| `order_id` | many2one → sale.order | Parent order |
| `name` | char | Description |
| `product_id` | many2one → product.product | Product |
| `product_template_id` | many2one → product.template | Product template |
| `product_uom_qty` | float | Ordered quantity |
| `qty_delivered` | float | Delivered quantity (computed) |
| `qty_invoiced` | float | Invoiced quantity (computed) |
| `qty_to_invoice` | float | Quantity pending invoice (computed) |
| `price_unit` | float | Unit price |
| `discount` | float | Discount % |
| `tax_id` | many2many → account.tax | Taxes |
| `price_subtotal` | monetary | Line subtotal excl. tax (computed) |
| `price_total` | monetary | Line total incl. tax (computed) |
| `analytic_distribution` | json | Analytic distribution |
| `state` | selection | From parent order |
| `company_id` | many2one → res.company | Company |

---

## crm.lead

CRM leads and opportunities (same model, differentiated by `type`).

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Opportunity / lead name |
| `type` | selection | `lead`, `opportunity` |
| `stage_id` | many2one → crm.stage | Pipeline stage |
| `kanban_state` | selection | `normal`, `done`, `blocked` |
| `active` | bool | False = archived/lost |
| `probability` | float | Win probability % (0–100) |
| `automated_probability` | float | AI-predicted probability |
| `partner_id` | many2one → res.partner | Linked customer/contact |
| `partner_name` | char | Company name (if partner not yet created) |
| `contact_name` | char | Contact name |
| `email_from` | char | Contact email |
| `phone` | char | Contact phone |
| `user_id` | many2one → res.users | Salesperson |
| `team_id` | many2one → crm.team | Sales team |
| `company_id` | many2one → res.company | Company |
| `expected_revenue` | float | Expected revenue |
| `prorated_revenue` | float | probability × expected_revenue (computed) |
| `date_deadline` | date | Expected closing date |
| `date_open` | datetime | Date converted to opportunity |
| `date_closed` | datetime | Date won/lost |
| `date_last_stage_update` | datetime | Last stage change |
| `won_status` | selection | `won`, `lost`, `pending` |
| `lost_reason_id` | many2one → crm.lost.reason | Reason if lost |
| `referred` | char | Referral source |
| `description` | html | Internal notes |
| `priority` | selection | `0` (normal), `1` (low), `2` (high), `3` (very high) |
| `tag_ids` | many2many → crm.tag | Tags |

### Common Queries

```python
# Open opportunities in pipeline
opps = models.execute_kw(db, uid, api_key, 'crm.lead', 'search_read',
    [[('type', '=', 'opportunity'), ('active', '=', True),
      ('company_id', '=', company_id)]],
    {'fields': ['name', 'partner_id', 'stage_id', 'expected_revenue',
                'probability', 'date_deadline', 'user_id'],
     'order': 'expected_revenue desc'})

# Won deals in a period
won = models.execute_kw(db, uid, api_key, 'crm.lead', 'search_read',
    [[('type', '=', 'opportunity'), ('won_status', '=', 'won'),
      ('date_closed', '>=', '2025-01-01'), ('date_closed', '<=', '2025-03-31'),
      ('company_id', '=', company_id)]],
    {'fields': ['name', 'partner_id', 'expected_revenue', 'date_closed', 'user_id']})
```

---

## crm.stage

Pipeline stages for CRM.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Stage ID |
| `name` | char | Stage name e.g. `New`, `Qualified`, `Proposition` |
| `sequence` | int | Display order |
| `probability` | float | Default probability at this stage |
| `is_won` | bool | True = this stage marks a won deal |
| `fold` | bool | Folded in kanban view |
| `team_id` | many2one → crm.team | Specific to a team (null = shared) |

---

## crm.team

Sales teams — group salespersons and pipeline views.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Team ID |
| `name` | char | Team name |
| `active` | bool | Active status |
| `user_id` | many2one → res.users | Team leader |
| `member_ids` | many2many → res.users | Team members |
| `company_id` | many2one → res.company | Company |

---

## purchase.order

Purchase orders (RFQ and confirmed POs).

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | PO number e.g. `P00042` |
| `state` | selection | `draft`, `sent`, `to approve`, `purchase`, `done`, `cancel` |
| `partner_id` | many2one → res.partner | Vendor |
| `partner_ref` | char | Vendor's reference number |
| `date_order` | datetime | Order date |
| `date_approve` | datetime | Date approved |
| `date_planned` | datetime | Scheduled receipt date |
| `user_id` | many2one → res.users | Purchase representative |
| `company_id` | many2one → res.company | Company |
| `currency_id` | many2one → res.currency | Currency |
| `payment_term_id` | many2one → account.payment.term | Payment terms |
| `order_line` | one2many → purchase.order.line | PO lines |
| `amount_untaxed` | monetary | Subtotal excl. tax (computed) |
| `amount_tax` | monetary | Tax (computed) |
| `amount_total` | monetary | Total incl. tax (computed) |
| `invoice_ids` | many2many → account.move | Linked vendor bills |
| `invoice_status` | selection | `nothing`, `to invoice`, `invoiced` |
| `notes` | html | Terms and conditions |
| `fiscal_position_id` | many2one → account.fiscal.position | Tax mapping |

### State Values

| Value | Meaning |
|-------|---------|
| `draft` | Request for Quotation (RFQ) |
| `sent` | RFQ sent to vendor |
| `to approve` | Waiting for approval |
| `purchase` | Purchase order (confirmed) |
| `done` | Locked / receipt complete |
| `cancel` | Cancelled |

### Common Queries

```python
# Open POs for a company
pos = models.execute_kw(db, uid, api_key, 'purchase.order', 'search_read',
    [[('company_id', '=', company_id), ('state', 'in', ['draft', 'sent', 'purchase'])]],
    {'fields': ['name', 'partner_id', 'date_order', 'amount_total', 'state',
                'invoice_status', 'date_planned'],
     'order': 'date_order desc'})

# POs pending billing
to_bill = models.execute_kw(db, uid, api_key, 'purchase.order', 'search_read',
    [[('company_id', '=', company_id), ('state', '=', 'purchase'),
      ('invoice_status', '=', 'to invoice')]],
    {'fields': ['name', 'partner_id', 'amount_total', 'date_planned']})
```

---

## purchase.order.line

Individual lines on a purchase order.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Line ID |
| `order_id` | many2one → purchase.order | Parent PO |
| `name` | char | Description |
| `product_id` | many2one → product.product | Product |
| `product_qty` | float | Ordered quantity |
| `qty_received` | float | Received quantity (computed) |
| `qty_invoiced` | float | Invoiced quantity (computed) |
| `price_unit` | float | Unit price |
| `taxes_id` | many2many → account.tax | Taxes |
| `price_subtotal` | monetary | Line subtotal excl. tax (computed) |
| `price_total` | monetary | Line total incl. tax (computed) |
| `account_analytic_id` | many2one → account.analytic.account | Analytic account (legacy) |
| `analytic_distribution` | json | Analytic distribution (Odoo 18) |
| `date_planned` | datetime | Scheduled receipt for this line |
| `company_id` | many2one → res.company | Company |

---

## project.project

Projects — containers for tasks.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Project name |
| `active` | bool | False = archived |
| `state` | selection | `open`, `done` |
| `user_id` | many2one → res.users | Project manager |
| `partner_id` | many2one → res.partner | Customer |
| `company_id` | many2one → res.company | Company |
| `date_start` | date | Start date |
| `date` | date | End / deadline date |
| `description` | html | Project description |
| `privacy_visibility` | selection | `followers`, `employees`, `portal` |
| `task_ids` | one2many → project.task | Tasks in this project |
| `task_count` | int | Total tasks (computed) |
| `open_task_count` | int | Open tasks (computed) |
| `analytic_account_id` | many2one → account.analytic.account | Linked analytic account |
| `tag_ids` | many2many → project.tags | Project tags |
| `allow_timesheets` | bool | Timesheets enabled |
| `color` | int | Kanban color index |

### Common Queries

```python
# All active projects for a company
projects = models.execute_kw(db, uid, api_key, 'project.project', 'search_read',
    [[('company_id', '=', company_id), ('active', '=', True)]],
    {'fields': ['id', 'name', 'user_id', 'partner_id', 'date', 'task_count',
                'open_task_count', 'analytic_account_id'],
     'order': 'name asc'})
```

---

## project.task

Tasks within a project.

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Task title |
| `project_id` | many2one → project.project | Project |
| `stage_id` | many2one → project.task.type | Kanban stage |
| `state` | selection | `01_in_progress`, `1_done`, `04_waiting_normal`, `03_approved`, `02_changes_requested`, `1_canceled` — Odoo 18 |
| `kanban_state` | selection | `normal`, `done`, `blocked` |
| `active` | bool | False = archived |
| `user_ids` | many2many → res.users | Assignees |
| `partner_id` | many2one → res.partner | Customer contact |
| `company_id` | many2one → res.company | Company |
| `date_deadline` | date | Deadline |
| `date_assign` | datetime | Date assigned |
| `description` | html | Task description |
| `priority` | selection | `0` (normal), `1` (urgent) |
| `tag_ids` | many2many → project.tags | Tags |
| `planned_hours` | float | Estimated hours |
| `effective_hours` | float | Logged hours (computed) |
| `remaining_hours` | float | Remaining hours (computed) |
| `child_ids` | one2many → project.task | Subtasks |
| `parent_id` | many2one → project.task | Parent task (if subtask) |
| `depend_on_ids` | many2many → project.task | Blocking tasks |

### State Values — Odoo 18

| Value | Meaning |
|-------|---------|
| `01_in_progress` | In Progress |
| `1_done` | Done |
| `04_waiting_normal` | Waiting |
| `03_approved` | Approved |
| `02_changes_requested` | Changes Requested |
| `1_canceled` | Cancelled |

### Common Queries

```python
# Open tasks in a project
tasks = models.execute_kw(db, uid, api_key, 'project.task', 'search_read',
    [[('project_id', '=', project_id), ('active', '=', True),
      ('state', 'not in', ['1_done', '1_canceled'])]],
    {'fields': ['name', 'stage_id', 'user_ids', 'date_deadline', 'priority',
                'planned_hours', 'effective_hours'],
     'order': 'date_deadline asc'})

# Overdue tasks across all projects for a company
import datetime
today = datetime.date.today().isoformat()
overdue = models.execute_kw(db, uid, api_key, 'project.task', 'search_read',
    [[('company_id', '=', company_id), ('active', '=', True),
      ('date_deadline', '<', today),
      ('state', 'not in', ['1_done', '1_canceled'])]],
    {'fields': ['name', 'project_id', 'user_ids', 'date_deadline', 'stage_id']})
```

---

## project.task.type

Kanban stages for tasks (can be shared across projects).

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Stage ID |
| `name` | char | Stage name |
| `sequence` | int | Display order |
| `fold` | bool | Folded in kanban |
| `project_ids` | many2many → project.project | Projects using this stage |

---

## Common Cross-App Patterns

### Link from CRM Opportunity to Sales Order

```python
# Get sales orders created from an opportunity
orders = models.execute_kw(db, uid, api_key, 'sale.order', 'search_read',
    [[('opportunity_id', '=', lead_id)]],
    {'fields': ['name', 'state', 'amount_total', 'date_order']})
```

### Link from Sales Order to Invoices

```python
so = models.execute_kw(db, uid, api_key, 'sale.order', 'read',
    [[order_id]], {'fields': ['invoice_ids']})
invoice_ids = so[0]['invoice_ids']
invoices = models.execute_kw(db, uid, api_key, 'account.move', 'read',
    [invoice_ids], {'fields': ['name', 'state', 'amount_total', 'payment_state']})
```

### Link from Purchase Order to Vendor Bills

```python
po = models.execute_kw(db, uid, api_key, 'purchase.order', 'read',
    [[po_id]], {'fields': ['invoice_ids']})
bill_ids = po[0]['invoice_ids']
bills = models.execute_kw(db, uid, api_key, 'account.move', 'read',
    [bill_ids], {'fields': ['name', 'state', 'amount_total', 'payment_state']})
```

### Partner → All Related Records

```python
# All SOs for a partner
sos = models.execute_kw(db, uid, api_key, 'sale.order', 'search_read',
    [[('partner_id', 'child_of', partner_id), ('state', '!=', 'cancel')]],
    {'fields': ['name', 'date_order', 'amount_total', 'state']})

# All POs for a partner
pos = models.execute_kw(db, uid, api_key, 'purchase.order', 'search_read',
    [[('partner_id', 'child_of', partner_id), ('state', '!=', 'cancel')]],
    {'fields': ['name', 'date_order', 'amount_total', 'state']})

# All invoices/bills for a partner
moves = models.execute_kw(db, uid, api_key, 'account.move', 'search_read',
    [[('partner_id', 'child_of', partner_id), ('state', '=', 'posted')]],
    {'fields': ['name', 'move_type', 'date', 'amount_total', 'payment_state']})
```

### Project → Analytic Account → GL Actuals

```python
# Get analytic account linked to a project
project = models.execute_kw(db, uid, api_key, 'project.project', 'read',
    [[project_id]], {'fields': ['analytic_account_id']})
analytic_id = project[0]['analytic_account_id'][0]

# Query GL lines tagged to this analytic account
gl_lines = models.execute_kw(db, uid, api_key, 'account.move.line', 'search_read',
    [[('analytic_distribution', 'ilike', str(analytic_id)),
      ('parent_state', '=', 'posted'),
      ('company_id', '=', company_id)]],
    {'fields': ['move_name', 'date', 'account_id', 'name', 'debit', 'credit',
                'analytic_distribution']})
```
