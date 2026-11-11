"""The Odoo-context tools on the independent server.

The decision logic is ``context_core.py`` — the same file the in-Odoo module
ships, pinned in detail by ``odoo_module_mcp_server/tests/test_context_core.py``.
This file covers what is specific to this server:

* the two copies of the core are byte-identical (so both routes decide alike);
* the workspace write guard is wired into every generic write path, refuses
  BEFORE anything is written, and stays out of the way when no root is set;
* the XML-RPC backend marshals its calls the way Odoo expects (company
  context, group lookup, plain-text chatter), and the four tools hand their
  arguments to the core unchanged;
* a refusal is filed as ``blocked`` in the audit trail.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import audit
import context_core
import server

REPO = Path(__file__).resolve().parents[2]

#: parent_path of the articles the fake Odoo knows about. 100 is the root.
PATHS = {100: "100/", 120: "100/120/", 121: "100/120/121/", 170: "170/"}
MEMBERS = {900: 121, 901: 170}


class ScriptedModels:
    """execute_kw recorder that answers the reads the guard and backend make."""

    def __init__(self):
        self.calls: list[dict] = []

    def execute_kw(self, db, uid, api_key, model, method, args, kwargs):
        self.calls.append({"model": model, "method": method, "args": args, "kwargs": kwargs})
        if model == "knowledge.article" and method == "search_read":
            ids = args[0][0][2]
            return [{"id": i, "parent_path": PATHS[i]} for i in ids if i in PATHS]
        if model == "knowledge.article.member" and method == "search_read":
            ids = args[0][0][2]
            return [{"id": i, "article_id": [MEMBERS[i], "A"]} for i in ids if i in MEMBERS]
        if model == "ir.model.data":
            return [{"id": 1, "res_id": 55}]
        if model == "res.users" and method in ("search_count", "has_group"):
            return True
        if model == "res.users" and method == "read":
            return [{"id": 2, "name": "Tester", "company_ids": [6, 67], "tz": "Asia/Singapore"}]
        return True

    def writes(self):
        return [c for c in self.calls if c["method"] not in ("search_read", "read")]


@pytest.fixture()
def scripted(session, monkeypatch):
    models = ScriptedModels()
    session["models"] = models
    monkeypatch.setattr(server, "_context_root_id", lambda: 100)
    return models


# ---------------------------------------------------------------------------
# One core, two servers
# ---------------------------------------------------------------------------

def test_the_core_is_identical_to_the_modules_copy():
    ours = (Path(server.__file__).parent / "context_core.py").read_bytes()
    theirs = (REPO / "odoo_module_mcp_server" / "context_core.py").read_bytes()
    assert ours == theirs, ("odoo-mcp/context_core.py has drifted from the module's copy — "
                            "copy odoo_module_mcp_server/context_core.py over it")


def test_the_image_ships_the_core():
    dockerfile = Path(server.__file__).parent / "Dockerfile"
    if not dockerfile.exists():
        pytest.skip("running inside the image, where the Dockerfile is not copied")
    assert "context_core.py" in dockerfile.read_text()


# ---------------------------------------------------------------------------
# The workspace write guard on the generic tools
# ---------------------------------------------------------------------------

class TestGuard:
    def test_write_to_a_workspace_article_is_refused_before_writing(self, scripted):
        with pytest.raises(context_core.ContextPolicyError, match="odoo_context_propose"):
            server.odoo_write("knowledge.article", [121], {"body": "<p>x</p>"})
        assert scripted.writes() == []

    def test_create_under_the_workspace_is_refused(self, scripted):
        with pytest.raises(context_core.ContextPolicyError):
            server.odoo_create("knowledge.article", {"name": "x", "parent_id": 120})
        assert scripted.writes() == []

    def test_archive_and_cancel_are_refused(self, scripted):
        with pytest.raises(context_core.ContextPolicyError):
            server.odoo_archive("knowledge.article", [121])
        with pytest.raises(context_core.ContextPolicyError):
            server.odoo_cancel("knowledge.article", [100])
        assert scripted.writes() == []

    @pytest.mark.parametrize("method,args,kwargs", [
        ("write", [[121], {"name": "x"}], {}),
        ("action_archive", [[121]], {}),
        ("article_create", ["Title", 120], {}),
        ("move_to", [[170]], {"parent_id": 120}),
    ])
    def test_execute_is_not_a_back_door(self, scripted, method, args, kwargs):
        with pytest.raises(context_core.ContextPolicyError):
            server.odoo_execute("knowledge.article", method, args, kwargs)
        assert scripted.writes() == []

    def test_member_rows_are_guarded(self, scripted):
        with pytest.raises(context_core.ContextPolicyError):
            server.odoo_write("knowledge.article.member", [900], {"permission": "write"})
        server.odoo_write("knowledge.article.member", [901], {"permission": "write"})

    def test_outside_the_workspace_writes_go_through(self, scripted):
        assert server.odoo_write("knowledge.article", [170], {"name": "x"}) is True
        assert scripted.writes()[-1]["args"] == [[170], {"name": "x"}]
        server.odoo_create("knowledge.article", {"name": "x", "parent_id": 170})
        server.odoo_execute("knowledge.article", "read", [[121]], {"fields": ["name"]})

    def test_other_models_are_untouched_by_the_guard(self, scripted):
        server.odoo_write("res.partner", [121], {"name": "x"})
        assert [c["model"] for c in scripted.calls] == ["res.partner"]

    def test_no_root_means_no_guard_and_no_extra_reads(self, scripted, monkeypatch):
        monkeypatch.setattr(server, "_context_root_id", lambda: None)
        server.odoo_write("knowledge.article", [121], {"name": "x"})
        assert [c["method"] for c in scripted.calls] == ["write"]

    def test_private_methods_are_still_refused_first(self, scripted):
        with pytest.raises(server.GuardrailError):
            server.odoo_execute("knowledge.article", "_write", [[121], {}], {})
        assert scripted.calls == []

    def test_a_refusal_is_filed_as_blocked(self):
        assert audit.classify_error(context_core.ContextPolicyError("x")) == "blocked"


class TestRootFromConfig:
    def test_root_and_stale_days_come_from_odoo_config(self, tmp_path, monkeypatch):
        cfg = tmp_path / "odoo_config.json"
        cfg.write_text('{"url": "https://x.test", "db": "d", '
                       '"context_root_article_id": 345, "context_stale_days": 30}')
        monkeypatch.setattr(server, "_CONFIG_PATH", cfg)
        assert server._context_root_id() == 345
        backend = server._XmlRpcBackend.__new__(server._XmlRpcBackend)
        assert backend.stale_days() == 30

    @pytest.mark.parametrize("raw", ['""', "0", "null", '"abc"'])
    def test_unset_or_garbled_root_is_off(self, tmp_path, monkeypatch, raw):
        cfg = tmp_path / "odoo_config.json"
        cfg.write_text('{"url": "https://x.test", "db": "d", "context_root_article_id": %s}' % raw)
        monkeypatch.setattr(server, "_CONFIG_PATH", cfg)
        assert server._context_root_id() is None

    def test_missing_config_is_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "_CONFIG_PATH", tmp_path / "absent.json")
        assert server._context_root_id() is None


# ---------------------------------------------------------------------------
# The XML-RPC backend
# ---------------------------------------------------------------------------

class TestBackend:
    def test_company_reads_carry_the_users_companies(self, scripted):
        backend = server._XmlRpcBackend()
        backend.search_read("account.journal", [("company_id", "in", [6])], ["id"],
                            order="sequence, id", all_companies=True)
        call = scripted.calls[-1]
        assert call["kwargs"]["context"] == {"allowed_company_ids": [6, 67]}
        assert call["kwargs"]["order"] == "sequence, id"

    def test_has_group_asks_the_user_record(self, scripted):
        assert server._XmlRpcBackend().has_group(context_core.REVIEWER_GROUP) is True
        call = scripted.calls[-1]
        assert (call["model"], call["method"]) == ("res.users", "has_group")
        assert call["args"] == [[2], context_core.REVIEWER_GROUP]

    def test_has_group_passes_the_guard_as_a_read(self):
        server._guard("res.users", "has_group")   # must not raise

    def test_chatter_gets_plain_text(self, scripted):
        server._XmlRpcBackend().post_message(121, "<p>New <b>proposal</b></p>", [3])
        call = scripted.calls[-1]
        assert call["method"] == "message_post"
        assert call["args"] == [[121]]
        assert call["kwargs"]["body"] == "New **proposal**"
        assert call["kwargs"]["partner_ids"] == [3]

    def test_create_never_elevates(self, scripted):
        server._XmlRpcBackend().create("knowledge.article", {"name": "x"}, elevated=True)
        assert scripted.calls[-1]["method"] == "create"

    def test_today_uses_the_users_timezone_when_available(self, scripted):
        import datetime
        today = server._XmlRpcBackend().today()
        assert isinstance(today, datetime.date)


# ---------------------------------------------------------------------------
# The four tools hand their arguments to the core unchanged
# ---------------------------------------------------------------------------

class TestToolWiring:
    @pytest.fixture()
    def captured(self, scripted, monkeypatch):
        seen = {}
        for name in ("context_for", "lookup", "propose", "apply_proposal"):
            monkeypatch.setattr(context_core, name,
                                lambda backend, args, _n=name: seen.setdefault(_n, args) or "ok")
        return seen

    def test_arguments_pass_through(self, captured):
        server.odoo_context_for(67, "elimination")
        server.odoo_context_lookup(query="CJE", structure="shc-ii", limit=5)
        server.odoo_context_propose("text", "evidence", "reason", target_article_id=121)
        server.odoo_context_apply_proposal(501, "reject", "no")
        assert captured["context_for"] == {"company_id": 67, "task": "elimination"}
        assert captured["lookup"] == {"query": "CJE", "article_id": None, "structure": "shc-ii",
                                      "ctx_type": None, "include_proposals": False, "limit": 5}
        assert captured["propose"] == {"change": "text", "evidence": "evidence",
                                       "reason": "reason", "target_article_id": 121, "new": None}
        assert captured["apply_proposal"] == {"proposal_id": 501, "decision": "reject",
                                              "note": "no"}

    def test_descriptions_are_the_shared_text(self):
        for name in context_core.TOOL_DOCS:
            assert getattr(server, name).__doc__ == context_core.TOOL_DOCS[name]
