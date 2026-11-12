# Context import

`import_context.py` moves an Odoo-context folder — the markdown bundle that `build-context` and
`design-bookkeeping-rules` write — into the Knowledge workspace that the `odoo_context_*` tools
read. Run it once, before the workspace root is set in Settings (until then the write guard is off).

What it builds: the root and folder articles with the property definition on every parent; one
structure page per structure and top-level company, with its journals, chart of accounts, taxes,
banking and partners pages; entity pages with year end, parent and currency filled from live Odoo;
rulebooks; FS section maps; Read me first, Organisation and Reports; a fresh Change log; and
Proposals → Rejected. Relative `.md` links become links to the new articles. Structures and their
top-level companies are worked out from the entity index and `res.company.root_id`, so nothing
instance-specific lives in the script.

## Credentials file

Keep it outside the repository and out of chat, and delete the API key in Odoo afterwards.

```json
{
  "url": "https://your-odoo",
  "db": "your-database",
  "login": "you@example.com",
  "api_key": "…",
  "writer_groups": ["account.group_account_manager"],
  "reader_group_categories": ["base.module_category_accounting_accounting"],
  "exclude_logins": []
}
```

Knowledge grants access to people, not groups, so the groups are turned into workspace members:
editors are the users in `writer_groups`, readers the other users in any group of
`reader_group_categories` (plus optional `writers` / `readers` logins, minus `exclude_logins`).

## Use

```bash
python import_context.py --creds creds.json --source "<folder>" --plan
```

```bash
python import_context.py --creds creds.json --source "<folder>" --import
```

```bash
python import_context.py --creds creds.json --verify <root_id>
```

```bash
python import_context.py --creds creds.json --sync-members <root_id>
```

`--import` refuses to run if a workspace root already exists, and ends with `--verify`: every
article has its properties, every entity page has its drift facts, no `.md` link is left
unconverted. Re-run `--sync-members` when group membership changes; leavers drop to no access
rather than being deleted.

Dry-run it first on a scratch database (see `integration-tests/context/README.md` for running
Odoo Enterprise with Knowledge in Docker).
