"""Drive every spec acceptance scenario through BOTH servers against a real Odoo 18.

Prerequisites: the harness in docker-compose.yml is up and seeded
(scripts/seed.py wrote seed.json). Then, from this folder:

    python run.py

Route A is the in-Odoo module, over HTTP JSON-RPC at /mcp/v1 with each test
user's API key. Route B is the independent server (odoo-mcp/server.py),
imported here with the MCP SDK stubbed and pointed at the same Odoo over
XML-RPC. Read results from the two routes are compared for equality.
"""
import json
import os
import sys
import time
import types
import urllib.request
import xmlrpc.client
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
URL = os.environ.get('CTX_URL', 'http://localhost:18069')
DB = os.environ.get('CTX_DB', 'ctx')
SEED = json.loads(Path(os.environ.get('CTX_SEED', HERE / 'seed.json')).read_text())
C, J, A, KEYS = SEED['companies'], SEED['journals'], SEED['articles'], SEED['keys']
LOGINS = SEED['logins']

# ---- route B: the independent server ----------------------------------------
config = HERE / 'run-config.json'
config.write_text(json.dumps({'url': URL, 'db': DB, 'context_root_article_id': A['root'],
                              'context_stale_days': 90}))
os.environ['ODOO_CONFIG_PATH'] = str(config)
os.environ['MCP_AUDIT_LOG'] = str(HERE / 'run-audit.jsonl')
try:
    import mcp.server.mcpserver  # noqa: F401
except Exception:
    class _Stub:
        def __init__(self, *a, **k):
            pass

        def tool(self, *a, **k):
            return lambda fn: fn
    pkg, srv, mod = (types.ModuleType(n) for n in ('mcp', 'mcp.server', 'mcp.server.mcpserver'))
    mod.MCPServer = _Stub
    pkg.server, srv.mcpserver = srv, mod
    for name in [n for n in list(sys.modules) if n == 'mcp' or n.startswith('mcp.')]:
        del sys.modules[name]
    sys.modules.update({'mcp': pkg, 'mcp.server': srv, 'mcp.server.mcpserver': mod})
sys.path.insert(0, str(REPO / 'odoo-mcp'))
import context_core  # noqa: E402
import server  # noqa: E402


def as_server(user):
    server._authenticate(LOGINS[user], KEYS[user], db=DB, url=URL)


def call_b(user, tool, **args):
    as_server(user)
    try:
        return {'ok': True, 'value': json.loads(json.dumps(getattr(server, tool)(**args),
                                                           default=str))}
    except Exception as error:   # noqa: BLE001
        return {'ok': False, 'error': str(error), 'type': type(error).__name__}


# ---- route A: the in-Odoo module --------------------------------------------
def call_a(user, tool, **args):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                       'params': {'name': tool, 'arguments': args}}).encode()
    request = urllib.request.Request(URL + '/mcp/v1', data=body, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEYS[user]})
    reply = json.loads(urllib.request.urlopen(request, timeout=60).read())
    if 'error' in reply:
        return {'ok': False, 'error': reply['error']['message'], 'type': 'protocol'}
    result = reply['result']
    text = result['content'][0]['text']
    if result.get('isError'):
        return {'ok': False, 'error': text, 'type': 'tool'}
    try:
        return {'ok': True, 'value': json.loads(text)}
    except ValueError:
        return {'ok': True, 'value': text}


def rpc_list(method='tools/list'):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method}).encode()
    request = urllib.request.Request(URL + '/mcp/v1', data=body, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEYS['admin']})
    return json.loads(urllib.request.urlopen(request, timeout=60).read())


# ---- admin XML-RPC for set-up and inspection (bypasses both servers) --------
_common = xmlrpc.client.ServerProxy(URL + '/xmlrpc/2/common')
_obj = xmlrpc.client.ServerProxy(URL + '/xmlrpc/2/object', allow_none=True)
_uid = _common.authenticate(DB, 'admin', KEYS['admin'], {})


def admin(model, method, *args, **kwargs):
    return _obj.execute_kw(DB, _uid, KEYS['admin'], model, method, list(args), kwargs)


def article(aid, fields=('name', 'body', 'parent_id', 'active', 'article_properties',
                         'create_uid', 'write_date')):
    rows = admin('knowledge.article', 'read', [aid], fields=list(fields),
                 context={'active_test': False})
    row = rows[0]
    row['props'] = context_core.props(row)
    return row


# ---- checks -------------------------------------------------------------------
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(('PASS ' if condition else 'FAIL ') + name + ('' if condition else '  -> %s' % detail))


def both(user, tool, **args):
    return call_a(user, tool, **args), call_b(user, tool, **args)


def same(name, a, b):
    check(name + ' [routes agree]', a == b,
          'A=%s\nB=%s' % (json.dumps(a)[:900], json.dumps(b)[:900]))


def ids(result):
    return {x['id'] for x in result['value']['articles']}


# ==============================================================================
print('\n== tools/list on the module')
listed = {t['name']: t for t in rpc_list()['result']['tools']}
for name in context_core.TOOL_DOCS:
    check('module lists %s with all four hints' % name,
          name in listed and set(listed[name]['annotations']) >= {
              'readOnlyHint', 'destructiveHint', 'idempotentHint', 'openWorldHint'})

print('\n== odoo_context_for: routing (spec acceptance 1, 2)')
a, b = both('contrib', 'odoo_context_for', company_id=C['67'], task='elimination')
check('67/elimination succeeds on A', a['ok'], a)
want = {A['guide'], A['w1'], A['w1r'], A['journals'], A['chart'], A['partners']}
check('67/elimination routes guide, entity, rulebook, shc-ii Journals/Chart/Partners',
      a['ok'] and ids(a) == want, a.get('value', {}).get('articles') if a['ok'] else a)
check('67/elimination has the 31 July year-end warning',
      a['ok'] and 'Year end 31 Jul: a July close is a year-end close.' in a['value']['warnings'],
      a.get('value', {}).get('warnings'))
check('67/elimination: company facts parent 9 / root 6 / 31 Jul',
      a['ok'] and (a['value']['company']['parent_id'], a['value']['company']['root_id'],
                   a['value']['company']['year_end']) == (C['9'], C['6'], '31 Jul'),
      a.get('value', {}).get('company'))
check('entity markdown carries the table',
      a['ok'] and '| Year end | 31 Jul |' in next(
          x['markdown'] for x in a['value']['articles'] if x['id'] == A['w1']))
same('context_for 67/elimination', a, b)

a, b = both('admin', 'odoo_context_for', company_id=C['23'])
check('23 returns Edinburgh 1 + sp6 articles and nothing from shc-ii',
      a['ok'] and ids(a) == {A['guide'], A['ed1'], A['ed1r'], A['sp6j'], A['sp6c']}, a)
same('context_for 23', a, b)

print('\n== journals (spec acceptance: 68 consolidation, 9 bank)')
a, b = both('contrib', 'odoo_context_for', company_id=C['68'])
cje = a['value']['journals'].get('consolidation', {}) if a['ok'] else {}
check('68 consolidation = CJE owned by 6, shared',
      (cje.get('id'), cje.get('code'), cje.get('company_id'), cje.get('shared'))
      == (J['CJE6'], 'CJE', C['6'], True), cje)
general = a['value']['journals'].get('general', {}) if a['ok'] else {}
check('68 general = MISC of 6 (not CJE/EXCH)', general.get('id') == J['MISC6'], general)
same('context_for 68', a, b)

a, b = both('admin', 'odoo_context_for', company_id=C['23'])
check('23 consolidation = CJE of sp6 (never shc-ii or QIPDG)',
      a['value']['journals']['consolidation']['id'] == J['CJE14'], a['value']['journals'])

a, b = both('contrib', 'odoo_context_for', company_id=C['9'])
bank = a['value']['journals']['bank'] if a['ok'] else None
check('9 bank = its own two bank journals only',
      isinstance(bank, list) and sorted(j['id'] for j in bank) == sorted([J['BNK4'], J['BNK7']])
      and all(j['company_id'] == C['9'] for j in bank), bank)
same('context_for 9', a, b)
a, _ = both('contrib', 'odoo_context_for', company_id=C['67'])
check('67 has no own bank journal -> error, never a parent bank',
      'error' in a['value']['journals']['bank'], a['value']['journals']['bank'])

a, b = both('w1only', 'odoo_context_for', company_id=C['67'])
check('user allowed only into 67: shared general journal is an error naming ancestors',
      a['ok'] and 'error' in a['value']['journals']['general']
      and str(C['6']) in a['value']['journals']['general']['error'], a)
same('context_for 67 as w1only', a, b)

print('\n== drift (spec acceptance 3)')
before = article(A['w1'])
# Odoo 18 requires a subsidiary's year end to match its root's, and a change
# on the root cascades, so change it there.
admin('res.company', 'write', [C['6']], {'fiscalyear_last_month': '6', 'fiscalyear_last_day': 30})
a, b = both('contrib', 'odoo_context_for', company_id=C['67'])
drift = [w for w in a['value']['warnings'] if w.startswith('Drift:')] if a['ok'] else []
check('changing year end in Odoo returns one drift warning', len(drift) == 1, a)
after = article(A['w1'])
check('drift never edits the article', (before['write_date'], before['body']) ==
      (after['write_date'], after['body']))
same('context_for with drift', a, b)
admin('res.company', 'write', [C['6']], {'fiscalyear_last_month': '7', 'fiscalyear_last_day': 31})

print('\n== access and the company filter (spec acceptance 6 + company filter)')
a, b = both('outsider', 'odoo_context_for', company_id=C['67'])
check('no workspace access: no articles, facts and journals still there',
      a['ok'] and a['value']['articles'] == [] and a['value']['company']['id'] == C['67']
      and any('cannot open' in w for w in a['value']['warnings']), a)
same('context_for as outsider', a, b)
for tool, args in (('odoo_context_lookup', {'query': 'Journals'}),
                   ('odoo_context_lookup', {'article_id': A['guide']})):
    a, b = both('outsider', tool, **args)
    check('outsider gets nothing from %s %s' % (tool, args), not a['ok'] and not b['ok'], (a, b))

a, b = both('sp6', 'odoo_context_lookup', query='o', limit=50)
got = {r['id'] for r in a['value']['results']} if a['ok'] else set()
hidden = {A['shcii'], A['journals'], A['chart'], A['taxes'], A['banking'], A['partners'],
          A['w1'], A['bw'], A['w1r']}
check('SP6-only user: no shc-ii or Woolwich articles by query', a['ok'] and not got & hidden, got)
check('SP6-only user: still gets sp6 pages by query', {A['sp6j'], A['ed1']} <= got, got)
check('SP6-only user: still reads Read me first by id',
      call_a('sp6', 'odoo_context_lookup', article_id=A['guide'])['ok'])
check('search never lists the Proposals / Rejected folders',
      not got & {A['proposals'], A['rejected']}, got)
same('lookup as sp6', a, b)
a, b = both('sp6', 'odoo_context_lookup', article_id=A['w1'])
check('SP6-only user: Woolwich article by id is "not found" on both routes',
      not a['ok'] and not b['ok'] and 'not found' in a['error'] and 'not found' in b['error'],
      (a, b))
a, b = both('sp6', 'odoo_context_for', company_id=C['67'])
check('SP6-only user: context_for(67) refused on both routes', not a['ok'] and not b['ok'], (a, b))

print('\n== lookup')
for args in ({}, {'query': 'x', 'article_id': A['guide']}):
    a, b = both('admin', 'odoo_context_lookup', **args)
    check('lookup %s refused on both' % args, not a['ok'] and not b['ok'], (a, b))
a, b = both('admin', 'odoo_context_lookup', article_id=A['w1'])
check('lookup by id returns markdown and properties',
      a['ok'] and a['value']['properties']['ctx_company_id'] == C['67'], a)
same('lookup by id', a, b)
a, b = both('admin', 'odoo_context_lookup', query='Journals', structure='sp6')
check('lookup filtered by structure', a['ok'] and [r['id'] for r in a['value']['results']]
      == [A['sp6j']], a)
same('lookup filtered', a, b)

print('\n== untyped articles (spec: parent with no definition)')
a, _ = both('admin', 'odoo_context_for', company_id=C['67'])
check('article under a parent without the definition is ignored and counted',
      A['stray'] not in ids(a) and any('1 workspace article(s) have no context properties' in w
                                       for w in a['value']['warnings']), a['value']['warnings'])

print('\n== propose (spec acceptance 7, 8)')
target_before = article(A['w1'])
a = call_a('contrib', 'odoo_context_propose', target_article_id=A['w1'],
           change='# Woolwich 1 Elimination\n\nUpdated by test.\n\n| Field | Value |\n|---|---|\n| Year end | 31 Jul |',
           evidence='odoo_read(res.company, [%d])' % C['67'], reason='integration test')
check('module: read-only contributor files a proposal', a['ok'], a)
pid = a['value']['proposal_id'] if a['ok'] else None
if pid:
    prop = article(pid)
    check('proposal sits under Proposals with proposed status and target',
          prop['parent_id'][0] == A['proposals'] and prop['props'].get('ctx_status') == 'proposed'
          and prop['props'].get('ctx_target') == A['w1'], prop['props'])
    check('proposal creator is the contributor, not the superuser',
          prop['create_uid'][0] == SEED['users']['contrib'], prop['create_uid'])
    messages = admin('mail.message', 'search_count', [('model', '=', 'knowledge.article'),
                                                      ('res_id', '=', pid)])
    check('reviewers are notified on the proposal', messages >= 1, messages)
check('target unchanged after proposing', article(A['w1'])['write_date'] == target_before['write_date'])

b = call_b('contrib', 'odoo_context_propose', target_article_id=A['w1'], change='x',
           evidence='e', reason='r')
check('server: read-only contributor cannot create (no elevation) - documented difference',
      not b['ok'], b)
for route in (call_a, call_b):
    r = route('plain', 'odoo_context_propose', target_article_id=A['w1'], change='x',
              evidence='e', reason='r')
    check('%s: user outside the contributor group is refused' % route.__name__,
          not r['ok'] and 'Context contributor' in r['error'], r)
for route in (call_a, call_b):
    r = route('sp6', 'odoo_context_propose', target_article_id=A['w1'], change='x',
              evidence='e', reason='r')
    check('%s: SP6-only user proposing on Woolwich is refused' % route.__name__,
          not r['ok'] and 'outside your allowed companies' in r['error'], r)

# Give contrib edit rights on Proposals (as the spec's rollout step does for route B).
admin('knowledge.article.member', 'create',
      {'article_id': A['proposals'], 'partner_id': admin('res.users', 'read', [SEED['users']['contrib']],
                                                         fields=['partner_id'])[0]['partner_id'][0],
       'permission': 'write'})
b = call_b('contrib', 'odoo_context_propose', target_article_id=A['ed1'] if False else A['bw'],
           change='B Woolwich, updated via the independent server.',
           evidence='odoo_read(res.company, [%d])' % C['9'], reason='server route test')
check('server: contributor with edit on Proposals files a proposal', b['ok'], b)
pid_b = b['value']['proposal_id'] if b['ok'] else None

a, b = both('admin', 'odoo_context_lookup', query='Woolwich')
check('lookup leaves proposals out by default',
      a['ok'] and all(r['type'] != 'proposal' for r in a['value']['results']), a)
a, b = both('admin', 'odoo_context_lookup', query='Woolwich', include_proposals=True)
check('lookup includes proposals when asked',
      a['ok'] and any(r['type'] == 'proposal' for r in a['value']['results']), a)
same('lookup with proposals', a, b)

print('\n== apply (spec acceptance 8)')
for route in (call_a, call_b):
    r = route('contrib', 'odoo_context_apply_proposal', proposal_id=pid, decision='approve')
    check('%s: contributor calling apply is refused' % route.__name__,
          not r['ok'] and 'Context reviewer' in r['error'], r)
log_before = article(A['log'])['body']
a = call_a('reviewer2', 'odoo_context_apply_proposal', proposal_id=pid, decision='approve',
           note='checked against Odoo')
check('module: reviewer approves', a['ok'], a)
target = article(A['w1'])
today = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 8 * 3600))
check('target body replaced (with table)', 'Updated by test.' in target['body']
      and '<table' in target['body'], target['body'])
check('target verified-on = today (SGT) and verified-via = evidence',
      target['props'].get('ctx_verified_on') == today
      and target['props'].get('ctx_verified_via') == 'odoo_read(res.company, [%d])' % C['67'],
      target['props'])
check('target keeps its other properties', (target['props'].get('ctx_type'),
      target['props'].get('ctx_company_id'), target['props'].get('ctx_year_end'),
      target['props'].get('ctx_status')) == ('entity', C['67'], '31-07', 'approved'),
      target['props'])
log_after = article(A['log'])['body']
check('exactly one change-log row added', log_after.count('<tr>') - log_before.count('<tr>') == 1,
      log_after)
check('change-log row names proposer and approver', 'Contrib' in log_after and 'Reviewer2' in log_after,
      log_after)
check('proposal marked approved', article(pid)['props'].get('ctx_status') == 'approved')
r = call_a('reviewer2', 'odoo_context_apply_proposal', proposal_id=pid, decision='approve')
check('cannot apply the same proposal twice', not r['ok'], r)

if pid_b:
    bw_before = article(A['bw'])
    b = call_b('admin', 'odoo_context_apply_proposal', proposal_id=pid_b, decision='reject',
               note='not needed')
    check('server: reviewer rejects', b['ok'], b)
    prop = article(pid_b)
    check('rejected proposal moved under Rejected, still active, status rejected',
          prop['parent_id'][0] == A['rejected'] and prop['active']
          and prop['props'].get('ctx_status') == 'rejected', prop)
    check('rejected proposal kept its other properties',
          prop['props'].get('ctx_type') == 'proposal' and prop['props'].get('ctx_target') == A['bw'],
          prop['props'])
    check('target untouched by rejection', article(A['bw'])['write_date'] == bw_before['write_date'])

a = call_a('contrib', 'odoo_context_propose', new={
    'parent_id': A['entities'], 'title': 'Woolwich 2 Elimination',
    'properties': {'ctx_type': 'entity', 'ctx_company_id': C['68'], 'ctx_year_end': '31-07',
                   'ctx_parent_id': C['8'], 'ctx_currency': 'GBP'}},
    change='# Woolwich 2 Elimination\n\nNew entity page.', evidence='odoo_read', reason='new')
check('module: new-article proposal filed', a['ok'], a)
b = call_b('admin', 'odoo_context_apply_proposal', proposal_id=a['value']['proposal_id'],
           decision='approve') if a['ok'] else {'ok': False}
check('server: new article approved and created', b['ok'], b)
if b['ok']:
    new = article(b['value']['article_id'])
    check('new article has its properties and approved status',
          new['parent_id'][0] == A['entities'] and new['props'].get('ctx_company_id') == C['68']
          and new['props'].get('ctx_status') == 'approved', new['props'])
    r = call_a('contrib', 'odoo_context_for', company_id=C['68'])
    check('the new entity article is routed for 68', r['ok'] and b['value']['article_id'] in ids(r), r)

print('\n== workspace write guard (spec acceptance 9)')
calls = [
    ('odoo_write', {'model': 'knowledge.article', 'ids': [A['w1']], 'values': {'body': '<p>x</p>'}}),
    ('odoo_create', {'model': 'knowledge.article', 'values': {'name': 'x', 'parent_id': A['entities']}}),
    ('odoo_archive', {'model': 'knowledge.article', 'ids': [A['guide']]}),
    ('odoo_execute', {'model': 'knowledge.article', 'method': 'article_create',
                      'args': ['Sneaky', A['entities']]}),
    ('odoo_execute', {'model': 'knowledge.article', 'method': 'write',
                      'args': [[A['root']], {'name': 'x'}]}),
    ('odoo_write', {'model': 'knowledge.article.member',
                    'ids': admin('knowledge.article.member', 'search', [('article_id', '=', A['root'])])[:1],
                    'values': {'permission': 'write'}}),
]
for tool, args in calls:
    w_before = article(A['w1'])['write_date']
    a = call_a('admin', tool, **args)
    b = call_b('admin', tool, **args)
    check('guard refuses %s %s on both routes' % (tool, args.get('method', '')),
          not a['ok'] and not b['ok'] and 'odoo_context_propose' in a['error']
          and 'odoo_context_propose' in b['error'], (a, b))
check('nothing was written by refused calls', article(A['w1'])['write_date'] == w_before)
a = call_a('admin', 'odoo_write', model='knowledge.article', ids=[A['outside']], values={'name': 'Unrelated 2'})
b = call_b('admin', 'odoo_write', model='knowledge.article', ids=[A['outside']], values={'name': 'Unrelated 3'})
check('writes outside the workspace still work on both routes', a['ok'] and b['ok'], (a, b))
blocked = admin('custom.mcp.log', 'search_count', [('outcome', '=', 'blocked'),
                                                   ('tool', 'in', ['odoo_write', 'odoo_create',
                                                                   'odoo_archive', 'odoo_execute'])])
check('module activity log files guard refusals as blocked', blocked >= 6, blocked)

print('\n== performance (spec: under 3 s with all companies loaded)')
extra = []
for i in range(43):
    extra.append(admin('knowledge.article', 'create', {
        'name': 'Entity %d' % i, 'parent_id': A['entities'],
        'body': '<h1>Entity %d</h1>' % i + '<p>Lorem ipsum dolor sit amet.</p>' * 60}))
    admin('knowledge.article', 'create', {
        'name': 'Rulebook %d' % i, 'parent_id': SEED['articles']['root'] and A['entities'],
        'body': '<p>rules</p>' * 200})
timings = {}
for name, route in (('module', call_a), ('server', call_b)):
    started = time.monotonic()
    r = route('contrib', 'odoo_context_for', company_id=C['67'])
    timings[name] = time.monotonic() - started
    check('%s context_for with ~130 articles under 3 s (%.2f s)' % (name, timings[name]),
          r['ok'] and timings[name] < 3, r if not r['ok'] else '')

print('\n== summary')
failed = [r for r in RESULTS if not r[1]]
print('%d checks, %d passed, %d failed' % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
