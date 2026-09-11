# Playbook: setup-new-entity

Set up a **new company (entity) in Odoo** end-to-end on the accounting side: the company record,
the fiscal localization package, and — the hard part — the **chart of accounts**, including the
systematic **replacement and retirement of the template's default accounts** (Odoo refuses to
archive/delete an account while *anything* still references it as a default, and the UI never shows
you where those references are; this playbook finds and remaps them mechanically).

Detailed runbook — written so a competent model (e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "set up a new entity/company", "new SPV / HoldCo in Odoo", "create a company
in Odoo", "chart of accounts setup", "set up the CoA", "replace the default accounts", "clean up
the template accounts", "why can't I delete/archive this account".

**Entry modes** (ask which, or infer from the request):
- **Mode A — full new entity:** Phases 1→5 (company → localization → CoA → remap-and-retire → journals).
- **Mode B — CoA rework for an EXISTING entity:** the company exists; skip Phases 1–2, run 3→5.
- **Mode C — defaults clean-up only:** the entity and CoA exist; run **Phase 4 alone** against the
  unwanted (usually template) accounts. This is the answer to "Odoo won't let me remove this account".

---

## Scope & guardrails
- **Explicit-only.** Never part of monthly-book-keeping. **One entity per run.**
- **This is the heaviest-write playbook — and a company record is NEVER draft.** It therefore has
  **four hard confirmation gates**; do not pass any of them without an explicit user go-ahead:
  - **G1** — the full proposed company config, before `odoo_create("res.company", …)`.
  - **G2** — the CoA plan (rename / create / retire buckets), before any account writes.
  - **G3** — the **remap plan** (the dry-run Excel), before any default-reference writes.
  - **G4** — the final retire list, before archiving.
- **Never delete.** The server blocks `unlink` anyway; retirement = `odoo_archive`. If Odoo refuses
  an archive, something still references the account — go back to the sweep, don't force.
- **Read siblings, write only the new entity.** Sibling/template companies are consulted read-only;
  every write is scoped to the new company (`company_id` / `company_ids` / context).
- **Module installation is out of bounds** (server policy blocks `ir.module*` writes). If a needed
  l10n module isn't installed, stop and hand the user the UI step.
- **Propose, user decides.** Every mapping/remap is a *proposal* (sourced from the sibling where
  possible); the user confirms or amends at the gates. Never guess silently.
- **Analytic plans, elimination-company pairing, users/access design are OUT of scope** — the user
  instructs those separately. Note them on the leftover checklist instead.
- **Always Excel report + links** (skill convention), plus the remap-plan workbook at G3.

## Preconditions
1. Connected (`odoo_whoami`); working folder set; `odoo-context/` loaded if present (entities,
   chart-of-accounts, journals docs — reuse, don't re-derive).
2. The name of a **template sibling entity** (an existing company the new one should resemble).
   Almost everything below is proposed by mirroring the sibling.
3. Outputs go to `<working folder>/entity-setup/<company-slug>/`.

---

## Phase 1 — Entity record (Mode A only)

1. **Interview (short):** legal name · country · currency (default = country's) · parent company
   (`parent_id`, from the group hierarchy in `odoo-context/entities`) · company registry no. / VAT ·
   registered address · fiscal year end (only if ≠ 31 Dec: `fiscalyear_last_day` +
   `fiscalyear_last_month` on `res.company`) · the **template sibling**.

2. **Partner autocomplete first (house preference).** Odoo's partner-autocomplete (IAP) can pre-fill
   address/registry data from the name or VAT. Its RPC surface varies by version — discover, try,
   and fall back gracefully:
   - Check it's installed:
     `odoo_search_read("ir.module.module", domain=[["name","like","partner_autocomplete"]], fields=["name","state"])`.
   - Try, in order (each via `odoo_execute("res.partner", <method>, …)`, wrapped — an unknown-method
     or IAP error just means "next"): `autocomplete_by_vat` / `read_by_vat` with the VAT;
     `autocomplete_by_name` / `autocomplete` with the name. Show the returned suggestion(s), let the
     user pick/correct, and merge into the config.
   - **If nothing works over RPC**, two fallbacks — ask which: **(a)** user supplies the details
     manually; **(b)** user creates the bare company in the Odoo UI (typing the name there gives the
     native autocomplete), then tells Odin, which re-reads it and continues from Phase 2 (Mode B).

3. **Gate G1 — present the full config** as a JSON block (name, country_id, currency_id, parent_id,
   vat, company_registry, address fields, fiscal year end) and confirm. Then:
   ```
   odoo_create("res.company", values={ …confirmed config… })
   ```
   (Creating a company auto-creates its `res.partner`.)

4. **Make the new company visible to the session user** — later phases read/write in its context:
   `odoo_whoami` → if the new company isn't listed, **stop and ask the user to grant it in the UI**
   (Settings → Users → their user → Allowed Companies). `res.users` is a **policy-protected model**
   on the server (auth surface) — Odin cannot write it; don't attempt the write. Re-check with
   `odoo_whoami` once granted, then continue.

---

## Phase 2 — Fiscal localization package (Mode A; verify-only in Mode B)

In the UI Odoo usually loads the country's chart automatically; **via RPC it often does not** — so
*verify first, load only if missing*:

1. **Verify:** `odoo_read("res.company", ids=[cid], fields=["chart_template","country_id","currency_id"])`
   and `odoo_search_count("account.account", domain=[["company_ids","in",[cid]]])`.
   Chart set + accounts > 0 → already loaded, go to Phase 3.
2. **If not loaded:**
   - Discover the valid template codes from the selection on the field:
     `odoo_fields_get("res.company", attributes=["selection"])` → `chart_template` — pick the code
     matching the country (mirror the **sibling's** `chart_template` when in doubt; confirm with user).
   - Check the backing module is installed (e.g. `l10n_uk`, `l10n_sg`, `l10n_jp`):
     `odoo_search_read("ir.module.module", domain=[["name","=","<l10n_module>"]], fields=["name","state"])`.
     **Not installed → STOP:** installing modules is blocked by server policy. Tell the user:
     *"Install it in Odoo (Apps → search '<module>' → Install, or Settings → Accounting → Fiscal
     Localization) and tell me when done."*
   - Load: `odoo_execute("account.chart.template", "try_loading", ["<template_code>", <cid>], {"install_demo": false})`.
     Returns `None` (the marshal-None gotcha — **not** a failure): verify by re-running step 1's
     count. This creates the localized CoA, the default journals, taxes/tax groups, and wires the
     **default accounts** everywhere — which Phase 4 will unpick.

---

## Phase 3 — Target chart of accounts (two scenarios)

**Detect the group pattern from the sibling** (or `odoo-context/chart-of-accounts`): read a few
sibling accounts — if their `company_ids` list several companies, the group runs a **shared CoA
(Scenario A)**; if the sibling owns its accounts alone, it's an **own local CoA (Scenario B)**.
Confirm the scenario with the user; the same group can contain both kinds.

> **Company-dependent `code` (Odoo 18 — applies throughout):** always **read** codes with the
> `company_id=<cid>` param on the read tools, and always **write** a code in the owning company's
> context: `odoo_execute("account.account","write",[[id],{"code":"<code>"}],
> {"context":{"allowed_company_ids":[cid],"company_id":cid}})`. Verify by re-reading with
> `company_id=cid`. Never trust a code read in the wrong company (it comes back empty).

### Before bucketing: run the reference sweep (Phase 4 · Step 1) now
You need to know **which template accounts are wired as defaults** before deciding their fate —
prefer **rename-in-place** for referenced accounts (an account kept live needs no remap at all).

### Scenario A — join the shared group CoA
1. **Target set** = the sibling's accounts:
   `odoo_search_read("account.account", domain=[["company_ids","in",[sib_cid]]], fields=["code","name","account_type","reconcile"], company_id=sib_cid)`.
2. **Gate G2:** present the plan — N shared accounts to link, per-company codes to set (mirror the
   sibling's codes unless the user supplies a different numbering), and the template accounts from
   Phase 2 bucketed **rename-in-place** (only if the user wants some template accounts kept and
   folded into the shared chart — rare) or **retire** (the usual case: the shared chart supersedes
   them; defaults get remapped to shared accounts in Phase 4).
3. **Link** the shared accounts to the new company, batched (~100/call):
   `odoo_write("account.account", [ids…], {"company_ids": [[4, new_cid]]})`.
4. **Set the per-company codes** in the new company's context (see the box above), mirroring the
   sibling's codes. Spot-verify a sample with `company_id=new_cid`.

### Scenario B — own local CoA (default: copy the sibling; Excel also supported)
1. **Target list** — either:
   - **Copy sibling (default):** the sibling read from A-1 → target = code + name + account_type +
     reconcile per account; or
   - **User Excel:** columns at least `code | name | type` (map `type` to Odoo `account_type`
     selection values — show the mapping for confirmation).
2. **Bucket** the template accounts (Phase 2) against the target, matching on `account_type` first,
   then code/name similarity:
   - **rename-in-place** — template account ≈ a target account → write `name` (plain `odoo_write`)
     and `code` (context-write). **Assign every default-referenced template account here if any
     target of the same type exists** — this is what shrinks Phase 4.
   - **create** — target with no template counterpart →
     `odoo_execute("account.account","create",[{"name":…, "code":…, "account_type":…, "reconcile":…, "company_ids": [[6,0,[cid]]]}], {"context":{"allowed_company_ids":[cid],"company_id":cid}})`
     (create in-context so the code lands in this company's `code_store`). Batch sensibly.
   - **retire** — template account with no target → Phase 4 set.
3. **Gate G2:** present the three buckets as a table (template code/name → action → target
   code/name) and confirm/amend before writing. Then apply rename + create; verify by re-reading
   the full chart with `company_id=cid` and diffing against the target list — must match 1-for-1.

---

## Phase 4 — Remap-and-retire the default accounts (the engine; Mode C entry point)

**Input:** the retire set (Phase 3's bucket, or in Mode C the accounts the user can't remove).

### Step 1 — Build the complete reference map (mechanic, not a hardcoded list)
1. Discover **every stored many2one field pointing at `account.account`**:
   ```
   odoo_search_read("ir.model.fields",
     domain=[["relation","=","account.account"],["ttype","=","many2one"],["store","=",true]],
     fields=["model","name","field_description","company_dependent"])
   ```
   Drop transient models (`odoo_search_read("ir.model", domain=[["model","in",[…]]],
   fields=["model","transient"])`) and **transactional models** (`account.move`,
   `account.move.line`, `account.payment`, `account.bank.statement.line`, `account.partial.reconcile`,
   `account.analytic.line` — those are data, not configuration; see Step 3 for the data check).
2. For each remaining (model, field): find references to the retire set, scoped to the new company —
   - normal fields: `odoo_search_read(<model>, domain=[["<field>","in",retire_ids]] + ([["company_id","=",cid]] if the model has company_id), fields=["id","display_name","<field>"])`
   - **company-dependent fields** (flagged in step 1 — e.g. `product.category` income/expense,
     `res.partner` receivable/payable): search **in the new company's context**:
     `odoo_execute(<model>,"search_read",[[["<field>","in",retire_ids]],["id","display_name","<field>"]], {"context":{"allowed_company_ids":[cid],"company_id":cid}})`
   - also sweep **company-level fallback defaults**:
     `odoo_search_read("ir.default", domain=[["field_id.relation","=","account.account"],["company_id","=",cid]], fields=["field_id","json_value"])`.
3. **Hotspot checklist** (the sweep finds these anyway — verify none was missed): `res.company`
   (journal suspense, transfer/internal-transfer, FX gain & loss, cash-difference income/expense,
   deferred revenue/expense, discount allocations) · `account.journal` (`default_account_id`,
   `suspense_account_id`, `profit_account_id`, `loss_account_id`) ·
   `account.payment.method.line.payment_account_id` (outstanding receipts/payments) ·
   `account.tax.repartition.line.account_id` · `account.fiscal.position` account-mapping rows ·
   `account.reconcile.model.line.account_id` · `product.category` + `res.partner` property accounts.

### Step 2 — Propose replacements (sibling-informed)
For every reference found, propose the replacement account: **read how the SIBLING configures the
same model/field** (in the sibling's context where company-dependent), take that account's **code**,
and resolve the same code in the **new** company's chart. Where the sibling gives no answer, propose
by `account_type` match and mark the row **ASK**.

### Step 3 — Data check on the retire set
`odoo_search_read("account.move.line", domain=[["account_id","in",retire_ids]], fields=["id"], limit=1)`
per account (or one grouped `odoo_read_group` by `account_id`). Any account **with journal items
cannot be retired** — pull it out of the set and flag it (it must be kept, renamed, or its history
dealt with under separate instruction).

### Step 4 — Gate G3: the dry-run remap plan (nothing written yet)
Write `entity-setup/<slug>/remap-plan_<company>.xlsx`:
- **Sheet "Remaps"** — one row per reference: Model | Record (display_name + link) | Field |
  Current account (code name) | **Proposed replacement** (code name) | Source (sibling-mirror /
  type-match / **ASK**).
- **Sheet "Retire"** — the accounts to archive once dereferenced (code, name, type, #refs, has-data flag).
- **Sheet "CoA plan"** — Phase 3's rename/create record (for the audit trail).
The user reviews, amends the ASK rows (and anything else), and confirms.

### Step 5 — Apply, verify, archive
1. Apply each confirmed remap: `odoo_write(<model>, [id], {"<field>": <new_account_id>})` — or the
   context-write via `odoo_execute` for company-dependent fields. Batch per model/field.
2. **Re-run the Step-1 sweep on the retire set** → must return **zero** references. Any survivor:
   show it, remap it (confirm), re-verify. Never proceed to archive with refs outstanding.
3. **Gate G4** — confirm the final list, then `odoo_archive("account.account", retire_ids)`.
   If Odoo still refuses one, report the exact fault, leave it active, and add it to the leftover
   checklist — **do not** work around it.

---

## Phase 5 — Journals, bank & wrap-up

1. **Journals:** `odoo_search_read("account.journal", domain=[["company_id","=",cid]], fields=["name","code","type","default_account_id"])`.
   Propose renames/recodes mirroring the **sibling's** journal names/codes; archive template
   journals the entity won't use (only after Phase 4 — their default accounts must already be
   resolved). If the entity will book through **parent journals** (the group's shared-journal
   pattern), say so, archive nothing blindly, and note it for the rulebook.
2. **Bank (optional, if details provided):** `odoo_create("res.partner.bank", {"acc_number":…, "partner_id": <company's partner>, "company_id": cid})`
   then a bank journal `odoo_create("account.journal", {"name":…, "code":…, "type":"bank", "company_id": cid, "bank_account_id": …})`.
   Bank journals are always the entity's **own** (never shared).
3. **Default taxes sanity:** read `res.company` `account_sale_tax_id` / `account_purchase_tax_id`;
   confirm they're sensible for the entity (template defaults usually are). Change only on instruction.
4. **Leftover checklist (UI-only / out-of-scope items)** — hand to the user explicitly: user access
   rights for the new company · fiscal localization double-check in Settings → Accounting · bank
   feed/sync (e.g. SaltEdge) · analytic plans · elimination pairing · anything Phase 4/G4 left flagged.
5. **Update context:** append the new entity to `odoo-context/` (entities + per-entity profile, CoA
   doc if Scenario A membership changed) or flag build-context for a verify-update pass.
6. **Hand off** to `design-bookkeeping-rules` — the new entity needs its rulebook next.

## Output (always: Excel + links)
- On-screen: what was created/changed per phase, the remap verification result (✅ zero refs /
  ❌ survivors), archive results, and the leftover checklist.
- **Excel action report** `entity-setup/<slug>/entity-setup_<company>.xlsx` — one row per record
  created/changed (company, accounts renamed/created/linked, remapped references, archived
  accounts, journals, bank), each with state and a **clickable link**
  (`<url>/web#id=<id>&model=<model>&view_type=form`), plus the remap-plan workbook from G3.
  Header block: entity, sibling used, scenario (A/B), mode, generated date. Run-metadata line with
  the token estimate (skill convention).

## Do-NOT list
- ❌ Create the company, write CoA changes, apply remaps, or archive **without the matching gate
  (G1–G4)** being explicitly confirmed.
- ❌ Delete anything, or force an archive Odoo refuses — find the reference instead.
- ❌ Install modules or touch `ir.model*`/technical models (server blocks; user does it in the UI).
- ❌ Write to the sibling or any other existing company — siblings are read-only templates.
- ❌ Read or write `account.account.code` outside the owning company's context.
- ❌ Retire an account that has journal items, or guess a replacement without marking it ASK.
- ❌ Run this inside monthly-book-keeping, or for more than one entity per run.
- ❌ Skip the remap-plan workbook, the Excel action report, or the links.
