"""Test scaffolding for the Odoo MCP server.

Runs with ONLY pytest installed — the `mcp` SDK is stubbed out before server.py is
imported (its @mcp.tool() decorator just needs to return the function unchanged), and
Odoo itself is replaced by FakeModels, an execute_kw recorder. No network, no live
instance, no credentials.
"""
from __future__ import annotations

import sys
import types
import xmlrpc.client
from pathlib import Path

import pytest

# ---------- Stub the mcp SDK before importing server.py ----------
#
# The suite runs with only pytest installed, which keeps it fast and offline.
# The cost is that it cannot see the real SDK at all — when mcp 2.0 renamed
# FastMCP to MCPServer, every test still passed against a stub of the OLD API
# while the actual image could not import server.py. So two things matter here:
# the stub must track the module path the code really imports, and it must
# RECORD what run() was asked for, so the transport wiring that moved from
# `mcp.settings` to run() kwargs in v2 is covered by something.
# test_server.py::TestTransportWiring is that cover.


class _StubMCPServer:
    """Stands in for mcp.server.mcpserver.MCPServer."""

    def __init__(self, *a, **k):
        self.run_calls: list[dict] = []

    def tool(self, *a, **k):
        def deco(fn):
            return fn
        return deco

    def run(self, transport="stdio", **kwargs):
        # Records rather than serves: _run() is worth testing, a live server is
        # not something a unit test should ever start.
        self.run_calls.append(dict(transport=transport, **kwargs))


class _StubTransportSecuritySettings:
    """Stands in for the SDK's settings object: a record of what was asked for."""

    def __init__(self, enable_dns_rebinding_protection=True,
                 allowed_hosts=None, allowed_origins=None):
        self.enable_dns_rebinding_protection = enable_dns_rebinding_protection
        self.allowed_hosts = allowed_hosts or []
        self.allowed_origins = allowed_origins or []


def _real_sdk_available() -> bool:
    """Is the genuine SDK importable, with the API server.py targets?"""
    try:
        import mcp.server.mcpserver           # noqa: F401
        import mcp.server.transport_security  # noqa: F401
    except Exception:
        return False
    return True


def _install_mcp_stub():
    """Stub the SDK — but ONLY when it is genuinely absent.

    Deferring to a real installation is the whole point. `setdefault` alone was
    not enough: nothing has imported `mcp` this early, so the stub always won,
    even inside the image where the SDK is installed. That is precisely how a
    renamed entry point in 2.0 reached a built container with a green suite.
    Where the real SDK is present — the image, a dev box with requirements
    applied — the tests now run against it and an incompatible release fails
    here rather than at runtime.
    """
    if _real_sdk_available():
        return False
    mcp_pkg = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    mcpserver_mod = types.ModuleType("mcp.server.mcpserver")
    security_mod = types.ModuleType("mcp.server.transport_security")
    mcpserver_mod.MCPServer = _StubMCPServer
    security_mod.TransportSecuritySettings = _StubTransportSecuritySettings
    mcp_pkg.server = server_mod
    server_mod.mcpserver = mcpserver_mod
    server_mod.transport_security = security_mod
    sys.modules.setdefault("mcp", mcp_pkg)
    sys.modules.setdefault("mcp.server", server_mod)
    sys.modules.setdefault("mcp.server.mcpserver", mcpserver_mod)
    sys.modules.setdefault("mcp.server.transport_security", security_mod)
    return True


#: True when the SDK is stubbed, False when the tests run against a real one.
#: Not the same as "is `mcp` importable": an OLD mcp leaves the package real
#: but `mcp.server.mcpserver` stubbed. Tests that need the genuine SDK gate on
#: this flag, never on the import.
MCP_IS_STUBBED = _install_mcp_stub()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audit  # noqa: E402
import server  # noqa: E402  (import after the stub, deliberately)


# ---------- Fake Odoo ----------


class FakeModels:
    """Stands in for the XML-RPC `models` proxy. Records every execute_kw call and
    returns a canned value (or raises a canned exception)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.next_result = True
        self.next_exception: Exception | None = None

    def execute_kw(self, db, uid, api_key, model, method, args, kwargs):
        self.calls.append({
            "db": db, "uid": uid, "api_key": api_key,
            "model": model, "method": method, "args": args, "kwargs": kwargs,
        })
        if self.next_exception is not None:
            exc, self.next_exception = self.next_exception, None
            raise exc
        return self.next_result

    @property
    def last(self) -> dict:
        assert self.calls, "expected at least one execute_kw call"
        return self.calls[-1]


def marshal_none_fault() -> xmlrpc.client.Fault:
    return xmlrpc.client.Fault(1, "cannot marshal None unless allow_none is enabled")


@pytest.fixture()
def fake_models():
    return FakeModels()


@pytest.fixture()
def session(fake_models, monkeypatch):
    """An authenticated session with BOTH policy controls in the default (locked) state."""
    state = {
        "url": "https://odoo.example.test", "db": "testdb", "uid": 2,
        "username": "tester@example.test", "api_key": "test-key",
        "models": fake_models,
        "allow_record_deletion": False,
        "allow_model_changes": False,
    }
    monkeypatch.setattr(server, "_state", state)
    return state


# ---------- Keep the suite off the working tree ----------


@pytest.fixture(autouse=True)
def isolated_audit_sink(tmp_path, monkeypatch):
    """No test may leave a real audit trail behind.

    `configure(install=True)` sets a module-level `_ACTIVE` that outlives the
    test that made it, and its unconfigured default is a file beside the config
    — the repo root when pytest runs from there. So a single `configure({})`
    anywhere in the suite silently turns every later tool-call test into a
    writer of `mcp-audit.jsonl` in the working tree.

    Point the default at `tmp_path` and put `_ACTIVE` back afterwards, so each
    test starts from a clean sink and none of it reaches the repo.
    """
    monkeypatch.setenv("MCP_AUDIT_LOG", str(tmp_path / "autouse-audit.jsonl"))
    previous = audit._ACTIVE
    monkeypatch.setattr(audit, "_ACTIVE",
                        audit.AuditLog(path=tmp_path / "autouse-active.jsonl"))
    yield
    audit._ACTIVE = previous
