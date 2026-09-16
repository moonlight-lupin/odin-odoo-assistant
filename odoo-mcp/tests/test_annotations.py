"""Every tool advertises all four MCP behavioural hints, and they tell the truth.

`readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` are what
a host shows the user before it runs a tool — "this only reads the ledger" vs
"this can overwrite posted entries". Omitting them is not cosmetic. The spec's
defaults are deliberately pessimistic (`destructiveHint` defaults to **true**),
so an unannotated read tool is advertised as capable of destroying data, while
some directories reject a tool whose hints are absent or non-boolean outright.
Either way the user loses the signal that matters.

So the hints are asserted here rather than left to review: all four present on
every tool, all four boolean, and consistent with what the handler actually
does. `server.TOOL_ANNOTATIONS` is the plain-dict record `_tool()` keeps as it
registers, which lets the offline suite (SDK stubbed — see conftest) assert on
exactly the values a real SDK would receive; one test, skipped when the SDK is
absent, checks that they do in fact arrive.
"""
from __future__ import annotations

import pytest

import server

HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

#: The tools that only ever read. Everything else mutates something — Odoo
#: records, or (connect/disconnect) the server's own session.
READ_ONLY = {
    "odoo_whoami", "odoo_search_read", "odoo_search_count", "odoo_read_group",
    "odoo_name_search", "odoo_read", "odoo_fields_get", "odoo_render_report",
    "odoo_audit_tail",
}

#: The tools that can overwrite or discard existing data, and so must carry
#: `destructiveHint: true`. odoo_create only ever adds; odoo_archive is
#: reversible by design (it is the *alternative* to deletion); odoo_connect and
#: odoo_disconnect touch session state, not the books.
DESTRUCTIVE = {"odoo_write", "odoo_cancel", "odoo_execute"}

#: Tools that never leave the machine: one clears cached session state, the
#: other reads a local log file. Every other tool reaches a live Odoo.
CLOSED_WORLD = {"odoo_disconnect", "odoo_audit_tail"}


def registered_tools() -> set[str]:
    """Tool functions as they exist on the module, independent of the registry."""
    return {
        name for name in dir(server)
        if name.startswith("odoo_") and callable(getattr(server, name))
    }


class TestEveryToolIsAnnotated:

    def test_the_registry_covers_exactly_the_registered_tools(self):
        """A tool added without going through `_tool()` would show up here."""
        assert set(server.TOOL_ANNOTATIONS) == registered_tools()

    @pytest.mark.parametrize("name", sorted(registered_tools()))
    def test_all_four_hints_are_present_and_boolean(self, name):
        hints = server.TOOL_ANNOTATIONS[name]
        assert set(hints) == set(HINTS), f"{name} does not declare all four hints"
        for hint in HINTS:
            assert isinstance(hints[hint], bool), \
                f"{name}.{hint} is {hints[hint]!r}, not a bool"


class TestTheHintsMatchTheHandlers:

    def test_read_only_tools_are_marked_read_only(self):
        actual = {n for n, h in server.TOOL_ANNOTATIONS.items() if h["readOnlyHint"]}
        assert actual == READ_ONLY

    def test_no_read_only_tool_claims_to_be_destructive(self):
        """The two hints contradict each other; a host reading both gets nonsense."""
        for name in READ_ONLY:
            assert server.TOOL_ANNOTATIONS[name]["destructiveHint"] is False, \
                f"{name} is read-only but advertises destructiveHint"

    def test_destructive_tools_are_labelled(self):
        actual = {n for n, h in server.TOOL_ANNOTATIONS.items() if h["destructiveHint"]}
        assert actual == DESTRUCTIVE

    def test_writes_and_the_escape_hatch_are_destructive(self):
        """The specific claim worth pinning: these overwrite what is already there."""
        for name in ("odoo_write", "odoo_execute"):
            assert server.TOOL_ANNOTATIONS[name]["destructiveHint"] is True

    def test_create_and_archive_are_not_destructive(self):
        """odoo_create only adds; odoo_archive is the reversible stand-in for delete."""
        for name in ("odoo_create", "odoo_archive"):
            assert server.TOOL_ANNOTATIONS[name]["destructiveHint"] is False

    def test_create_and_the_escape_hatch_are_not_idempotent(self):
        """Calling either twice does not leave the database where once did."""
        for name in ("odoo_create", "odoo_execute"):
            assert server.TOOL_ANNOTATIONS[name]["idempotentHint"] is False

    def test_only_the_local_tools_are_closed_world(self):
        actual = {n for n, h in server.TOOL_ANNOTATIONS.items() if not h["openWorldHint"]}
        assert actual == CLOSED_WORLD


@pytest.mark.skipif(server.ToolAnnotations is None,
                    reason="the SDK is stubbed; nothing to hand annotations to")
class TestTheHintsReachTheSDK:
    """The registry is only a record — what matters is what `tools/list` says."""

    def advertised(self) -> dict[str, dict]:
        import anyio
        tools = anyio.run(server.mcp.list_tools)
        return {
            t.name: t.annotations.model_dump(by_alias=True, exclude_none=True)
            for t in tools if t.annotations is not None
        }

    def test_every_tool_advertises_its_hints(self):
        advertised = self.advertised()
        for name, hints in server.TOOL_ANNOTATIONS.items():
            assert name in advertised, f"{name} reaches the client with no annotations"
            assert {k: advertised[name].get(k) for k in HINTS} == hints
