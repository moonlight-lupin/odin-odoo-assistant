"""Behavioural tests for ``custom.mcp.log``'s write path.

The model itself needs Odoo, so this runs it against a small fake: enough of
``models.Model`` / ``fields`` / ``api.Environment`` for the class body to
execute and ``log_call`` to run end to end. What that buys is coverage of the
control flow that has no other test — gating, redaction, the payload toggle,
and the two properties the design rests on:

* **rows are written on a fresh cursor**, so a tool call that aborted the
  request transaction still leaves a trail;
* **logging never raises**, whatever the sink does.

Under Odoo's own test runner these same behaviours are exercised for real by
any call through /mcp/v1; this is the version that runs without a database.
"""
import json
import sys
import types
import unittest

# Belt and braces: this file stands the model up against a FAKE odoo, which is
# only meaningful when the real one is absent. If a real Odoo is importable,
# skip the module rather than subclassing a live model outside its registry.
try:
    import odoo as _odoo
    _REAL_ODOO = hasattr(_odoo, 'addons')
except ImportError:
    _REAL_ODOO = False

if _REAL_ODOO:                                              # pragma: no cover
    raise unittest.SkipTest(
        "Runs only without Odoo installed — under Odoo's own test runner these "
        "behaviours are exercised live through /mcp/v1.")


# ---------------------------------------------------------------------------
# A fake `odoo` just large enough to import models/mcp_log.py
# ---------------------------------------------------------------------------

class _FakeField:
    def __init__(self, *args, **kwargs):
        pass


def _install_fake_odoo():
    if 'odoo' in sys.modules:
        return
    odoo = types.ModuleType('odoo')
    fields_mod = types.SimpleNamespace(
        Many2one=_FakeField, Selection=_FakeField, Char=_FakeField,
        Integer=_FakeField, Boolean=_FakeField, Float=_FakeField,
        Text=_FakeField, Datetime=types.SimpleNamespace(
            now=lambda: 'NOW', subtract=lambda dt, **kw: 'CUTOFF'),
    )

    class Model:
        _name = None

    api_mod = types.SimpleNamespace(
        model=lambda fn: fn,
        Environment=lambda cr, uid, ctx: cr.env,
    )
    odoo._ = lambda text: text
    odoo.api = api_mod
    odoo.fields = fields_mod
    odoo.models = types.SimpleNamespace(Model=Model, AbstractModel=Model)
    odoo.SUPERUSER_ID = 1
    exceptions = types.ModuleType('odoo.exceptions')

    class UserError(Exception):
        pass
    exceptions.UserError = UserError
    odoo.exceptions = exceptions
    sys.modules['odoo'] = odoo
    sys.modules['odoo.exceptions'] = exceptions
    sys.modules['odoo.api'] = types.ModuleType('odoo.api')
    sys.modules['odoo.api'].__dict__.update(vars(api_mod))


_install_fake_odoo()

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import log_utils                                             # noqa: E402
sys.modules.setdefault('odoo_module_mcp_server', types.ModuleType('odoo_module_mcp_server'))
sys.modules['odoo_module_mcp_server'].log_utils = log_utils

import importlib.util                                         # noqa: E402
_spec = importlib.util.spec_from_file_location(
    'odoo_module_mcp_server.models.mcp_log',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'models', 'mcp_log.py'))
mcp_log = importlib.util.module_from_spec(_spec)
mcp_log.__package__ = 'odoo_module_mcp_server.models'
sys.modules['odoo_module_mcp_server.models'] = types.ModuleType('odoo_module_mcp_server.models')
_spec.loader.exec_module(mcp_log)


# ---------------------------------------------------------------------------
# A fake recordset / cursor
# ---------------------------------------------------------------------------

class FakeCursor:
    """Records commit/close so a test can assert the row was committed."""

    def __init__(self, log):
        self.env = {'custom.mcp.log': log}
        self.committed = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.committed = True
        self.closed = True
        return False


class FakeLog(mcp_log.McpLog):
    """The model under test, with the ORM bits stubbed out."""

    def __init__(self, level='all', payloads=True, request_context=None,
                 create_raises=None):
        self.rows = []
        self.cursors = []
        self._level = level
        self._payloads = payloads
        self._ctx = request_context if request_context is not None else {
            'user_id': 7, 'auth_method': 'api_key', 'remote_addr': '10.0.0.9'}
        self._create_raises = create_raises
        self.env = types.SimpleNamespace(
            registry=types.SimpleNamespace(cursor=self._cursor))

    def _cursor(self):
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    # stubbed ORM / settings surface
    def create(self, values):
        if self._create_raises:
            raise self._create_raises
        self.rows.append(values)
        return values

    def _log_level(self):
        return self._level

    def _log_payloads(self):
        return self._payloads

    def _request_context(self):
        return dict(self._ctx)


class LogCallTests(unittest.TestCase):
    def test_a_successful_call_is_recorded_with_its_facets(self):
        log = FakeLog()
        log.log_call('odoo_write', {'model': 'account.move', 'ids': [1841],
                                    'values': {'ref': 'INV-1'}},
                     outcome='ok', duration_ms=61.4321, result=True)
        self.assertEqual(len(log.rows), 1)
        row = log.rows[0]
        self.assertEqual(row['tool'], 'odoo_write')
        self.assertEqual(row['model_name'], 'account.move')
        self.assertEqual(row['record_ids'], '1841')
        self.assertEqual(row['record_count'], 1)
        self.assertTrue(row['is_write'])
        self.assertEqual(row['outcome'], 'ok')
        self.assertEqual(row['duration_ms'], 61.432)
        self.assertEqual(row['kind'], 'tool_call')
        self.assertEqual(row['user_id'], 7)
        self.assertEqual(row['remote_addr'], '10.0.0.9')
        self.assertEqual(json.loads(row['arguments'])['values'], {'ref': 'INV-1'})
        self.assertEqual(json.loads(row['result']), True)

    def test_the_row_is_committed_on_its_own_cursor(self):
        # The point of the separate cursor: a tool that aborted the request
        # transaction must still leave a trail.
        log = FakeLog()
        log.log_call('odoo_create', {'model': 'account.move'}, outcome='ok', result=1841)
        self.assertEqual(len(log.cursors), 1)
        self.assertTrue(log.cursors[0].committed)
        self.assertTrue(log.cursors[0].closed)

    def test_credentials_are_redacted(self):
        log = FakeLog()
        log.log_call('odoo_execute',
                     {'model': 'res.users', 'kwargs': {'api_key': 'REAL-KEY'}},
                     outcome='ok', result=None)
        self.assertNotIn('REAL-KEY', json.dumps(log.rows[0]))

    def test_a_guardrail_refusal_is_stored_as_blocked(self):
        log = FakeLog()
        error = ValueError("Refusing to delete records — MCP record deletion is disabled.")
        log.log_call('odoo_unlink', {'model': 'account.move', 'ids': [1]},
                     outcome=log_utils.classify_error(error), error=error)
        row = log.rows[0]
        self.assertEqual(row['outcome'], 'blocked')
        self.assertEqual(row['error_type'], 'ValueError')
        self.assertIn('Refusing to delete', row['error_message'])
        self.assertNotIn('result', row)     # no result to record on a refusal

    def test_level_off_records_nothing(self):
        log = FakeLog(level='off')
        log.log_call('odoo_write', {'model': 'account.move'}, outcome='ok')
        self.assertEqual(log.rows, [])

    def test_level_error_drops_successes_but_keeps_blocks(self):
        log = FakeLog(level='error')
        log.log_call('odoo_search_read', {'model': 'account.move'}, outcome='ok')
        log.log_call('odoo_write', {'model': 'ir.model'}, outcome='blocked',
                     error=ValueError('Refusing to modify structural model'))
        self.assertEqual([r['outcome'] for r in log.rows], ['blocked'])

    def test_payloads_off_keeps_the_metadata_only(self):
        log = FakeLog(payloads=False)
        log.log_call('odoo_write',
                     {'model': 'account.move', 'values': {'ref': 'SECRET-REF'}},
                     outcome='ok', result=True)
        row = log.rows[0]
        self.assertEqual(row['tool'], 'odoo_write')
        self.assertEqual(row['model_name'], 'account.move')
        self.assertNotIn('arguments', row)
        self.assertNotIn('result', row)
        self.assertNotIn('SECRET-REF', json.dumps(row))

    def test_a_broken_sink_never_propagates(self):
        log = FakeLog(create_raises=RuntimeError('table is gone'))
        log.log_call('odoo_write', {'model': 'account.move'}, outcome='ok')   # must not raise
        self.assertEqual(log.rows, [])

    def test_an_unserialisable_payload_still_produces_a_row(self):
        class Opaque:
            def __repr__(self):
                return '<opaque>'
        log = FakeLog()
        log.log_call('odoo_execute', {'model': 'x', 'args': [Opaque()]},
                     outcome='ok', result=Opaque())
        self.assertEqual(len(log.rows), 1)
        self.assertIn('opaque', log.rows[0]['arguments'])


class LogEventTests(unittest.TestCase):
    def test_auth_failure_is_recorded_without_the_token(self):
        log = FakeLog()
        log.log_event('auth_failure', 'MCP authentication rejected: invalid Bearer token',
                      outcome='denied')
        row = log.rows[0]
        self.assertEqual(row['kind'], 'auth_failure')
        self.assertEqual(row['outcome'], 'denied')
        self.assertFalse(row['is_write'])
        self.assertNotIn('arguments', row)

    def test_events_respect_the_level(self):
        log = FakeLog(level='off')
        log.log_event('protocol_error', 'boom')
        self.assertEqual(log.rows, [])

    def test_a_broken_sink_never_propagates(self):
        log = FakeLog(create_raises=RuntimeError('nope'))
        log.log_event('protocol_error', 'boom')
        self.assertEqual(log.rows, [])


class RequestContextTests(unittest.TestCase):
    """Who/where is read off the live request — and must touch no database."""

    def setUp(self):
        self.http = types.ModuleType('odoo.http')
        sys.modules['odoo.http'] = self.http
        self.addCleanup(sys.modules.pop, 'odoo.http', None)

    def _request(self, **attrs):
        class Cursor:
            def execute(self, *a, **k):
                raise AssertionError("_request_context must not touch the database")

        user = types.SimpleNamespace(id=attrs.pop('user_id', 7))
        request = types.SimpleNamespace(
            env=types.SimpleNamespace(user=user, cr=Cursor()),
            httprequest=types.SimpleNamespace(
                remote_addr=attrs.pop('remote_addr', '10.0.0.9')),
            **attrs)
        self.http.request = request
        return request

    def _context(self):
        log = FakeLog()
        return mcp_log.McpLog._request_context(log)

    def test_the_authenticated_method_is_recorded(self):
        self._request(mcp_auth_method='oauth')
        context = self._context()
        self.assertEqual(context['auth_method'], 'oauth')
        self.assertEqual(context['user_id'], 7)
        self.assertEqual(context['remote_addr'], '10.0.0.9')

    def test_an_unauthenticated_request_is_not_labelled_api_key(self):
        # ir_http stamps mcp_auth_method only once a credential is ACCEPTED.
        # A rejected token never gets that far, and must not be recorded as
        # though it had authenticated by API key.
        self._request()
        self.assertEqual(self._context()['auth_method'], 'none')

    def test_off_request_returns_nothing_rather_than_raising(self):
        self.http.request = None
        self.assertEqual(self._context(), {})


class AppendOnlyTests(unittest.TestCase):
    def test_write_is_refused(self):
        from odoo.exceptions import UserError
        log = FakeLog()
        with self.assertRaises(UserError):
            log.write({'summary': 'tampered'})


if __name__ == '__main__':
    unittest.main()
