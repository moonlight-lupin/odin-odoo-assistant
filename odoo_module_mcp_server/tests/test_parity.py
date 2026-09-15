"""Parity between the two servers.

The repo's premise is that the independent server (`odoo-mcp/server.py`) and
the in-Odoo module are interchangeable: "Tool names match the external MCP
server 1:1 so the same client skill drives either", and both are documented as
enforcing the same two controls. A skill written against one must be safe
against the other — so the tool surface and the write denylist are compared
here directly rather than trusted to stay in step by hand.

Divergences that ARE intended are listed explicitly below, with the reason.
Anything else is a drift bug: one server refusing an operation the other
allows means the safety posture depends on which one you deployed.
"""
import sys
import types
import unittest
from pathlib import Path

from . import _fake_odoo

if _fake_odoo.real_odoo_present():                          # pragma: no cover
    raise unittest.SkipTest("fake-Odoo suite; skipped when a real Odoo is present")

_fake_odoo.install()
generic_tools = _fake_odoo.load('generic_tools')
log_utils = _fake_odoo.load('log_utils')
mcp_registry = _fake_odoo.load('mcp_registry')

# -- load the independent server, stubbing the MCP SDK the same way its own
#    conftest does -------------------------------------------------------
_EXTERNAL = _fake_odoo.ADDON_DIR.parent / 'odoo-mcp'


def _load_external_server():
    if 'mcp' not in sys.modules:
        class _StubFastMCP:
            def __init__(self, *a, **k):
                self.settings = types.SimpleNamespace()

            def tool(self, *a, **k):
                return lambda fn: fn

            def run(self, *a, **k):
                raise RuntimeError('mcp.run() must not run in tests')

        pkg = types.ModuleType('mcp')
        server_mod = types.ModuleType('mcp.server')
        fastmcp = types.ModuleType('mcp.server.fastmcp')
        fastmcp.FastMCP = _StubFastMCP
        pkg.server = server_mod
        server_mod.fastmcp = fastmcp
        sys.modules.update({'mcp': pkg, 'mcp.server': server_mod,
                            'mcp.server.fastmcp': fastmcp})
    if str(_EXTERNAL) not in sys.path:
        sys.path.insert(0, str(_EXTERNAL))
    import server
    return server


external = _load_external_server()


# ---------------------------------------------------------------------------
# Intended differences
# ---------------------------------------------------------------------------

#: Models the MODULE refuses but the external server allows, with the reason.
#: These are deliberate: the module runs inside Odoo where it can reach tables
#: the external server has no business with, and it is stricter about reference
#: data.
MODULE_STRICTER_BY_DESIGN = {
    'bus.bus': 'internal pub/sub; only reachable in-process',
    'custom.mcp.oauth.client': "the module's own credential store — never let "
                               "an agent mint or revoke its own tokens",
    'custom.mcp.oauth.authorization': "the module's own credential store",
    'custom.mcp.oauth.token': "the module's own credential store",
    'res.currency': 'agents should not be inventing currencies',
    'res.currency.rate': 'the external server deliberately leaves FX rates '
                         'writable (a legitimate bookkeeping action); the '
                         'module does not',
    'res.lang': 'reference data',
    'res.country': 'reference data',
    'res.country.group': 'reference data',
    'res.country.state': 'reference data',
}

#: Representative models spanning the whole policy surface. Not exhaustive —
#: the point is to catch drift in the CATEGORIES both servers claim to cover.
SURFACE = [
    # auth / privilege
    'res.users', 'res.users.apikeys', 'res.users.settings', 'res.users.log',
    'res.users.role', 'res.users.apikeys.description', 'res.groups',
    'res.groups.privilege',
    # schema / structure / modules / UI
    'ir.model', 'ir.model.fields', 'ir.model.data', 'ir.module.module',
    'ir.ui.view', 'ir.ui.menu', 'ir.actions.act_window', 'ir.actions.report',
    'ir.rule', 'ir.cron', 'ir.config_parameter',
    # code-bearing / routing / automation
    'base.automation', 'mail.template', 'mail.alias', 'studio.approval.rule',
    # carve-outs and ordinary business data
    'ir.attachment', 'account.move', 'account.move.line', 'account.account',
    'res.partner', 'sale.order', 'purchase.order', 'stock.picking',
    'project.task', 'x_custom.model',
]


def _disagreements():
    out = []
    for model in SURFACE:
        blocked_external = external._is_protected_model(model)
        blocked_module = generic_tools._is_structural_model(model)
        if blocked_external != blocked_module:
            out.append((model, blocked_external, blocked_module))
    return out


class TestDenylistParity(unittest.TestCase):

    def test_the_module_is_never_weaker_than_the_external_server(self):
        """The safety-critical direction.

        A model the external server refuses but the module permits is a hole:
        the same agent, the same skill, a weaker guardrail purely because of
        which server the site deployed.
        """
        weaker = [model for model, ext, mod in _disagreements() if ext and not mod]
        self.assertEqual(
            weaker, [],
            "the module allows writes to models the external server refuses: %s"
            % ', '.join(weaker))

    def test_every_extra_restriction_is_a_documented_decision(self):
        stricter = [model for model, ext, mod in _disagreements() if mod and not ext]
        undocumented = [m for m in stricter if m not in MODULE_STRICTER_BY_DESIGN]
        self.assertEqual(
            undocumented, [],
            "the module refuses models the external server allows, with no "
            "recorded reason: %s" % ', '.join(undocumented))

    def test_ordinary_business_models_are_writable_on_both(self):
        for model in ('account.move', 'account.move.line', 'account.account',
                      'res.partner', 'sale.order', 'stock.picking',
                      'ir.attachment', 'x_custom.model'):
            with self.subTest(model=model):
                self.assertFalse(external._is_protected_model(model))
                self.assertFalse(generic_tools._is_structural_model(model))

    def test_the_auth_surface_is_closed_on_both(self):
        # Creating users, minting API keys, granting groups: the thing a
        # compromised agent would reach for first.
        for model in ('res.users', 'res.users.apikeys', 'res.groups'):
            with self.subTest(model=model):
                self.assertTrue(external._is_protected_model(model))
                self.assertTrue(generic_tools._is_structural_model(model))


class TestToolSurfaceParity(unittest.TestCase):
    """Tool NAMES must match, so one skill drives either server."""

    #: Tools that exist on only one side, and why.
    EXTERNAL_ONLY = {
        'odoo_connect': 'the module authenticates via the MCP client key',
        'odoo_disconnect': 'no cached session to clear in-process',
        'odoo_audit_tail': 'the module surfaces its log as Odoo views instead',
    }
    MODULE_ONLY = {
        'odoo_models_list': 'introspection via ir.model, cheap in-process',
        'odoo_unlink': 'present but refused by default; the external server '
                       'ships no delete tool at all',
    }

    def _external_tools(self):
        return {name for name in dir(external)
                if name.startswith('odoo_') and callable(getattr(external, name))}

    def test_the_shared_tools_are_named_identically(self):
        ext = self._external_tools() - set(self.EXTERNAL_ONLY)
        mod = set(mcp_registry.McpRegistry.tools()) - set(self.MODULE_ONLY)
        self.assertEqual(
            sorted(ext), sorted(mod),
            "tool names have drifted apart — a skill written against one "
            "server will call a tool the other does not have")

    def test_every_one_sided_tool_is_accounted_for(self):
        ext = self._external_tools()
        mod = set(mcp_registry.McpRegistry.tools())
        self.assertEqual(sorted(ext - mod), sorted(self.EXTERNAL_ONLY))
        self.assertEqual(sorted(mod - ext), sorted(self.MODULE_ONLY))




class TestMethodGuardrailParity(unittest.TestCase):
    """The denylist parity above compares MODELS. Guardrails also key off the
    METHOD, and that dimension drifted unnoticed: the module refused private
    methods and the external server did not, so `odoo_execute(model, '_write')`
    — reaching around the write path, the access checks and the deletion
    control — was refused on one server and executed on the other.
    """

    PRIVATE = ['_write', '_unlink', '_create', '__class__', '_name']

    # Both helpers answer one question: was this refused BY POLICY? Not "did
    # it raise" — a fake model lacking `action_post` also raises, and counting
    # that as a refusal would test the harness rather than the guardrail.

    def _external_refuses(self, model, method):
        external._state.update({
            'url': 'https://odoo.example', 'db': 'testdb', 'uid': 2,
            'username': 't@example', 'api_key': 'k', 'models': None,
            'allow_record_deletion': False, 'allow_model_changes': False,
        })
        try:
            external._guard(model, method)
        except external.GuardrailError:
            return True          # the server's policy-refusal type
        return False

    def _module_refuses(self, model, method):
        params = {'odoo_module_mcp_server.protect_structural': 'True',
                  'odoo_module_mcp_server.allow_record_deletion': 'False'}
        try:
            generic_tools.odoo_execute(_fake_odoo.FakeEnv(params=params),
                                       {'model': model, 'method': method})
        except Exception as error:
            # The module signals policy with the message, not the type — the
            # same classification the activity log files the row under.
            return log_utils.classify_error(error) == 'blocked'
        return False

    def test_private_methods_are_refused_on_both(self):
        for method in self.PRIVATE:
            for model in ('res.partner', 'account.move', 'res.users'):
                with self.subTest(model=model, method=method):
                    self.assertTrue(
                        self._external_refuses(model, method),
                        'the external server allows %s.%s' % (model, method))
                    self.assertTrue(
                        self._module_refuses(model, method),
                        'the module allows %s.%s' % (model, method))

    def test_unlink_is_refused_on_both(self):
        for model in ('res.partner', 'account.move'):
            with self.subTest(model=model):
                self.assertTrue(self._external_refuses(model, 'unlink'))
                self.assertTrue(self._module_refuses(model, 'unlink'))

    def test_public_methods_are_allowed_on_both(self):
        # The guardrails must agree on what is PERMITTED too — a server that
        # refuses everything is trivially "safe" and useless.
        for method in ('action_post', 'button_confirm', 'name_get'):
            with self.subTest(method=method):
                self.assertFalse(self._external_refuses('account.move', method))
                self.assertFalse(self._module_refuses('account.move', method))


if __name__ == '__main__':
    unittest.main()
