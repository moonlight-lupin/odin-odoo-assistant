# Playbook: build-context

Produce durable, reusable documentation of the Odoo environment so future sessions and the other
playbooks read it instead of re-deriving it from Odoo. Two kinds of output:
- **Instance context (SHAREABLE)** — facts that are the same for every user of the instance: the
  entities, chart of accounts, journals, taxes/analytic/payment-methods, custom reports, and
  per-entity profiles. **Generate once and share with the team** (a shared repo/drive) → each
  member's Odin reads it instead of re-running this playbook = **big token saving**.
- **User context (PRIVATE)** — specific to the connected user: their **access scope**. Stays in the
  user's working folder, never shared. (Credentials live in `creds.txt` — never generated or shared.)

**Trigger phrases:** "build context", "map my entities", "understand my Odoo access /
environment", "profile the entities/companies", "what companies do I have", "document the
chart of accounts / journals / custom reports", "set up Odoo context".

---

## Modes — fresh vs. verify-update (check first)
This playbook runs in one of two modes; decide before pulling data:
- **Verify-update** — if `<working folder>/odoo-context/` already exists. **Do not regenerate
  from scratch.** Re-pull the key facts (entity list/hierarchy, access, CoA totals, per-entity
  counts), **diff against the existing docs**, and amend only what changed (note the change and
  date). Keep everything still valid. This is the default when context already exists and the
  user asked to "build/refresh context".
- **Fresh** — if no `odoo-context/` exists. Produce it from scratch as below.

(When context exists and the user did *not* ask to rebuild, don't run this playbook at all —
just read the existing docs. See the skill's Session Setup Step 2.)

## Principles

- **100% read-only against Odoo.** This playbook only *reads* Odoo (`odoo_search_read`,
  `odoo_read`, `odoo_search_count`, `odoo_fields_get`, and `read_group`/access-check methods
  via `odoo_execute`). The *only* writes are local markdown files. Never create/modify Odoo data.
- **Be efficient — this is a multi-company instance** (can be dozens of companies). Prefer
  `odoo_search_count` and `read_group` aggregations over pulling raw rows. Never fetch full
  `account.move.line` tables. Budget your calls.
- **Scope before you sweep.** Confirm with the user which entities to profile before doing all
  of them (see Step 1). Always produce the index + access + chart-of-accounts; gate the
  per-entity deep profiles behind the user's chosen scope.
- **Write to the wiki standard.** Every file follows
  **`references/context-doc-style.md`** (read it before writing): YAML frontmatter with
  `name`/`description`/`type`/`instance`/`generated`/`updated`/`source`, one topic per page,
  every page listed in the README index, cross-links instead of repeated facts, stable
  sentence-case headings, Google developer documentation style prose, ids always paired with
  their human labels. The frontmatter replaces the old `_Generated: <date>_` line and carries
  the connected `url` / `db` so readers know how fresh the doc is and where it came from.

---

## Output layout — instance (shareable) vs user (private)

**Instance context (SHAREABLE)** — write under the **shared context location** if one is set,
otherwise default to **`<working folder>/odoo-context/`** (+ `bookkeeping-rules/` from
design-bookkeeping-rules). Tell the user this folder is **shareable**: move it to / point Odin at a
shared repo/drive so the team reuses it. *(The shared location may be "decided later" — default to
the working folder until then.)*
```
odoo-context/                     ← SHAREABLE instance context
├── README.md                     ← index: instance url/db, generated date, contents, links
├── entities.md                   ← companies + hierarchy + roles + similar-company clusters
├── chart-of-accounts.md          ← CoA design: numbering/types, control accts, shared-vs-per-company
├── journals.md                   ← journals per company — OWN vs SHARED-FROM-PARENT (+ suspense accts)
├── taxes-analytic.md             ← tax codes, analytic plans, payment-method lines
├── custom-reports.md             ← custom `ir.actions.report` by model (Print actions + report_name)
└── entities/<NN>-<slug>.md       ← per-entity profiles
bookkeeping-rules/<NN>-<slug>.md  ← per-company rulebooks (design-bookkeeping-rules) — also shareable
```

**User context (PRIVATE)** — write under the user's **working folder**; never share:
```
<working folder>/
├── creds.txt                     ← username + api_key (user-provided; NEVER generated/shared)
└── access/<user-slug>.md         ← THIS user's access scope (groups, companies, per-model rights)
```

> **Why the split:** the instance context is identical for everyone → generate once, share, save
> tokens. Only the small per-user access scope (and credentials) is private. Keep `creds.txt` and
> `access/` out of any shared/committed location.

---

## Procedure

### Step 0 — Connect
Follow the skill's Session Setup: `odoo_connect(username, api_key)`. Capture `uid`, `url`,
`db`, and the company list from the result.

### Step 1 — Agree the scope
Report the entity count and propose a scope before profiling:
> "You have access to **N companies**. I'll always document your access scope and the chart of
> accounts. For the per-entity accounting profiles, which do you want — **all N**, only the ones
> with posted activity, or a subset you name (e.g. operating companies, excluding 'Elimination'/
> SPV shells)?"

Default if the user is unsure: profile entities that have **any posted `account.move`** (skip
dormant shells), and list the skipped ones in the index.

### Step 2 — Entities
```
odoo_search_read("res.company",
  fields=["id","name","currency_id","country_id","parent_id","child_ids",
          "partner_id","vat","email"],
  order="id asc")
```
Build the parent/child hierarchy from `parent_id` / `child_ids`. Note the presentation/holding
companies vs operating vs elimination entities (often visible from naming).

### Step 3 — Access scope of the connected user → **USER-PRIVATE**
> This is the one **user-specific** output: write it to `<working folder>/access/<user-slug>.md`,
> **not** into the shareable `odoo-context/`. It's small and derived per user.
1. Read the user record:
   ```
   odoo_read("res.users", ids=[<uid>],
     fields=["login","name","company_id","company_ids","groups_id","share","lang","tz"])
   ```
   `company_ids` = the companies this user may switch into (this bounds everything else).
2. Resolve group names (this is the human-readable role):
   ```
   odoo_read("res.groups", ids=<groups_id list>, fields=["full_name","category_id"])
   ```
   Summarise the meaningful ones (Accounting/Adviser vs Billing, Settings/Administration,
   Sales, etc.). Presence of an "Administration / Settings" group ⇒ admin.
3. **Determine model-level rights.** **Primary source: the group membership above** — e.g.
   "Administration / Settings" + "Accounting / Administrator" ⇒ full access; "Accounting /
   Read-only" or "Invoicing" only ⇒ limited. This is reliable and needs no extra calls.
   - Optional empirical probe: `check_access_rights(operation, raise_exception=False)` returns a
     boolean and is RPC-friendly:
     ```
     odoo_execute("account.move", "check_access_rights", ["write"], {"raise_exception": False})
     ```
     **Do NOT rely on `has_access(operation)` via the MCP** — in Odoo 18 it raises
     "missing required argument 'operation'" when called positionally, so fall back to roles /
     `check_access_rights`.
   - **Caveat to record in the doc:** these are *model-level* rights only. Record rules and
     multi-company rules restrict further at the row level, and the MCP server's own policy
     blocks deletes + technical-model writes regardless of what Odoo would allow. State all three.

### Step 4 — Chart of accounts (per company — the code is company-dependent)
**The account `code` is company-dependent in Odoo 18** (a non-stored computed over the per-company
`code_store`). It only resolves when the account is read **in that company's context**, and you
**cannot `order` by it** (non-stored → ordering is ignored/errors). So build the CoA **per company**:
1. Size it: `odoo_search_count("account.account", [["company_ids","in",<companyIds>]])`.
   (`account.account` ownership is the **`company_ids` many2many** — not `company_id`.)
2. For **each** company `cid`, read its accounts with **`company_id=cid`** so `code` populates
   (a plain read returns `code=""` for any non-default company):
   ```
   odoo_search_read("account.account",
     domain=[["company_ids","in",[cid]]],
     fields=["code","name","account_type","company_ids","deprecated","currency_id"],
     company_id=cid)
   ```
   Sort by `code` **client-side** (don't pass `order="code"` — it's non-stored).
3. Determine the sharing model: an account record may be **shared** (the same id lists multiple
   `company_ids`) yet still carry a **different `code` per company** — so capture the **code per
   company**, not one global code. Document shared-vs-per-company, and group by `account_type`
   (asset_receivable, liability_payable, income, expense, …).
> Store the per-company code in `chart-of-accounts.md` (and per-entity files) — e.g. a `code`
> column **scoped to the company** — so downstream playbooks reuse the cached `code name` and never
> re-read in context or leak the raw id.

### Step 4b — Journals per company (own vs shared-from-parent) → `journals.md`
List every company's own journals (a journal belongs to one company via `company_id`):
```
odoo_search_read("account.journal", domain=[["company_id","in",<companyIds>]],
  fields=["id","company_id","name","code","type","default_account_id","suspense_account_id","currency_id"],
  order="company_id,type,code")
```
- Group by company. **Flag companies that have few/no own journals** — their entries are booked via a
  **parent/holding company's journal** (the *shared-from-parent* pattern; see SKILL.md → Shared /
  parent-journal workaround). Record, per company: own journals (by type), and "uses parent journal:
  yes/no (which parent)". This directly feeds the move-creating playbooks.

### Step 4c — Taxes, analytic plans, payment methods → `taxes-analytic.md`
```
odoo_search_read("account.tax", domain=[["company_id","in",<companyIds>]],
  fields=["id","company_id","name","amount","amount_type","type_tax_use","price_include"], order="company_id,type_tax_use")
odoo_search_read("account.analytic.plan", fields=["id","name"])          # analytic plans
odoo_search_read("account.analytic.account", fields=["id","name","plan_id"], limit=200)
odoo_search_read("account.payment.method.line", domain=[["journal_id.company_id","in",<companyIds>]],
  fields=["id","name","journal_id","payment_type","payment_method_id"])  # payment methods per journal
```
Summarise the tax codes (per company), the analytic plans/segments in use (incl. any custom `x_*`
plan fields discovered via `odoo_fields_get`), and the outbound/inbound payment-method lines.

### Step 4d — Custom reports (Print actions) → `custom-reports.md`
Catalog the `ir.actions.report` records (what the **Print** menu can produce) so playbooks can
replicate Print → \<report\> without rediscovering:
```
odoo_search_read("ir.actions.report",
  domain=[["model","in",["account.move","account.payment","account.bank.statement","res.partner"]]],
  fields=["id","name","model","report_name","report_type"], order="model,name")
```
- Record name + **`report_name`** (instance-specific — what `odoo_render_report` needs) + model + type.
- **Flag the CUSTOM ones**: `report_name` whose module prefix isn't a core Odoo module (e.g. not
  `account.`/`web.`/`base.`/`stock.`/`sale.`/`purchase.`) — e.g. `acme_account_printout.print_payment_list`
  ("Payment Request Form"). These are the bespoke Print actions worth knowing. Widen the model list if
  the user uses custom reports elsewhere.

### Step 4e — Similar-company clusters (mirror candidates) → into `entities.md`
Groups of near-identical entities are common (a holding with a dozen lookalike SPVs/OPCOs). **Flag
them** so the team can build **one** rulebook per cluster and **mirror** it across the rest
(design-bookkeeping-rules **Mode C**) instead of onboarding each from scratch. Cluster the in-scope
companies using signals already gathered — no new queries needed:
- **currency** (Step 2) · **parent / branch** in the hierarchy (Step 2),
- **role** — holding / intermediate HoldCo / SPV / OPCO / elimination shell / property-co (from naming
  + structure),
- **journal profile** (Step 4b) — full own set · own bank only · **no own journals (post via parent)** ·
  HFS/third-party feed,
- *(optional, heavier)* **CoA usage shape** — same `account_type` spread / shared CoA.

Companies matching on **currency + role + journal profile** (usually sharing a parent) form a cluster.
For each cluster: list members, the shared traits, and **nominate an "anchor"** — the cleanest, most
complete member whose rulebook the others mirror. Write this to `entities.md` (Clusters section).
Keep it **advisory**: a cluster is a *starting hypothesis* for Mode C, not a claim the books are
identical — the mirror still re-resolves every id and validates against each company's own history.

### Step 5 — Per-entity accounting profile (scoped from Step 1)
For each in-scope company, build a profile **using aggregations, not row dumps**:
- **Document mix & volume** — counts by `move_type` and `state`:
  ```
  odoo_execute("account.move", "read_group",
    [[["company_id","=",<cid>]], ["id:count"], ["move_type","state"]])
  ```
- **Activity window** — earliest/latest entry:
  ```
  odoo_search_read("account.move", domain=[["company_id","=",<cid>]],
    fields=["date","name"], order="date asc", limit=1)   # + a desc query for latest
  ```
- **Journals** in use:
  ```
  odoo_search_read("account.journal", domain=[["company_id","=",<cid>]],
    fields=["code","name","type","currency_id"], order="type")
  ```
- **Most-active accounts** (optional, cap it) — group posted lines by account:
  ```
  odoo_read_group(model="account.move.line",
    domain=[["company_id","=",<cid>],["parent_state","=","posted"]],
    fields=["balance:sum","id:count"], groupby=["account_id"], company_id=<cid>)
  # company_id=<cid> → account_id labels carry the code (company-dependent in Odoo 18)
  ```
  Take the top ~15 by absolute balance or line count; don't render the whole list.
- **Currencies / fiscal info** — from the company currency and any `account.fiscal.year`
  records (check existence with `odoo_search_count` first; the model may be absent).

Infer a one-paragraph **"what this entity does"** from the evidence (operating vs holding vs
elimination/consolidation shell; trading currency; whether it has revenue vs only intercompany
movements; how recent the activity is).

### Step 6 — Write the files
**Read `references/context-doc-style.md` first** and write every file to that standard (frontmatter,
index line, cross-links, stable headings, Google developer style). Run its validation checklist on
each file before moving on.
- **Instance context (shareable)** → `odoo-context/` (+ `entities/`): `README.md`, `entities.md`,
  `chart-of-accounts.md`, `journals.md`, `taxes-analytic.md`, `custom-reports.md`,
  `entities/<NN>-<slug>.md`. Keep tables compact; cross-link.
- **User context (private)** → `<working folder>/access/<user-slug>.md` (the Step-3 access scope).
- **Verify-update mode:** edit existing files — update changed facts, add new entities/reports, bump
  the frontmatter `updated:` date and rewrite `description` if the headline facts changed; leave
  unchanged sections intact. Record each change in the page's Change log and note the refresh in
  the README. (Older docs without frontmatter: add it on this pass, keeping the original date as
  `generated:`.)

### Step 7 — Summarise
Tell the user what was written (the **shareable** `odoo-context/` paths vs the **private**
`access/<user>.md`), the headline findings (N entities, M active, CoA size/sharing, # custom reports,
which entities use a parent journal), and what was skipped. **Remind them the `odoo-context/` +
`bookkeeping-rules/` are shareable** — put them in a shared repo/drive so the team reuses them (keep
`creds.txt` + `access/` private). Offer next steps (run design-bookkeeping-rules, month-end-review, etc.).

---

## File templates

All templates below follow `references/context-doc-style.md`. The frontmatter block is shown in
full on the README template; on the others it is abbreviated to `--- (frontmatter …) ---` — emit
the **full** block every time, with the right `type`, a ≤160-char `description` carrying that
page's headline facts, and `company:` only on entity profiles.

### `README.md` (index of the SHAREABLE instance context)
```markdown
---
name: readme
description: >-
  Index of the shared Odoo instance context for <db> — <N> entities, <count>-account CoA,
  journals, taxes, <n> custom reports.
type: index
instance: <url> · <db>
generated: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
source: build-context (fresh)
status: current
---
# Odoo context — <db>   (instance context — SHAREABLE; safe to put in a team repo/drive)

> **Note:** shareable — same for every user (read-only snapshot). Per-user access scope +
> credentials live OUTSIDE this folder.

## Contents
- [entities.md](entities.md) — <N> companies + hierarchy + similar-company clusters (Mode-C anchors)
- [chart-of-accounts.md](chart-of-accounts.md) — <count> accounts, <shared|per-company>
- [journals.md](journals.md) — journals per company (own vs shared-from-parent)
- [taxes-analytic.md](taxes-analytic.md) — taxes, analytic plans, payment methods
- [custom-reports.md](custom-reports.md) — <n> custom Print reports
- entities/ — per-entity profiles (<M> profiled): [<NN>-<slug>.md](entities/<NN>-<slug>.md) — <hook>, …
- ../bookkeeping-rules/ — per-company rulebooks: [<NN>-<slug>.md](../bookkeeping-rules/<NN>-<slug>.md) — <hook>, …

## How to refresh
Re-run `build-context` (verify-update mode amends these). User access scope is at
`<working folder>/access/<user>.md` (private, not here).

## Change log
| Date | Change | By |
|------|--------|----|
```

### `access/<user-slug>.md` (USER-PRIVATE — keep out of any shared location)
```markdown
--- (frontmatter: type: user-access · description: access scope of <name> (uid <uid>) on <db> — <effective level>) ---
# Access scope — <name> (uid <uid>)   [PRIVATE]

## Identity
- Login: <login> · Default company: <company_id> · Languages/TZ: …
- Companies this user can access (`company_ids`): <list ids+names>

## Roles (groups)
- <Category> → <full_name>
- …
**Effective level:** <e.g. "Full accounting adviser across all 57 companies; not a system admin">

## Model-level rights (probed)
| Model | read | write | create | unlink |
|-------|:--:|:--:|:--:|:--:|
| account.move | ✓ | ✓ | ✓ | ✗ |
| … | | | | |

> **Caveats:** model-level only. (1) Odoo record rules + multi-company rules further restrict
> at the row level. (2) The MCP server policy blocks all `unlink` and writes to technical models
> regardless. (3) Deletion is replaced by archive/cancel.
```

### `chart-of-accounts.md`
```markdown
--- (frontmatter: type: instance-context · description: CoA for <db> — <count> accounts, <shared|per-company>, per-company codes) ---
# Chart of accounts
<count> accounts · **Sharing:** <shared across companies | per-company> · codes are
company-dependent (see [entities.md](entities.md) for company ids)

## By type
### Receivable (asset_receivable)
| Code | Name | Companies | Deprecated |
|------|------|-----------|:--:|
| … | | | |
### Payable (liability_payable)
…
### Income / Expense / Bank / Equity / …
…
```

### `entities/<NN>-<slug>.md`
```markdown
--- (frontmatter: type: entity-profile · company: <id> — <name> · description: <one-line role + activity headline>) ---
# <Company name> (id <id>)

- Currency: <ccy> · Country: <country> · VAT: <vat>
- Hierarchy: parent <…>, children <…>
- **Profile:** <one paragraph: what this entity appears to do, from the evidence below.>

## Activity
- Entries by type/state:
  | move_type | state | count |
  |-----------|-------|------:|
- Activity window: <earliest> → <latest>
- Journals: <code/name/type list>

## Most-active accounts (top N by |balance|)
| Account | Lines | Net balance |
|---------|------:|------------:|
```

### `entities.md`
```markdown
--- (frontmatter: type: instance-context · description: <N> companies on <db> — hierarchy, roles, clusters w/ Mode-C anchors) ---
# Entities (<N>)
| ID | Name | Role | Currency | Country | Parent | Cluster | Profiled? |
|---:|------|------|----------|---------|--------|---------|-----------|
| 1 | … | Holding | SGD | SG | — | — | [yes](entities/01-….md) |
## Hierarchy
<indented tree of parent → children>

## Similar-company clusters (mirror candidates — design-bookkeeping-rules Mode C)
> Advisory groupings of near-identical entities: build one rulebook for the **anchor**, then mirror to
> the rest (Mode C re-resolves ids + validates per company). Not a claim the books are identical.

| Cluster | Members (ids) | Shared traits (currency · role · journal profile · parent) | Anchor |
|---------|---------------|------------------------------------------------------------|--------|
| SP6 OPCOs | … | GBP · OPCO shell · no own journals (via parent 14) · parent 14 | <id> |
| Straits property cos | … | GBP · property-co · HFS feed | <id> |
```

### `journals.md`
```markdown
--- (frontmatter: type: instance-context · description: journals per company on <db> — own vs shared-from-parent, suspense accounts) ---
# Journals by company
| Company | Own journals (type · code · name) | Suspense acct | Uses parent journal? |
|---------|-----------------------------------|---------------|----------------------|
| Acme HoldCo (cid) | bank · BNK1 · …; misc · MISC · … | … | no |
| … OPCO (cid) | (none) | — | **yes → <parent> (id)** |
```

### `taxes-analytic.md`
```markdown
--- (frontmatter: type: instance-context · description: taxes, analytic plans + payment-method lines per company on <db>) ---
# Taxes, analytic & payment methods
## Taxes (by company) | id | name | rate | type_tax_use |
## Analytic plans / accounts | plan | accounts… | (+ custom x_* plan fields)
## Payment-method lines (per journal) | journal | method (in/out) |
```

### `custom-reports.md`
```markdown
--- (frontmatter: type: instance-context · description: <n> Print actions (ir.actions.report) on <db>, <m> custom — report_name per model) ---
# Reports / Print actions (ir.actions.report)
| Model | Report name | report_name (for odoo_render_report) | Type | Custom? |
|-------|-------------|--------------------------------------|------|:------:|
| account.payment | Payment Request Form | acme_account_printout.print_payment_list | qweb-pdf | **✔** |
| account.move | … | account.report_invoice_with_payments | qweb-pdf | |
> "Custom?" = report_name module prefix isn't a core Odoo module.
```

---

## Efficiency & limits
- Hard cap raw-row fetches; lean on `read_group` and `search_count`.
- If the user picked "all" on a very large instance, do the index/access/CoA first, then profile
  entities in batches, checking in after the active ones.
- If a model referenced here is absent (e.g. `account.fiscal.year`), note it and move on — don't error.
- This playbook never posts, writes, archives, or cancels in Odoo. If the user asks for a change
  mid-run, treat it as a separate task under the normal Safety Protocol.
```
