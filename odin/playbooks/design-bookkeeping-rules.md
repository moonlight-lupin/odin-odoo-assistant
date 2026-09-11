# Playbook: design-bookkeeping-rules

Co-design a reusable **bookkeeping rulebook** for **one company**: first understand the nature of
the company, then agree the conventions Odin follows when recording its transactions — which
accounts, journals, taxes and analytic tags to use, the recurring/adjusting entries **classified by
cadence (monthly / quarterly / yearly / ad-hoc)**, naming, and the treatment of common items. The
deliverable is a durable, **living** markdown file in the working folder that the other bookkeeping
playbooks (process-vendor-bills, create-customer-invoices, bank-reconciliation, monthly-book-keeping)
read for their defaults.

**Trigger phrases:** "design bookkeeping rules", "set up posting rules", "bookkeeping conventions",
"onboard this company", "how should we code …", "create a coding/mapping guide".

---

## ⛔ The core rule: ONE COMPANY PER RULEBOOK

**Bookkeeping rules are single-company specific.** Account IDs, journal IDs and tax IDs differ from
company to company even on a shared chart of accounts, so a rule written for Company A is **not**
valid for Company B.

- Each rulebook covers **exactly one** `res.company`. Never write a "group" rulebook and never apply
  one company's rulebook to another.
- Multiple companies → process **one at a time**, **one file each**. Confirm the order; repeat the
  whole playbook per company.
- The other playbooks load the rulebook **for the exact company they post into**.

## 🔁 The rulebook is a LIVING document
It is never "finished". It is **created** here and **updated continuously** as new transaction types
or trends appear — e.g. when monthly-book-keeping or any posting hits something the rulebook doesn't
cover, Odin proposes a rule and appends it (with a dated Change Log entry). See "Maintenance" below.

## Three modes — decide which at the very start
This playbook serves three jobs; don't run the long version when a short one fits.
- **Mode A — Full onboarding** *(no rulebook yet for this company, or a deliberate full review)*:
  run end-to-end (Steps 1→9) — understand the company, gather its building blocks, learn its current
  practice, then co-design the whole rule set. A proper sit-down.
- **Mode B — Incremental rule-add** *(a rulebook already exists; one new/uncovered transaction type
  has surfaced — usually handed up by monthly-book-keeping or the bill/invoice playbooks)*: **skip the
  interview.** Load the existing rulebook, do a Step 3 lookup for **only** the accounts/journal/tax the
  new item needs, run the Step 4 evidence check for **that one item**, propose the rule (Step 6),
  verify-update the file + Change Log (Step 7). Minutes, not a session.
- **Mode C — Mirror from a similar company** *(the user says "company A is very similar to company B"
  / "use B's rulebook as the starting point")*: take **B's rulebook as the structural template** — its
  transaction types, cadences, treatments and profile shape — but **re-resolve every account / journal
  / tax to A's OWN ids** (the one-company rule: B's ids are invalid for A). Then validate against A's
  own history and confirm the deltas. Much faster than Mode A when the entities really are alike, and
  safe because nothing is copied blindly. Procedure below.

Choose by asking: *does a rulebook for this company exist?* (→ B), *is there a similar company with a
good rulebook to start from?* (→ C), else full onboarding (→ A). Confirm the mode with the user before
diving in. **Tip:** `odoo-context/entities.md` (build-context) lists **similar-company clusters** with a
nominated **anchor** per cluster — if the target is in a cluster, propose Mode C from that anchor.

### Mode C — mirror procedure (the whole trick is re-basing the IDs, never copying them)
1. **Confirm the source.** *"Use **B**'s rulebook as the template for **A**?"* Load
   `bookkeeping-rules/<B>.md`. (A and B are different `res.company` — one file each, always.)
2. **Pull A's building blocks** (Step 3 for A): A's journals, CoA, taxes, analytic.
3. **Re-resolve each rule's references B → A by CODE + NAME (not id):**
   - exact `code`+`name` (or journal `code`+`type`, tax `name`+`rate`) match in A → use **A's** id;
   - close match (same name, different code — or vice-versa) → propose it, mark **needs confirm**;
   - **no equivalent in A** → mark **OPEN — A has no equivalent (create it / pick another / drop the
     rule)**. **Never reuse B's id.**
4. **Validate against A's reality** — run Step 4's evidence engine on A. Where A's posting history
   disagrees with the mirrored default, flag it (*"B codes utilities to `6201`; A has been using
   `6205` — keep A's?"*). **A's own evidence wins.**
5. **Mirror the profile + cadences as a hypothesis, confirm against A.** A may not share B's revenue /
   intercompany / FX / fixed-asset picture — adjust the Company Profile for A; drop rules that don't
   apply.
6. **Write A's own file** (`bookkeeping-rules/<NN-A-slug>.md`); the Change Log records "seeded from B
   (id <bid>) on <date>". B's rulebook is untouched.

---

## Preconditions
1. **Connected** — `odoo_*` tools respond / `odoo_whoami` shows a session. Else run Session Setup
   (working folder → credentials file → `odoo_connect`).
2. **Working folder** known (from Session Setup). All output goes under it.
3. **Context (recommended)** — read `<working folder>/odoo-context/` if present (entities, CoA).
   Not required; Step 3 re-pulls the company's building blocks live.
4. **Accounting policy document (ask early)** — **before drafting any rules, ask the user whether
   they have an accounting policy / manual** (group policy, revenue/expense recognition, capitalisation,
   depreciation, FX, intercompany, materiality, etc.), as a file in the working folder or that they
   can share. **If one exists, read it and make every rule (and its double entry) comply with it**;
   cite the relevant policy in the rule's Notes. If none exists, note that and proceed on the user's
   confirmed conventions.

## Operating principles
- **Evidence-first, then confirm (the core working style).** Wherever you can, **derive** the answer
  from data — `odoo-context/`, the company's building blocks (Step 3), and its posting history
  (Step 4) — and ask the user to **confirm or correct**, rather than interrogating from a blank slate.
  Lead with *"Your books code utilities to `6201` in 47 of the last 50 bills — make that the default?"*,
  not *"What account for utilities?"*. Only ask outright what the data genuinely can't tell you. This
  respects the user's time, follows the "scan context first" rule (SKILL.md), and produces defaults
  that match reality.
- **Read-only discovery.** Steps 1–6 only *read* Odoo. The deliverable is a **local markdown file**.
  Odoo *writes* happen only in the optional Step 8 (Safety Protocol applies).
- **Never invent IDs.** Every account/journal/tax/analytic a rule references must come from this
  company's Step 3 lookups. If none fits, **ask the user** — don't guess or borrow another company's id.
- **Record the ID and the human label** (e.g. ``6201 Utilities` (id 412)``) so both Odin and a person
  can use it.
- **Confirm every rule.** This rulebook encodes the user's policy — propose, then get a yes/adjust.
  Mark anything unconfirmed as **OPEN**.
- **Write to the wiki standard.** The rulebook is part of the shared context wiki: it follows
  **`references/context-doc-style.md`** (frontmatter, index line, cross-links to `odoo-context/`
  pages, stable sentence-case headings, Google developer documentation style). Read that file
  before Step 7.
- **Be deterministic.** Follow steps in order; when a step says "ask/confirm", stop and do so.

---

## Step 1 — Pick exactly one company
1. Ask: *"Which single company should this rulebook cover?"*
2. Resolve to one `res.company`:
   ```
   odoo_search_read("res.company", domain=[["name","ilike","<text>"]], fields=["id","name","currency_id","country_id"])
   ```
   Disambiguate if several match. If the user names several companies, do one rulebook each and ask
   which to start with.
3. **Confirm:** *"I'll build the rulebook for **[name] (id [cid])**, reporting in [currency]. Correct?"*
   Cache `cid`, `company_name`, `currency`. Everything below is filtered to this `cid`.

## Step 2 — Understand the NATURE of the company (adaptive — infer, then confirm)
Before any coding rules, understand what this company *is and does*. **Run this evidence-first**
(Operating principles): you may pull **Step 3's building blocks first** (journals/CoA/taxes are cheap
reads) and skim recent entries, then **draft the profile from what you find** and present it for
confirmation — e.g. *"This looks like a GBP property SPV: one bank journal, postings via a parent
purchase journal, VAT-registered, year-end Dec — that right?"*. The areas below are a **checklist of
what the profile must end up covering, not a script to read aloud**:
- For each area, first answer it from `odoo-context`/Step 3/history if you can; mark it **inferred**
  and ask the user to confirm.
- Only the areas the data can't settle become **actual questions** — ask those in **one focused
  batch**, not eleven separate prompts.
Capture the result in the rulebook's **Company Profile** section — it drives which rules and which
cadences of adjustments are relevant.

**A. Role & nature**
- What is this company's role? (group **holding** / intermediate **HoldCo** / **SPV** asset-owner /
  **OPCO** operating / **management** company / **elimination/consolidation** shell / trading / fund /
  dormant). Where does it sit in the group structure?
- What sector / activity? (e.g. property investment, development, operating/leasing, services, fund.)

**B. Revenue**
- How does it earn (if at all)? (rent, service-charge recharge, management/asset-mgmt fees, interest,
  none.) Billing cadence (monthly/quarterly/annual/one-off)? Customers internal, external, or both?

**C. Costs**
- Main cost categories (e.g. property running costs, professional/legal/audit fees, utilities, rates,
  insurance, development/construction WIP, software). Any **payroll/staff costs**?

**D. Tax**
- VAT/GST registered? Which scheme/rate(s)? Reverse charge / partial exemption? Corporate-tax
  provision? Withholding tax on interest/dividends?

**E. Currency**
- Functional currency. Does it transact in foreign currencies? Are bank accounts multi-currency?
  Is **FX revaluation** of monetary balances required at period end?

**F. Financing**
- Intercompany loans (receivable/payable)? External loans/facilities? Interest accrual basis?
  Drawdowns / capital calls? Development funding?

**G. Fixed assets / WIP**
- Holds PP&E or investment property? Depreciation policy & frequency? Capital WIP (development costs
  accumulated then transferred)?

**H. Intercompany**
- Which group entities does it transact with, and how (recharges, loans, interest, shared costs)?
  How are these eliminated on consolidation (which IC accounts; same-account vs needs an elimination
  entry — ties to month-end-review Check C)?

**I. Period-end & reporting**
- **Fiscal year end** (month)? Reporting framework (IFRS / UK GAAP / local)? Audited? Does it
  consolidate or feed an elimination entity?

**J. Banking & operations**
- How many bank/cash accounts and with whom? Statement source & frequency (synced/feed vs manual
  CSV/PDF)? Where do source documents (bills/invoices) come from and who approves postings? Rough
  monthly transaction volume? Cut-off timing?

**K. Third-party / external records**
- Are this company's books (in whole or part) **uploaded from a third party's reports** (e.g. a
  property manager, managing agent, external system)? If yes: which third party, what report, format,
  frequency — and note that a **third-party → Odoo CoA mapping** must be defined and recorded here (in
  the rulebook). The extraction/conversion/posting itself is the **third-party-conversion** playbook,
  which reads this mapping.

**L. Controls, approval & materiality**
- Who **prepares** vs **approves/posts** entries (segregation of duties)? Is there a posting cut-off /
  lock date? Any **materiality threshold** below which adjustments aren't booked (drives month-end and
  what's worth a recurring rule)? Any items that always need sign-off before posting (e.g. JEs over X,
  intercompany, manual entries to control accounts)? Capture these — they set the guardrails the
  bookkeeping playbooks and month-end-review apply.

> Summarise back: *"So this is a [role] that earns [revenue], main costs are [...], VAT [status],
> functional currency [...], year end [...]. That means we'll need rules for [...] and adjustments
> at these cadences [...]. Right?"* Confirm before proceeding.

## Step 3 — Gather this company's building blocks (read-only, all filtered to `cid`)
1. **Journals** (company-bound via `company_id`):
   ```
   odoo_search_read("account.journal", domain=[["company_id","=",cid]],
     fields=["id","name","code","type","default_account_id","suspense_account_id","currency_id"], order="type,code")
   ```
2. **Chart of accounts** (`account.account` ownership is the **`company_ids` m2m** — filter
   `company_ids in [cid]`, NOT `company_id`):
   ```
   odoo_search_count("account.account", domain=[["company_ids","in",[cid]]])
   odoo_search_read("account.account", domain=[["company_ids","in",[cid]]],
     fields=["id","code","name","account_type"], order="code", limit=... )   # paginate if large
   ```
   **Then extract & review the CoA *design*** (not just a list): the **code numbering scheme** and
   ranges (e.g. 1xxx assets, 2xxx liabilities, 4xxx income, 6xxx expense), how accounts **group by
   `account_type`**, the **naming conventions**, presence of **control accounts** (receivable/payable),
   intercompany accounts, suspense/clearing accounts, and any **analytic plans/segments** used
   alongside the CoA. Note gaps/inconsistencies (mis-ranged accounts, duplicates, deprecated). This
   design summary goes in the rulebook (template's "Chart of accounts design") and informs which
   accounts each rule should use.
3. **Taxes** (company-bound; `type_tax_use` ∈ sale/purchase/none):
   ```
   odoo_search_read("account.tax", domain=[["company_id","=",cid]],
     fields=["id","name","amount","amount_type","type_tax_use","price_include"], order="type_tax_use,name")
   ```
4. **Analytic accounts/plans** (analytic is **not** company-bound — confirm which the company uses):
   ```
   odoo_search_read("account.analytic.account", fields=["id","name","plan_id"], limit=200)
   ```
   Discover any custom analytic-plan fields with `odoo_fields_get` if relevant.
5. (Optional) **Fiscal positions:** `odoo_search_read("account.fiscal.position", domain=[["company_id","=",cid]], fields=["id","name"])`

## Step 4 — Learn current practice (the evidence engine) — drives every proposed default
This is where rules become **grounded, not guessed**. For each transaction type the profile flagged,
find what this company has *actually* been doing and turn it into a **proposed default with a
confidence signal** that Step 6 confirms. Method per transaction type:

1. **Find the dominant account(s).** Aggregate posted lines over a representative window (~12 months),
   grouped by account, ranked by usage. **Pass `company_id=cid`** so the `account_id` label carries the
   code (Gotchas — code is company-dependent):
   ```
   odoo_read_group("account.move.line",
     domain=[["company_id","=",cid],["parent_state","=","posted"],["date",">=","<~12m ago>"],
             ["account_id.account_type","in",["expense","expense_direct_cost"]]],
     fields=["balance:sum","__count"], groupby=["account_id"], company_id=cid)
   ```
   The top account by line count (`__count`) is the candidate default; record **confidence = its share
   of lines** (e.g. "47/50 = 94%").
2. **Confirm the account ↔ tax pairing in context.** Sample recent source documents to see which tax
   travels with each account:
   ```
   odoo_search_read("account.move.line",
     domain=[["company_id","=",cid],["parent_state","=","posted"],["move_id.move_type","=","in_invoice"]],
     fields=["account_id","tax_ids","partner_id","name","balance"], order="date desc", limit=50, company_id=cid)
   ```
   Repeat with `out_invoice` for revenue. Note the usual `account ↔ tax` combos and the journals used.
3. **Group by counterparty where it matters.** For vendors/recharges that always code the same way,
   also group by `partner_id`, so the rule can be "vendor X → account Y".
4. **Flag inconsistency = a decision to raise.** When one category/vendor has hit **several** accounts,
   surface it: *"Utilities went to `6201` (40×) and `6209` (10×) — standardise on one?"* These are the
   highest-value findings — list them for Step 6 / Open questions.

**Output of this step**, per transaction type, a ready-to-confirm line such as:
**`utilities → Dr 6201 Utilities (id 412) · tax Std 20% (id 7) · journal BILL · evidence 47/50 (94%)`**.
Items with **no/low history** are proposed from the profile + CoA design instead and clearly marked
**"no history — proposed"** so the user knows it's a judgement call, not an observed pattern.

## Step 5 — Build the transaction & adjustment catalogue (informed by Steps 2 & 4)
List what to cover, limited to what the nature interview says applies. Two groups:

**(a) Transaction rules** — recurring postings driven by source documents:
vendor bills by category, customer invoices by revenue type, bank charges/interest, FX, transfers,
payroll, intercompany recharges/loans, taxes.

**(b) Adjusting/recurring entries — classify each by CADENCE:**
- **Monthly:** depreciation, prepayment amortisation, routine accruals (utilities, interest),
  payroll journals, recurring rent/management-fee accruals, monthly FX revaluation (if policy).
- **Quarterly:** VAT/GST return postings, quarterly accruals/management fees, quarterly interest,
  quarter-end provisions.
- **Yearly:** year-end accruals (audit fees, bonuses), depreciation/true-ups, tax provision,
  annual FX revaluation, dividends, WIP-to-asset transfers.
- **Ad-hoc (event-driven):** one-off provisions/impairments, asset disposals, refinancing/drawdowns,
  error corrections, acquisitions.
For each adjusting entry capture the **cadence**, the **trigger/timing** (e.g. "last day of month",
"on receipt of VAT return"), whether it **reverses** next period, and the accounts/journal.

## Step 6 — Draft the rules WITH the user (confirm each)
Propose each rule **led by Step 4's evidence** (the dominant account + confidence), using this
company's Step 3 ids — *"utilities → `6201` (94% of history), tax `Std 20%` — confirm?"*. Confirm per
rule or in small batches; the user just says yes / corrects. Capture the **evidence/confidence in
Notes** so a reader knows whether a default is observed or a judgement call. For each rule capture:

| Field | Meaning |
|-------|---------|
| Transaction / entry | description / trigger |
| Cadence | (adjusting only) monthly / quarterly / yearly / ad-hoc + timing |
| Debit account | `code name` (id) — from Step 3 |
| Credit account | `code name` (id) |
| Journal | `code` (id) of this company |
| Tax | tax `name` (id) or none |
| Analytic | analytic account/plan or none |
| Reverse? | (adjusting) yes/no |
| Notes | conditions, thresholds, approver |

Use only this company's IDs. Mark unconfirmed rules **OPEN — needs decision**.

## Step 7 — Write the rulebook (verify-update if it exists)
Path: **`bookkeeping-rules/<NN>-<company-slug>.md`** (zero-padded id + slug, e.g.
`14-sp6-uk-holdco.md`) under the **shared context location** if set, else the working folder.
Create `bookkeeping-rules/` if missing. **This rulebook is instance-shareable** (same for every user
— keep it alongside `odoo-context/` in the shared repo/drive so the team reuses it). It may reference
the shared instance files (`odoo-context/journals.md`, `taxes-analytic.md`, `custom-reports.md`).
- **Format:** write the file to **`references/context-doc-style.md`** (read it first) — full YAML
  frontmatter, an index line for this rulebook in the context README, cross-links to the
  `odoo-context/` pages instead of restating their facts, sentence-case headings. Run the style
  file's validation checklist before finishing.
- **If the file exists** (re-run, or a living-document update): **verify-update** — re-pull Step 3
  lookups, check existing rules still reference valid IDs, amend/append, add a **Change log** entry
  with the date and what changed, bump the frontmatter `updated:` (and `description` if the
  headline changed); keep confirmed rules intact. (Older files without frontmatter: add it on this
  pass, keeping the original date as `generated:`.)

Template:
```markdown
---
name: <NN>-<company-slug>
description: >-
  Bookkeeping rulebook for <company_name> (id <cid>) — <role>, <ccy>, YE <month>;
  <r> rules, <a> adjusting entries, <o> OPEN.
type: rulebook
instance: <url> · <db>
company: <cid> — <company_name>
generated: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
source: design-bookkeeping-rules (Mode A|B|C)
status: current
---
# Bookkeeping rules — <company_name> (company id <cid>)
Single-company rulebook · Currency: <ccy> · Year end: <month> · Owner: <user>
> **Warning:** applies ONLY to company <cid>. Living document — update when new transaction
> types/trends appear. Journals/CoA/taxes context: see [odoo-context/](../odoo-context/README.md).

## Compliance basis
- Accounting policy document: <path/name, or "none provided">. Rules below comply with it; each
  rule's Notes cite the relevant policy clause where applicable.
- Reporting framework: <IFRS / UK GAAP / local>.

## Company profile (from onboarding)
- Role / nature: <…> · Sector: <…> · Place in group: <…>
- Revenue: <…> · Main costs: <…> · Payroll: <…>
- Tax: VAT <…>, CIT <…> · Currency & FX policy: <…>
- Financing / intercompany: <…> · Fixed assets / WIP: <…>
- Reporting framework: <…> · Consolidation role: <…> · Banking: <…> · Doc flow & approvals: <…>
- Controls: preparer vs approver (SoD) <…> · Materiality threshold <…> · Mandatory sign-off (JEs > X,
  IC, control-account postings) <…> · Cut-off / lock date <…>

## Chart of accounts design
- Numbering scheme & ranges: <e.g. 1xxx assets, 2xxx liabilities, 4xxx income, 6xxx expense>.
- Grouping by account_type; naming conventions: <…>.
- Key accounts: AR control <code(id)>, AP control <code(id)>, intercompany <…>, suspense/clearing <…>.
- Analytic plans/segments used: <…>. Notes/inconsistencies: <mis-ranged, duplicates, deprecated>.

## Defaults
- Journals: purchases `<code>`(id), sales `<code>`(id), bank `<code>`(id), misc `<code>`(id).
  **Mark each journal as OWN or SHARED-FROM-PARENT** (if shared, note the parent company id + the
  journal's owning company — so move-creating playbooks apply the shared/parent-journal workaround:
  create under the parent then reassign `company_id`). See SKILL.md → "Shared / parent-journal workaround".
- Default tax: purchases `<name>`(id), sales `<name>`(id).

## Transaction rules
| Transaction | Dr account | Cr account | Journal | Tax | Analytic | Notes |
|-------------|-----------|-----------|---------|-----|----------|-------|
| Vendor bill — utilities | `6201 Utilities`(id 412) | AP `2100`(id 380) | `BILL`(id 36) | `Std 20%`(id 7) | property | |

## Adjusting / recurring entries — by cadence
### Monthly
| Entry | Trigger/timing | Dr | Cr | Journal | Reverse? | Notes |
|-------|----------------|----|----|---------|----------|-------|
| Depreciation | last day of month | … | … | MISC | no | |
### Quarterly
| … | | | | | | |
### Yearly
| … | | | | | | |
### Ad-hoc (event-driven)
| Event | When | Dr | Cr | Journal | Notes |
|-------|------|----|----|---------|-------|

## Period-end conventions
- Accruals / prepayments / depreciation / FX revaluation: …
- Intercompany coding (which IC accounts; same-account vs needs elimination) — ties to month-end-review C.

## Open questions / decisions needed
- <unconfirmed items>

## Third-party conversion mapping (only if records are uploaded from a third party — see Step K)
> If applicable, record the source, upload journal, basis, tie-out, the third-party → Odoo **account
> mapping table**, and transformation rules here (template + details in the **third-party-conversion**
> playbook, Part A). The third-party-conversion playbook reads this section.

## Change log
| Date | Change | By |
|------|--------|----|
| <date> | Initial rulebook from onboarding | Odin |
```

## Step 8 — (Optional) implement in Odoo — explicit writes only
Only if asked: create `account.reconcile.model` rules (bank auto-coding), recurring entries, etc.
Safety Protocol (describe, payload, draft where possible, confirm, never post without instruction).
Per-company.

## Step 9 — Report
Give the file path, # rules confirmed vs OPEN, the cadence summary (how many monthly/quarterly/yearly/
ad-hoc adjustments), and inconsistencies found in Step 4. Remind: this rulebook is for company <cid> only.

---

## Maintenance (living document) — keep it current
- **Trigger an update whenever** a posting/transaction type isn't covered by the rulebook (often
  surfaced by monthly-book-keeping or the bill/invoice playbooks), or a **trend** changes (new revenue
  stream, new cost type, new tax treatment, new intercompany arrangement, a recurring one-off).
- On such an event: propose the new/changed rule, confirm with the user, **verify-update** the file
  (Step 7), and add a dated **Change Log** entry. Don't silently diverge from the written rulebook.
- The consuming playbooks should **flag uncovered items back here** rather than guessing a coding.

## How the other playbooks use this
They load **`<working folder>/bookkeeping-rules/<NN>-<slug>.md` for the exact company they post into**.
If missing, they offer to run this playbook first (or proceed with explicit per-item confirmation and
no defaults), and feed any newly-encountered transaction types back into this playbook's Maintenance.

## Do-NOT list
- ❌ Group-wide rulebook, or reusing one company's file for another.
- ❌ Referencing an account/journal/tax id not in this company's Step 3 lookups.
- ❌ In a **mirror (Mode C)**, carrying **B's ids** into A's rulebook — re-resolve every reference to
  A's own ids by code+name; B's ids are simply wrong for A.
- ❌ Finalising unconfirmed rules (leave under "Open questions").
- ❌ Skipping the company understanding in a **full onboarding** (Mode A) — rules without context are
  guesses. (A **Mode B** incremental add legitimately skips it, relying on the existing profile.)
- ❌ Proposing a coding as if observed when it has **no posting history** — mark it "no history —
  proposed". Don't dress up a guess as evidence.
- ❌ Posting/modifying Odoo data in Steps 1–7 (read-only); Step 8 writes only with confirmation.
