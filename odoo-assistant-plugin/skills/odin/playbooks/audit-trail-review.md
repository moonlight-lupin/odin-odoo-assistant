# Playbook: audit-trail-review

For one entity and a specified period, build the **audit trail** — who created, modified, posted,
or cancelled accounting records, and when — and **review it critically for unusual activity and by
whom**. **Read-only**: queries Odoo and writes only a local report (markdown + Excel). Detailed
runbook — written so a competent model (e.g. Sonnet) can execute step-by-step.

**Trigger phrases:** "audit trail", "who posted/changed <…>", "review unusual activity",
"audit log for <entity> <period>", "forensic / controls review", "segregation of duties check".

---

## Scope & guardrails
- **Read-only.** No Odoo writes. Output is local (`<working folder>/audit/<period>/`).
- **One entity + one period.** **Always ask the period** and confirm the company.
- **Be efficient:** `read_group`/`search_read` with explicit fields; resolve user ids once.
- **Critical, not just descriptive:** every flag says *what's unusual, who, when, and why it matters*,
  with a severity (high/med/low) and a link to the record. Findings are **observations for the user
  to investigate**, not accusations.

## Preconditions
1. Connected (`odoo_whoami`). 2. Working folder known. 3. Period + company confirmed.

## Step 1 — Gather the trail (period, company)
- **Moves in/affecting the period:**
  ```
  odoo_search_read("account.move",
    domain=[["company_id","=",cid],"|",["date",">=",start],["date","<=",end]],   # refine to the period
    fields=["id","name","ref","move_type","journal_id","date","state","amount_total","partner_id",
            "create_uid","create_date","write_uid","write_date"], order="create_date")
  ```
  Capture **who** (`create_uid`/`write_uid`) and **when** (`create_date`/`write_date`) vs the
  accounting `date`. Resolve user ids → names (`res.users` read `name,login,share`).
- **Activity by user (volume):**
  ```
  odoo_execute("account.move","read_group",
    [[["company_id","=",cid],["create_date",">=",start_dt],["create_date","<=",end_dt]]], [], ["create_uid"])
  ```
- **Cancelled entries:** moves with `state="cancel"` in the period (potential reversals/voids).
- **Field-change history on posted entries** (if the Audit Trail feature / chatter tracking is on):
  ```
  odoo_search_read("mail.message",
    domain=[["model","=","account.move"],["res_id","in",<posted move ids>]],
    fields=["res_id","date","author_id","tracking_value_ids","body"], order="date")
  ```
  (Inspect `mail.tracking.value` for what field changed from→to, and by whom.) Note if the account
  **Audit Trail** is enabled (Enterprise) — if so, post-posting edits are logged here.
- **Master-data changes (high-risk):** vendor/customer **bank details** added/edited in the window:
  ```
  odoo_search_read("res.partner.bank", domain=[["company_id","in",[cid,false]],"|",["create_date",">=",start_dt],["write_date",">=",start_dt]],
    fields=["partner_id","acc_number","create_uid","create_date","write_uid","write_date"])
  ```

## Step 2 — Critical review: flag the unusual (with who / when / why)
Assess the gathered data against these red-flags (tune to the entity):
- **Segregation of duties:** the **same user** created **and** posted (and/or paid) an entry —
  especially manual journals or payments. Flag user + entries.
- **Unexpected actor:** activity by users outside the finance team, or by a shared **admin/superuser**
  account; new users posting.
- **Timing:** **out-of-hours / weekend** create/post times (derive day-of-week & hour from
  `create_date`); bursts of activity at odd times.
- **Backdating / period games:** accounting `date` **much earlier** than `create_date` (booked into a
  prior/near-closed period), or **future-dated**; entries created after period end but dated within it.
- **Sensitive accounts:** manual journal lines touching **suspense, equity/retained earnings,
  intercompany, tax, or bank** accounts (read the lines of manual `move_type="entry"` moves).
- **Edits to posted entries / reversals:** posted→draft→reposted, many `write_date`s after posting,
  cancelled-and-recreated; **sequence gaps** in a journal's numbering (order by `name`; a missing
  number can indicate a deleted entry).
- **Amounts:** unusually **large** or suspiciously **round** manual journals; duplicates (same
  partner+amount+date).
- **Master data:** changed **vendor bank account** then paid; brand-new partner used immediately.

## Step 3 — Output (markdown + Excel, with links)
- **Markdown report** `<working folder>/audit/<period>/audit-trail-review_<entity>_<period>.md`:
  - Summary: # moves, # users active (with counts), # cancelled, # flagged by severity.
  - **Findings** table: severity | what's unusual | who (user) | when | record (link) | why it matters.
  - "By whom" breakdown: activity per user (created/modified/posted counts) and any SoD breaches.
- **Excel** `…/audit-trail-review_<entity>_<period>.xlsx`: a `Trail` sheet (one row per move: name,
  date, created/modified by + timestamps, state, amount, **link**) and a `Flags` sheet (the findings
  with severity + links). Clickable links `<url>/web#id=<id>&model=account.move&view_type=form`.
- Reproduce the headline flags in chat.

## Step 4 — Report
State the top flags (high severity first) with who/when and the record links, the per-user activity
summary, and recommended follow-ups. Be clear these are **items to investigate**, evidence-based and
neutral.

## Do-NOT list
- ❌ Write/modify anything in Odoo (read-only).
- ❌ State conclusions as fact — present evidence + severity for the user to investigate.
- ❌ Skip the period question or the links/Excel output.

## To refine with the user
- The expected finance users / approvers (to calibrate "unexpected actor" and SoD checks).
- The entity's working hours / timezone (for out-of-hours detection) and materiality thresholds.
- Whether the Enterprise **Audit Trail** feature is enabled (richer change history if so).
