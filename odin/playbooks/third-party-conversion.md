# Playbook: third-party-conversion

Some companies' records originate **outside Odoo** — uploaded from a third party's reports (e.g. a
property manager, a managing agent, an external accounting system). Their **chart of accounts differs
from Odoo's**, so the data must be **converted (CoA-mapped) and posted into Odoo**. This playbook has
**two parts**:
- **Part A — Consultation & documentation:** co-design the conversion process and the **third-party →
  Odoo CoA mapping**, and record it. *(The CoA mapping is owned by the bookkeeping rulebook — see
  design-bookkeeping-rules; this playbook produces/uses it.)*
- **Part B — Conversion & posting:** with a provided third-party report + the documented mapping,
  **extract → convert → post to Odoo as DRAFT**, tied back to the source.

Detailed runbook — written so a competent model (e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "convert third-party records", "upload from <property manager / agent / system>",
"map their accounts to our CoA", "convert and post the third-party report", "set up the conversion".

---

## Scope & guardrails
- **One company at a time.** The mapping & process are **company-specific** and live in that company's
  **rulebook** (`bookkeeping-rules/<NN>-<slug>.md`, design-bookkeeping-rules).
- **Part A is read-only/documentation** (no Odoo writes). **Part B writes DRAFT only** — Odin creates
  the converted journal entry/entries in **draft**; **never posts** without explicit instruction.
- **No unmapped accounts.** Every third-party account/line must map to an Odoo account; if any don't,
  **stop and ask** (or add the mapping in Part A) — never guess a target account.
- **Must tie out.** The converted entry must **balance** (Σdebit = Σcredit) and **tie to the source
  total**. Don't post a conversion that doesn't tie.
- **Shared/parent journal workaround** for the upload journal if the company has none of its own.
- **Always Excel report + links.** Settlement model still applies — actual cash for these entities
  still arrives via the bank statement and is settled by bank-reconciliation.

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder + the company's rulebook (create via
   design-bookkeeping-rules if missing). 3. For Part B, the third-party report (file/data) to convert.

---

## Part A — Consultation & documentation (define the conversion)
Goal: agree and record *how* this company's third-party data becomes Odoo entries. Read-only.

1. **Identify the source:** which company, which third party/system, what report (trial balance?
   transaction listing? rent roll?), **format** (CSV/XLSX/PDF), **frequency** (monthly/…), currency,
   and the **tie-out figure** on the report (a control total / period movement / closing balance).
2. **Get a sample report** + this company's Odoo CoA (`account.account` where `company_ids in [cid]`,
   from context/rulebook).
3. **Co-design the CoA mapping (confirm each):** third-party account/line → **Odoo account**
   (`code name` + id), plus analytic, tax, and partner handling where relevant. Note one-to-one vs
   many-to-one (several source accounts → one Odoo account) and any accounts to ignore.
4. **Define the transformation rules:** sign convention; whether the upload is **balances** (post the
   movement = period change) or **transactions** (one line each / summarised); the **target journal**
   (a dedicated upload journal, e.g. an "HFS"/"3rd-party" misc journal — own or parent-shared); the
   **naming/ref** convention; how the period is represented; and the **tie-out basis**.
5. **Document it in the rulebook** — write a **"Third-party conversion mapping"** section into
   `bookkeeping-rules/<NN>-<slug>.md` (mapping table + rules + process steps + tie-out). This is the
   single source of truth Part B reads. Add a Change Log entry. *(If records do NOT come from a third
   party, this whole playbook doesn't apply.)*

### Rulebook section to write (template)
```markdown
## Third-party conversion mapping
- Source: <third party / system> · Report: <type> · Format: <csv/xlsx/pdf> · Frequency: <…> · Currency: <…>
- Upload journal: `<code>`(id) — OWN / SHARED-FROM-PARENT.  Naming/ref: <convention>.
- Basis: <balances (movement) | transactions>.  Tie-out: <control total on the report>.
### Account mapping (third-party → Odoo)
| Third-party account/code | Odoo account (code name, id) | Analytic | Tax | Notes |
|--------------------------|------------------------------|----------|-----|-------|
| 4000 Rent received | `4000 Rent` (id …) | property | none | sign: credit |
| … | | | | |
### Transformation rules
- Sign convention: …  · Aggregation: …  · Partner handling: …  · Accounts ignored: …
```

---

## Part B — Conversion & posting (draft)
Inputs: the provided third-party report (the references) + the rulebook's conversion mapping.

1. **Load the mapping** from the company's rulebook (Part A). If none exists → run Part A first.
2. **Extract** the data from the report (read CSV/XLSX/PDF): the per-account amounts (or transactions)
   and the **control total** for the tie-out.
3. **Convert:** apply the CoA mapping (source account → Odoo account, +analytic/tax) and the
   transformation rules. **Every source line must map** — list any **unmapped** items and stop/ask.
   Aggregate per the basis (balances → movement per Odoo account; transactions → per line).
4. **Validate (paper, before creating):** the converted lines **balance** (Σdebit = Σcredit), and the
   total **ties to the report's control figure**. If not, do not proceed — reconcile the difference.
5. **Build the DRAFT entry** in the upload journal (own, else shared/parent workaround):
   ```
   odoo_create("account.move", values={
     "move_type": "entry", "journal_id": <upload_journal_id>, "date": "<period end>",
     "company_id": <cid>, "ref": "<naming per mapping, e.g. '3rd-party upload — Mar 2026'>",
     "line_ids": [
       [0, 0, {"account_id": <mapped>, "name": "<desc>", "debit": <amt>, "credit": 0.0,
               "analytic_distribution": {"<analytic>": 100}}],
       [0, 0, {"account_id": <mapped>, "name": "<desc>", "debit": 0.0, "credit": <amt>}]
       # … one per mapped account/transaction
     ]
   })
   ```
   - Leave in **draft**; read back and confirm it balances and `company_id` is right.
   - For large uploads, split into batched draft entries (note the split) but keep the set tied to the total.
   - *(Optional)* attach the source report to the entry via `ir.attachment`.
   - **Return the link** to each draft entry.
6. **Do NOT post** — the user reviews and posts in Odoo (post only on explicit instruction).

## Output (always: Excel + links)
- On-screen: converted lines (source account → Odoo account, amount), the **tie-out result**
  (✅ ties / ❌ off by X), and the draft entry link(s).
- **Excel action report** (skill's convention):
  `<working folder>/bookkeeping/<period>/third-party-conversion_<company>_<period>.xlsx` — one row per
  converted line: Source account | Odoo account | Debit | Credit | Analytic | Entry (link). Header
  block: company, source, period, control total, tied? Tell the user the path + reproduce links.
- Feed any new/unmapped source account back into Part A (update the rulebook mapping — living document).

## Do-NOT list
- ❌ Convert without the documented mapping (run Part A first); **never guess** an Odoo account for an
  unmapped source account.
- ❌ Post the entry (draft only); create an entry that doesn't **balance** or doesn't **tie to the source**.
- ❌ Post a subsidiary entry on a parent-owned journal — use the shared/parent-journal workaround.
- ❌ Skip the Excel report / links.
