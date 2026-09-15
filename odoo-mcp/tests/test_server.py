"""Tests for the Odoo MCP server's policy controls and tool plumbing.

These pin the HARD controls the whole plugin's safety posture rests on:
  1. allow_record_deletion=False → unlink refused on every model, via every tool path.
  2. allow_model_changes=False   → writes to protected/technical models refused;
     reads on those same models still allowed.
plus the load-bearing plumbing: the marshal-None fault treated as success, the
company-context injection for company-dependent fields, archive semantics, and the
not-connected error. Run: `python -m pytest odoo-mcp/tests -q` (needs only pytest).
"""
from __future__ import annotations

import xmlrpc.client

import pytest

from conftest import FakeModels, marshal_none_fault
import server


# ---------- Control 2: no hard deletes, any model, any path ----------


class TestDeletionBlocked:
    @pytest.mark.parametrize("model", ["res.partner", "account.move", "ir.attachment", "x_custom.model"])
    def test_unlink_refused_on_every_model(self, session, fake_models, model):
        with pytest.raises(server.GuardrailError, match="Deletion is disabled"):
            server.odoo_execute(model, "unlink", [[1, 2]])
        assert fake_models.calls == [], "guard must refuse BEFORE anything reaches Odoo"

    def test_unlink_error_names_the_alternatives(self, session):
        with pytest.raises(server.GuardrailError, match="odoo_archive.*odoo_cancel"):
            server.odoo_execute("res.partner", "unlink", [[1]])

    def test_unlink_allowed_when_flag_lifted(self, session, fake_models):
        session["allow_record_deletion"] = True
        server.odoo_execute("res.partner", "unlink", [[1]])
        assert fake_models.last["method"] == "unlink"

    def test_archive_is_the_sanctioned_removal(self, session, fake_models):
        server.odoo_archive("res.partner", [7])
        assert fake_models.last["method"] == "write"
        assert fake_models.last["args"] == [[7], {"active": False}]

    def test_unarchive_restores(self, session, fake_models):
        server.odoo_archive("res.partner", [7], archive=False)
        assert fake_models.last["args"] == [[7], {"active": True}]


# ---------- Control 1: no model/structure tampering ----------

# Every protected prefix, exercised both as the bare model and a dotted child.
_PROTECTED_CASES = [
    p for prefix in server._PROTECTED_MODEL_PREFIXES for p in (prefix, prefix + ".child")
]


class TestModelTamperingBlocked:
    @pytest.mark.parametrize("model", _PROTECTED_CASES)
    def test_write_refused_on_every_protected_model(self, session, fake_models, model):
        with pytest.raises(server.GuardrailError, match="disabled by policy"):
            server.odoo_write(model, [1], {"name": "x"})
        assert fake_models.calls == []

    @pytest.mark.parametrize("model", ["ir.model", "res.users", "res.groups", "ir.cron",
                                       "base.automation", "mail.template", "res.users.apikeys"])
    def test_create_refused(self, session, fake_models, model):
        with pytest.raises(server.GuardrailError):
            server.odoo_create(model, {"name": "x"})
        assert fake_models.calls == []

    def test_escape_hatch_is_also_guarded(self, session, fake_models):
        # odoo_execute must not be a bypass — e.g. self-privilege-escalation via res.users.
        with pytest.raises(server.GuardrailError):
            server.odoo_execute("res.users", "write", [[2], {"groups_id": [[4, 1]]}])
        with pytest.raises(server.GuardrailError):
            server.odoo_execute("ir.module.module", "button_immediate_install", [[42]])
        assert fake_models.calls == []

    def test_archive_and_cancel_paths_are_guarded_too(self, session, fake_models):
        with pytest.raises(server.GuardrailError):
            server.odoo_archive("res.users", [9])
        with pytest.raises((server.GuardrailError, RuntimeError)):
            server.odoo_cancel("ir.cron", [3])
        assert fake_models.calls == []

    @pytest.mark.parametrize("model", _PROTECTED_CASES)
    def test_reads_on_protected_models_still_allowed(self, session, fake_models, model):
        fake_models.next_result = []
        server.odoo_search_read(model, domain=[], fields=["id"])
        assert fake_models.last["model"] == model

    def test_fields_get_allowed_on_protected_model(self, session, fake_models):
        fake_models.next_result = {}
        server.odoo_fields_get("ir.model.fields")
        assert fake_models.last["method"] == "fields_get"

    def test_write_allowed_when_flag_lifted(self, session, fake_models):
        session["allow_model_changes"] = True
        server.odoo_write("ir.cron", [1], {"active": False})
        assert fake_models.last["method"] == "write"

    def test_prefix_match_is_precise_not_greedy(self):
        # Dotted children are protected…
        assert server._is_protected_model("ir.model.fields")
        assert server._is_protected_model("res.users.apikeys")
        # …but similarly-spelled OTHER models must stay writable.
        assert not server._is_protected_model("res.userstuff")
        assert not server._is_protected_model("ir.modelling")
        # And ordinary business models are untouched.
        for m in ("account.move", "res.partner", "account.account", "ir.attachment"):
            assert not server._is_protected_model(m)

    def test_business_writes_pass_through(self, session, fake_models):
        fake_models.next_result = 101
        assert server.odoo_create("account.move", {"move_type": "entry"}) == 101
        server.odoo_write("account.account", [5], {"name": "Renamed"})
        assert [c["model"] for c in fake_models.calls] == ["account.move", "account.account"]


# ---------- Fault handling ----------


class TestFaultHandling:
    def test_marshal_none_fault_is_success(self, session, fake_models):
        # action_post etc. return None; some endpoints can't marshal it. The method has
        # ALREADY committed — the server must report success (None), not an error.
        fake_models.next_exception = marshal_none_fault()
        assert server.odoo_execute("account.move", "action_post", [[7]]) is None

    def test_other_faults_surface_with_the_fault_string(self, session, fake_models):
        fake_models.next_exception = xmlrpc.client.Fault(2, "Journal/company mismatch")
        with pytest.raises(RuntimeError, match="Journal/company mismatch"):
            server.odoo_write("account.move", [7], {"company_id": 14})


# ---------- Company context (Odoo 18 company-dependent fields) ----------


class TestCompanyContext:
    def test_company_id_injects_context(self, session, fake_models):
        fake_models.next_result = []
        server.odoo_search_read("account.account", domain=[["company_ids", "in", [14]]],
                                fields=["code", "name"], company_id=14)
        ctx = fake_models.last["kwargs"]["context"]
        assert ctx == {"allowed_company_ids": [14], "company_id": 14}

    def test_no_company_id_no_context(self):
        assert server._company_ctx(None) is None


# ---------- Session ----------


class TestSession:
    def test_not_connected_raises_with_guidance(self, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "_state", {})
        monkeypatch.setattr(server, "_CONFIG_PATH", tmp_path / "missing.json")
        with pytest.raises(RuntimeError):
            server.odoo_search_read("res.partner")

    def test_config_creds_autoconnect_fallback(self, monkeypatch, tmp_path):
        # Legacy single-user setup: creds in config → _session auto-connects.
        cfg = tmp_path / "odoo_config.json"
        cfg.write_text('{"url": "https://x.test", "db": "d", "username": "u", "api_key": "k"}')
        monkeypatch.setattr(server, "_state", {})
        monkeypatch.setattr(server, "_CONFIG_PATH", cfg)
        seen = {}

        def fake_auth(username, api_key, db=None, url=None):
            seen.update(u=username, k=api_key)
            return {"models": FakeModels(), "db": "d", "uid": 1, "api_key": "k", "url": "https://x.test"}

        monkeypatch.setattr(server, "_authenticate", fake_auth)
        server._session()
        assert seen == {"u": "u", "k": "k"}

    def test_guard_flags_come_from_config_defaults_locked(self, monkeypatch, tmp_path):
        cfg = tmp_path / "odoo_config.json"
        cfg.write_text('{"url": "https://x.test", "db": "d"}')
        monkeypatch.setattr(server, "_CONFIG_PATH", cfg)
        monkeypatch.setattr(server, "_state", {})
        monkeypatch.setattr(server, "_make_proxies",
                            lambda url: (type("C", (), {"authenticate": staticmethod(lambda *a: 2)})(),
                                         FakeModels()))
        state = server._authenticate("u", "k")
        assert state["allow_record_deletion"] is False
        assert state["allow_model_changes"] is False


# ---------- Misc tool plumbing ----------


class TestToolPlumbing:
    def test_render_report_rejects_bad_converter(self, session):
        with pytest.raises(RuntimeError, match="converter must be one of"):
            server.odoo_render_report("account.report_invoice", [1], converter="docx")

    def test_cancel_tries_action_then_button(self, session, fake_models):
        # First method faults (a real fault, not marshal-None) → falls through to button_cancel.
        fake_models.next_exception = xmlrpc.client.Fault(3, "no action_cancel")
        result = server.odoo_cancel("sale.order", [4])
        assert result["method"] == "button_cancel"
        assert [c["method"] for c in fake_models.calls] == ["action_cancel", "button_cancel"]


# ---------- Control 3: no private methods ----------


class TestPrivateMethodsBlocked:
    """Odoo's `_`-prefixed methods are ORM internals. `_write` skips the
    `write()` override chain — and with it the access checks, constraints and
    tracking that make a change auditable — and `_unlink` reaches around the
    deletion control entirely. This is the control that stops odoo_execute
    being a way around the other two."""

    PRIVATE = ["_write", "_unlink", "_create", "__class__", "_name", "_inherit",
               "_compute_field", "__init__", "_search"]

    @pytest.mark.parametrize("method", PRIVATE)
    def test_private_methods_are_refused(self, session, fake_models, method):
        with pytest.raises(server.GuardrailError, match="private method"):
            server.odoo_execute("res.partner", method, [[1]])
        assert fake_models.calls == [], "refused only AFTER reaching Odoo"

    @pytest.mark.parametrize("method", PRIVATE)
    def test_refused_on_ordinary_models_too(self, session, fake_models, method):
        # Not a structural-model question: `_write` on res.partner is just as
        # much a bypass of the write path as `_write` on ir.model.
        with pytest.raises(server.GuardrailError):
            server.odoo_execute("account.move", method, [[1]])
        assert fake_models.calls == []

    def test_an_empty_method_is_refused(self, session, fake_models):
        with pytest.raises(server.GuardrailError, match="private method"):
            server.odoo_execute("res.partner", "", [[1]])
        assert fake_models.calls == []

    def test_the_block_is_not_overridable(self, session, fake_models):
        # Controls 1 and 2 have config escape hatches by design. This one does
        # not: "let the agent call ORM internals" is not a posture anyone needs.
        session["allow_model_changes"] = True
        session["allow_record_deletion"] = True
        with pytest.raises(server.GuardrailError, match="private method"):
            server.odoo_execute("res.partner", "_write", [[1], {"name": "x"}])
        assert fake_models.calls == []

    def test_public_methods_still_pass(self, session, fake_models):
        # The guardrail must not cost the escape hatch its purpose.
        for method in ("action_post", "button_confirm", "name_get", "write"):
            fake_models.calls.clear()
            server.odoo_execute("account.move", method, [[1]])
            assert fake_models.calls, "%s should have reached Odoo" % method


# ---------- SDK v2 transport wiring ----------


class TestTransportWiring:
    """In v1 the transport was configured by mutating `mcp.settings` before
    calling run(); in v2 `settings` is gone and the same values are run()
    arguments. Nothing covered that wiring, which is how the 1.x -> 2.x break
    reached a built image unnoticed — the stub was of the old API and every
    test passed. These assert what _run() actually asks the SDK for."""

    @pytest.fixture()
    def run_calls(self, monkeypatch):
        """Record what _run() asks the SDK for, without ever serving.

        Patches `run` on the server object rather than reading the stub's own
        log, so these pass identically against the stub and against a real
        installed SDK — and so _run() can never start a listener in a test.
        """
        calls: list[dict] = []
        monkeypatch.setattr(
            server.mcp, "run",
            lambda transport="stdio", **kw: calls.append(dict(transport=transport, **kw)))
        for var in ("MCP_TRANSPORT", "MCP_HOST", "MCP_PORT", "MCP_PATH",
                    "MCP_ALLOWED_HOSTS"):
            monkeypatch.delenv(var, raising=False)
        return calls

    def test_stdio_is_the_default_and_takes_no_options(self, run_calls):
        server._run()
        assert run_calls == [{"transport": "stdio"}]

    def test_streamable_http_passes_host_port_and_path(self, run_calls, monkeypatch):
        monkeypatch.setenv("MCP_TRANSPORT", "http")
        monkeypatch.setenv("MCP_HOST", "127.0.0.1")
        monkeypatch.setenv("MCP_PORT", "9001")
        monkeypatch.setenv("MCP_PATH", "/odoo-mcp")
        server._run()
        call = run_calls[-1]
        assert call["transport"] == "streamable-http"
        assert call["host"] == "127.0.0.1"
        assert call["port"] == 9001 and isinstance(call["port"], int)
        assert call["streamable_http_path"] == "/odoo-mcp"

    @pytest.mark.parametrize("alias", ["http", "streamable-http", "streamable_http"])
    def test_every_http_alias_reaches_the_same_transport(self, run_calls, monkeypatch, alias):
        monkeypatch.setenv("MCP_TRANSPORT", alias)
        server._run()
        assert run_calls[-1]["transport"] == "streamable-http"

    def test_http_defaults_match_the_documented_ones(self, run_calls, monkeypatch):
        monkeypatch.setenv("MCP_TRANSPORT", "http")
        server._run()
        call = run_calls[-1]
        assert call["host"] == "0.0.0.0"     # container-reachable, as documented
        assert call["port"] == 8000
        assert call["streamable_http_path"] == "/mcp"

    def test_sse_passes_host_and_port_but_no_http_path(self, run_calls, monkeypatch):
        monkeypatch.setenv("MCP_TRANSPORT", "sse")
        monkeypatch.setenv("MCP_PORT", "9002")
        server._run()
        call = run_calls[-1]
        assert call["transport"] == "sse"
        assert call["port"] == 9002
        assert "streamable_http_path" not in call     # not an SSE option

    def test_transport_security_reaches_the_sdk(self, run_calls, monkeypatch):
        # The control that stops a reverse-proxied host being 421'd. It used to
        # be set on mcp.settings; if it stopped being passed, the server would
        # still start and only fail behind a tunnel.
        monkeypatch.setenv("MCP_TRANSPORT", "http")
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "odoo.example.com")
        server._run()
        security = run_calls[-1]["transport_security"]
        assert security.enable_dns_rebinding_protection is True
        assert "odoo.example.com" in security.allowed_hosts

    def test_unset_allowed_hosts_disables_rebinding_checks(self, run_calls, monkeypatch):
        monkeypatch.setenv("MCP_TRANSPORT", "http")
        server._run()
        security = run_calls[-1]["transport_security"]
        assert security.enable_dns_rebinding_protection is False


class TestSdkPin:
    """The suite stubs the SDK, so it cannot notice a breaking SDK release on
    its own. An UNBOUNDED requirement is what turned that blind spot into a
    broken image when 2.0 landed, so pin the bound itself."""

    def _requirement(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
        lines = [l.strip() for l in text.splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        return next(l for l in lines if l.startswith("mcp"))

    def test_the_mcp_requirement_has_an_upper_bound(self):
        assert "<" in self._requirement(), (
            "mcp is pinned without an upper bound — the next major release will "
            "be installed into the image unnoticed, and the stub in conftest "
            "means no test will fail until someone runs the container"
        )

    def test_the_pin_matches_the_api_the_code_imports(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent / "server.py").read_text()
        requirement = self._requirement()
        if "mcp.server.mcpserver" in source:
            assert ">=2" in requirement, "v2 import path but the pin allows v1"
        elif "mcp.server.fastmcp" in source:
            assert "<2" in requirement, "v1 import path but the pin allows v2"
        else:
            raise AssertionError("server.py imports the SDK from an unknown path")


class TestAgainstTheRealSdk:
    """Runs only where the real SDK is installed (the image, a dev machine with
    requirements applied). This is the check the stub structurally cannot do:
    that the API server.py targets actually exists in the pinned release."""

    @pytest.fixture()
    def real_sdk(self):
        # conftest is the authority. "Is `mcp` importable?" is NOT the same
        # question: a machine can have an OLD mcp installed, in which case the
        # package is real but `mcp.server.mcpserver` is the stub — exactly the
        # half-real state these tests exist to distinguish.
        import conftest
        if conftest.MCP_IS_STUBBED:
            pytest.skip("SDK stubbed by conftest — no matching real install")

    def test_the_server_entry_point_exists(self, real_sdk):
        from mcp.server.mcpserver import MCPServer
        assert callable(MCPServer)

    def test_transport_security_settings_still_takes_our_arguments(self, real_sdk):
        from mcp.server.transport_security import TransportSecuritySettings
        settings = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["a.example"], allowed_origins=["https://a.example"])
        assert settings.enable_dns_rebinding_protection is True

    def test_run_accepts_the_options_we_pass(self, real_sdk):
        import inspect
        from mcp.server.mcpserver import MCPServer
        http = inspect.signature(MCPServer.run_streamable_http_async).parameters
        for option in ("host", "port", "streamable_http_path", "transport_security"):
            assert option in http, "run_streamable_http_async dropped %s" % option
        sse = inspect.signature(MCPServer.run_sse_async).parameters
        for option in ("host", "port", "transport_security"):
            assert option in sse, "run_sse_async dropped %s" % option
