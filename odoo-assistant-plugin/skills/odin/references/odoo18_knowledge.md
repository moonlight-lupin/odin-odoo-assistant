# Odoo 18 — Knowledge (Articles) Reference

The Knowledge app stores documentation / wiki content as articles in the
`knowledge.article` model. Knowledge is an **Enterprise** app — if
`knowledge.article` is not present on the instance, it is not installed.

Creating or updating an article is a **write operation** — follow the Safety
Protocol in `SKILL.md` (describe it, show the payload, confirm) before posting.

---

## knowledge.article

### Key Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Record ID |
| `name` | char | Article title |
| `body` | html | Article content — **must be HTML** (`<p>`, `<h2>`, `<ul>`, `<table>`, …). Plain text or markdown renders incorrectly; convert to HTML first. |
| `parent_id` | many2one → knowledge.article | Parent article. Omit for a top-level article; set it to nest the article under a section. |
| `child_ids` | one2many → knowledge.article | Sub-articles |
| `internal_permission` | selection | `write` / `read` / `none`. **Required on a top-level (root) article.** A company-wide article with no explicit members must be `write`. |
| `is_article_visible_by_everyone` | bool | **Set `True`** — otherwise the article is members-only and other users get a "Join" prompt instead of seeing it in their Workspace. |
| `category` | selection | Computed: `workspace` / `private` / `shared`. **Never set it directly** — it derives from permission, members, and parent. |
| `icon` | char | Emoji shown beside the title (optional) |
| `sequence` | int | Display order among sibling articles |
| `active` | bool | False = in Trash |
| `article_member_ids` | one2many → knowledge.article.member | Per-user permissions (not needed for a plain Workspace article) |

### Categories (computed)

| Category | Meaning |
|----------|---------|
| `workspace` | Company-wide — any internal user can reach it |
| `private` | Visible only to the owner |
| `shared` | Visible only to specific members |

A root article (no `parent_id`) with `internal_permission` set lands in
**Workspace**; child articles inherit their root's category.

---

## Posting Articles

### Create a top-level Workspace article

```python
article_id = models.execute_kw(db, uid, api_key, 'knowledge.article', 'create', [{
    'name': 'Month-End Close Checklist',
    'body': '<h2>Overview</h2><p>Steps for the monthly close.</p>',
    'internal_permission': 'write',
    'is_article_visible_by_everyone': True,
}])
```

### Nest a sub-article under an existing one

Children inherit permission and visibility from their root, but set the
visibility flag explicitly to be safe.

```python
child_id = models.execute_kw(db, uid, api_key, 'knowledge.article', 'create', [{
    'name': 'Day 1 — Accruals',
    'body': '<p>...</p>',
    'parent_id': parent_article_id,
    'is_article_visible_by_everyone': True,
}])
```

### Find an article — to nest under, or to update

```python
found = models.execute_kw(db, uid, api_key, 'knowledge.article', 'search_read',
    [[('name', '=', 'Month-End Close Checklist')]],
    {'fields': ['id', 'name', 'parent_id', 'body'], 'limit': 1})
```

### Update or append to an article

`body` is replaced wholesale on write — to append, fetch the current `body`
first and concatenate.

```python
models.execute_kw(db, uid, api_key, 'knowledge.article', 'write',
    [[article_id], {'body': existing_body + '<p>New section.</p>'}])
```

---

## Gotchas

- **`body` is HTML** — never post markdown or plain text directly; it renders
  literally. Convert to HTML (`<p>`, `<h2>`, `<ul>`, `<table>`, …).
- **Articles are members-only by default** — set
  `is_article_visible_by_everyone = True`, or internal users must click
  "Join" before the article shows in their Workspace.
- **Root articles require `internal_permission`** — use `'write'` for a
  memberless Workspace article; Odoo rejects a root without it.
- **Never set `category`** — it is computed.
- This skill targets Odoo 18. The `knowledge.article` core (`name`, `body`,
  `parent_id`, `internal_permission`) is stable across versions. If a write
  rejects `is_article_visible_by_everyone`, run `fields_get` on
  `knowledge.article` to confirm the visibility field name on the instance.
