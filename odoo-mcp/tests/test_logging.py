"""Tests for the MCP server's logging interface (audit trail + diagnostics).

These pin the properties an audit trail is worthless without:
  1. Credentials NEVER reach the log — not in args, not in the session block.
  2. Every tool call produces exactly one record, with the right outcome
     (ok / blocked / error) and a duration.
  3. Logging never breaks a tool call — an unwritable sink, an unserialisable
     payload or a full disk must not turn a working call into a failure.
  4. Nothing is ever written to STDOUT (it is the MCP channel on stdio).

Run: `python -m pytest odoo-mcp/tests -q` (needs only pytest).
"""
from __future__ import annotations

import json
import logging

import pytest

import audit
import server


# ---------- Helpers ----------


def read_lines(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture()
def sink(tmp_path, monkeypatch):
    """An AuditLog writing JSONL to a temp file, installed as the active log."""
    log = audit.AuditLog(path=tmp_path / "mcp-audit.jsonl")
    monkeypatch.setattr(audit, "_ACTIVE", log)
    log.file_path = tmp_path / "mcp-audit.jsonl"
    return log


# ---------- Redaction: credentials must never land in the log ----------


class TestRedaction:
    @pytest.mark.parametrize("key", [
        "api_key", "apiKey", "API_KEY", "password", "passwd", "secret",
        "token", "access_token", "refresh_token", "Authorization",
        "client_secret", "credentials",
    ])
    def test_secret_keys_are_masked(self, key):
        out = audit.redact({key: "hunter2"})
        assert out[key] == audit.REDACTED
        assert "hunter2" not in json.dumps(out)

    def test_redaction_is_recursive(self):
        payload = {"outer": [{"inner": {"api_key": "s3cret"}}], "ok": "keep"}
        out = audit.redact(payload)
        assert "s3cret" not in json.dumps(out)
        assert out["ok"] == "keep"

    def test_non_secret_keys_survive(self):
        out = audit.redact({"model": "account.move", "keyword": "x"})
        assert out == {"model": "account.move", "keyword": "x"}

    def test_odoo_connect_arguments_never_leak(self, session, fake_models, sink):
        fake_models.next_result = []
        audit.record_tool_call(
            tool="odoo_connect",
            args={"username": "tester@example.test", "api_key": "REAL-KEY"},
            outcome="ok", duration_ms=1.0, result={"uid": 2},
        )
        blob = sink.file_path.read_text()
        assert "REAL-KEY" not in blob
        assert "tester@example.test" in blob  # the WHO is the point of an audit trail


# ---------- Truncation: payloads stay bounded ----------


class TestTruncation:
    def test_long_strings_are_capped(self):
        out = audit.redact({"note": "x" * 5000}, max_string=100)
        assert len(out["note"]) < 200
        assert out["note"].startswith("x" * 100)
        assert "more chars" in out["note"]

    def test_long_lists_are_capped(self):
        out = audit.redact({"ids": list(range(500))}, max_items=10)
        assert len(out["ids"]) == 11          # 10 kept + one marker
        assert "more items" in str(out["ids"][-1])

    def test_deep_nesting_is_capped(self):
        deep = current = {}
        for _ in range(50):
            current["next"] = {}
            current = current["next"]
        out = audit.redact(deep, max_depth=5)
        assert json.dumps(out)  # serialises without blowing the recursion limit

    def test_bytes_are_summarised_not_embedded(self):
        out = audit.redact({"content": b"\x00" * 4096})
        assert out["content"] == "<4096 bytes>"

    def test_result_summary_counts_records(self):
        rows = [{"id": i, "name": "row %d" % i} for i in range(200)]
        summary = audit.summarize_result(rows)
        assert summary["count"] == 200
        assert len(summary["ids"]) <= audit.DEFAULT_MAX_ITEMS

    def test_result_summary_passes_scalars_through(self):
        assert audit.summarize_result(True) is True
        assert audit.summarize_result(42) == 42
        assert audit.summarize_result(None) is None


# ---------- The audit decorator on real tools ----------


class TestToolAuditing:
    def test_successful_call_is_recorded_once(self, session, fake_models, sink):
        fake_models.next_result = [{"id": 1}]
        server.odoo_search_read("res.partner", [], ["id"])
        records = read_lines(sink.file_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["tool"] == "odoo_search_read"
        assert rec["outcome"] == "ok"
        assert rec["kind"] == "tool_call"
        assert isinstance(rec["duration_ms"], (int, float))
        assert rec["args"]["model"] == "res.partner"

    def test_guardrail_block_is_recorded_as_blocked(self, session, sink):
        with pytest.raises(server.GuardrailError):
            server.odoo_execute("res.partner", "unlink", [[1]])
        rec = read_lines(sink.file_path)[-1]
        assert rec["outcome"] == "blocked"
        assert rec["error"]["type"] == "GuardrailError"
        assert "Deletion is disabled" in rec["error"]["message"]

    def test_failure_is_recorded_and_still_raises(self, session, fake_models, sink):
        fake_models.next_exception = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            server.odoo_search_read("res.partner")
        rec = read_lines(sink.file_path)[-1]
        assert rec["outcome"] == "error"
        assert "boom" in rec["error"]["message"]

    def test_session_context_is_attached_without_the_key(self, session, fake_models, sink):
        fake_models.next_result = []
        server.odoo_search_read("res.partner")
        rec = read_lines(sink.file_path)[-1]
        assert rec["session"]["uid"] == 2
        assert rec["session"]["db"] == "testdb"
        assert rec["session"]["username"] == "tester@example.test"
        assert "test-key" not in json.dumps(rec)

    def test_write_tools_record_the_target(self, session, fake_models, sink):
        fake_models.next_result = True
        server.odoo_write("res.partner", [7], {"name": "New"})
        rec = read_lines(sink.file_path)[-1]
        assert rec["tool"] == "odoo_write"
        assert rec["args"]["ids"] == [7]
        assert rec["args"]["values"] == {"name": "New"}

    def test_tool_signature_survives_the_decorator(self):
        import inspect
        params = inspect.signature(server.odoo_search_read).parameters
        assert "model" in params and "domain" in params and "company_id" in params
        assert server.odoo_search_read.__doc__.startswith("Search and read records")


# ---------- Levels and payload policy ----------


class TestLevels:
    def test_level_off_writes_nothing(self, session, fake_models, tmp_path, monkeypatch):
        log = audit.AuditLog(path=tmp_path / "a.jsonl", level="off")
        monkeypatch.setattr(audit, "_ACTIVE", log)
        fake_models.next_result = []
        server.odoo_search_read("res.partner")
        assert read_lines(tmp_path / "a.jsonl") == []

    def test_level_error_keeps_only_failures(self, session, fake_models, tmp_path, monkeypatch):
        log = audit.AuditLog(path=tmp_path / "a.jsonl", level="error")
        monkeypatch.setattr(audit, "_ACTIVE", log)
        fake_models.next_result = []
        server.odoo_search_read("res.partner")            # ok — dropped
        with pytest.raises(server.GuardrailError):
            server.odoo_execute("res.partner", "unlink", [[1]])  # blocked — kept
        records = read_lines(tmp_path / "a.jsonl")
        assert [r["outcome"] for r in records] == ["blocked"]

    def test_payloads_off_omits_args_and_result(self, session, fake_models, tmp_path, monkeypatch):
        log = audit.AuditLog(path=tmp_path / "a.jsonl", payloads=False)
        monkeypatch.setattr(audit, "_ACTIVE", log)
        fake_models.next_result = [{"id": 1}]
        server.odoo_search_read("res.partner", [["name", "=", "Secret Corp"]])
        rec = read_lines(tmp_path / "a.jsonl")[-1]
        assert rec["tool"] == "odoo_search_read"
        assert "args" not in rec and "result" not in rec
        assert "Secret Corp" not in json.dumps(rec)


# ---------- Sink durability ----------


class TestSink:
    def test_rotation_keeps_the_log_bounded(self, session, fake_models, tmp_path, monkeypatch):
        log = audit.AuditLog(path=tmp_path / "a.jsonl", max_bytes=1024, backups=2)
        monkeypatch.setattr(audit, "_ACTIVE", log)
        fake_models.next_result = []
        for _ in range(200):
            server.odoo_search_read("res.partner", [["x", "=", "y" * 100]])
        rotated = list(tmp_path.glob("a.jsonl*"))
        assert len(rotated) <= 3            # live + 2 backups, never unbounded
        assert (tmp_path / "a.jsonl").stat().st_size <= 4096

    def test_unwritable_sink_does_not_break_the_tool(self, session, fake_models, tmp_path, monkeypatch):
        log = audit.AuditLog(path=tmp_path / "nope" / "deep" / "a.jsonl")
        monkeypatch.setattr(audit, "_ACTIVE", log)
        monkeypatch.setattr(log, "_write", lambda line: (_ for _ in ()).throw(OSError("disk full")))
        fake_models.next_result = [{"id": 1}]
        assert server.odoo_search_read("res.partner") == [{"id": 1}]

    def test_unserialisable_payload_does_not_break_the_tool(self, session, fake_models, sink):
        class Opaque:
            def __repr__(self):
                return "<opaque>"
        fake_models.next_result = Opaque()
        assert isinstance(server.odoo_execute("res.partner", "read", [[1]]), Opaque)
        rec = read_lines(sink.file_path)[-1]
        assert rec["outcome"] == "ok"

    def test_nothing_is_written_to_stdout(self, session, fake_models, sink, capsys):
        fake_models.next_result = []
        server.odoo_search_read("res.partner")
        audit.get().event("diagnostic", message="hello operator")
        assert capsys.readouterr().out == ""


# ---------- Reading the trail back ----------


class TestTail:
    def test_tail_returns_the_most_recent_first(self, session, fake_models, sink):
        fake_models.next_result = []
        for i in range(5):
            server.odoo_search_read("res.partner", [["i", "=", i]])
        rows = sink.tail(limit=3)
        assert len(rows) == 3
        assert rows[0]["args"]["domain"] == [["i", "=", 4]]

    def test_tail_filters_by_outcome_and_tool(self, session, fake_models, sink):
        fake_models.next_result = []
        server.odoo_search_read("res.partner")
        with pytest.raises(server.GuardrailError):
            server.odoo_execute("res.partner", "unlink", [[1]])
        assert [r["tool"] for r in sink.tail(outcome="blocked")] == ["odoo_execute"]
        assert [r["tool"] for r in sink.tail(tool="odoo_search_read")] == ["odoo_search_read"]

    def test_audit_tail_tool_exposes_the_trail(self, session, fake_models, sink):
        fake_models.next_result = []
        server.odoo_search_read("res.partner")
        out = server.odoo_audit_tail(limit=5)
        assert out["path"].endswith("mcp-audit.jsonl")
        assert any(e["tool"] == "odoo_search_read" for e in out["entries"])

    def test_audit_tail_reports_a_disabled_trail(self, tmp_path, monkeypatch, session):
        monkeypatch.setattr(audit, "_ACTIVE", audit.AuditLog(path=None, level="off"))
        out = server.odoo_audit_tail()
        assert out["entries"] == []
        assert "disabled" in out["status"].lower() or "off" in out["status"].lower()


# ---------- Configuration ----------


class TestConfigure:
    def test_env_overrides_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_AUDIT_LOG", str(tmp_path / "env.jsonl"))
        monkeypatch.setenv("MCP_AUDIT_LEVEL", "error")
        log = audit.configure({"audit_log": str(tmp_path / "cfg.jsonl"), "audit_level": "all"})
        assert log.file_path == tmp_path / "env.jsonl"
        assert log.level == "error"

    def test_config_is_used_when_env_is_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MCP_AUDIT_LOG", raising=False)
        monkeypatch.delenv("MCP_AUDIT_LEVEL", raising=False)
        log = audit.configure({"audit_log": str(tmp_path / "cfg.jsonl"), "audit_level": "error"})
        assert log.file_path == tmp_path / "cfg.jsonl"
        assert log.level == "error"

    def test_audit_log_off_disables_the_file_sink(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCP_AUDIT_LOG", "off")
        log = audit.configure({})
        assert log.file_path is None

    def test_relative_path_resolves_against_the_config_folder(self, tmp_path, monkeypatch):
        # On stdio the process cwd is whatever the MCP client launched from, so a
        # relative path in odoo_config.json must anchor to the config, not the cwd.
        monkeypatch.delenv("MCP_AUDIT_LOG", raising=False)
        log = audit.configure({"audit_log": "mcp-audit.jsonl"}, default_dir=tmp_path)
        assert log.file_path == tmp_path / "mcp-audit.jsonl"

    def test_default_path_sits_next_to_the_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MCP_AUDIT_LOG", raising=False)
        log = audit.configure({}, default_dir=tmp_path)
        assert log.file_path == tmp_path / "mcp-audit.jsonl"
        assert not log.file_path.exists(), "configure() must not create the file eagerly"

    def test_invalid_level_falls_back_to_all(self, monkeypatch):
        monkeypatch.setenv("MCP_AUDIT_LEVEL", "loud")
        assert audit.configure({}).level == "all"

    def test_diagnostic_logger_goes_to_stderr(self):
        handlers = logging.getLogger("odoo_mcp").handlers
        assert handlers, "the diagnostic logger must have a handler installed"
        import sys
        assert all(getattr(h, "stream", sys.stderr) is not sys.stdout for h in handlers)
