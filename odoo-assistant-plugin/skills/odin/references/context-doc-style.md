# Context documentation style — LLM wiki, Google developer style

This is the writing standard for every document Odin **generates** into the shared context —
everything under `odoo-context/` (build-context) and `bookkeeping-rules/` (design-bookkeeping-rules),
including per-entity profiles. Load this file whenever you are about to **write or update** one of
those documents. It does not apply to chat replies or Excel action reports.

Two pillars: the docs form an **LLM wiki** (structured so a model can decide relevance and navigate
without reading everything), written in **Google developer documentation style** (clear, consistent,
scannable technical prose).

---

## Pillar 1 — LLM-wiki structure

### 1.1 Frontmatter on every file (mandatory)

Every generated `.md` starts with YAML frontmatter. The `description` alone must let a reader (human
or LLM) decide whether to open the file.

```yaml
---
name: chart-of-accounts            # kebab-case slug = the filename without .md; never renamed
description: >-                    # ONE line, <=160 chars: what's inside + the headline fact(s)
  CoA for acme_production — 505 accounts, single shared chart, per-company codes.
type: index | instance-context | entity-profile | rulebook | user-access
instance: <url> · <db>
company: <id> — <name>             # entity-profile and rulebook files ONLY; omit otherwise
generated: <YYYY-MM-DD>            # first generation date — never changes
updated: <YYYY-MM-DD>              # bumped on every verify-update
source: build-context (fresh)      # playbook + mode that last wrote it
status: current | needs-refresh    # set needs-refresh if a later session found it stale
---
```

Rules:
- `name` matches the filename and is **stable** — content changes, filenames and slugs don't
  (links elsewhere depend on them).
- `description` is rewritten whenever the headline facts change (counts, sharing model, etc.).
- The old `_Generated: <date>_` italic line is replaced by `generated:`/`updated:` — don't emit both.

### 1.2 One topic per page

Each file answers one question (entities, CoA, journals, one company's rules, one user's access).
If a section grows into its own topic, split it into a new file and link it — don't grow a page
into a second topic.

### 1.3 The index is the entry point

`odoo-context/README.md` (`type: index`) lists **every** page as one line:
`- [title](file.md) — hook` — where the hook is the page's headline fact, mirroring its
`description`. A page not in the index doesn't exist; add the line in the same edit that creates
the file. Rulebooks get their own index block in the README (or a `bookkeeping-rules/README.md`
if that folder lives separately).

### 1.4 Cross-links, not repetition

- State each fact on **one** page and link to it from the others (relative markdown links:
  `[journals.md](journals.md)`, `[SP6 HoldCo](entities/14-sp6-uk-holdco.md)`).
- Link on first mention within a page; don't re-link every occurrence.
- Deep-link to sections with heading anchors (`chart-of-accounts.md#receivable`), which is why
  heading text must stay stable (see 1.5).
- A link to a page that doesn't exist yet is allowed only in the index as a
  `*(planned)*`-marked line — never in body text.

### 1.5 Stable, predictable headings

Downstream playbooks cite sections by anchor. Keep section names identical across regenerations
and across sibling files (every entity profile has the same section set, every rulebook has the
same section set). Add new sections at the end; don't rename existing ones.

### 1.6 Facts in machine-scannable form

- Enumerable reference data (accounts, journals, taxes, rules) → **tables** with a header row.
- Small fact sets → `key: value` bullets, one fact per bullet.
- Every Odoo record reference carries **both** the human label and the id:
  `` `6201 Utilities` (id 412) ``, `` journal `BILL` (id 36) ``. Account codes are stated
  **per company** (codes are company-dependent in Odoo 18).
- Prose is for interpretation only (the "what this entity does" paragraph, caveats) — never for
  data that belongs in a table.

### 1.7 Freshness is explicit

- Every verify-update bumps `updated:` and appends to the page's **Change log** table
  (`| Date | Change | By |`) — changed facts are amended in place, but the change itself is
  recorded, never silent.
- Facts that later prove wrong are corrected on their page, not contradicted elsewhere.

---

## Pillar 2 — Google developer documentation style

Follow the Google developer documentation style guide conventions, in particular:

- **Sentence case headings** — "Most-active accounts", not "Most-Active Accounts".
- **Present tense, active voice, second person.** "The other playbooks read this file", not
  "this file will be read by the other playbooks". Address the reader as "you".
- **Short sentences, short paragraphs** — one idea per paragraph; prefer a list over a
  multi-clause sentence.
- **One term per concept, used consistently.** Pick "company" or "entity" once per wiki and
  stick to it (note the synonym once in the index if needed). Same for "rulebook",
  "instance context", "working folder".
- **Spell out abbreviations on first use** per page — "chart of accounts (CoA)", "intercompany
  (IC)" — then use the short form.
- **No ambiguous pronouns.** If "it" could point at two things, repeat the noun.
- **Numbered lists for procedures, bullets for unordered facts.** Steps a reader performs in
  order are numbered; everything else is bulleted.
- **Callouts as labelled blockquotes:** `> **Note:** …`, `> **Warning:** …`,
  `> **Caveat:** …` — not bare italics.
- **Don't editorialise.** No "simply", "just", "obviously", no filler intros
  ("This document describes…" — the frontmatter already does).

---

## Validation checklist (run before finishing a doc write)

1. Frontmatter present, `name` = filename, `description` ≤160 chars and current.
2. Page is listed in the index with a matching hook line.
3. All relative links resolve to files that exist (or are `*(planned)*` index lines).
4. Section headings match the sibling/template set; sentence case throughout.
5. Every record reference has label + id; account codes are company-scoped.
6. Reference data is in tables; no data trapped in prose.
7. `updated:` bumped and Change log appended (verify-update runs only).
