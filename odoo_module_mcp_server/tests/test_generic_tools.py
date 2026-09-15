"""Tests for the module's tool layer — the guardrails above all.

The module ships the same safety posture as the external server: no hard
deletes on any model, and no writes to structural / auth / code-bearing
models. On the external server that posture is pinned by ~40 tests; here it
had none, which is the gap this file closes.

What is asserted, in priority order:

1. **Deletion is refused everywhere** by default, through every path that
   could reach ``unlink`` — including ``odoo_execute``.
2. **Structural models are not writable**, through every write tool, and the
   refusal happens *before* anything reaches the ORM.
3. **Reads are never blocked** — introspection stays available.
4. The plumbing the tools rest on: model resolution, company scoping, private
   method refusal, archive/cancel semantics.

Runs against a fake Odoo (see ``_fake_odoo``) — it pins decision logic, not
ORM behaviour. Under a real Odoo this module is skipped.
"""
import unittest

from . import _fake_odoo

if _fake_odoo.real_odoo_present():                          # pragma: no cover
    raise unittest.SkipTest("fake-Odoo suite; skipped when a real Odoo is present")

_fake_odoo.install()
generic_tools = _fake_odoo.load('generic_tools')
log_utils = _fake_odoo.load('log_utils')

FakeEnv = _fake_odoo.FakeEnv

PROTECTED = 'True'
UNPROTECTED = 'False'
PROTECT_KEY = 'odoo_module_mcp_server.protect_structural'
DELETE_KEY = 'odoo_module_mcp_server.allow_record_deletion'


def env(protect=PROTECTED, allow_delete='False', **kwargs):
    """A fake env with the policy controls in an explicit state."""
    params = {PROTECT_KEY: protect, DELETE_KEY: allow_delete}
    params.update(kwargs.pop('params', {}))
    return FakeEnv(params=params, **kwargs)


# ---------------------------------------------------------------------------
# Control 1 — no hard deletes, any model, any path
# ---------------------------------------------------------------------------

class TestDeletionBlocked(unittest.TestCase):
    MODELS = ['res.partner', 'account.move', 'ir.attachment', 'x_custom.model']

    def test_unlink_refused_on_every_model(self):
        for model in self.MODELS:
            with self.subTest(model=model):
                e = env()
                with self.assertRaises(ValueError) as caught:
                    generic_tools.odoo_unlink(e, {'model': model, 'ids': [1, 2]})
                self.assertIn('Refusing to delete records', str(caught.exception))
                self.assertEqual(e[model].calls, [],
                                 'the gate must refuse BEFORE the ORM is touched')

    def test_the_refusal_names_the_alternatives(self):
        with self.assertRaises(ValueError) as caught:
            generic_tools.odoo_unlink(env(), {'model': 'res.partner', 'ids': [1]})
        message = str(caught.exception)
        self.assertIn('Archive', message)
        self.assertIn('cancel', message)

    def test_execute_is_not_a_back_door(self):
        e = env()
        with self.assertRaises(ValueError) as caught:
            generic_tools.odoo_execute(e, {'model': 'res.partner',
                                           'method': 'unlink', 'args': [[1]]})
        self.assertIn('Refusing to delete records', str(caught.exception))
        self.assertNotIn('call_kw', [c[0] for c in e['res.partner'].calls])

    def test_unlink_allowed_once_the_flag_is_lifted(self):
        e = env(allow_delete='True')
        self.assertTrue(generic_tools.odoo_unlink(e, {'model': 'res.partner', 'ids': [3]}))
        self.assertIn(('unlink', [3]), e['res.partner'].calls)

    def test_structural_protection_still_applies_when_deletion_is_allowed(self):
        # Lifting the no-delete policy must not also open up structural models.
        e = env(allow_delete='True')
        with self.assertRaises(ValueError) as caught:
            generic_tools.odoo_unlink(e, {'model': 'ir.model', 'ids': [1]})
        self.assertIn('structural', str(caught.exception))

    def test_archive_is_the_sanctioned_removal(self):
        e = env()
        generic_tools.odoo_archive(e, {'model': 'res.partner', 'ids': [7]})
        self.assertIn(('write', [7], {'active': False}), e['res.partner'].calls)

    def test_unarchive_restores(self):
        e = env()
        generic_tools.odoo_archive(e, {'model': 'res.partner', 'ids': [7],
                                       'archive': False})
        self.assertIn(('write', [7], {'active': True}), e['res.partner'].calls)


# ---------------------------------------------------------------------------
# Control 2 — no structural / auth / code-bearing writes
# ---------------------------------------------------------------------------

class TestStructuralProtection(unittest.TestCase):

    def test_every_listed_prefix_is_blocked(self):
        for prefix in generic_tools._STRUCTURAL_PREFIXES:
            for model in (prefix + 'thing', prefix + 'a.b'):
                with self.subTest(model=model):
                    self.assertTrue(generic_tools._is_structural_model(model))

    def test_every_listed_exact_name_is_blocked(self):
        for model in generic_tools._STRUCTURAL_EXACT:
            with self.subTest(model=model):
                self.assertTrue(generic_tools._is_structural_model(model))

    def test_carve_outs_stay_writable(self):
        # Uploading files to records is everyday work and must keep working.
        for model in generic_tools._STRUCTURAL_OVERRIDE_ALLOW:
            with self.subTest(model=model):
                self.assertFalse(generic_tools._is_structural_model(model))

    def test_business_models_are_not_structural(self):
        for model in ('account.move', 'account.move.line', 'res.partner',
                      'sale.order', 'purchase.order', 'account.account',
                      'stock.picking', 'project.task', 'x_custom.thing'):
            with self.subTest(model=model):
                self.assertFalse(generic_tools._is_structural_model(model))

    def test_prefix_matching_is_not_greedy(self):
        # 'ir.' must not swallow a model that merely starts with those letters.
        for model in ('iridium.sample', 'irrigation.zone', 'busy.worker',
                      'business.unit'):
            with self.subTest(model=model):
                self.assertFalse(generic_tools._is_structural_model(model))

    def test_create_is_refused_before_the_orm(self):
        for model in ('ir.model', 'ir.ui.view', 'res.users', 'res.groups',
                      'ir.cron', 'mail.template'):
            with self.subTest(model=model):
                e = env()
                with self.assertRaises(ValueError):
                    generic_tools.odoo_create(e, {'model': model, 'values': {'x': 1}})
                self.assertEqual(e[model].calls, [])

    def test_write_is_refused_before_the_orm(self):
        # NB: not ir.config_parameter — reading the policy flag legitimately
        # calls get_param on it, so it is never "untouched".
        e = env()
        with self.assertRaises(ValueError):
            generic_tools.odoo_write(e, {'model': 'ir.ui.view',
                                         'ids': [1], 'values': {'arch': '<x/>'}})
        self.assertEqual(e['ir.ui.view'].calls, [])

    def test_privilege_escalation_through_execute_is_refused(self):
        e = env()
        with self.assertRaises(ValueError):
            generic_tools.odoo_execute(e, {
                'model': 'res.users', 'method': 'write',
                'args': [[2], {'groups_id': [[4, 1]]}]})
        self.assertEqual(e['res.users'].calls, [])

    def test_module_installation_through_execute_is_refused(self):
        e = env()
        with self.assertRaises(ValueError):
            generic_tools.odoo_execute(e, {'model': 'ir.module.module',
                                           'method': 'button_immediate_install',
                                           'args': [[42]]})

    def test_archive_and_cancel_are_guarded_too(self):
        e = env()
        with self.assertRaises(ValueError):
            generic_tools.odoo_archive(e, {'model': 'ir.ui.view', 'ids': [1]})
        with self.assertRaises(ValueError):
            generic_tools.odoo_cancel(e, {'model': 'ir.cron', 'ids': [1]})

    def test_writes_are_allowed_when_protection_is_off(self):
        e = env(protect=UNPROTECTED)
        generic_tools.odoo_write(e, {'model': 'ir.config_parameter',
                                     'ids': [1], 'values': {'value': 'x'}})
        self.assertIn(('write', [1], {'value': 'x'}), e['ir.config_parameter'].calls)

    def test_protection_defaults_to_on_when_unset(self):
        # A fresh install with no parameter row must be safe, not open.
        e = FakeEnv(params={})
        with self.assertRaises(ValueError):
            generic_tools.odoo_write(e, {'model': 'ir.model', 'ids': [1],
                                         'values': {'name': 'x'}})

    def test_deletion_defaults_to_off_when_unset(self):
        e = FakeEnv(params={})
        with self.assertRaises(ValueError):
            generic_tools.odoo_unlink(e, {'model': 'res.partner', 'ids': [1]})

    def test_business_writes_pass_through(self):
        e = env()
        generic_tools.odoo_write(e, {'model': 'account.move', 'ids': [1841],
                                     'values': {'ref': 'INV-1'}})
        self.assertIn(('write', [1841], {'ref': 'INV-1'}), e['account.move'].calls)


class TestReadsAreNeverBlocked(unittest.TestCase):
    """Introspection stays available on protected models — only writes are gated."""

    PROTECTED_MODELS = ['ir.model', 'ir.ui.view', 'res.users', 'res.groups',
                        'ir.cron', 'mail.template']

    def test_search_read_allowed_on_protected_models(self):
        for model in self.PROTECTED_MODELS:
            with self.subTest(model=model):
                e = env()
                generic_tools.odoo_search_read(e, {'model': model, 'fields': ['id']})
                self.assertEqual(e[model].calls[0][0], 'search_read')

    def test_fields_get_allowed_on_a_protected_model(self):
        e = env()
        generic_tools.odoo_fields_get(e, {'model': 'ir.model'})
        self.assertEqual(e['ir.model'].calls[0][0], 'fields_get')

    def test_search_count_allowed_on_a_protected_model(self):
        e = env()
        generic_tools.odoo_search_count(e, {'model': 'res.users'})
        self.assertEqual(e['res.users'].calls[0][0], 'search_count')


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

class TestModelResolution(unittest.TestCase):
    def test_unknown_model_is_rejected_by_name(self):
        e = FakeEnv(params={}, known={'res.partner'})
        with self.assertRaises(ValueError) as caught:
            generic_tools.odoo_search_read(e, {'model': 'nope.nope'})
        self.assertIn('nope.nope', str(caught.exception))

    def test_missing_model_name_is_rejected(self):
        for bad in (None, '', 123):
            with self.subTest(model=bad):
                with self.assertRaises(ValueError):
                    generic_tools._model(FakeEnv(), bad)


class TestCompanyScoping(unittest.TestCase):
    """company_id must reach the ORM — Odoo 18 company-dependent fields
    (account.account.code, and therefore display_name) come back blank without it."""

    def test_company_id_pins_the_company(self):
        e = env()
        generic_tools.odoo_search_read(e, {'model': 'account.account',
                                           'company_id': 3, 'fields': ['code']})
        self.assertIn(('with_company', 3), e['account.account'].calls)

    def test_no_company_id_leaves_the_default_context(self):
        e = env()
        generic_tools.odoo_search_read(e, {'model': 'account.account'})
        self.assertIsNone(e['account.account'].company)


class TestExecuteEscapeHatch(unittest.TestCase):
    def test_private_methods_are_refused(self):
        for method in ('_write', '__class__', '_name', '_inherit'):
            with self.subTest(method=method):
                with self.assertRaises(ValueError) as caught:
                    generic_tools.odoo_execute(env(), {'model': 'account.move',
                                                       'method': method})
                self.assertIn('private', str(caught.exception))

    def test_a_missing_method_name_is_refused(self):
        with self.assertRaises(ValueError):
            generic_tools.odoo_execute(env(), {'model': 'account.move'})

    def test_an_unknown_method_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            generic_tools.odoo_execute(env(), {'model': 'account.move',
                                               'method': 'no_such_method'})
        self.assertIn('no_such_method', str(caught.exception))

    def test_a_public_method_reaches_call_kw(self):
        e = env()
        e['account.move'].workflow_methods.add('action_post')
        generic_tools.odoo_execute(e, {'model': 'account.move',
                                       'method': 'action_post', 'args': [[1841]]})
        self.assertIn(('call_kw', 'action_post', [[1841]], {}),
                      e['account.move'].calls)


class TestCancel(unittest.TestCase):
    def test_action_cancel_is_preferred(self):
        e = env()
        result = generic_tools.odoo_cancel(e, {'model': 'account.move', 'ids': [1]})
        self.assertEqual(result['method'], 'action_cancel')

    def test_a_model_without_a_cancel_method_is_reported(self):
        e = env()
        e['res.partner'].workflow_methods.clear()   # no action_cancel, no button_cancel
        with self.assertRaises(ValueError) as caught:
            generic_tools.odoo_cancel(e, {'model': 'res.partner', 'ids': [1]})
        self.assertIn('No cancel method', str(caught.exception))


class TestCreate(unittest.TestCase):
    def test_a_single_dict_returns_one_id(self):
        e = env()
        self.assertEqual(
            generic_tools.odoo_create(e, {'model': 'account.move',
                                          'values': {'ref': 'X'}}),
            1841)

    def test_a_list_returns_many_ids(self):
        e = env()
        result = generic_tools.odoo_create(
            e, {'model': 'account.move', 'values': [{'ref': 'A'}, {'ref': 'B'}]})
        self.assertEqual(result, [1, 2])




class TestGuardrailRefusalsAreLoggableAsBlocked(unittest.TestCase):
    """Cross-check: every refusal generic_tools raises must be recognised by
    log_utils as a POLICY block, not a generic error.

    Without this the two files drift silently — a new guardrail gets added
    here, its message is not added to log_utils._POLICY_MARKERS, and the
    attempt it refused disappears from the activity log's "Blocked by policy"
    filter, which is exactly where an operator looks for what was stopped.
    """

    def _refusal(self, call):
        with self.assertRaises(ValueError) as caught:
            call()
        return caught.exception

    def test_the_structural_write_guardrail(self):
        error = self._refusal(lambda: generic_tools.odoo_write(
            env(), {'model': 'ir.ui.view', 'ids': [1], 'values': {'name': 'x'}}))
        self.assertEqual(log_utils.classify_error(error), 'blocked')

    def test_the_deletion_guardrail(self):
        error = self._refusal(lambda: generic_tools.odoo_unlink(
            env(), {'model': 'res.partner', 'ids': [1]}))
        self.assertEqual(log_utils.classify_error(error), 'blocked')

    def test_the_private_method_guardrail(self):
        # The most security-relevant of the three: odoo_execute is the escape
        # hatch, and '_write' is what a compromised agent would reach for.
        error = self._refusal(lambda: generic_tools.odoo_execute(
            env(), {'model': 'res.users', 'method': '_write', 'args': [[1]]}))
        self.assertEqual(
            log_utils.classify_error(error), 'blocked',
            "the private-method refusal is filed as a generic error, so it "
            "drops out of the activity log's 'Blocked by policy' filter")

    def test_an_ordinary_tool_failure_is_not_dressed_up_as_a_block(self):
        error = self._refusal(lambda: generic_tools.odoo_search_read(
            env(known=['res.partner']), {'model': 'no.such.model'}))
        self.assertEqual(log_utils.classify_error(error), 'error')


if __name__ == '__main__':
    unittest.main()
