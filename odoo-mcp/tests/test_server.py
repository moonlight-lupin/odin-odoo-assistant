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
