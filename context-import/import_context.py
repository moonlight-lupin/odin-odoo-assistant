"""Import an Odoo-context folder (an OKF / "llm-wiki" markdown bundle) into a Knowledge workspace.

One-off migration for the Odoo-context tools (spec: docs/spec-odoo-context-knowledge.html).
It builds the workspace the tools expect — root, folders, structure pages, entity pages,
rulebooks, FS maps, Change log, Proposals → Rejected — with the ctx_* properties filled,
markdown converted to HTML, and relative .md links rewritten as article links.

Run it BEFORE the workspace root is set in Settings: until then the write guard is off.

    python import_context.py --creds creds.json --source "<folder>" --plan        # print the plan (reads Odoo)
    python import_context.py --creds creds.json --source "<folder>" --import      # create everything
    python import_context.py --creds creds.json --verify <root_id>                # check what was written
    python import_context.py --creds creds.json --sync-members <root_id>          # re-sync members from groups

creds.json (keep it out of git and out of chat):
    {"url": "https://…", "db": "…", "login": "you@…", "api_key": "…",
     "writer_groups": ["account.group_account_manager"], "writers": ["login", …],
     "reader_group_categories": ["Accounting"],
     "readers": ["login", …], "exclude_logins": ["login", …]}

Expected folder layout: CLAUDE.md (becomes "Read me first"), context/organisation.md,
context/reports/instance.md, context/entities/index.md with a lookup table
`| <company id> | <name> | `<structure>` | [page](<file>.md) |`, context/entities/*.md,
context/{journals,chart-of-accounts,taxes,banking,partners}/<structure>.md,
context/chart-of-accounts/index.md, context/fs-sections/*.md, and
bookkeeping-rules/<company id>-<slug>.md. Structures and their top-level companies are worked
out from the entity table and live Odoo (res.company.root_id), so nothing instance-specific
is hard-coded here.

Writers (the context reviewers) get edit rights on the workspace. Readers get read access:
every user in a group of the named application categories (e.g. all Accounting groups),
plus any listed logins, minus exclude_logins. The importing user is always a writer.
Knowledge permissions are per person, not per group, so someone who joins an Accounting
group later must be added as a member in the Knowledge app.
"""
import argparse
import datetime
import json
import re
import sys
import xmlrpc.client
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'odoo-mcp'))
import context_core as core  # noqa: E402

MODEL = core.MODEL
ROOT_TITLE = 'Finance – Odoo context'

PARTS = [('journals', 'Journals'), ('chart-of-accounts', 'Chart of accounts'),
         ('taxes', 'Taxes'), ('banking', 'Banking'), ('partners', 'Partners')]
PART_TYPE = {'journals': 'journals', 'chart-of-accounts': 'chart', 'taxes': 'taxes',
             'banking': 'banking', 'partners': 'partners'}

#: The definition written on the root and every parent. The core's types plus
#: two the folder needs: the organisation pages and the reports page.
DEFINITION = [dict(p) for p in core.PROPERTY_DEFINITION]
for p in DEFINITION:
    if p['name'] == 'ctx_type':
        p['selection'] = p['selection'] + [['organisation', 'organisation'], ['report', 'report']]

MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}


# ---------------------------------------------------------------------------
# Reading the folder
# ---------------------------------------------------------------------------

def split_frontmatter(text):
    text = text.replace('\r\n', '\n')
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---', 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].split('\n'):
        m = re.match(r'^([A-Za-z_]+):\s*(.*)$', line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"')
    body = text[end + 4:].lstrip('\n')
    return meta, body


def iso_date(value):
    """'2026-08-04T00:00:00Z' or '03 Nov 2026' → '2026-08-04'."""
    if not value:
        return None
    m = re.match(r'(\d{4}-\d{2}-\d{2})', value)
    if m:
        return m.group(1)
    m = re.search(r'(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})', value)
    if m and m.group(2) in MONTHS:
        return '%s-%02d-%02d' % (m.group(3), MONTHS[m.group(2)], int(m.group(1)))
    return None


class Page:
    """One article to create."""

    def __init__(self, key, title, parent, body_md='', props=None, defines=False,
                 source=None, root_company=None):
        self.key = key
        self.title = title
        self.parent = parent
        self.body_md = body_md
        self.props = props or {}
        self.defines = defines
        self.source = source            # relative path of the source file, for link rewriting
        self.root_company = root_company
        self.id = None


def entity_table(src: Path):
    """[(company id, structure, file name)] from the entity index's lookup table."""
    _, idx = split_frontmatter((src / 'context/entities/index.md').read_text(encoding='utf-8'))
    return [(int(c), st, f) for c, st, f in re.findall(
        r'^\|\s*(\d+)\s*\|[^|]*\|\s*`([^`]+)`\s*\|\s*\[[^\]]*\]\(([^)]+\.md)\)', idx, re.M)]


def structure_roots(odoo, src: Path):
    """{structure: [top-level company ids]}, from the entity table and res.company.root_id.

    The tools route a company to its structure pages through its top-level company, so a
    structure whose companies sit under several top-level companies gets one copy of its
    pages per top-level company."""
    table = entity_table(src)
    ids = sorted({cid for cid, _, _ in table})
    rows = odoo('res.company', 'search_read', [('id', 'in', ids)], fields=['root_id'])
    root_of = {r['id']: (r['root_id'] or [r['id']])[0] for r in rows}
    missing = [cid for cid in ids if cid not in root_of]
    if missing:
        raise SystemExit('Companies in the entity table that this user cannot read: %s' % missing)
    out = {}
    for cid, struct, _ in table:
        roots = out.setdefault(struct, [])
        if root_of[cid] not in roots:
            roots.append(root_of[cid])
    return {k: sorted(v) for k, v in out.items()}, root_of


def build_plan(src: Path, today: str, structures, root_of):
    ctx = src / 'context'
    pages = []
    by_key = {}

    def add(page):
        pages.append(page)
        by_key[page.key] = page
        return page

    def read(rel):
        return split_frontmatter((src / rel).read_text(encoding='utf-8'))

    def verified(meta, fallback=today):
        return {'ctx_status': 'approved',
                'ctx_verified_on': iso_date(meta.get('timestamp')) or fallback,
                'ctx_verified_via': (meta.get('verified_via') or 'imported from the context folder')[:250]}

    add(Page('root', ROOT_TITLE, None, defines=True, body_md=(
        '# Finance – Odoo context\n\nThe shared context for this Odoo instance, read by '
        'the `odoo_context_*` MCP tools. One article per concept; the tools find articles by '
        'their properties, so titles and order can change freely.\n\nChanges go through '
        '`odoo_context_propose` and a reviewer\'s `odoo_context_apply_proposal`. Imported from the '
        'context folder on %s.' % today)))

    meta, body = read('CLAUDE.md')
    note = ('> Migrated from the context folder on %s. Where this page says "file" or "folder", '
            'read "article" or "folder article" in Knowledge; changes now go through '
            '`odoo_context_propose`, not edits to files.\n\n' % today)
    add(Page('guide', 'Read me first', 'root', note + body, dict(ctx_type='guide', **verified(meta)),
             source='CLAUDE.md'))
    meta, body = read('context/organisation.md')
    add(Page('organisation', meta.get('title') or 'Organisation', 'root', body,
             dict(ctx_type='organisation', **verified(meta)), source='context/organisation.md'))
    meta, body = read('context/reports/instance.md')
    add(Page('reports', meta.get('title') or 'Reports', 'root', body,
             dict(ctx_type='report', **verified(meta)), source='context/reports/instance.md'))

    add(Page('structures', 'Structures', 'root', defines=True,
             body_md='One page per structure and top-level company, each with its journals, chart '
                     'of accounts, taxes, banking and partners pages.'))
    meta, body = read('context/chart-of-accounts/index.md')
    add(Page('chart-index', 'Chart of accounts — shared scheme', 'structures', body,
             dict(ctx_type='chart', **verified(meta)), source='context/chart-of-accounts/index.md'))
    for struct, roots in structures.items():
        for root_company in roots:
            suffix = '' if len(roots) == 1 else ' (company %d)' % root_company
            skey = 'structure:%s:%d' % (struct, root_company)
            add(Page(skey, 'Structure %s%s' % (struct, suffix), 'structures', defines=True,
                     body_md='Structure `%s`%s. Its pages are listed below this article.' % (
                         struct, '' if len(roots) == 1 else
                         ', top-level company %d (the structure spans companies %s; each has its own '
                         'copy of these pages)' % (root_company, ', '.join(map(str, roots)))),
                     props=dict(ctx_type='structure', ctx_company_id=root_company,
                                ctx_structure=struct, ctx_status='approved',
                                ctx_verified_on=today, ctx_verified_via='res.company root_id'),
                     root_company=root_company))
            for folder, label in PARTS:
                rel = 'context/%s/%s.md' % (folder, struct)
                meta, body = read(rel)
                add(Page('%s:%s:%d' % (folder, struct, root_company), label, skey, body,
                         dict(ctx_type=PART_TYPE[folder], ctx_structure=struct, **verified(meta)),
                         source=rel, root_company=root_company))

    # Entities: the index's lookup table gives company id and structure per file.
    meta, idx = read('context/entities/index.md')
    table = entity_table(src)
    add(Page('entities', 'Entities', 'root', defines=True,
             body_md='One article per Odoo company in scope.'))
    add(Page('entities-index', 'Entities — index and company lookup', 'root', idx,
             dict(ctx_type='organisation', **verified(meta, today)),
             source='context/entities/index.md'))
    entity_of = {}
    for cid, struct, fname in table:
        rel = 'context/entities/%s' % fname
        meta, body = read(rel)
        page = add(Page('entity:%d' % cid, meta.get('title') or fname[:-3], 'entities', body,
                        dict(ctx_type='entity', ctx_company_id=cid, ctx_structure=struct,
                             **verified(meta)), source=rel))
        entity_of[cid] = page
        page.structure = struct
        page.root_company = root_of.get(cid) if len(structures.get(struct, [])) > 1 else None

    add(Page('rules', 'Posting rules', 'root', defines=True,
             body_md='One rulebook per Odoo company. Load the rulebook for the exact company you post into.'))
    for path in sorted((src / 'bookkeeping-rules').glob('*.md')):
        m = re.match(r'^(\d+)-', path.name)
        if not m:
            continue
        cid = int(m.group(1))
        text = path.read_text(encoding='utf-8').replace('\r\n', '\n')
        title_line = text.split('\n', 1)[0].lstrip('# ').strip()
        generated = re.search(r'Generated:\s*([0-9]{1,2} [A-Z][a-z]{2} [0-9]{4})', text)
        struct = getattr(entity_of.get(cid), 'structure', None)
        add(Page('rulebook:%d' % cid, title_line or path.stem, 'rules', text,
                 dict(ctx_type='rulebook', ctx_company_id=cid, ctx_structure=struct or False,
                      ctx_status='approved',
                      ctx_verified_on=iso_date(generated.group(1)) if generated else today,
                      ctx_verified_via='rulebook (design-bookkeeping-rules)'),
                 source='bookkeeping-rules/%s' % path.name))

    add(Page('fs', 'FS sections', 'root', defines=True,
             body_md='Financial-statement caption → account maps, by archetype.'))
    for path in sorted((ctx / 'fs-sections').glob('*.md')):
        rel = 'context/fs-sections/%s' % path.name
        meta, body = read(rel)
        title = meta.get('title') or ('FS sections — index' if path.stem == 'index' else path.stem)
        add(Page('fs:%s' % path.stem, title, 'fs', body,
                 dict(ctx_type='fs_section', **verified(meta, today)), source=rel))

    add(Page('log', 'Change log', 'root', props=dict(ctx_type='log', ctx_status='approved'),
             body_md=('Append-only. One row per approved change, written by '
                      '`odoo_context_apply_proposal`.\n\n'
                      '| Date | Article | Change | Proposed by | Approved by |\n|---|---|---|---|---|\n'
                      '| %s | Finance – Odoo context | Imported from the context folder (archive '
                      'excluded). | — | — |' % core.fmt_date(today))))
    add(Page('proposals', 'Proposals', 'root', defines=True,
             props=dict(ctx_type='proposals', ctx_status='approved'),
             body_md='Agent-written proposals waiting for a reviewer.'))
    add(Page('rejected', 'Rejected', 'proposals', defines=True,
             props=dict(ctx_type='rejected', ctx_status='approved'),
             body_md='Rejected proposals, kept for the record.'))
    return pages, by_key


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

_LINK = re.compile(r'\[([^\]]*)\]\(([^)\s]+?\.md)(#[^)\s]*)?\)')


def rewrite_links(page, by_source, by_key):
    """Point relative .md links at the new articles. Unknown targets keep their text only."""
    if not page.source:
        return page.body_md
    base = Path(page.source).parent

    def target(rel):
        resolved = (base / rel).as_posix()
        parts = []
        for piece in resolved.split('/'):
            if piece == '..' and parts:
                parts.pop()
            elif piece not in ('.', ''):
                parts.append(piece)
        path = '/'.join(parts)
        candidates = by_source.get(path, [])
        if not candidates:
            return None
        if len(candidates) > 1:
            root = page.root_company
            for cand in candidates:
                if cand.root_company == root:
                    return cand
        return candidates[0]

    def sub(m):
        hit = target(m.group(2))
        if hit is None:
            return m.group(1)
        return '[%s](/knowledge/article/%d)' % (m.group(1), hit.id)

    return _LINK.sub(sub, page.body_md)


# ---------------------------------------------------------------------------
# Odoo
# ---------------------------------------------------------------------------

class Odoo:
    def __init__(self, creds):
        self.url, self.db = creds['url'].rstrip('/'), creds['db']
        self.key = creds['api_key']
        common = xmlrpc.client.ServerProxy(self.url + '/xmlrpc/2/common')
        self.obj = xmlrpc.client.ServerProxy(self.url + '/xmlrpc/2/object', allow_none=True)
        self.uid = common.authenticate(self.db, creds['login'], self.key, {})
        if not self.uid:
            raise SystemExit('Authentication failed for %s on %s' % (creds['login'], self.db))

    def __call__(self, model, method, *args, **kwargs):
        return self.obj.execute_kw(self.db, self.uid, self.key, model, method, list(args), kwargs)

    def partner_of(self, login):
        rows = self('res.users', 'search_read', [('login', '=', login)], fields=['partner_id'])
        if not rows:
            raise SystemExit('No Odoo user with login %r' % login)
        return rows[0]['partner_id'][0]


def company_facts(odoo, company_ids):
    mine = set(odoo('res.users', 'read', [odoo.uid], fields=['company_ids'])[0]['company_ids'])
    readable = sorted(set(company_ids) & mine)
    if not readable:
        return {}
    rows = odoo('res.company', 'search_read', [('id', 'in', readable)],
                fields=['parent_id', 'currency_id', 'fiscalyear_last_day', 'fiscalyear_last_month'],
                context={'allowed_company_ids': readable})
    out = {}
    for r in rows:
        out[r['id']] = {
            'ctx_year_end': '%02d-%02d' % (int(r['fiscalyear_last_day'] or 31),
                                           int(r['fiscalyear_last_month'] or 12)),
            'ctx_parent_id': r['parent_id'][0] if r['parent_id'] else 0,
            'ctx_currency': r['currency_id'][1] if r['currency_id'] else False,
        }
    return out


def desired_members(odoo, creds):
    """Partner ids that should edit / read the workspace, from groups and logins.

    Editors: the importing user, users in any of `writer_groups` (xml ids, e.g.
    account.group_account_manager) and the `writers` logins. Readers: internal users in
    any group of the `reader_group_categories` applications (e.g. Accounting) and the
    `readers` logins. `exclude_logins` are left out of both, except the importing user.
    """
    excluded = set(creds.get('exclude_logins', []))
    me = odoo('res.users', 'read', [odoo.uid], fields=['partner_id'])[0]['partner_id'][0]

    def users(domain):
        return odoo('res.users', 'search_read', domain + [('share', '=', False)],
                    fields=['login', 'partner_id'])

    def ref(xmlid, model):
        module, name = xmlid.split('.', 1)
        rows = odoo('ir.model.data', 'search_read', [('module', '=', module), ('name', '=', name),
                                                     ('model', '=', model)], fields=['res_id'])
        if not rows:
            raise SystemExit('No %s %s' % (model, xmlid))
        return rows[0]['res_id']

    writers = {me}
    for xmlid in creds.get('writer_groups', []):
        writers |= {u['partner_id'][0] for u in users([('groups_id', 'in', [ref(xmlid, 'res.groups')])])
                    if u['login'] not in excluded}
    writers |= {odoo.partner_of(l) for l in creds.get('writers', []) if l not in excluded}
    readers = set()
    # Categories by xml id (stable, e.g. base.module_category_accounting_accounting) or by
    # name (what the Settings screen shows, e.g. "Accounting").
    for category in creds.get('reader_group_categories', []):
        domain = ([('groups_id.category_id', '=', ref(category, 'ir.module.category'))]
                  if '.' in category else [('groups_id.category_id.name', '=', category)])
        readers |= {u['partner_id'][0] for u in users(domain) if u['login'] not in excluded}
    readers |= {odoo.partner_of(l) for l in creds.get('readers', []) if l not in excluded}
    return writers, readers - writers


def sync_members(odoo, creds, root_id):
    """Bring the root's members in line with the groups. Leavers drop to 'none'
    (kept on record, no access) rather than being deleted."""
    writers, readers = desired_members(odoo, creds)
    want = {p: 'write' for p in writers}
    want.update({p: 'read' for p in readers})
    rows = odoo('knowledge.article.member', 'search_read', [('article_id', '=', root_id)],
                fields=['partner_id', 'permission'])
    have = {r['partner_id'][0]: r for r in rows}
    changes = 0
    for partner, permission in want.items():
        row = have.get(partner)
        if row is None:
            odoo('knowledge.article.member', 'create',
                 {'article_id': root_id, 'partner_id': partner, 'permission': permission})
            changes += 1
        elif row['permission'] != permission:
            odoo('knowledge.article.member', 'write', [row['id']], {'permission': permission})
            changes += 1
    for partner, row in have.items():
        if partner not in want and row['permission'] != 'none':
            odoo('knowledge.article.member', 'write', [row['id']], {'permission': 'none'})
            changes += 1
    print('Members synced: %d editor(s), %d reader(s), %d change(s)'
          % (len(writers), len(readers), changes))


def do_import(odoo, pages, by_key, creds, today):
    existing = odoo(MODEL, 'search_count', [('name', '=', ROOT_TITLE), ('parent_id', '=', False)])
    if existing:
        raise SystemExit('A root article named %r already exists — refusing to import twice.' % ROOT_TITLE)
    writers, readers = desired_members(odoo, creds)
    print('Workspace members: %d editor(s), %d reader(s)' % (len(writers), len(readers)))
    members = ([(0, 0, {'partner_id': p, 'permission': 'write'}) for p in sorted(writers)]
               + [(0, 0, {'partner_id': p, 'permission': 'read'}) for p in sorted(readers)])

    entity_ids = sorted({p.props['ctx_company_id'] for p in pages if p.props.get('ctx_type') == 'entity'})
    facts = company_facts(odoo, entity_ids)
    missing = [c for c in entity_ids if c not in facts]
    for p in pages:
        if p.props.get('ctx_type') == 'entity':
            p.props.update(facts.get(p.props['ctx_company_id'], {}))

    # Pass 1: create every article (parents first; the plan is ordered that way),
    # definition before children so their properties have something to land on.
    for p in pages:
        values = {'name': p.title, 'body': '<p></p>'}
        if p.parent:
            values['parent_id'] = by_key[p.parent].id
        else:
            values.update({'internal_permission': 'none', 'article_member_ids': members})
        if p.defines:
            values['article_properties_definition'] = DEFINITION
        p.id = odoo(MODEL, 'create', values)
        if p.props:
            odoo(MODEL, 'write', [p.id], {'article_properties': {k: v for k, v in p.props.items()
                                                                if v is not None}})
        print('  created %5d  %s' % (p.id, p.title))

    # Pass 2: bodies, with links pointing at the new articles.
    by_source = {}
    for p in pages:
        if p.source:
            by_source.setdefault(p.source, []).append(p)
    for p in pages:
        html_body = core.markdown_to_html(rewrite_links(p, by_source, by_key))
        odoo(MODEL, 'write', [p.id], {'body': html_body})
    return by_key['root'].id, missing


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify(odoo, root_id):
    root = odoo(MODEL, 'read', [root_id], fields=['parent_path'])[0]
    rows = odoo(MODEL, 'search_read', [('parent_path', '=like', root['parent_path'] + '%')],
                fields=['name', 'body', 'article_properties', 'parent_id'])
    counts, problems = {}, []
    for r in rows:
        p = core.props(r)
        kind = p.get('ctx_type') or '(folder/untyped)'
        counts[kind] = counts.get(kind, 0) + 1
        if r['id'] != root_id and not r['article_properties']:
            problems.append('no properties (parent lacks the definition): %d %s' % (r['id'], r['name']))
        body = str(r.get('body') or '')
        if re.search(r'href="[^"]*\.md', body):
            problems.append('unrewritten .md link: %d %s' % (r['id'], r['name']))
        if kind == 'entity':
            for key in ('ctx_company_id', 'ctx_year_end', 'ctx_parent_id', 'ctx_currency'):
                if p.get(key) in (None, False, '') and not (key == 'ctx_parent_id' and p.get(key) == 0):
                    problems.append('entity %d %s missing %s' % (r['id'], r['name'], key))
        if kind not in ('(folder/untyped)', 'log', 'proposals', 'rejected') and p.get('ctx_status') != 'approved':
            problems.append('not approved: %d %s' % (r['id'], r['name']))
    print('Articles under root %d: %d' % (root_id, len(rows)))
    for kind in sorted(counts):
        print('  %-18s %d' % (kind, counts[kind]))
    print('Problems: %d' % len(problems))
    for line in problems:
        print('  - ' + line)
    return not problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--creds', required=True)
    ap.add_argument('--source')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--import', dest='do_import', action='store_true')
    mode.add_argument('--verify', type=int, metavar='ROOT_ID')
    mode.add_argument('--sync-members', type=int, metavar='ROOT_ID')
    args = ap.parse_args()
    creds = json.loads(Path(args.creds).read_text(encoding='utf-8'))
    today = datetime.date.today().isoformat()
    odoo = Odoo(creds)
    if args.plan or args.do_import:
        structures, root_of = structure_roots(odoo, Path(args.source))
        pages, by_key = build_plan(Path(args.source), today, structures, root_of)
    if args.plan:
        for p in pages:
            depth = 0
            q = p
            while q.parent:
                depth += 1
                q = by_key[q.parent]
            print('%s%s  [%s]' % ('  ' * depth, p.title, ', '.join(
                '%s=%s' % (k, v) for k, v in p.props.items()
                if k in ('ctx_type', 'ctx_company_id', 'ctx_structure', 'ctx_verified_on'))))
        print('\n%d articles' % len(pages))
        return
    if args.do_import:
        root_id, missing = do_import(odoo, pages, by_key, creds, today)
        print('\nRoot article id: %d' % root_id)
        if missing:
            print('Companies not readable for drift facts: %s' % missing)
        ok = verify(odoo, root_id)
    elif args.sync_members:
        sync_members(odoo, creds, args.sync_members)
        ok = True
    else:
        ok = verify(odoo, args.verify)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
