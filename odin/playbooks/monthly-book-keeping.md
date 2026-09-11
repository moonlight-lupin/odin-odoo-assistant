# Playbook: monthly-book-keeping

The recurring bookkeeping run that gets a company's books **ready for review** for a period. This is
an **orchestrator**: it sequences the other bookkeeping playbooks (process-vendor-bills →
create-customer-invoices → bank-reconciliation → posting-closing-journal-entries) and standard
period checks, then hands off to month-end-review. Detailed runbook — written so a competent model
(e.g. Sonnet) can execute step-by-step. **Draft-only by default** (each sub-playbook carries its own
guardrails).

**Trigger phrases:** "monthly bookkeeping", "do the books for <month>", "bookkeeping run",
"monthly/period close prep", "get <entity> ready for review".

---

## Sources of truth
1. **The bookkeeping guide (per company)** — `<working folder>/bookkeeping-rules/<NN>-<slug>.md`.
   **Ask for it first.** It defines the coding, cadence, naming, and which task types even apply
   (a holding co with no revenue won't have customer invoices). If missing → run
   design-bookkeeping-rules first.
2. **`<working folder>/odoo-context/`** — entities, hierarchy, journals, CoA. Use it; don't re-derive.
3. **Past-period trend** — each sub-playbook consults prior periods (e.g. posting-closing's missing-
   entry detection, vendor-bill/invoice house style).

## Scope & guardrails
- **One company at a time.** Bookkeeping is per-company (per-company rulebook). For several companies
  / a subgroup, **loop** — repeat the whole run per company with that company's rulebook; never mix.
- **Always ask the period.** The run targets one period (month/quarter/year); pass it to each sub-playbook.
- **Draft-only by default; never post/pay.** Each sub-playbook enforces this; this orchestrator never
  bypasses it.
- **Delegate, don't reinvent.** For each task, **open and follow the relevant sub-playbook** (it has
  the detailed steps, field references, naming, shared/parent-journal workaround, and its own Excel
  report). This playbook coordinates order, tracking, and the consolidated report.
- **Every sub-step emits its own Excel action report + links**, and this playbook adds a
  **consolidated run report** (Step 4).

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder known. 3. For each in-scope company, its **rulebook**
   exists (else run design-bookkeeping-rules first); `odoo-context/` present (else offer build-context).

## Step 1 — Scope & period
1. **Ask which company (or companies)** to run. If a parent is named and the user wants the group,
   confirm whether to include subsidiaries (like month-end-review's expansion); then **process each
   company one at a time**.
2. **Ask the period** (month/quarter/year) — pass it consistently to every sub-playbook.
3. For each company, load its **rulebook** + profile.

## Step 2 — Tailor the task list to the company's nature
From the rulebook's Company Profile, decide which tasks apply to this entity, e.g.:
- No revenue (holding/SPV) → skip customer invoices.
- No own bank account / dormant → skip/limit bank reconciliation.
- Consolidation/elimination entity → focus on closing/elimination entries.
Present the tailored checklist for the company and confirm before running.

## Step 3 — Run the sequence (per company, in this order)
For each company, work the checklist by **delegating to the sub-playbook** (each draft-only):
1. **Open** — confirm company, period, currency; load rulebook + context.
2. **Vendor bills** → **process-vendor-bills** (record/complete the period's bills; draft).
3. **Customer invoices** → **create-customer-invoices** (raise the period's billing; draft).
4. **Bank & cash** → **bank-reconciliation** for each bank/cash journal (match + clear suspense).
5. **Closing / adjusting entries** → **posting-closing-journal-entries** (Mode A: cadence-due **+
   missing** entries for the period; draft; reversals where flagged).
6. **Intercompany** — ensure IC postings for the period are recorded consistently so month-end-review's
   IC check reconciles (book via posting-closing-journal-entries where needed).
7. **Completeness sweep** — check for stray drafts that should be posted, and nothing obviously
   missing vs prior months (quick `read_group` count comparison by journal/type):
   ```
   odoo_execute("account.move","read_group",
     [[["company_id","=",cid],["date",">=",period_start],["date","<=",period_end]]], [], ["journal_id","state"])
   ```
8. **Hand off to review** → run **month-end-review** for the company/period and surface exceptions.

Order matters: bills/invoices first (populate AP/AR), then bank rec (matches the resulting payments),
then closing entries (accruals/depreciation on top), then IC, then the review.

## Step 4 — Outputs (consolidated)
- Each sub-playbook produces **its own Excel action report** with clickable record links (per the
  skill's *Action report convention*).
- **Consolidated monthly run report** (always):
  `<working folder>/bookkeeping/<period>/monthly-run_<period>.xlsx` —
  - a **Summary sheet**: a **task × company matrix** with status per task (done / drafts created N /
    skipped / exceptions), plus the period and generated date;
  - **links** to each sub-report file and to the month-end-review deliverables;
  - a **"ready / not ready for review"** verdict per company with the open items.
- Reproduce the headline status + key links in chat.

## Step 5 — Report
Tell the user, per company: what was drafted (counts per task with links to the sub-reports), what
was skipped and why, the completeness-sweep findings, the month-end-review exceptions, and the
**ready/not-ready** verdict with the to-do list. Feed any new patterns back to design-bookkeeping-rules.

## Do-NOT list
- ❌ Post or pay anything (draft-only; sub-playbooks enforce this).
- ❌ Mix companies / apply one company's rulebook to another — loop one at a time.
- ❌ Re-implement a sub-task inline — always delegate to its sub-playbook so its guardrails apply.
- ❌ Run tasks that don't apply to the entity's nature (e.g. customer invoices for a dormant holdco).
- ❌ Skip the consolidated Excel run report or the hand-off to month-end-review.
- ❌ **Pay vendors / register payments** — paying is **never** part of the monthly run; it's the
  separate, explicit **pay-vendor-bill** playbook (draft payments only, user-initiated).

## To refine with the user
- The definitive monthly task list and which entities follow it (record in each rulebook).
- Cut-off timing, who approves postings, and how much Odin should auto-prepare (draft) vs confirm at
  each step (e.g. allow posting-closing's "breeze-through" skeletons for speed).
