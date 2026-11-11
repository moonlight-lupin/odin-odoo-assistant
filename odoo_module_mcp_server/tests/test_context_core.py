"""Tests for the Odoo-context core shared by both servers (``context_core``).

The core is plain Python over a small backend interface, so it is tested here
against an in-memory Odoo: the companies, parent chains and journals mirror
production (checked read-only on 03 Nov 2026), and the Knowledge workspace is
the article tree from the spec. Each acceptance test in the spec has a test
below; the guard, the company filter, the proposal flow and the HTML/markdown
conversion are pinned in detail.

The same core file ships in ``odoo-mcp/``; ``test_parity`` checks the two
copies are identical, so these tests cover both servers' logic.
"""
import copy
import datetime
import json
import re
import unittest

from . import _fake_odoo

core = _fake_odoo.load('context_core')

TODAY = datetime.date(2026, 11, 3)
ROOT = 100


# ---------------------------------------------------------------------------
# In-memory Odoo
# ---------------------------------------------------------------------------

def _company(cid, name, parent=None, root=None, path='', month='12', day=31):
    return {'id': cid, 'name': name, 'parent_id': [parent, 'P%d' % parent] if parent else False,
            'root_id': [root or cid, 'R%d' % (root or cid)], 'parent_path': path,
            'currency_id': [143, 'GBP'], 'fiscalyear_last_month': month,
            'fiscalyear_last_day': day, 'account_opening_date': '2025-08-01',
            'fiscalyear_lock_date': False, 'tax_lock_date': '2026-06-30',
            'sale_lock_date': False, 'purchase_lock_date': False, 'hard_lock_date': False}


COMPANIES = {c['id']: c for c in [
    _company(6, 'STRAITS HOLDCO II LIMITED', path='6/', month='7'),
    _company(7, 'QIP BERESFORD LIMITED', 6, 6, '6/7/', month='7'),
    _company(9, 'B WOOLWICH LIMITED', 7, 6, '6/7/9/', month='7'),
    _company(67, 'Woolwich 1 Elimination', 9, 6, '6/7/9/67/', month='7'),
    _company(8, 'QIP B WOOLWICH 2 LIMITED', 7, 6, '6/7/8/', month='7'),
    _company(68, 'Woolwich 2 Elimination', 8, 6, '6/7/8/68/', month='7'),
    _company(14, 'Q.I.P. SP6 UK HOLDCO LIMITED', path='14/'),
    _company(17, 'EDINBURGH (LONDON ROAD) SPV LIMITED', 14, 14, '14/17/'),
    _company(23, 'Edinburgh 1 Elimination', 17, 14, '14/17/23/'),
    _company(61, 'QIP DEVELOPMENT GROUP LLC', path='61/'),
]}


def _journal(jid, code, jtype, cid, seq=10):
    return {'id': jid, 'name': code, 'code': code, 'type': jtype,
            'company_id': [cid, COMPANIES[cid]['name']], 'sequence': seq, 'active': True}


JOURNALS = [
    _journal(29, 'BILL', 'purchase', 6), _journal(28, 'INV', 'sale', 6),
    _journal(30, 'MISC', 'general', 6, 5), _journal(76, 'BNK1', 'bank', 6),
    _journal(224, 'CJE', 'general', 6, 1), _journal(31, 'EXCH', 'general', 6, 2),
    _journal(49, 'BNK2', 'bank', 7),
    _journal(51, 'BNK4', 'bank', 9), _journal(220, 'BNK7', 'bank', 9),
    _journal(122, 'CJE', 'general', 14), _journal(130, 'MISC', 'general', 14),
    _journal(131, 'BILL', 'purchase', 14),
    _journal(202, 'CJE', 'general', 61),
]

STRUCTURE_PARTS = {'journals': 'Journals', 'chart': 'Chart of accounts', 'taxes': 'Taxes',
                   'banking': 'Banking', 'partners': 'Partners'}


def _workspace():
    """id -> article. ``defines`` marks a parent carrying the property definition."""
    a = {}

    def add(aid, name, parent, defines=False, body='', **props):
        a[aid] = {'id': aid, 'name': name, 'parent': parent, 'defines': defines,
                  'body': body or '<p>%s body</p>' % name, 'props': props,
                  'active': True, 'create_uid': [7, 'Agent']}

    add(ROOT, 'Finance – Odoo context', None, defines=True)
    ok = dict(ctx_status='approved', ctx_verified_on='2026-10-01',
              ctx_verified_via='odoo_read(res.company)')
    add(101, 'Read me first', ROOT, ctx_type='guide', **ok)
    add(102, 'Structures', ROOT, defines=True)
    add(103, 'shc-ii', 102, defines=True, ctx_type='structure', ctx_company_id=6,
        ctx_structure='shc-ii', **ok)
    for i, (kind, label) in enumerate(STRUCTURE_PARTS.items()):
        add(104 + i, label, 103, ctx_type=kind, ctx_structure='shc-ii',
            body='<h2>%s</h2><p>shc-ii %s, see MISC journal.</p>' % (label, kind), **ok)
    add(110, 'sp6', 102, defines=True, ctx_type='structure', ctx_company_id=14,
        ctx_structure='sp6', **ok)
    add(111, 'Journals', 110, ctx_type='journals', ctx_structure='sp6', **ok)
    add(112, 'Chart of accounts', 110, ctx_type='chart', ctx_structure='sp6', **ok)
    add(120, 'Entities', ROOT, defines=True)
    add(121, 'Woolwich 1 Elimination', 120, ctx_type='entity', ctx_company_id=67,
        ctx_year_end='31-07', ctx_parent_id=9, ctx_currency='GBP',
        body='<h1>Woolwich 1 Elimination</h1><p>Elimination shell for B Woolwich.</p>', **ok)
    add(122, 'Edinburgh 1 Elimination', 120, ctx_type='entity', ctx_company_id=23,
        ctx_year_end='31-12', ctx_parent_id=17, ctx_currency='GBP', **ok)
    add(123, 'B Woolwich', 120, ctx_type='entity', ctx_company_id=9,
        ctx_year_end='31-07', ctx_parent_id=7, ctx_currency='GBP', **ok)
    add(130, 'Posting rules', ROOT, defines=True)
    add(131, 'Woolwich 1 Elimination rulebook', 130, ctx_type='rulebook', ctx_company_id=67, **ok)
    add(132, 'Edinburgh 1 Elimination rulebook', 130, ctx_type='rulebook', ctx_company_id=23, **ok)
    add(140, 'Change log', ROOT, ctx_type='log', ctx_status='approved',
        body='<table><thead><tr><th>Date</th></tr></thead><tbody></tbody></table>')
    add(150, 'Proposals', ROOT, defines=True, ctx_type='proposals', ctx_status='approved')
    add(151, 'Rejected', 150, defines=True, ctx_type='rejected', ctx_status='approved')
    add(160, 'Stray note', 999)     # parent outside: no definition reaches it
    a[160]['parent'] = 120
    a[160]['no_props'] = True
    add(170, 'Unrelated', None)     # outside the workspace entirely
    return a


def _path(articles, aid):
    parts = []
    while aid is not None:
        parts.append(str(aid))
        aid = articles[aid]['parent']
    return '/'.join(reversed(parts)) + '/'


def _match(row, domain):
    """A small Odoo-domain evaluator (prefix | and implicit &)."""
    def term(t):
        field, op, value = t
        have = row.get(field)
        if isinstance(have, list) and field != 'article_properties':
            have = have[0] if have else False
        if op == '=':
            return have == value
        if op == '!=':
            return have != value
        if op == 'in':
            return have in value
        if op == '=like':
            return str(have or '').startswith(value.rstrip('%'))
        if op == 'ilike':
            return value.lower() in str(have or '').lower()
        raise AssertionError('unsupported operator %s' % op)

    stack = list(domain)

    def evaluate():
        token = stack.pop(0)
        if token == '|':
            left, right = evaluate(), evaluate()
            return left or right
        if token == '&':
            left, right = evaluate(), evaluate()
            return left and right
        return term(token)

    result = True
    while stack:
        result = evaluate() and result
    return result


class FakeBackend(core.Backend):
    """The in-memory Odoo above, seen as one user."""

    def __init__(self, company_ids=(6, 7, 8, 9, 14, 17, 23, 61, 67, 68), groups=(),
                 root=ROOT, articles=None, companies=None, readable=None, name='Agent'):
        self.companies = companies or copy.deepcopy(COMPANIES)
        self.journals = copy.deepcopy(JOURNALS)
        self.articles = articles or _workspace()
        self.company_ids = list(company_ids)
        self.groups = set(groups)
        self.root = root
        self.readable = readable          # None = every article readable
        self.name = name
        self.writes = []
        self.creates = []
        self.messages = []
        self.next_id = 500

    # -- rows as Odoo's read would return them ----------------------------
    def _article_row(self, aid):
        art = self.articles[aid]
        parent = self.articles.get(art['parent']) if art['parent'] else None
        properties = []
        if parent and parent['defines'] and not art.get('no_props'):
            for definition in core.PROPERTY_DEFINITION:
                prop = dict(definition)
                value = art['props'].get(definition['name'], False)
                if definition['type'] == 'many2one' and value:
                    value = [value, self.articles.get(value, {}).get('name', '?')]
                prop['value'] = value
                properties.append(prop)
        return {'id': aid, 'name': art['name'],
                'parent_id': [art['parent'], parent['name']] if parent else False,
                'parent_path': _path(self.articles, aid), 'article_properties': properties,
                'body': art['body'], 'active': art['active'], 'create_uid': art['create_uid']}

    def search_read(self, model, domain, fields, order=None, limit=None, all_companies=False):
        if model == core.MODEL:
            rows = [self._article_row(aid) for aid in sorted(self.articles)
                    if self.readable is None or aid in self.readable]
        elif model == 'res.company':
            rows = [dict(c) for c in self.companies.values() if c['id'] in self.company_ids]
        elif model == 'account.journal':
            rows = [dict(j) for j in self.journals
                    if j['company_id'][0] in self.company_ids]
            rows.sort(key=lambda j: (j['sequence'], j['id']))
        else:
            raise AssertionError('unexpected model %s' % model)
        rows = [r for r in rows if _match(r, domain)]
        if limit:
            rows = rows[:limit]
        return [{k: v for k, v in r.items() if k in fields or k == 'id'} for r in rows]

    def _set(self, aid, values):
        art = self.articles[aid]
        for key, value in values.items():
            if key == 'article_properties':
                # Odoo writes the dict against the (new) parent's definition.
                art['props'] = {k: v for k, v in value.items() if v not in (False, None)}
            elif key == 'parent_id':
                art['parent'] = value
            elif key == 'name':
                art['name'] = value
            elif key == 'body':
                art['body'] = value
            else:
                raise AssertionError('unexpected field %s' % key)

    def create(self, model, values, elevated=False):
        assert model == core.MODEL
        self.creates.append((dict(values), elevated))
        aid = self.next_id
        self.next_id += 1
        self.articles[aid] = {'id': aid, 'name': '', 'parent': None, 'defines': False,
                              'body': '', 'props': {}, 'active': True,
                              'create_uid': [7, self.name]}
        self._set(aid, values)
        return aid

    def write(self, model, ids, values):
        assert model == core.MODEL
        self.writes.append((list(ids), copy.deepcopy(values)))
        for aid in ids:
            self._set(aid, values)
        return True

    def post_message(self, article_id, body_html, partner_ids=()):
        self.messages.append((article_id, body_html, list(partner_ids)))

    def user(self):
        return {'id': 7, 'name': self.name, 'company_ids': list(self.company_ids)}

    def has_group(self, xmlid):
        if xmlid == core.CONTRIBUTOR_GROUP and core.REVIEWER_GROUP in self.groups:
            return True             # reviewer implies contributor
        return xmlid in self.groups

    def today(self):
        return TODAY

    def base_url(self):
        return 'https://odoo.example.test'

    def root_article_id(self):
        return self.root


CONTRIB = (core.CONTRIBUTOR_GROUP,)
REVIEW = (core.REVIEWER_GROUP,)
SP6_ONLY = (14, 17, 23)


def types_of(result):
    return [(a['type'], a['id']) for a in result['articles']]


# ---------------------------------------------------------------------------
# odoo_context_for — routing and the acceptance examples
# ---------------------------------------------------------------------------

class TestContextForRouting(unittest.TestCase):

    def test_woolwich_1_elimination(self):
        """Spec: odoo_context_for(67, "elimination")."""
        result = core.context_for(FakeBackend(), {'company_id': 67, 'task': 'elimination'})
        ids = {a['id'] for a in result['articles']}
        self.assertEqual(ids, {101, 121, 131, 104, 105, 108})   # guide, entity, rulebook, J/C/P
        self.assertNotIn(106, ids, 'elimination drops Taxes')
        self.assertNotIn(107, ids, 'elimination drops Banking')
        self.assertEqual(result['structure'], 'shc-ii')
        company = result['company']
        self.assertEqual((company['parent_id'], company['root_id'], company['year_end']),
                         (9, 6, '31 Jul'))
        self.assertIn('Year end 31 Jul: a July close is a year-end close.', result['warnings'])

    def test_edinburgh_1_gets_sp6_and_nothing_from_shc_ii(self):
        result = core.context_for(FakeBackend(), {'company_id': 23})
        ids = {a['id'] for a in result['articles']}
        self.assertIn(122, ids)
        self.assertIn(132, ids)
        self.assertTrue({111, 112} <= ids)
        self.assertFalse(ids & {103, 104, 105, 106, 107, 108, 121, 131})
        self.assertEqual(result['structure'], 'sp6')
        self.assertFalse(any('Year end' in w for w in result['warnings']),
                         'a December year end needs no warning')

    def test_no_task_returns_every_routed_type(self):
        result = core.context_for(FakeBackend(), {'company_id': 67})
        kinds = {a['type'] for a in result['articles']}
        self.assertEqual(kinds, {'guide', 'entity', 'rulebook', 'journals', 'chart',
                                 'taxes', 'banking', 'partners'})

    def test_bank_task_keeps_banking_and_the_rulebook(self):
        kinds = {a['type'] for a in core.context_for(
            FakeBackend(), {'company_id': 67, 'task': 'bank'})['articles']}
        self.assertEqual(kinds, {'guide', 'entity', 'rulebook', 'journals', 'banking'})

    def test_articles_come_back_as_markdown_with_links(self):
        result = core.context_for(FakeBackend(), {'company_id': 67, 'task': 'elimination'})
        entity = next(a for a in result['articles'] if a['type'] == 'entity')
        self.assertEqual(entity['markdown'],
                         '# Woolwich 1 Elimination\n\nElimination shell for B Woolwich.')
        self.assertEqual(entity['url'], 'https://odoo.example.test/knowledge/article/121')
        self.assertEqual(entity['verified_on'], '01 Oct 2026')

    def test_unknown_task_is_refused(self):
        with self.assertRaises(core.ContextError):
            core.context_for(FakeBackend(), {'company_id': 67, 'task': 'payroll'})

    def test_company_id_is_required(self):
        with self.assertRaises(core.ContextError):
            core.context_for(FakeBackend(), {})

    def test_a_company_outside_the_users_companies_is_refused(self):
        with self.assertRaises(core.ContextPolicyError) as caught:
            core.context_for(FakeBackend(company_ids=SP6_ONLY), {'company_id': 67})
        self.assertIn('not one of your allowed companies', str(caught.exception))

    def test_lock_and_opening_dates_are_reported(self):
        company = core.context_for(FakeBackend(), {'company_id': 67})['company']
        self.assertEqual(company['opening_date'], '01 Aug 2025')
        self.assertEqual(company['lock_dates'], {'tax': '30 Jun 2026'})

    def test_reads_never_write(self):
        backend = FakeBackend()
        core.context_for(backend, {'company_id': 67})
        core.lookup(backend, {'query': 'shc-ii'})
        self.assertEqual((backend.writes, backend.creates, backend.messages), ([], [], []))


class TestDriftAndFreshness(unittest.TestCase):

    def test_a_year_end_change_in_odoo_is_reported_not_written(self):
        backend = FakeBackend()
        backend.companies[67]['fiscalyear_last_month'] = '6'
        backend.companies[67]['fiscalyear_last_day'] = 30
        before = copy.deepcopy(backend.articles[121])
        warnings = core.context_for(backend, {'company_id': 67})['warnings']
        drift = [w for w in warnings if w.startswith('Drift:')]
        self.assertEqual(len(drift), 1)
        self.assertIn('year end is 30-06 in Odoo but 31-07', drift[0])
        self.assertEqual(backend.articles[121], before)
        self.assertEqual(backend.writes, [])

    def test_parent_and_currency_drift(self):
        backend = FakeBackend()
        backend.companies[67]['parent_id'] = [8, 'P8']
        backend.companies[67]['currency_id'] = [1, 'EUR']
        warnings = core.context_for(backend, {'company_id': 67})['warnings']
        self.assertTrue(any('parent company id is 8 in Odoo but 9' in w for w in warnings))
        self.assertTrue(any('currency is EUR in Odoo but GBP' in w for w in warnings))

    def test_missing_drift_properties_are_flagged(self):
        backend = FakeBackend()
        del backend.articles[121]['props']['ctx_currency']
        warnings = core.context_for(backend, {'company_id': 67})['warnings']
        self.assertTrue(any('no currency (ctx_currency)' in w for w in warnings))

    def test_stale_articles_are_flagged(self):
        backend = FakeBackend()
        backend.articles[104]['props']['ctx_verified_on'] = '2026-06-01'
        warnings = core.context_for(backend, {'company_id': 67})['warnings']
        self.assertTrue(any('"Journals" is stale: last verified 01 Jun 2026' in w
                            for w in warnings))

    def test_stale_threshold_is_configurable(self):
        backend = FakeBackend()
        backend.stale_days = lambda: 10
        warnings = core.context_for(backend, {'company_id': 67})['warnings']
        self.assertTrue(sum('is stale' in w for w in warnings) >= 6)

    def test_a_missing_entity_article_is_flagged(self):
        backend = FakeBackend()
        backend.articles[121]['props']['ctx_status'] = 'proposed'
        warnings = core.context_for(backend, {'company_id': 67})['warnings']
        self.assertIn('No approved entity article found.', warnings)

    def test_untyped_articles_are_counted(self):
        """Spec: an article whose parent has no definition is not returned, with a warning."""
        backend = FakeBackend()
        backend.articles[160]['parent'] = 104        # under a child without a definition
        result = core.context_for(backend, {'company_id': 67})
        self.assertNotIn(160, {a['id'] for a in result['articles']})
        self.assertTrue(any('1 workspace article(s) have no context properties' in w
                            for w in result['warnings']))

    def test_grandchildren_are_found_by_their_properties(self):
        """Spec: shc-ii → Journals, two levels below the root, is routed."""
        result = core.context_for(FakeBackend(), {'company_id': 67})
        self.assertIn(104, {a['id'] for a in result['articles']})


# ---------------------------------------------------------------------------
# Journal resolution
# ---------------------------------------------------------------------------

class TestJournals(unittest.TestCase):

    def journals(self, company_id, **kwargs):
        return core.context_for(FakeBackend(**kwargs), {'company_id': company_id})['journals']

    def test_woolwich_2_consolidation_is_cje_224_shared(self):
        """Spec: resolve for 68 → CJE (224), owner 6, shared."""
        cje = self.journals(68)['consolidation']
        self.assertEqual((cje['id'], cje['code'], cje['company_id'], cje['shared']),
                         (224, 'CJE', 6, True))
        self.assertIn('set company_id', cje['procedure'])

    def test_edinburgh_consolidation_is_cje_122_never_224_or_202(self):
        cje = self.journals(23)['consolidation']
        self.assertEqual((cje['id'], cje['company_id']), (122, 14))

    def test_bank_is_only_ever_the_companys_own(self):
        bank = self.journals(9)['bank']
        self.assertEqual(sorted(j['id'] for j in bank), [51, 220])
        self.assertTrue(all(j['company_id'] == 9 and not j['shared'] for j in bank))

    def test_no_own_bank_journal_is_an_error_not_a_parent_journal(self):
        bank = self.journals(67)['bank']
        self.assertIn('error', bank)
        self.assertIn('never resolved from another company', bank['error'])

    def test_general_skips_consolidation_and_fx_journals(self):
        general = self.journals(67)['general']
        self.assertEqual((general['id'], general['code'], general['shared']), (30, 'MISC', True))
        self.assertNotIn('alternatives', general)

    def test_own_journal_is_preferred_and_not_shared(self):
        general = self.journals(14)['general']
        self.assertEqual((general['id'], general['shared']), (130, False))
        self.assertEqual(general['procedure'], core.OWN_PROCEDURE)

    def test_nearest_ancestor_wins(self):
        backend = FakeBackend()
        backend.journals.append(_journal(900, 'BILL9', 'purchase', 9))
        purchase = core.context_for(backend, {'company_id': 67})['journals']['purchase']
        self.assertEqual((purchase['id'], purchase['company_id']), (900, 9))

    def test_several_candidates_are_listed_as_alternatives(self):
        backend = FakeBackend()
        backend.journals.append(_journal(901, 'MISC2', 'general', 6, 50))
        general = core.context_for(backend, {'company_id': 6})['journals']['general']
        self.assertEqual(general['id'], 30)
        self.assertEqual([a['id'] for a in general['alternatives']], [901])

    def test_inaccessible_owner_is_an_error_naming_the_ancestors(self):
        """Spec: a user without access to the owning company gets an error."""
        journals = self.journals(67, company_ids=(67,))
        self.assertIn('error', journals['general'])
        self.assertIn('9, 7, 6', journals['general']['error'])
        self.assertIn('error', journals['consolidation'])

    def test_no_consolidation_entry_when_no_ancestor_has_one(self):
        backend = FakeBackend()
        backend.journals = [j for j in backend.journals if j['id'] != 224]
        self.assertNotIn('consolidation', core.context_for(
            backend, {'company_id': 67})['journals'])


# ---------------------------------------------------------------------------
# Workspace access and the company filter
# ---------------------------------------------------------------------------

class TestAccess(unittest.TestCase):

    def test_no_workspace_access_gets_no_articles_but_still_facts_and_journals(self):
        backend = FakeBackend(readable=set())
        result = core.context_for(backend, {'company_id': 67})
        self.assertEqual(result['articles'], [])
        self.assertEqual(result['company']['id'], 67)
        self.assertEqual(result['journals']['consolidation']['id'], 224)
        self.assertTrue(any('cannot open the Odoo context workspace' in w
                            for w in result['warnings']))
        with self.assertRaises(core.ContextError):
            core.lookup(backend, {'query': 'Journals'})
        with self.assertRaises(core.ContextError):
            core.lookup(backend, {'article_id': 101})

    def test_unconfigured_workspace(self):
        backend = FakeBackend(root=None)
        result = core.context_for(backend, {'company_id': 67})
        self.assertEqual(result['articles'], [])
        self.assertTrue(any('not configured' in w for w in result['warnings']))

    def test_sp6_only_user_sees_no_shc_ii_or_woolwich_articles(self):
        backend = FakeBackend(company_ids=SP6_ONLY)
        hits = core.lookup(backend, {'query': 'o'})['results']
        ids = {h['id'] for h in hits}
        self.assertFalse(ids & {103, 104, 105, 106, 107, 108, 121, 123, 131})
        self.assertTrue({101, 111, 122} <= ids)

    def test_hidden_articles_read_as_not_found(self):
        backend = FakeBackend(company_ids=SP6_ONLY)
        for article_id in (121, 104, 103, 99999):
            with self.subTest(article=article_id):
                with self.assertRaises(core.ContextError) as caught:
                    core.lookup(backend, {'article_id': article_id})
                self.assertEqual(str(caught.exception),
                                 'Article %d not found in the Odoo context workspace.'
                                 % article_id)

    def test_general_articles_reach_everyone_with_workspace_access(self):
        backend = FakeBackend(company_ids=SP6_ONLY)
        self.assertEqual(core.lookup(backend, {'article_id': 101})['id'], 101)

    def test_structure_access_follows_any_company_in_the_structure(self):
        """A user allowed into Woolwich 1 only still sees the shc-ii structure pages."""
        backend = FakeBackend(company_ids=(67,))
        self.assertEqual(core.lookup(backend, {'article_id': 104})['id'], 104)
        with self.assertRaises(core.ContextError):
            core.lookup(backend, {'article_id': 123})     # B Woolwich's own entity page

    def test_articles_outside_the_workspace_are_not_found(self):
        with self.assertRaises(core.ContextError):
            core.lookup(FakeBackend(), {'article_id': 170})


# ---------------------------------------------------------------------------
# odoo_context_lookup
# ---------------------------------------------------------------------------

class TestLookup(unittest.TestCase):

    def test_exactly_one_of_query_or_article_id(self):
        for args in ({}, {'query': 'x', 'article_id': 101}, {'query': '  '}):
            with self.subTest(args=args):
                with self.assertRaises(core.ContextError):
                    core.lookup(FakeBackend(), args)

    def test_search_ranks_title_matches_first_and_snippets(self):
        result = core.lookup(FakeBackend(), {'query': 'Journals'})
        self.assertEqual(result['results'][0]['title'], 'Journals')
        self.assertIn('Journals', result['results'][0]['snippet'])

    def test_filters_by_structure_and_type(self):
        results = core.lookup(FakeBackend(), {'query': 'Journals', 'structure': 'sp6'})['results']
        self.assertEqual([r['id'] for r in results], [111])
        results = core.lookup(FakeBackend(), {'query': 'body', 'ctx_type': 'rulebook'})['results']
        self.assertEqual({r['type'] for r in results}, {'rulebook'})

    def test_proposals_are_left_out_unless_asked_for(self):
        backend = FakeBackend(groups=CONTRIB)
        core.propose(backend, {'target_article_id': 121, 'change': 'New Woolwich text',
                               'evidence': 'odoo_read(res.company, 67)', 'reason': 'test'})
        query = {'query': 'Woolwich'}
        self.assertFalse(any(r['type'] == 'proposal' for r in core.lookup(backend, query)['results']))
        with_proposals = core.lookup(backend, dict(query, include_proposals=True))['results']
        self.assertTrue(any(r['type'] == 'proposal' for r in with_proposals))

    def test_read_one_returns_markdown_and_properties(self):
        out = core.lookup(FakeBackend(), {'article_id': 121})
        self.assertEqual(out['properties']['ctx_company_id'], 67)
        self.assertEqual(out['properties']['ctx_verified_on'], '01 Oct 2026')
        self.assertTrue(out['markdown'].startswith('# Woolwich 1 Elimination'))

    def test_unapproved_articles_are_not_returned(self):
        backend = FakeBackend()
        backend.articles[121]['props']['ctx_status'] = 'rejected'
        with self.assertRaises(core.ContextError):
            core.lookup(backend, {'article_id': 121})

    def test_limit_is_bounded_and_validated(self):
        self.assertLessEqual(core.lookup(FakeBackend(), {'query': 'o', 'limit': 2})['count'], 2)
        with self.assertRaises(core.ContextError):
            core.lookup(FakeBackend(), {'query': 'o', 'limit': 'ten'})
        with self.assertRaises(core.ContextError):
            core.lookup(FakeBackend(), {'query': 'o', 'ctx_type': 'nonsense'})


# ---------------------------------------------------------------------------
# odoo_context_propose
# ---------------------------------------------------------------------------

def _propose(backend, **extra):
    args = {'target_article_id': 121, 'change': '# Woolwich 1 Elimination\n\nUpdated.',
            'evidence': 'odoo_read(res.company, [67])', 'reason': 'Year end confirmed'}
    args.update(extra)
    return core.propose(backend, args)


class TestPropose(unittest.TestCase):

    def test_contributor_files_a_proposal_and_the_target_is_unchanged(self):
        backend = FakeBackend(groups=CONTRIB)
        before = copy.deepcopy(backend.articles[121])
        out = _propose(backend)
        self.assertEqual(out['status'], 'proposed')
        values, elevated = backend.creates[0]
        self.assertTrue(elevated, 'the module creates the proposal with elevated rights')
        self.assertEqual(values['parent_id'], 150)
        self.assertEqual(values['article_properties']['ctx_type'], 'proposal')
        self.assertEqual(values['article_properties']['ctx_status'], 'proposed')
        self.assertEqual(values['article_properties']['ctx_target'], 121)
        self.assertEqual(values['article_properties']['ctx_company_id'], 67)
        self.assertEqual(backend.articles[121], before)
        self.assertEqual(backend.writes, [])
        self.assertEqual(len(backend.messages), 1, 'reviewers are notified')

    def test_the_proposal_carries_reason_evidence_and_a_machine_copy(self):
        backend = FakeBackend(groups=CONTRIB)
        out = _propose(backend, change='Line with <b>tags</b> & "quotes"')
        body = backend.articles[out['proposal_id']]['body']
        self.assertIn('Year end confirmed', body)
        self.assertIn('odoo_read(res.company, [67])', body)
        self.assertIn('class="o_ctx_change"', body)
        self.assertNotIn('<b>tags</b>', body.split('o_ctx_change')[1],
                         'the machine copy is escaped')

    def test_non_contributors_are_refused(self):
        with self.assertRaises(core.ContextPolicyError):
            _propose(FakeBackend())

    def test_other_company_target_is_refused(self):
        """Spec: an SP6-only user proposing against a Woolwich article is refused."""
        backend = FakeBackend(company_ids=SP6_ONLY, groups=CONTRIB)
        with self.assertRaises(core.ContextPolicyError) as caught:
            _propose(backend)
        self.assertIn('outside your allowed companies', str(caught.exception))
        self.assertEqual(backend.creates, [])

    def test_targets_must_be_approved_context_articles_in_the_workspace(self):
        backend = FakeBackend(groups=CONTRIB)
        backend.articles[122]['props']['ctx_status'] = 'proposed'
        for target in (170, 99999, 140, 150, 122):
            with self.subTest(target=target):
                with self.assertRaises(core.ContextPolicyError):
                    _propose(backend, target_article_id=target)

    def test_required_inputs(self):
        backend = FakeBackend(groups=CONTRIB)
        for missing in ('change', 'evidence', 'reason'):
            with self.subTest(missing=missing):
                with self.assertRaises(core.ContextError):
                    _propose(backend, **{missing: ''})
        with self.assertRaises(core.ContextError):
            _propose(backend, new={'parent_id': 120, 'title': 'X',
                                   'properties': {'ctx_type': 'entity'}})
        with self.assertRaises(core.ContextError):
            _propose(backend, target_article_id=None)

    def test_new_article_proposal(self):
        backend = FakeBackend(groups=CONTRIB)
        out = _propose(backend, target_article_id=None, new={
            'parent_id': 120, 'title': 'Woolwich 2 Elimination',
            'properties': {'ctx_type': 'entity', 'ctx_company_id': 68,
                           'ctx_status': 'approved'}})
        body = backend.articles[out['proposal_id']]['body']
        spec = json.loads(re.search(r'class="o_ctx_new">(.*?)</pre>', body).group(1)
                          .replace('&quot;', '"'))
        self.assertEqual(spec['parent_id'], 120)
        self.assertNotIn('ctx_status', spec['properties'], 'status is set on approval only')

    def test_new_article_validation(self):
        backend = FakeBackend(groups=CONTRIB, company_ids=SP6_ONLY)
        bad = [
            {'parent_id': 170, 'title': 'X', 'properties': {'ctx_type': 'entity'}},
            {'parent_id': 120, 'title': 'X', 'properties': {'ctx_type': 'log'}},
            {'parent_id': 120, 'title': 'X', 'properties': {'ctx_type': 'entity', 'bogus': 1}},
            {'parent_id': 120, 'title': '', 'properties': {'ctx_type': 'entity'}},
            {'parent_id': 150, 'title': 'X', 'properties': {'ctx_type': 'entity'}},
            {'parent_id': 120, 'title': 'X',
             'properties': {'ctx_type': 'entity', 'ctx_company_id': 67}},
        ]
        for new in bad:
            with self.subTest(new=new):
                with self.assertRaises(core.ContextError):
                    _propose(backend, target_article_id=None, new=new)

    def test_no_proposals_folder(self):
        backend = FakeBackend(groups=CONTRIB)
        backend.articles[150]['props']['ctx_type'] = False
        with self.assertRaises(core.ContextError):
            _propose(backend)

    def test_notification_failure_does_not_lose_the_proposal(self):
        backend = FakeBackend(groups=CONTRIB)

        def boom(*a, **k):
            raise RuntimeError('mail down')
        backend.post_message = boom
        out = _propose(backend)
        self.assertIn('could not be notified', out['warning'])
        self.assertIn(out['proposal_id'], backend.articles)


# ---------------------------------------------------------------------------
# odoo_context_apply_proposal
# ---------------------------------------------------------------------------

class TestApply(unittest.TestCase):

    def setUp(self):
        self.backend = FakeBackend(groups=REVIEW, name='Reviewer')
        self.proposal = _propose(self.backend)['proposal_id']

    def apply(self, decision='approve', **extra):
        args = {'proposal_id': self.proposal, 'decision': decision, 'note': 'checked'}
        args.update(extra)
        return core.apply_proposal(self.backend, args)

    def test_contributors_cannot_apply(self):
        self.backend.groups = set(CONTRIB)
        with self.assertRaises(core.ContextPolicyError):
            self.apply()
        self.assertEqual(self.backend.articles[121]['props']['ctx_verified_on'], '2026-10-01')

    def test_approve_updates_target_sets_verified_and_logs_one_row(self):
        out = self.apply()
        target = self.backend.articles[121]
        self.assertEqual(target['body'], '<h1>Woolwich 1 Elimination</h1><p>Updated.</p>')
        self.assertEqual(target['props']['ctx_verified_on'], '2026-11-03')
        self.assertEqual(target['props']['ctx_verified_via'], 'odoo_read(res.company, [67])')
        self.assertEqual(out['verified_on'], '03 Nov 2026')
        log = self.backend.articles[140]['body']
        self.assertEqual(log.count('<tr><td>'), 1)
        self.assertIn('03 Nov 2026', log)
        self.assertIn('Reviewer', log)
        self.assertEqual(self.backend.articles[self.proposal]['props']['ctx_status'], 'approved')

    def test_approval_keeps_the_other_properties(self):
        self.apply()
        props = self.backend.articles[121]['props']
        self.assertEqual((props['ctx_type'], props['ctx_company_id'], props['ctx_year_end'],
                          props['ctx_status']), ('entity', 67, '31-07', 'approved'))
        proposal = self.backend.articles[self.proposal]['props']
        self.assertEqual((proposal['ctx_type'], proposal['ctx_target']), ('proposal', 121))

    def test_a_proposal_cannot_be_applied_twice(self):
        self.apply()
        with self.assertRaises(core.ContextPolicyError):
            self.apply()

    def test_reject_files_under_rejected_and_leaves_target(self):
        before = copy.deepcopy(self.backend.articles[121])
        out = self.apply('reject')
        proposal = self.backend.articles[self.proposal]
        self.assertEqual((out['status'], proposal['parent'], proposal['props']['ctx_status']),
                         ('rejected', 151, 'rejected'))
        self.assertTrue(proposal['active'], 'rejected proposals are kept, not archived')
        self.assertEqual(self.backend.articles[121], before)
        self.assertNotIn('<tr><td>', self.backend.articles[140]['body'])

    def test_reviewer_outside_the_company_is_refused(self):
        self.backend.company_ids = list(SP6_ONLY)
        with self.assertRaises(core.ContextPolicyError):
            self.apply()

    def test_amended_machine_copy_is_what_gets_applied(self):
        body = self.backend.articles[self.proposal]['body']
        self.backend.articles[self.proposal]['body'] = re.sub(
            r'(class="o_ctx_change">).*?(</pre>)', r'\1Reviewer amended text\2', body, flags=re.S)
        self.apply()
        self.assertEqual(self.backend.articles[121]['body'], '<p>Reviewer amended text</p>')

    def test_new_article_is_created_approved_and_logged(self):
        proposal = _propose(self.backend, target_article_id=None, new={
            'parent_id': 120, 'title': 'Woolwich 2 Elimination',
            'properties': {'ctx_type': 'entity', 'ctx_company_id': 68}})['proposal_id']
        out = core.apply_proposal(self.backend, {'proposal_id': proposal, 'decision': 'approve'})
        created = self.backend.articles[out['article_id']]
        self.assertEqual((created['parent'], created['name']), (120, 'Woolwich 2 Elimination'))
        self.assertEqual(created['props']['ctx_status'], 'approved')
        self.assertEqual(created['props']['ctx_company_id'], 68)
        self.assertIn('Woolwich 2 Elimination', self.backend.articles[140]['body'])

    def test_bad_inputs(self):
        for args in ({'proposal_id': self.proposal, 'decision': 'maybe'},
                     {'proposal_id': 121, 'decision': 'approve'},
                     {'proposal_id': 'x', 'decision': 'approve'}):
            with self.subTest(args=args):
                with self.assertRaises(core.ContextError):
                    core.apply_proposal(self.backend, args)

    def test_missing_change_log_still_applies_with_a_warning(self):
        self.backend.articles[140]['props']['ctx_type'] = False
        out = self.apply()
        self.assertIn('no Change log article', out['warning'])


# ---------------------------------------------------------------------------
# The workspace write guard
# ---------------------------------------------------------------------------

class TestGuard(unittest.TestCase):

    def setUp(self):
        self.backend = FakeBackend()
        self.root = ROOT

    def paths(self, model, ids):
        if model == core.MODEL:
            return {i: _path(self.backend.articles, i) for i in ids if i in self.backend.articles}
        members = {900: 121, 901: 170}
        return {i: _path(self.backend.articles, members[i]) for i in ids if i in members}

    def records(self, model, ids=None, values=None, root=ROOT):
        core.guard_records(model, ids, values, lambda: root, self.paths)

    def execute(self, model, method, args, kwargs=None, root=ROOT):
        core.guard_execute(model, method, args, kwargs or {}, lambda: root, self.paths)

    def test_writes_to_workspace_articles_are_refused(self):
        for ids in ([121], [100], [170, 104]):
            with self.subTest(ids=ids):
                with self.assertRaises(core.ContextPolicyError) as caught:
                    self.records(core.MODEL, ids, {'body': 'x'})
                self.assertIn('odoo_context_propose', str(caught.exception))

    def test_creating_under_the_workspace_is_refused(self):
        with self.assertRaises(core.ContextPolicyError):
            self.records(core.MODEL, None, {'name': 'x', 'parent_id': 120})
        with self.assertRaises(core.ContextPolicyError):
            self.records(core.MODEL, None, [{'name': 'a'}, {'name': 'b', 'parent_id': 100}])

    def test_outside_the_workspace_is_allowed(self):
        self.records(core.MODEL, [170], {'body': 'x', 'sequence': 121})
        self.records(core.MODEL, None, {'name': 'x', 'parent_id': 170})
        self.records(core.MODEL, None, {'name': 'x'})
        self.records('res.partner', [121], {'name': 'x'})

    def test_member_rows_are_guarded(self):
        with self.assertRaises(core.ContextPolicyError):
            self.records('knowledge.article.member', [900], {'permission': 'write'})
        with self.assertRaises(core.ContextPolicyError):
            self.records('knowledge.article.member', None, {'article_id': 121, 'partner_id': 3})
        self.records('knowledge.article.member', [901], {'permission': 'write'})

    def test_execute_refuses_any_non_read_method_touching_the_workspace(self):
        for method, args, kwargs in (
                ('write', [[121], {'name': 'x'}], {}),
                ('action_archive', [[104]], {}),
                ('move_to', [[170]], {'parent_id': 120}),
                ('article_create', ['Title', 120], {}),
                ('message_post', [[121]], {'body': 'x'})):
            with self.subTest(method=method):
                with self.assertRaises(core.ContextPolicyError):
                    self.execute(core.MODEL, method, args, kwargs)

    def test_execute_reads_and_outside_calls_pass(self):
        self.execute(core.MODEL, 'read', [[121]])
        self.execute(core.MODEL, 'search_read', [[('id', '=', 121)]])
        self.execute(core.MODEL, 'write', [[170], {'name': 'x'}])
        self.execute('res.partner', 'write', [[121], {'name': 'x'}])

    def test_guard_is_off_until_a_root_is_set(self):
        self.records(core.MODEL, [121], {'body': 'x'}, root=None)
        self.execute(core.MODEL, 'write', [[121], {}], root=None)

    def test_in_workspace_matches_path_segments_not_prefixes(self):
        self.assertTrue(core.in_workspace('100/120/121/', 100))
        self.assertTrue(core.in_workspace('5/100/120/', 100))
        self.assertFalse(core.in_workspace('1000/1/', 100))
        self.assertFalse(core.in_workspace('10/', 100))
        self.assertFalse(core.in_workspace(None, 100))


# ---------------------------------------------------------------------------
# HTML ↔ markdown
# ---------------------------------------------------------------------------

class TestMarkdown(unittest.TestCase):

    def test_common_knowledge_markup(self):
        html_body = (
            '<h1>Journals</h1><p>Use <strong>MISC</strong> for <em>accruals</em>, '
            'code <code>MISC</code>. See <a href="/knowledge/article/12">the chart</a>.</p>'
            '<ul><li>One</li><li>Two<ul><li>Two a</li></ul></li></ul>'
            '<ol><li>First</li><li>Second</li></ol>'
            '<table><thead><tr><th>Code</th><th>Name</th></tr></thead>'
            '<tbody><tr><td>CJE</td><td>Consolidation | elim</td></tr></tbody></table>'
            '<blockquote>Note this</blockquote><pre>a  b\n  c</pre><hr/>'
            '<script>alert(1)</script><p>End</p>')
        md = core.html_to_markdown(html_body, 'https://odoo.example.test/')
        self.assertEqual(md, '\n\n'.join([
            '# Journals',
            'Use **MISC** for *accruals*, code `MISC`. See '
            '[the chart](https://odoo.example.test/knowledge/article/12).',
            '- One\n- Two\n  - Two a',
            '1. First\n2. Second',
            '| Code | Name |\n|---|---|\n| CJE | Consolidation \\| elim |',
            '> Note this',
            '```\na  b\n  c\n```',
            '---',
            'End',
        ]))

    def test_empty_and_plain(self):
        self.assertEqual(core.html_to_markdown(''), '')
        self.assertEqual(core.html_to_markdown(None), '')
        self.assertEqual(core.html_to_markdown('just text'), 'just text')

    def test_markdown_to_html_round_trips(self):
        md = '\n\n'.join([
            '## Posting rules', 'Use **MISC** and `CJE` — see [chart](https://x.test/a).',
            '- One\n- Two\n  - Two a', '1. First\n2. Second',
            '| Code | Name |\n|---|---|\n| CJE | Consolidation \\| elim |',
            '> Quote', '```\ncode  here\n```', '---', 'Tail <b> & stuff'])
        html_body = core.markdown_to_html(md)
        self.assertIn('&lt;b&gt; &amp; stuff', html_body, 'text is escaped')
        self.assertEqual(core.html_to_markdown(html_body), md)

    def test_tables_with_header_only_and_ragged_rows(self):
        md = core.html_to_markdown('<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>')
        self.assertEqual(md, '| a | b |\n|---|---|\n| c |  |')


class TestDates(unittest.TestCase):

    def test_house_style(self):
        self.assertEqual(core.fmt_date('2026-11-03'), '03 Nov 2026')
        self.assertEqual(core.fmt_date(datetime.date(2026, 7, 31)), '31 Jul 2026')
        self.assertIsNone(core.fmt_date(False))
        self.assertIsNone(core.fmt_date('not a date'))


if __name__ == '__main__':
    unittest.main()
