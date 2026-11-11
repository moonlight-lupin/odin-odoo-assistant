"""The module's wiring of the Odoo-context tools and the workspace guard.

``test_context_core`` pins the decisions; this file pins that the module
applies them: every generic write path consults the guard and refuses before
the ORM is touched, the guard reads the root from settings and is off until
it is set, the four tools are registered with the shared schemas, and a
refusal is logged as ``blocked``.
"""
import types
import unittest

from . import _fake_odoo

if _fake_odoo.real_odoo_present():                          # pragma: no cover
    raise unittest.SkipTest("fake-Odoo suite; skipped when a real Odoo is present")

_fake_odoo.install()
generic_tools = _fake_odoo.load('generic_tools')
context_tools = _fake_odoo.load('context_tools')
context_core = _fake_odoo.load('context_core')
log_utils = _fake_odoo.load('log_utils')
mcp_registry = _fake_odoo.load('mcp_registry')

FakeEnv = _fake_odoo.FakeEnv
ROOT_KEY = 'odoo_module_mcp_server.context_root_article_id'
PATHS = {100: '100/', 120: '100/120/', 121: '100/120/121/', 170: '170/'}
MEMBERS = {900: 121, 901: 170}


def env(root='100'):
    params = {'odoo_module_mcp_server.protect_structural': 'True'}
    if root is not None:
        params[ROOT_KEY] = root
    return FakeEnv(params=params)


class GuardTestCase(unittest.TestCase):

    def setUp(self):
        self.original = context_tools.paths_of
        self.lookups = []

        def paths_of(env_, model, ids):
            self.lookups.append((model, list(ids)))
            if model == context_core.MODEL:
                return {i: PATHS[i] for i in ids if i in PATHS}
            return {i: PATHS[MEMBERS[i]] for i in ids if i in MEMBERS}
        context_tools.paths_of = paths_of

    def tearDown(self):
        context_tools.paths_of = self.original


class TestGenericToolsConsultTheGuard(GuardTestCase):

    def assertRefused(self, e, model, call):
        with self.assertRaises(context_core.ContextPolicyError):
            call()
        mutations = [c for c in e[model].calls
                     if c[0] in ('write', 'create', 'unlink', 'call_kw',
                                 'action_cancel', 'button_cancel')]
        self.assertEqual(mutations, [], 'refused before the ORM was touched')

    def test_write(self):
        e = env()
        self.assertRefused(e, 'knowledge.article', lambda: generic_tools.odoo_write(
            e, {'model': 'knowledge.article', 'ids': [121], 'values': {'body': 'x'}}))

    def test_create_under_the_workspace(self):
        e = env()
        self.assertRefused(e, 'knowledge.article', lambda: generic_tools.odoo_create(
            e, {'model': 'knowledge.article', 'values': {'name': 'x', 'parent_id': 120}}))

    def test_archive_cancel_unlink(self):
        for tool in (generic_tools.odoo_archive, generic_tools.odoo_cancel):
            with self.subTest(tool=tool.__name__):
                e = env()
                self.assertRefused(e, 'knowledge.article', lambda: tool(
                    e, {'model': 'knowledge.article', 'ids': [121]}))
        e = FakeEnv(params={ROOT_KEY: '100',
                            'odoo_module_mcp_server.allow_record_deletion': 'True'})
        self.assertRefused(e, 'knowledge.article', lambda: generic_tools.odoo_unlink(
            e, {'model': 'knowledge.article', 'ids': [121]}))

    def test_execute(self):
        e = env()
        e['knowledge.article'].workflow_methods.add('move_to')
        self.assertRefused(e, 'knowledge.article', lambda: generic_tools.odoo_execute(
            e, {'model': 'knowledge.article', 'method': 'move_to',
                'args': [[170]], 'kwargs': {'parent_id': 120}}))

    def test_members(self):
        e = env()
        self.assertRefused(e, 'knowledge.article.member', lambda: generic_tools.odoo_write(
            e, {'model': 'knowledge.article.member', 'ids': [900],
                'values': {'permission': 'write'}}))

    def test_outside_the_workspace_goes_through(self):
        e = env()
        generic_tools.odoo_write(e, {'model': 'knowledge.article', 'ids': [170],
                                     'values': {'name': 'x'}})
        self.assertIn(('write', [170], {'name': 'x'}), e['knowledge.article'].calls)

    def test_other_models_never_look(self):
        e = env()
        generic_tools.odoo_write(e, {'model': 'res.partner', 'ids': [121], 'values': {}})
        self.assertEqual(self.lookups, [])

    def test_off_until_a_root_is_set(self):
        for root in (None, '', '0', 'garbled'):
            with self.subTest(root=root):
                e = env(root)
                generic_tools.odoo_write(e, {'model': 'knowledge.article', 'ids': [121],
                                             'values': {'name': 'x'}})
                self.assertEqual(self.lookups, [])

    def test_refusals_are_logged_as_blocked(self):
        error = context_core.ContextPolicyError(context_core.GUARD_MESSAGE)
        self.assertEqual(log_utils.classify_error(error), 'blocked')
        self.assertEqual(log_utils.classify_error(
            context_core.ContextPolicyError('Refusing company 67')), 'blocked')


class TestRegistration(unittest.TestCase):

    NAMES = ('odoo_context_for', 'odoo_context_lookup', 'odoo_context_propose',
             'odoo_context_apply_proposal')

    def test_registered_with_the_shared_schemas_and_text(self):
        tools = mcp_registry.McpRegistry.tools()
        for name in self.NAMES:
            with self.subTest(tool=name):
                self.assertEqual(tools[name]['input_schema'], context_core.TOOL_SCHEMAS[name])
                self.assertEqual(tools[name]['description'], context_core.TOOL_DOCS[name])

    def test_tools_hand_a_backend_and_the_arguments_to_the_core(self):
        seen = {}
        originals = {}
        for name in ('context_for', 'lookup', 'propose', 'apply_proposal'):
            originals[name] = getattr(context_core, name)
            setattr(context_core, name,
                    lambda backend, args, _n=name: seen.setdefault(_n, (backend, args)))
        try:
            e = env()
            context_tools.odoo_context_for(e, {'company_id': 67})
            context_tools.odoo_context_lookup(e, {'query': 'x'})
            context_tools.odoo_context_propose(e, {'change': 'c'})
            context_tools.odoo_context_apply_proposal(e, {'proposal_id': 1})
        finally:
            for name, fn in originals.items():
                setattr(context_core, name, fn)
        self.assertEqual(set(seen), {'context_for', 'lookup', 'propose', 'apply_proposal'})
        for backend, _args in seen.values():
            self.assertIsInstance(backend, context_tools.OdooBackend)

    def test_backend_settings(self):
        e = env('345')
        e.params[context_tools.STALE_PARAM] = '30'
        backend = context_tools.OdooBackend(e)
        self.assertEqual(backend.root_article_id(), 345)
        self.assertEqual(backend.stale_days(), 30)
        for raw in (None, '0', '-5', 'x'):
            with self.subTest(raw=raw):
                e.params[context_tools.STALE_PARAM] = raw
                self.assertEqual(backend.stale_days(), context_core.DEFAULT_STALE_DAYS)


if __name__ == '__main__':
    unittest.main()
