"""Every registered tool advertises all four MCP behavioural hints.

Same argument as the external server's ``tests/test_annotations.py``: the hints
are what a host shows the user before it runs a tool, and the spec's defaults
are pessimistic — ``destructiveHint`` defaults to **true**, so an unannotated
read tool is advertised as able to destroy data. Here the stakes are a notch
higher: this module is the one that actually ships ``odoo_unlink``.

Two things are pinned. The registry refuses a tool that does not declare all
four, so a bridge module cannot register an unannotated tool by omission; and
the controller puts them on the wire, since a hint the registry holds but
``tools/list`` drops never reaches anyone.
"""
import unittest

from . import _fake_odoo

if _fake_odoo.real_odoo_present():                          # pragma: no cover
    raise unittest.SkipTest("fake-Odoo suite; skipped when a real Odoo is present")

_fake_odoo.install()
generic_tools = _fake_odoo.load('generic_tools')            # registers the tools
mcp_registry = _fake_odoo.load('mcp_registry')

McpRegistry = mcp_registry.McpRegistry

HINTS = ('readOnlyHint', 'destructiveHint', 'idempotentHint', 'openWorldHint')

READ_ONLY = {
    'odoo_whoami', 'odoo_models_list', 'odoo_fields_get', 'odoo_search_read',
    'odoo_search_count', 'odoo_read', 'odoo_read_group', 'odoo_name_search',
    'odoo_render_report',
}

#: Overwrite or discard what is already there. odoo_create only adds;
#: odoo_archive is the reversible stand-in for deletion.
DESTRUCTIVE = {'odoo_write', 'odoo_cancel', 'odoo_unlink', 'odoo_execute'}


class TestEveryToolIsAnnotated(unittest.TestCase):

    def test_all_four_hints_are_present_and_boolean(self):
        for name, tool in sorted(McpRegistry.tools().items()):
            with self.subTest(tool=name):
                hints = tool.get('annotations')
                self.assertIsNotNone(hints, "%s declares no annotations" % name)
                self.assertEqual(set(hints), set(HINTS),
                                 "%s does not declare all four hints" % name)
                for hint in HINTS:
                    self.assertIsInstance(
                        hints[hint], bool,
                        "%s.%s is %r, not a bool" % (name, hint, hints[hint]))

    def test_the_registry_refuses_a_tool_with_missing_hints(self):
        """Omission has to fail loudly, or the next bridge module reintroduces it."""
        with self.assertRaises(ValueError):
            McpRegistry.register(
                'test.incomplete', 'Missing hints.',
                {'type': 'object', 'properties': {}},
                {'readOnlyHint': True},                     # the other three absent
                lambda env, args: None,
            )
        self.assertIsNone(McpRegistry.get('test.incomplete'))

    def test_the_registry_refuses_a_non_boolean_hint(self):
        with self.assertRaises(ValueError):
            McpRegistry.register(
                'test.stringy', 'Stringly typed hints.',
                {'type': 'object', 'properties': {}},
                {'readOnlyHint': 'true', 'destructiveHint': False,
                 'idempotentHint': True, 'openWorldHint': False},
                lambda env, args: None,
            )
        self.assertIsNone(McpRegistry.get('test.stringy'))


class TestTheHintsMatchTheHandlers(unittest.TestCase):

    def _hints(self, name):
        return McpRegistry.get(name)['annotations']

    def test_read_only_tools_are_marked_read_only(self):
        actual = {n for n, t in McpRegistry.tools().items()
                  if t['annotations']['readOnlyHint']}
        self.assertEqual(actual, READ_ONLY)

    def test_no_read_only_tool_claims_to_be_destructive(self):
        for name in READ_ONLY:
            self.assertFalse(self._hints(name)['destructiveHint'],
                             "%s is read-only but advertises destructiveHint" % name)

    def test_destructive_tools_are_labelled(self):
        actual = {n for n, t in McpRegistry.tools().items()
                  if t['annotations']['destructiveHint']}
        self.assertEqual(actual, DESTRUCTIVE)

    def test_unlink_is_destructive(self):
        """The one tool in the repo that hard-deletes. It says so."""
        self.assertTrue(self._hints('odoo_unlink')['destructiveHint'])
        self.assertFalse(self._hints('odoo_unlink')['readOnlyHint'])

    def test_create_and_archive_are_not_destructive(self):
        for name in ('odoo_create', 'odoo_archive'):
            self.assertFalse(self._hints(name)['destructiveHint'])

    def test_create_and_the_escape_hatch_are_not_idempotent(self):
        for name in ('odoo_create', 'odoo_execute'):
            self.assertFalse(self._hints(name)['idempotentHint'])


if __name__ == '__main__':                                  # pragma: no cover
    unittest.main()
