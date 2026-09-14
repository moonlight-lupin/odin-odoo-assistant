"""Tests for the independent server's tool plumbing and configuration.

The existing suite pins the two policy controls hard. This one covers the rest
of what ships: how each tool marshals its arguments into `execute_kw`, what the
session tools return, and how the config and transport are read.

The argument marshalling matters more than it looks. Every one of these tools
is a thin translation from a tool call into an Odoo RPC, and a translation bug
is silent: `read_group` without `lazy=False` returns a different shape,
`search_read` dropping `order` returns the wrong rows, `fields_get` sending
attributes positionally returns everything. None of that raises — it just
gives the agent wrong data to act on.
"""
from __future__ import annotations

import json

import pytest

import server


# ---------------------------------------------------------------------------
# Read tools — argument marshalling
# ---------------------------------------------------------------------------

class TestSearchRead:
    def test_domain_and_fields_reach_odoo(self, session, fake_models):
        server.odoo_search_read("account.move", [["state", "=", "posted"]], ["id", "ref"])
        call = fake_models.last
        assert call["model"] == "account.move"
        assert call["method"] == "search_read"
        assert call["args"] == [[["state", "=", "posted"]]]
        assert call["kwargs"]["fields"] == ["id", "ref"]

    def test_an_absent_domain_becomes_match_everything(self, session, fake_models):
        server.odoo_search_read("res.partner")
        assert fake_models.last["args"] == [[]]

    def test_paging_and_ordering_are_passed_through(self, session, fake_models):
        server.odoo_search_read("res.partner", limit=25, offset=50, order="name asc")
        kwargs = fake_models.last["kwargs"]
        assert kwargs["limit"] == 25
        assert kwargs["offset"] == 50
        assert kwargs["order"] == "name asc"

    def test_the_default_limit_is_bounded(self, session, fake_models):
        # An unbounded default would let one call pull a whole ledger into context.
        server.odoo_search_read("account.move.line")
        assert fake_models.last["kwargs"]["limit"] == 80

    def test_no_fields_means_no_fields_key(self, session, fake_models):
        # Odoo reads every field when `fields` is absent — so it must be absent,
        # not an empty list (which means the same thing but by accident).
        server.odoo_search_read("res.partner")
        assert "fields" not in fake_models.last["kwargs"]

    def test_order_is_omitted_when_not_asked_for(self, session, fake_models):
        server.odoo_search_read("res.partner")
        assert "order" not in fake_models.last["kwargs"]


class TestReadGroup:
    def test_grouping_is_non_lazy(self, session, fake_models):
        # lazy=True (Odoo's default) groups by only the FIRST groupby field and
        # returns a different shape. Every caller here expects full grouping.
        server.odoo_read_group("account.move.line", [], ["balance:sum"],
                               ["account_id", "date:month"])
        assert fake_models.last["kwargs"]["lazy"] is False

    def test_domain_fields_and_groupby_are_positional_in_that_order(self, session, fake_models):
        server.odoo_read_group("account.move.line", [["parent_state", "=", "posted"]],
                               ["balance:sum"], ["account_id"])
        assert fake_models.last["args"] == [
            [["parent_state", "=", "posted"]], ["balance:sum"], ["account_id"]]

    def test_missing_arguments_become_empty_lists(self, session, fake_models):
        server.odoo_read_group("account.move.line")
        assert fake_models.last["args"] == [[], [], []]

    def test_limit_is_omitted_unless_given(self, session, fake_models):
        server.odoo_read_group("account.move.line")
        assert "limit" not in fake_models.last["kwargs"]
        server.odoo_read_group("account.move.line", limit=10)
        assert fake_models.last["kwargs"]["limit"] == 10

    def test_orderby_is_passed_through(self, session, fake_models):
        server.odoo_read_group("account.move.line", orderby="account_id")
        assert fake_models.last["kwargs"]["orderby"] == "account_id"

    def test_company_context_is_injected(self, session, fake_models):
        server.odoo_read_group("account.move.line", company_id=3)
        assert fake_models.last["kwargs"]["context"]["company_id"] == 3


class TestOtherReads:
    def test_read_takes_ids_positionally(self, session, fake_models):
        server.odoo_read("account.move", [1, 2, 3], ["ref"])
        assert fake_models.last["method"] == "read"
        assert fake_models.last["args"] == [[1, 2, 3]]
        assert fake_models.last["kwargs"]["fields"] == ["ref"]

    def test_fields_get_sends_attributes_as_a_keyword(self, session, fake_models):
        server.odoo_fields_get("account.move", ["string", "type"])
        assert fake_models.last["args"] == []
        assert fake_models.last["kwargs"]["attributes"] == ["string", "type"]

    def test_fields_get_has_a_useful_default_attribute_set(self, session, fake_models):
        server.odoo_fields_get("account.move")
        attrs = fake_models.last["kwargs"]["attributes"]
        assert "string" in attrs and "type" in attrs

    def test_search_count_passes_the_domain(self, session, fake_models):
        fake_models.next_result = 412
        assert server.odoo_search_count("account.move", [["state", "=", "draft"]]) == 412
        assert fake_models.last["method"] == "search_count"
        assert fake_models.last["args"] == [[["state", "=", "draft"]]]

    def test_name_search_passes_its_arguments_positionally(self, session, fake_models):
        # Odoo's name_search signature is (name, args, operator, limit) and the
        # keyword names have moved between versions — position is the contract.
        fake_models.next_result = [[7, "ACME Pte Ltd"]]
        server.odoo_name_search("res.partner", "ACME", limit=5)
        assert fake_models.last["method"] == "name_search"
        assert fake_models.last["args"] == ["ACME", [], "ilike", 5]

    def test_name_search_returns_id_and_display_name_dicts(self, session, fake_models):
        fake_models.next_result = [[7, "ACME Pte Ltd"], [9, "Beta Ltd"]]
        assert server.odoo_name_search("res.partner", "A") == [
            {"id": 7, "display_name": "ACME Pte Ltd"},
            {"id": 9, "display_name": "Beta Ltd"},
        ]


class TestWrites:
    def test_create_passes_values_positionally(self, session, fake_models):
        fake_models.next_result = 1841
        assert server.odoo_create("account.move", {"ref": "INV-1"}) == 1841
        assert fake_models.last["args"] == [{"ref": "INV-1"}]

    def test_write_takes_ids_then_values(self, session, fake_models):
        server.odoo_write("account.move", [1841], {"ref": "INV-2"})
        assert fake_models.last["args"] == [[1841], {"ref": "INV-2"}]

    def test_write_returns_a_bool(self, session, fake_models):
        fake_models.next_result = True
        assert server.odoo_write("account.move", [1], {"ref": "x"}) is True


# ---------------------------------------------------------------------------
# Session tools
# ---------------------------------------------------------------------------

class TestSessionTools:
    def test_whoami_reports_the_connection_and_companies(self, session, fake_models):
        fake_models.next_result = [{"id": 1, "name": "ACME Pte Ltd"}]
        out = server.odoo_whoami()
        assert out["db"] == "testdb"
        assert out["uid"] == 2
        assert out["username"] == "tester@example.test"
        assert out["companies"] == [{"id": 1, "name": "ACME Pte Ltd"}]

    def test_whoami_never_returns_the_api_key(self, session, fake_models):
        fake_models.next_result = []
        assert "api_key" not in server.odoo_whoami()
        assert "test-key" not in json.dumps(server.odoo_whoami())

    def test_disconnect_clears_the_session(self, session, fake_models):
        assert server.odoo_disconnect() == {"status": "disconnected"}
        assert server._state == {}

    def test_a_tool_after_disconnect_asks_you_to_connect(self, session, fake_models,
                                                         monkeypatch, tmp_path):
        # No config on disk, so there is no legacy auto-connect to fall back on.
        monkeypatch.setattr(server, "_CONFIG_PATH", tmp_path / "absent.json")
        server.odoo_disconnect()
        with pytest.raises(RuntimeError):
            server.odoo_search_read("res.partner")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_a_missing_config_names_the_path_it_looked_at(self, monkeypatch, tmp_path):
        missing = tmp_path / "nope" / "odoo_config.json"
        monkeypatch.setattr(server, "_CONFIG_PATH", missing)
        with pytest.raises(RuntimeError) as caught:
            server._load_cfg()
        assert str(missing) in str(caught.value)

    def test_invalid_json_is_reported_as_such(self, monkeypatch, tmp_path):
        path = tmp_path / "odoo_config.json"
        path.write_text("{not json")
        monkeypatch.setattr(server, "_CONFIG_PATH", path)
        with pytest.raises(RuntimeError, match="not valid JSON"):
            server._load_cfg()

    @pytest.mark.parametrize("cfg", [
        {}, {"url": "https://x"}, {"db": "d"}, {"url": "", "db": "d"},
        {"url": "https://x", "db": ""},
    ])
    def test_url_and_db_are_both_required(self, monkeypatch, tmp_path, cfg):
        path = tmp_path / "odoo_config.json"
        path.write_text(json.dumps(cfg))
        monkeypatch.setattr(server, "_CONFIG_PATH", path)
        with pytest.raises(RuntimeError, match="url.*db"):
            server._load_cfg()

    def test_a_valid_config_loads(self, monkeypatch, tmp_path):
        path = tmp_path / "odoo_config.json"
        path.write_text(json.dumps({"url": "https://odoo.example", "db": "prod"}))
        monkeypatch.setattr(server, "_CONFIG_PATH", path)
        assert server._load_cfg()["db"] == "prod"

    def test_credentials_are_not_required_in_the_config(self, monkeypatch, tmp_path):
        # The whole point of the per-user credential model: url+db is enough.
        path = tmp_path / "odoo_config.json"
        path.write_text(json.dumps({"url": "https://odoo.example", "db": "prod"}))
        monkeypatch.setattr(server, "_CONFIG_PATH", path)
        cfg = server._load_cfg()
        assert "api_key" not in cfg and "username" not in cfg


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class TestTransportSecurity:
    def test_unset_disables_host_checks_for_proxied_deployments(self, monkeypatch):
        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
        assert server._transport_security().enable_dns_rebinding_protection is False

    def test_a_star_disables_host_checks(self, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*")
        assert server._transport_security().enable_dns_rebinding_protection is False

    def test_named_hosts_enable_protection_for_exactly_those_hosts(self, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a.example.com, b.example.com")
        settings = server._transport_security()
        assert settings.enable_dns_rebinding_protection is True
        assert "a.example.com" in settings.allowed_hosts
        assert "b.example.com" in settings.allowed_hosts
        assert "evil.example.com" not in settings.allowed_hosts

    def test_both_schemes_and_any_port_are_accepted_for_a_named_host(self, monkeypatch):
        # A host behind a tunnel is reached on its public name, any port.
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a.example.com")
        settings = server._transport_security()
        assert "a.example.com:*" in settings.allowed_hosts
        assert "https://a.example.com" in settings.allowed_origins
        assert "http://a.example.com" in settings.allowed_origins

    def test_blank_entries_are_ignored(self, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a.example.com,,  ,")
        settings = server._transport_security()
        assert settings.allowed_hosts == ["a.example.com", "a.example.com:*"]


class TestProxies:
    def test_https_urls_get_a_tls_transport(self, session):
        common, models = server._make_proxies("https://odoo.example.com")
        assert common is not None and models is not None

    def test_a_trailing_slash_does_not_double_up_the_path(self, session):
        common, _ = server._make_proxies("https://odoo.example.com/")
        assert "//xmlrpc" not in str(common)

    def test_plain_http_is_still_supported(self, session):
        common, models = server._make_proxies("http://localhost:8069")
        assert common is not None and models is not None


class TestRenderReport:
    @pytest.mark.parametrize("converter", ["docx", "xlsx", "", "exe"])
    def test_only_the_supported_converters_are_accepted(self, session, converter):
        with pytest.raises((ValueError, RuntimeError)):
            server.odoo_render_report("account.report_invoice", [1], converter)

    @pytest.mark.parametrize("converter", ["pdf", "html", "text"])
    def test_the_supported_converters_get_past_validation(self, session, monkeypatch,
                                                          converter):
        # Stop before the network call — we are pinning the validation, not the fetch.
        def no_network(*args, **kwargs):
            raise RuntimeError("network reached")
        monkeypatch.setattr(server, "_web_opener", no_network)
        with pytest.raises(RuntimeError, match="network reached"):
            server.odoo_render_report("account.report_invoice", [1], converter)
