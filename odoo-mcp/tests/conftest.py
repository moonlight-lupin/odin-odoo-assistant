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


class _StubFastMCP:
    def __init__(self, *a, **k):
        self.settings = types.SimpleNamespace()

    def tool(self, *a, **k):
        def deco(fn):
            return fn
        return deco

    def run(self, *a, **k):  # never called in tests
        raise RuntimeError("mcp.run() must not run in tests")


class _StubTransportSecuritySettings:
    """Stands in for the SDK's settings object: a record of what was asked for."""

    def __init__(self, enable_dns_rebinding_protection=True,
                 allowed_hosts=None, allowed_origins=None):
        self.enable_dns_rebinding_protection = enable_dns_rebinding_protection
        self.allowed_hosts = allowed_hosts or []
        self.allowed_origins = allowed_origins or []


def _install_mcp_stub():
    mcp_pkg = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    security_mod = types.ModuleType("mcp.server.transport_security")
    fastmcp_mod.FastMCP = _StubFastMCP
    security_mod.TransportSecuritySettings = _StubTransportSecuritySettings
    mcp_pkg.server = server_mod
    server_mod.fastmcp = fastmcp_mod
    server_mod.transport_security = security_mod
    sys.modules.setdefault("mcp", mcp_pkg)
    sys.modules.setdefault("mcp.server", server_mod)
    sys.modules.setdefault("mcp.server.fastmcp", fastmcp_mod)
    sys.modules.setdefault("mcp.server.transport_security", security_mod)


_install_mcp_stub()

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
