"""Tests for the MCP endpoint's JSON-RPC 2.0 dispatch.

This layer is what every client talks to, and its rules are easy to break
silently: a notification must produce NO response, a tool that raises is a
*result* with isError (not a protocol error), and only genuine dispatch
problems get JSON-RPC error codes. A client that receives a response to a
notification, or a protocol error where it expected a tool result, misbehaves
in ways that are hard to trace back here.

Runs against a fake Odoo; under a real one this module is skipped.
"""
import json
import types
import unittest

from . import _fake_odoo

if _fake_odoo.real_odoo_present():                          # pragma: no cover
    raise unittest.SkipTest("fake-Odoo suite; skipped when a real Odoo is present")

_fake_odoo.install()
mcp_registry = _fake_odoo.load('mcp_registry')
main = _fake_odoo.load('controllers.main')


class ControllerCase(unittest.TestCase):
    """Base: a controller wired to a fake request, with a disposable registry."""

    def setUp(self):
        self.env = _fake_odoo.FakeEnv()
        # The controller logs each call through this model; let it record.
        self.env['custom.mcp.log'].workflow_methods.update({'log_call', 'log_event'})
        self.request = types.SimpleNamespace(
            env=self.env,
            httprequest=types.SimpleNamespace(data=b'{}', remote_addr='10.0.0.9'),
            make_response=lambda body, headers=None: types.SimpleNamespace(
                body=body, headers=headers),
        )
        # main.py did `from odoo.http import request`, so the name to patch is
        # the one bound in that module.
        self._saved_request = main.request
        main.request = self.request
        self.addCleanup(setattr, main, 'request', self._saved_request)

        # The registry is process-wide: restore it exactly, or tools registered
        # here leak into every suite that runs after this one.
        self._saved_tools = dict(mcp_registry.McpRegistry._tools)

        def restore():
            mcp_registry.McpRegistry._tools.clear()
            mcp_registry.McpRegistry._tools.update(self._saved_tools)
        self.addCleanup(restore)

        self.controller = main.McpController()

    #: Hints for the throwaway tools registered by these tests. The registry
    #: requires all four, so the protocol tests have to supply them too.
    HINTS = {'readOnlyHint': True, 'destructiveHint': False,
             'idempotentHint': True, 'openWorldHint': False}

    def register(self, name, fn, description='test tool', schema=None,
                 annotations=None):
        mcp_registry.McpRegistry.register(
            name, description, schema or {'type': 'object'},
            annotations or dict(self.HINTS), fn)

    def handle(self, message):
        return self.controller._handle_message(message)

    @property
    def log_calls(self):
        return self.env['custom.mcp.log'].calls


class TestHandshake(ControllerCase):
    def test_initialize_reports_the_protocol_version_and_tools_capability(self):
        result = self.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'})
        self.assertEqual(result['id'], 1)
        self.assertEqual(result['jsonrpc'], '2.0')
        body = result['result']
        self.assertEqual(body['protocolVersion'], main.PROTOCOL_VERSION)
        self.assertIn('tools', body['capabilities'])
        self.assertIn('name', body['serverInfo'])
        self.assertIn('version', body['serverInfo'])

    def test_ping_returns_an_empty_result(self):
        result = self.handle({'jsonrpc': '2.0', 'id': 9, 'method': 'ping'})
        self.assertEqual(result['result'], {})


class TestToolsList(ControllerCase):
    def test_every_registered_tool_is_advertised_with_its_schema(self):
        result = self.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        tools = result['result']['tools']
        self.assertEqual(len(tools), len(mcp_registry.McpRegistry.tools()))
        for tool in tools:
            with self.subTest(tool=tool['name']):
                self.assertTrue(tool['description'])
                self.assertEqual(tool['inputSchema'].get('type'), 'object')

    def test_every_tool_is_advertised_with_its_behavioural_hints(self):
        """Hints the registry holds but tools/list drops reach nobody.

        They are how a client tells the user 'this only reads' from 'this can
        delete your entries' before it runs anything — and the spec's default
        for a missing destructiveHint is *true*, so dropping them does not
        fail safe, it just misinforms.
        """
        result = self.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        for tool in result['result']['tools']:
            with self.subTest(tool=tool['name']):
                hints = tool.get('annotations')
                self.assertIsNotNone(hints,
                                     '%s is advertised unannotated' % tool['name'])
                self.assertEqual(
                    set(hints),
                    {'readOnlyHint', 'destructiveHint',
                     'idempotentHint', 'openWorldHint'})
                for key, value in hints.items():
                    self.assertIsInstance(value, bool,
                                          '%s.%s is not a bool' % (tool['name'], key))

    def test_unlink_is_advertised_as_destructive(self):
        result = self.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        unlink = next(t for t in result['result']['tools']
                      if t['name'] == 'odoo_unlink')
        self.assertTrue(unlink['annotations']['destructiveHint'])

    def test_tools_are_listed_in_a_stable_order(self):
        first = self.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        second = self.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        self.assertEqual([t['name'] for t in first['result']['tools']],
                         [t['name'] for t in second['result']['tools']])


class TestToolsCall(ControllerCase):
    def test_a_dict_result_comes_back_as_json_text(self):
        self.register('t.echo', lambda env, args: {'echoed': args['message']})
        result = self.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                              'params': {'name': 't.echo',
                                         'arguments': {'message': 'hi'}}})
        content = result['result']['content'][0]
        self.assertEqual(content['type'], 'text')
        self.assertEqual(json.loads(content['text']), {'echoed': 'hi'})
        self.assertFalse(result['result']['isError'])

    def test_a_string_result_is_passed_through_unquoted(self):
        self.register('t.text', lambda env, args: 'plain text')
        result = self.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                              'params': {'name': 't.text'}})
        self.assertEqual(result['result']['content'][0]['text'], 'plain text')

    def test_a_raising_tool_is_a_RESULT_with_isError_not_a_protocol_error(self):
        # This distinction matters: a client shows isError content to the user,
        # but treats a JSON-RPC error as a transport/protocol fault.
        def boom(env, args):
            raise ValueError('the books are closed for this period')
        self.register('t.boom', boom)
        result = self.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                              'params': {'name': 't.boom'}})
        self.assertNotIn('error', result)
        self.assertTrue(result['result']['isError'])
        self.assertIn('books are closed', result['result']['content'][0]['text'])

    def test_an_unknown_tool_is_an_invalid_params_error(self):
        result = self.handle({'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                              'params': {'name': 'no.such.tool'}})
        self.assertEqual(result['error']['code'], main.INVALID_PARAMS)
        self.assertIn('no.such.tool', result['error']['message'])

    def test_a_missing_tool_name_is_an_invalid_params_error(self):
        result = self.handle({'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call',
                              'params': {}})
        self.assertEqual(result['error']['code'], main.INVALID_PARAMS)

    def test_absent_arguments_default_to_an_empty_dict(self):
        seen = {}
        self.register('t.args', lambda env, args: seen.update(args) or 'ok')
        self.handle({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                     'params': {'name': 't.args'}})
        self.assertEqual(seen, {})

    def test_the_tool_receives_the_request_environment(self):
        captured = {}
        self.register('t.env', lambda env, args: captured.setdefault('env', env) and 'ok')
        self.handle({'jsonrpc': '2.0', 'id': 8, 'method': 'tools/call',
                     'params': {'name': 't.env'}})
        self.assertIs(captured['env'], self.env)


class TestCallsAreLogged(ControllerCase):
    """The audit trail must not depend on the call succeeding."""

    def test_a_successful_call_is_logged_as_ok(self):
        self.register('t.ok', lambda env, args: 'fine')
        self.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 't.ok'}})
        logged = [c for c in self.log_calls if c[0] == 'log_call']
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0][2]['outcome'], 'ok')
        self.assertEqual(logged[0][2]['tool'], 't.ok')

    def test_a_failing_call_is_still_logged(self):
        def boom(env, args):
            raise RuntimeError('odoo said no')
        self.register('t.boom', boom)
        self.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 't.boom'}})
        logged = [c for c in self.log_calls if c[0] == 'log_call']
        self.assertEqual(logged[0][2]['outcome'], 'error')

    def test_a_guardrail_refusal_is_logged_as_blocked(self):
        def refuse(env, args):
            raise ValueError("Refusing to delete records — MCP record deletion "
                             "is disabled.")
        self.register('t.refuse', refuse)
        self.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 't.refuse'}})
        logged = [c for c in self.log_calls if c[0] == 'log_call']
        self.assertEqual(logged[0][2]['outcome'], 'blocked')

    def test_a_broken_log_does_not_break_the_call(self):
        self.env['custom.mcp.log'].workflow_methods.clear()   # log_call now raises
        self.register('t.ok', lambda env, args: 'fine')
        result = self.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                              'params': {'name': 't.ok'}})
        self.assertFalse(result['result']['isError'])
        self.assertEqual(result['result']['content'][0]['text'], 'fine')


class TestNotificationsAndErrors(ControllerCase):
    def test_a_notification_produces_no_response(self):
        # No `id` means a notification. Replying to one is a protocol violation.
        for method in ('notifications/initialized', 'notifications/cancelled',
                       'initialize', 'tools/list'):
            with self.subTest(method=method):
                self.assertIsNone(self.handle({'jsonrpc': '2.0', 'method': method}))

    def test_an_unknown_notification_is_silently_dropped(self):
        self.assertIsNone(self.handle({'jsonrpc': '2.0', 'method': 'nope/nope'}))

    def test_an_unknown_method_with_an_id_is_method_not_found(self):
        result = self.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'nope/nope'})
        self.assertEqual(result['error']['code'], main.METHOD_NOT_FOUND)
        self.assertIn('nope/nope', result['error']['message'])

    def test_a_handler_crash_becomes_an_internal_error(self):
        def explode(params):
            raise RuntimeError('registry on fire')
        self.controller._handle_tools_list = explode
        result = self.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        self.assertEqual(result['error']['code'], main.INTERNAL_ERROR)

    def test_a_failing_notification_still_produces_no_response(self):
        def explode(params):
            raise RuntimeError('registry on fire')
        self.controller._handle_tools_list = explode
        self.assertIsNone(self.handle({'jsonrpc': '2.0', 'method': 'tools/list'}))


class TestBatches(ControllerCase):
    def test_a_batch_returns_one_response_per_request(self):
        self.register('t.ok', lambda env, args: 'fine')
        main.request.httprequest.data = json.dumps([
            {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 't.ok'}},
        ]).encode()
        response = self.controller.mcp_endpoint()
        body = json.loads(response.body)
        self.assertEqual([entry['id'] for entry in body], [1, 2])

    def test_notifications_are_omitted_from_a_batch_response(self):
        main.request.httprequest.data = json.dumps([
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
        ]).encode()
        body = json.loads(self.controller.mcp_endpoint().body)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['id'], 1)

    def test_malformed_json_is_a_parse_error(self):
        main.request.httprequest.data = b'{not json'
        body = json.loads(self.controller.mcp_endpoint().body)
        self.assertEqual(body['error']['code'], main.PARSE_ERROR)

    def test_an_empty_body_does_not_crash_the_endpoint(self):
        # An empty body parses as {}: no id, so it is treated as a notification
        # and answered with `null` rather than an error. Degenerate but harmless
        # — what matters is that it never raises.
        main.request.httprequest.data = b''
        self.assertIsNone(json.loads(self.controller.mcp_endpoint().body))

    def test_a_request_with_an_id_but_no_method_is_reported(self):
        main.request.httprequest.data = b'{"jsonrpc":"2.0","id":1}'
        body = json.loads(self.controller.mcp_endpoint().body)
        self.assertEqual(body['error']['code'], main.METHOD_NOT_FOUND)

    def test_the_response_is_declared_as_json(self):
        main.request.httprequest.data = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
        response = self.controller.mcp_endpoint()
        self.assertIn(('Content-Type', 'application/json'), response.headers)


class TestSerialisation(unittest.TestCase):
    """_json_default keeps non-JSON Odoo values from breaking a response."""

    def test_dates_become_iso_strings(self):
        import datetime
        self.assertEqual(
            main.McpController._json_default(datetime.date(2026, 9, 14)),
            '2026-09-14')
        self.assertTrue(
            main.McpController._json_default(
                datetime.datetime(2026, 9, 14, 10, 30)).startswith('2026-09-14T10:30'))

    def test_bytes_become_base64(self):
        import base64
        encoded = main.McpController._json_default(b'hello')
        self.assertEqual(base64.b64decode(encoded), b'hello')

    def test_a_recordset_becomes_id_and_display_name(self):
        record = types.SimpleNamespace(id=7, display_name='ACME')
        recordset = types.SimpleNamespace(_name='res.partner',
                                          __iter__=lambda self: iter([record]))

        class Recordset:
            _name = 'res.partner'

            def __iter__(self):
                return iter([record])
        self.assertEqual(main.McpController._json_default(Recordset()),
                         [{'id': 7, 'display_name': 'ACME'}])
        del recordset

    def test_anything_else_falls_back_to_str(self):
        class Opaque:
            def __str__(self):
                return '<opaque>'
        self.assertEqual(main.McpController._json_default(Opaque()), '<opaque>')


if __name__ == '__main__':
    unittest.main()
