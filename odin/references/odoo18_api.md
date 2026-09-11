# Odoo 18 XML-RPC API Reference

## Table of Contents
1. [Connection & Authentication](#connection--authentication)
2. [Core Methods](#core-methods)
3. [Domain Syntax](#domain-syntax)
4. [ORM Command Syntax (for relational fields)](#orm-command-syntax)
5. [Pagination](#pagination)
6. [Fields Discovery](#fields-discovery)
7. [Error Handling](#error-handling)
8. [Multi-company Patterns](#multi-company-patterns)
9. [Performance Tips](#performance-tips)

---

## Connection & Authentication

```python
import xmlrpc.client, json

# Load config
with open(config_path) as f:
    cfg = json.load(f)

url      = cfg["url"]          # e.g. https://my-odoo.com
db       = cfg["db"]           # database name
username = cfg["username"]     # login email
api_key  = cfg["api_key"]      # Settings > Technical > API Keys

# Two endpoints — always both needed
common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

# Authenticate — returns uid (int) or False
uid = common.authenticate(db, username, api_key, {})
assert uid, "Authentication failed"
```

**API Key location in Odoo:**
Settings → Technical → API Keys (requires developer mode on)

---

## Core Methods

All calls go through:
```python
models.execute_kw(db, uid, api_key, model, method, args, kwargs)
```

### search_read — most common

```python
records = models.execute_kw(db, uid, api_key,
    'account.move',         # model
    'search_read',          # method
    [[                      # positional args: [domain]
        ('state', '=', 'posted'),
        ('company_id', '=', 1),
    ]],
    {                       # keyword args
        'fields': ['name', 'date', 'amount_total', 'partner_id'],
        'order':  'date desc',
        'limit':  100,
        'offset': 0,
    }
)
# Returns: list of dicts
```

### search — returns IDs only

```python
ids = models.execute_kw(db, uid, api_key,
    'account.move', 'search',
    [[('state', '=', 'posted'), ('company_id', '=', 1)]],
    {'limit': 500}
)
```

### read — fetch specific fields for known IDs

```python
records = models.execute_kw(db, uid, api_key,
    'account.move', 'read',
    [ids],  # list of IDs
    {'fields': ['name', 'date', 'amount_total']}
)
```

### create — single record

```python
new_id = models.execute_kw(db, uid, api_key,
    'account.move', 'create',
    [{
        'move_type': 'entry',
        'journal_id': 5,
        'date': '2025-05-01',
        'company_id': 1,
        'ref': 'Manual accrual',
        'line_ids': [
            (0, 0, {'account_id': 100, 'name': 'Debit', 'debit': 1000.0, 'credit': 0.0}),
            (0, 0, {'account_id': 200, 'name': 'Credit', 'debit': 0.0, 'credit': 1000.0}),
        ]
    }]
)
# Returns: int (new record ID)
```

### write — update existing records

```python
success = models.execute_kw(db, uid, api_key,
    'account.move', 'write',
    [[move_id], {'ref': 'Updated reference'}]
)
# Returns: True
```

### unlink — delete records (use with caution)

```python
success = models.execute_kw(db, uid, api_key,
    'account.move', 'unlink',
    [[move_id]]
)
```

### search_count — count without fetching

```python
count = models.execute_kw(db, uid, api_key,
    'account.move.line', 'search_count',
    [[('company_id', '=', 1), ('parent_state', '=', 'posted')]]
)
```

---

## Domain Syntax

Domains are lists of conditions. Conditions are `(field, operator, value)` tuples.
Multiple conditions are AND by default. Use `'|'` and `'&'` as prefixes for OR/AND logic.

### Operators

| Operator | Meaning |
|----------|---------|
| `=` | Equals |
| `!=` | Not equals |
| `>`, `>=`, `<`, `<=` | Numeric/date comparisons |
| `in` | Value in list: `('state', 'in', ['draft', 'posted'])` |
| `not in` | Value not in list |
| `like` | SQL LIKE (case-sensitive) |
| `ilike` | SQL LIKE (case-insensitive) — prefer this for text |
| `not like`, `not ilike` | Negated text match |
| `=like`, `=ilike` | Exact match with wildcards |
| `child_of` | Hierarchical — matches record and all children |
| `parent_of` | Hierarchical — matches record and all parents |
| `any` | Odoo 17+: any element of a collection satisfies |
| `not any` | None element satisfies |

### Boolean Logic

```python
# OR: at least one of the sub-conditions must be true
['|', ('state', '=', 'draft'), ('state', '=', 'posted')]

# AND (explicit — same as default)
['&', ('company_id', '=', 1), ('state', '=', 'posted')]

# Complex: (A OR B) AND C
['&', '|', ('type', '=', 'out_invoice'), ('type', '=', 'in_invoice'),
           ('state', '=', 'posted')]

# OR of three: A OR B OR C
['|', '|', ('a', '=', 1), ('b', '=', 2), ('c', '=', 3)]
```

### Dot Notation (traverse relations)

```python
# Filter by a field on a related model
('account_id.account_type', '=', 'expense')
('journal_id.type', '=', 'bank')
('partner_id.supplier_rank', '>', 0)
('crossovered_budget_id.state', '=', 'validate')
```

---

## ORM Command Syntax

Used for one2many and many2many fields in `create` and `write` payloads.

| Command | Tuple | Meaning |
|---------|-------|---------|
| CREATE | `(0, 0, {values})` | Create new related record |
| UPDATE | `(1, id, {values})` | Update existing related record |
| DELETE | `(2, id, 0)` | Delete related record (unlink) |
| UNLINK | `(3, id, 0)` | Disconnect (many2many only) |
| LINK | `(4, id, 0)` | Link existing record (many2many only) |
| CLEAR | `(5, 0, 0)` | Remove all links (many2many only) |
| SET | `(6, 0, [ids])` | Replace all links with given list (many2many) |

### Examples

```python
# Add a new line to an existing journal entry
models.execute_kw(db, uid, api_key, 'account.move', 'write',
    [[move_id], {
        'line_ids': [(0, 0, {
            'account_id': 300,
            'name': 'Additional line',
            'debit': 500.0,
            'credit': 0.0,
        })]
    }]
)

# Replace tax_ids on an invoice line (many2many SET)
models.execute_kw(db, uid, api_key, 'account.move.line', 'write',
    [[line_id], {'tax_ids': [(6, 0, [tax_id_1, tax_id_2])]}]
)
```

---

## Pagination

For large result sets, always paginate:

```python
def fetch_all(db, uid, api_key, model, domain, fields, batch=200):
    results = []
    offset = 0
    while True:
        batch_data = models.execute_kw(db, uid, api_key, model, 'search_read',
            [domain],
            {'fields': fields, 'limit': batch, 'offset': offset, 'order': 'id asc'}
        )
        results.extend(batch_data)
        if len(batch_data) < batch:
            break
        offset += batch
    return results
```

Always check count first to warn the user if the dataset is large:

```python
count = models.execute_kw(db, uid, api_key, model, 'search_count', [domain])
if count > 1000:
    print(f"Warning: {count} records found. Fetching in batches...")
```

---

## Fields Discovery

When unsure about fields on a model:

```python
# Get all fields with type and label
fields = models.execute_kw(db, uid, api_key,
    'account.move', 'fields_get',
    [],
    {'attributes': ['string', 'type', 'required', 'relation', 'readonly', 'store']}
)

# Filter to stored, non-readonly fields
writable = {k: v for k, v in fields.items()
            if v.get('store') and not v.get('readonly')}

# Print summary
for fname, finfo in sorted(writable.items()):
    print(f"  {fname:40s} {finfo['type']:15s} {finfo.get('string','')}")
```

---

## Error Handling

```python
import xmlrpc.client

try:
    result = models.execute_kw(db, uid, api_key, model, method, args, kwargs)
except xmlrpc.client.Fault as e:
    # e.faultCode  — numeric code (usually 1 for user errors)
    # e.faultString — full traceback from Odoo server
    print(f"Odoo error [{e.faultCode}]: {e.faultString}")
except ConnectionRefusedError:
    print("Cannot reach Odoo — check URL and network")
except Exception as e:
    print(f"Unexpected error: {e}")
```

### Common Fault Causes

| Symptom | Likely Cause |
|---------|-------------|
| `AccessError` | API key lacks permissions for this model/operation |
| `UserError: unbalanced` | Journal entry debits ≠ credits |
| `ValidationError: account` | Account is deprecated or wrong type for journal |
| `MissingError` | Record ID doesn't exist or not visible to this user |
| `UserError: locked period` | Accounting period is locked — cannot post |
| `False` returned from authenticate | Wrong credentials or wrong DB name |

---

## Multi-Company Patterns

### Always filter by company on reads:

```python
domain = [
    ('company_id', '=', company_id),
    # ... other conditions
]
```

### Always include company_id on creates:

```python
payload = {
    'company_id': company_id,
    # ... other fields
}
```

### Switching company context — use `allowed_company_ids` in context:

```python
# Some methods accept a context dict to set active company
models.execute_kw(db, uid, api_key, 'account.move', 'create',
    [payload],
    {'context': {'allowed_company_ids': [company_id]}}
)
```

### Fetching cross-company data (admin only):

```python
# Search without company filter — only works if user has multi-company access
all_companies_moves = models.execute_kw(db, uid, api_key,
    'account.move', 'search_read',
    [[('state', '=', 'posted')]],  # no company filter
    {'fields': ['name', 'company_id', 'amount_total']}
)
```

---

## Performance Tips

1. **Always specify `fields`** — never leave it empty or you'll fetch 50+ fields per record
2. **Use `search_count` first** for large queries to warn the user
3. **Batch creates** — if creating many records, loop and create one at a time (Odoo XML-RPC
   doesn't support bulk create well); use `create` with list for small batches
4. **Prefer `search_read`** over `search` + `read` — saves a round trip
5. **Use `order: 'id asc'`** for paginated fetches — ensures stable ordering
6. **Cache lookups** — if you need account/journal/partner IDs, look them up once and
   store in a dict for the session
7. **Limit analytic queries** — `analytic_distribution` is a JSON field; filtering with
   `ilike` works but is slow on large tables; prefer filtering on parent move fields first

---

## Quick Reference — Common Lookups

```python
# Find account by code
acct = models.execute_kw(db, uid, api_key, 'account.account', 'search_read',
    [[('code', '=', '1200'), ('company_ids', 'in', [company_id])]],
    {'fields': ['id', 'name', 'code']})

# Find journal by type
journals = models.execute_kw(db, uid, api_key, 'account.journal', 'search_read',
    [[('type', '=', 'general'), ('company_id', '=', company_id)]],
    {'fields': ['id', 'name', 'code']})

# Find partner by name (fuzzy)
partners = models.execute_kw(db, uid, api_key, 'res.partner', 'search_read',
    [[('name', 'ilike', 'Acme'), ('company_id', 'in', [False, company_id])]],
    {'fields': ['id', 'name', 'vat', 'supplier_rank', 'customer_rank']})

# Find analytic account by name/code
analytic = models.execute_kw(db, uid, api_key, 'account.analytic.account', 'search_read',
    [[('name', 'ilike', 'Project A'), ('company_id', 'in', [False, company_id])]],
    {'fields': ['id', 'name', 'code', 'plan_id']})

# Get all active budgets for a company
budgets = models.execute_kw(db, uid, api_key, 'crossovered.budget', 'search_read',
    [[('company_id', '=', company_id), ('state', 'in', ['confirm', 'validate'])]],
    {'fields': ['id', 'name', 'date_from', 'date_to', 'state']})
```
