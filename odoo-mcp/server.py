"""Odoo 18 MCP Server.

Exposes Odoo XML-RPC operations as MCP tools so Claude can query and write
to a live Odoo instance directly from the user's machine — no sandbox proxy
in the path.

Credential model:
  - `url` and `db` are the shared instance settings, read from odoo_config.json
    (set with config_gui.py).
  - `username` and `api_key` are PER-USER credentials supplied at runtime. The
    user calls `odoo_connect(username, api_key)`; the server authenticates and
    caches the session for the life of the process. Every other tool requires
    an active session.
  - Backward compatibility: if odoo_config.json still contains username/api_key,
    tools auto-connect from the config when no explicit session has been made.

Config: reads odoo_config.json. Default location is the parent folder of this
script (so put the server in `<workspace>/odoo-mcp/server.py` and the config
at `<workspace>/odoo_config.json`). Override with the ODOO_CONFIG_PATH env var.

Transport: stdio. Claude launches this as a subprocess and talks to it over
stdin/stdout per the MCP protocol.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import ssl
import urllib.error
import urllib.request
import xmlrpc.client
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("odoo-assistant")

# ---------- Config & connection ----------

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "odoo_config.json"
_CONFIG_PATH = Path(os.environ.get("ODOO_CONFIG_PATH", str(_DEFAULT_CONFIG)))

# Cached session for the life of the process. Populated by odoo_connect (or by
# auto-connect from config creds for backward compatibility). Keys when active:
# url, db, uid, username, api_key, models.
_state: dict[str, Any] = {}


def _load_cfg() -> dict[str, Any]:
    """Read odoo_config.json. Requires url + db; username/api_key are optional."""
    if not _CONFIG_PATH.exists():
        raise RuntimeError(
            f"Odoo config not found at {_CONFIG_PATH}. "
            f"Set ODOO_CONFIG_PATH or place odoo_config.json next to the odoo-mcp folder. "
            f"It must contain at least 'url' and 'db' (use config_gui.py to set them)."
        )
    try:
        cfg = json.loads(_CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"odoo_config.json is not valid JSON: {e}") from e
    if not cfg.get("url") or not cfg.get("db"):
        raise RuntimeError(
            "odoo_config.json must contain 'url' and 'db'. Run config_gui.py to set them."
        )
    return cfg


def _make_proxies(url: str) -> tuple[Any, Any]:
    """Build (common, object) XML-RPC proxies, TLS-tolerant for self-signed certs."""
    url = url.rstrip("/")
    if url.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        transport = xmlrpc.client.SafeTransport(context=ctx)
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", transport=transport, allow_none=True)
        models_proxy = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", transport=transport, allow_none=True)
    else:
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
        models_proxy = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
    return common, models_proxy


def _ssl_ctx() -> ssl.SSLContext:
    """TLS context tolerant of self-signed certs (matches the XML-RPC transport)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _web_opener(url: str, db: str, login: str, api_key: str) -> urllib.request.OpenerDirector:
    """Authenticate over HTTP and return a cookie-bearing opener for report downloads.

    Odoo's rendered reports come from HTTP controllers (not XML-RPC), so we log in via
    /web/session/authenticate to obtain a session cookie. The API key is accepted as the
    password (Odoo treats API keys as a credential, bypassing 2FA).
    """
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=_ssl_ctx()),
    )
    # Send a browser-like User-Agent on every request through this opener. Cloudflare-fronted
    # hosts (e.g. Odoo Online / *.run-odoo.com) return 403 to header-less requests to
    # /web/session/authenticate and /report/*. Overridable via MCP_HTTP_USER_AGENT.
    ua = os.environ.get(
        "MCP_HTTP_USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
    )
    opener.addheaders = [("User-Agent", ua), ("Accept", "*/*")]
    body = json.dumps({
        "jsonrpc": "2.0", "method": "call",
        "params": {"db": db, "login": login, "password": api_key},
    }).encode()
    req = urllib.request.Request(
        f"{url}/web/session/authenticate", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = opener.open(req, timeout=30)
        data = json.loads(resp.read())
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP login to {url} failed: {e}") from e
    if data.get("error") or not (data.get("result") or {}).get("uid"):
        raise RuntimeError(
            "HTTP session authentication failed. The server may disallow API-key web login "
            "or the db/login is wrong. Reports that need HTTP rendering are unavailable; "
            "fall back to building from data."
        )
    return opener


def _authenticate(username: str, api_key: str, db: str | None = None,
                  url: str | None = None) -> dict[str, Any]:
    """Authenticate with the given credentials and cache the session in _state.

    url and db default to the values in odoo_config.json. Raises on failure.
    """
    cfg = _load_cfg()
    url = (url or cfg["url"]).rstrip("/")
    db = db or cfg["db"]

    common, models_proxy = _make_proxies(url)
    try:
        uid = common.authenticate(db, username, api_key, {})
    except Exception as e:  # network/transport errors
        raise RuntimeError(f"Could not reach Odoo at {url}: {e}") from e
    if not uid:
        raise RuntimeError(
            "Authentication failed — check your username and api_key (and that 'db' is "
            f"correct: {db!r}). Odoo API keys require developer mode to be enabled."
        )

    _state.clear()
    _state.update({
        "url": url, "db": db, "uid": uid, "username": username,
        "api_key": api_key, "models": models_proxy,
        # Guardrail flags (see _guard). Default to the locked-down behaviour.
        "allow_record_deletion": bool(cfg.get("allow_record_deletion", False)),
        "allow_model_changes": bool(cfg.get("allow_model_changes", False)),
    })
    return _state


def _session() -> dict[str, Any]:
    """Return the active session, or raise with guidance to connect first.

    If no explicit session exists but odoo_config.json carries username/api_key
    (legacy single-user setup), auto-connect from the config.
    """
    if _state:
        return _state
    cfg = _load_cfg()
    if cfg.get("username") and cfg.get("api_key"):
        return _authenticate(cfg["username"], cfg["api_key"])
    raise RuntimeError(
        "Not connected. Call odoo_connect(username, api_key) with your own Odoo "
        "credentials first — the server uses them to authenticate to the database "
        "(url and db come from odoo_config.json)."
    )


# ---------- Guardrails ----------
#
# Two policy controls, both ON by default and configurable in odoo_config.json:
#   1. allow_model_changes  (default False) — no tampering with the Odoo data
#      model / structure. Writes to technical "model definition" records are
#      blocked; read access is still allowed.
#   2. allow_record_deletion (default False) — no hard deletes. `unlink` is
#      blocked on every model. Use odoo_archive (active=False) or odoo_cancel.

class GuardrailError(RuntimeError):
    """Raised when a policy control blocks an operation. Surfaced to Claude as-is."""


# Structural/technical models that define the schema, modules, UI, automation and
# security rules. Mutating these = "tampering with the model". Read-only is fine.
_PROTECTED_MODEL_PREFIXES = (
    "ir.model",            # ir.model, ir.model.fields, ir.model.data, .constraint, .relation
    "ir.module",           # module install / upgrade / uninstall
    "ir.ui.view",          # view definitions
    "ir.ui.menu",          # menu structure
    "ir.actions",          # window/server/report actions
    "ir.rule",             # record rules (security model)
    "ir.cron",             # scheduled actions
    "ir.config_parameter", # system parameters
    "base.automation",     # automation rules
    "studio",              # Odoo Studio customisations
    # Auth / privilege surface — blocking these stops an agent creating users,
    # minting API keys for itself, or granting itself groups. (The prefix
    # matcher catches the whole sub-tree, e.g. res.users.apikeys / .settings.)
    "res.users",           # user accounts + .apikeys + .settings + .log
    "res.groups",          # security groups + .privilege
    # Code-bearing / routing config: mail templates embed evaluated expressions,
    # aliases reroute inbound mail.
    "mail.template",
    "mail.alias",
    # NB: ir.attachment is deliberately NOT listed — uploading PDFs/files to
    # records is everyday work and stays writable. res.currency.rate is also
    # left writable (updating FX rates can be a legitimate bookkeeping action).
)

# Methods that only read — always allowed, even on protected models.
_READONLY_METHODS = frozenset({
    "read", "search", "search_read", "search_count", "read_group",
    "fields_get", "get_views", "fields_view_get", "name_get", "name_search",
    "default_get", "exists",
    # access-introspection (read-only): used by the build-context playbook
    "check_access_rights", "check_access_rule", "check_access", "has_access",
    "read_progress_bar", "search_panel_select_range", "search_panel_select_multi_range",
})


def _is_protected_model(model: str) -> bool:
    return any(model == p or model.startswith(p + ".") for p in _PROTECTED_MODEL_PREFIXES)


def _guard(model: str, method: str) -> None:
    """Enforce the deletion + model-tampering controls. Raises GuardrailError if blocked."""
    s = _state
    # Control 2 — no hard deletes anywhere.
    if method == "unlink" and not s.get("allow_record_deletion", False):
        raise GuardrailError(
            f"Deletion is disabled by policy — refusing to unlink {model} records. "
            "Use odoo_archive (sets active=False) or odoo_cancel (workflow cancel) instead. "
            "To override, set \"allow_record_deletion\": true in odoo_config.json."
        )
    # Control 1 — no tampering with the data model / structure.
    if (_is_protected_model(model) and method not in _READONLY_METHODS
            and not s.get("allow_model_changes", False)):
        raise GuardrailError(
            f"Modifying the Odoo model/structure is disabled by policy — refusing "
            f"'{model}.{method}'. Technical models like {model} are read-only; you can "
            "query them but not create/alter/delete schema, modules, views, actions, "
            "cron, or automation. To override, set \"allow_model_changes\": true in "
            "odoo_config.json."
        )


def _execute(model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
    s = _session()
    _guard(model, method)
    try:
        return s["models"].execute_kw(
            s["db"], s["uid"], s["api_key"], model, method, args, kwargs or {}
        )
    except xmlrpc.client.Fault as e:
        msg = e.faultString or ""
        # Several Odoo methods (button_draft, action_post, account.move.line.reconcile,
        # message_post, …) return None. If this instance's XML-RPC endpoint can't marshal
        # a None *result*, it raises "cannot marshal None unless allow_none is enabled" —
        # but the method has ALREADY executed and committed (response marshalling happens
        # after the commit). So treat that specific fault as a (None) success, not an error.
        if "cannot marshal None" in msg or "allow_none" in msg:
            return None
        # Surface any other fault string — Claude's skill knows how to read these.
        raise RuntimeError(f"Odoo fault on {model}.{method}: {e.faultString}") from e


def _company_ctx(company_id: int | None) -> dict | None:
    """Context that pins COMPANY-DEPENDENT fields to a specific company.

    Some Odoo 18 fields are company-dependent — notably ``account.account.code``
    (and therefore ``display_name``), computed per company from ``code_store``.
    A read with no company context runs in the connected user's *default*
    company, so those fields come back blank/empty for any other company (and
    only the internal id remains to show). Passing this context makes the field
    resolve for ``company_id``."""
    if not company_id:
        return None
    return {"allowed_company_ids": [company_id], "company_id": company_id}


# ---------- Tools ----------

@mcp.tool()
def odoo_connect(username: str, api_key: str, db: str | None = None,
                 url: str | None = None) -> dict[str, Any]:
    """Authenticate to Odoo with the user's OWN credentials and start a session.

    Call this once at the start of a session. The server uses these credentials to
    connect to the Odoo database for all subsequent tool calls (the session is cached
    for the life of the server process). `url` and `db` default to odoo_config.json —
    only pass them to override.

    Args:
        username: The user's Odoo login (usually their email).
        api_key: The user's Odoo API key (Settings → My Profile → Account Security →
                 New API Key; requires developer mode).
        db: Database name. Defaults to the `db` in odoo_config.json.
        url: Server URL. Defaults to the `url` in odoo_config.json.

    Returns: connection details (url, db, uid, username) and the list of companies
    visible to this user — use those company ids for downstream filters/payloads.
    """
    s = _authenticate(username, api_key, db=db, url=url)
    companies = _execute(
        "res.company", "search_read", [[]],
        {"fields": ["id", "name", "currency_id"], "order": "id asc"},
    )
    return {
        "url": s["url"], "db": s["db"], "uid": s["uid"],
        "username": s["username"], "companies": companies,
    }


@mcp.tool()
def odoo_disconnect() -> dict[str, str]:
    """Clear the cached session so a different user can connect with their own credentials."""
    _state.clear()
    return {"status": "disconnected"}


@mcp.tool()
def odoo_whoami() -> dict[str, Any]:
    """Return current connection details and the list of companies visible to the user.

    Use this to confirm who is connected and to discover valid company_id values for
    downstream domain filters and write payloads. If no session exists yet, this errors
    and asks you to call odoo_connect first (unless odoo_config.json carries legacy
    credentials, in which case it auto-connects).
    """
    s = _session()
    companies = _execute(
        "res.company", "search_read", [[]],
        {"fields": ["id", "name", "currency_id"], "order": "id asc"},
    )
    return {
        "url": s["url"], "db": s["db"], "uid": s["uid"],
        "username": s.get("username"), "companies": companies,
    }


@mcp.tool()
def odoo_search_read(
    model: str,
    domain: list | None = None,
    fields: list[str] | None = None,
    limit: int = 80,
    offset: int = 0,
    order: str | None = None,
    company_id: int | None = None,
) -> list[dict]:
    """Search and read records from any Odoo model.

    Args:
        model: Odoo model name, e.g. "account.move", "res.partner", "sale.order".
        domain: Odoo domain expression as a list of triples,
                e.g. [["company_id", "=", 1], ["state", "=", "posted"]].
                Defaults to [] (all records). Use "&" or "|" prefixes for explicit logic.
                Note: account.account uses many2many `company_ids` — filter with
                ["company_ids", "in", [company_id]].
        fields: List of fields to return. Always specify to keep payloads small.
        limit: Max records to return (default 80).
        offset: Records to skip — for pagination.
        order: SQL-style order clause, e.g. "date desc, id asc".
        company_id: Optional — read in this company's context so COMPANY-DEPENDENT
                fields resolve for it. Set this (to the company that owns the records)
                when reading `account.account` so `code`/`display_name` come back
                populated, e.g. "12501 Prepayments" — otherwise they're blank for any
                non-default company.

    Returns: List of dicts, one per record. Each dict includes "id" plus requested fields.
    """
    kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
    if fields is not None:
        kwargs["fields"] = fields
    if order:
        kwargs["order"] = order
    ctx = _company_ctx(company_id)
    if ctx:
        kwargs["context"] = ctx
    return _execute(model, "search_read", [domain or []], kwargs)


@mcp.tool()
def odoo_search_count(model: str, domain: list | None = None, company_id: int | None = None) -> int:
    """Count records matching a domain. Run this before search_read on large models.

    company_id: optional company context (see odoo_search_read) — rarely needed for a
    count, but available for parity when a domain references a company-dependent field.
    """
    kwargs: dict[str, Any] = {}
    ctx = _company_ctx(company_id)
    if ctx:
        kwargs["context"] = ctx
    return _execute(model, "search_count", [domain or []], kwargs)


@mcp.tool()
def odoo_read_group(
    model: str,
    domain: list | None = None,
    fields: list[str] | None = None,
    groupby: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    orderby: str | None = None,
    company_id: int | None = None,
) -> list[dict]:
    """Aggregate records into groups — Odoo's `read_group` (non-lazy).

    The workhorse for reports and month-end summaries: totals/counts per group
    in one call, instead of pulling rows and summing client-side.

    Args:
        model: Odoo model name, e.g. "account.move.line".
        domain: Filter applied before grouping, e.g. [["parent_state","=","posted"]].
        fields: Aggregates to compute. Use "field" (default agg) or "field:agg",
                e.g. ["balance:sum", "debit:sum", "credit:sum"].
        groupby: Group-by fields. Date fields can carry a granularity,
                 e.g. ["account_id", "date:month"].
        limit: Max groups to return (default: all).
        offset: Groups to skip — for pagination.
        orderby: Order clause over the grouped result, e.g. "account_id".
        company_id: Optional company context (see odoo_search_read). NB: grouping by
                `account_id` returns it as `[id, name]` (the name only, no code) — to
                label by code, resolve the ids with an `account.account` read passing
                this `company_id`, then map.

    Returns: One dict per group — the groupby key(s), the aggregates, plus a
             "__count" (rows in group) and "__domain" (drill-down domain).
    """
    kwargs: dict[str, Any] = {"offset": offset, "lazy": False}
    if limit is not None:
        kwargs["limit"] = limit
    if orderby:
        kwargs["orderby"] = orderby
    ctx = _company_ctx(company_id)
    if ctx:
        kwargs["context"] = ctx
    return _execute(
        model, "read_group",
        [domain or [], fields or [], groupby or []],
        kwargs,
    )


@mcp.tool()
def odoo_name_search(
    model: str,
    name: str = "",
    domain: list | None = None,
    operator: str = "ilike",
    limit: int = 10,
    company_id: int | None = None,
) -> list[dict]:
    """Resolve a display name to record id(s) — Odoo's `name_search`.

    Use this to turn "the Acme vendor" / an account code into an id before
    creating bills, invoices or payments, instead of a search_read on
    display_name.

    Args:
        model: Odoo model name, e.g. "res.partner", "account.account".
        name: Text to match against the record's display name.
        domain: Extra filter to scope the search, e.g. [["company_ids","in",[14]]].
        operator: Match operator (default "ilike").
        limit: Max matches to return (default 10).
        company_id: Optional company context (see odoo_search_read). Pass it when
                name-searching `account.account` so the match/label uses that
                company's codes (the display name includes the code).

    Returns: List of {"id", "display_name"} dicts, best matches first.
    """
    # name_search signature is (name, args/domain, operator, limit) — pass
    # positionally so it works regardless of the param's keyword name.
    kwargs: dict[str, Any] = {}
    ctx = _company_ctx(company_id)
    if ctx:
        kwargs["context"] = ctx
    rows = _execute(
        model, "name_search", [name, domain or [], operator, limit], kwargs,
    )
    return [{"id": rid, "display_name": label} for rid, label in rows]


@mcp.tool()
def odoo_read(
    model: str,
    ids: list[int],
    fields: list[str] | None = None,
    company_id: int | None = None,
) -> list[dict]:
    """Read specific records by ID.

    Args:
        model: Odoo model name.
        ids: Record IDs to fetch.
        fields: Fields to return. Pass None to fetch all (large for some models).
        company_id: Optional — read in this company's context so COMPANY-DEPENDENT
                fields resolve. Pass it (the owning company) when reading
                `account.account` to get `code`/`display_name` populated rather
                than blank. (See odoo_search_read.)
    """
    kwargs: dict[str, Any] = {"fields": fields} if fields else {}
    ctx = _company_ctx(company_id)
    if ctx:
        kwargs["context"] = ctx
    return _execute(model, "read", [ids], kwargs)


@mcp.tool()
def odoo_fields_get(model: str, attributes: list[str] | None = None) -> dict[str, dict]:
    """Discover the schema of any model — field names, types, required flags, relations.

    Use this when reference docs don't cover the model, or when you need to verify a
    field exists in this specific Odoo instance (custom modules vary).

    Args:
        model: Odoo model name.
        attributes: Field metadata to return. Defaults to a useful subset.
    """
    attrs = attributes or ["string", "type", "required", "relation", "selection", "help"]
    return _execute(model, "fields_get", [], {"attributes": attrs})


@mcp.tool()
def odoo_create(model: str, values: dict) -> int:
    """Create a single record. Returns the new record ID.

    Workflow models (account.move, sale.order, purchase.order, etc.) are created in
    their default initial state — typically DRAFT. This tool does NOT post, confirm,
    or validate. The user must transition the record in the Odoo UI or via an explicit
    odoo_execute call.

    For account.move (journal entries / invoices / bills):
      - Lines go in `line_ids` as [(0, 0, {...}), ...].
      - sum(debit) must equal sum(credit) before Odoo will accept the entry.
      - Always include `company_id` to avoid relying on the default company.
    """
    return _execute(model, "create", [values])


@mcp.tool()
def odoo_write(model: str, ids: list[int], values: dict) -> bool:
    """Update existing records. Returns True on success.

    Posted account.move records are locked — reset to draft first via odoo_execute
    (button_draft) before editing financial fields.
    """
    return _execute(model, "write", [ids, values])


@mcp.tool()
def odoo_archive(model: str, ids: list[int], archive: bool = True) -> bool:
    """Archive or unarchive records — the non-destructive alternative to deletion.

    Archiving sets `active = False`: the record is hidden from normal views and excluded
    from most operations, but is NOT deleted and can be restored. This is the supported
    way to "remove" a record under the no-deletion policy.

    Args:
        model: Odoo model name. The model must have an `active` field (most do).
        ids: Record IDs to archive/unarchive.
        archive: True to archive (active=False), False to unarchive (active=True).

    Returns: True on success. (If the model has no `active` field, Odoo raises a fault.)
    """
    return _execute(model, "write", [ids, {"active": not archive}])


@mcp.tool()
def odoo_cancel(model: str, ids: list[int]) -> dict[str, Any]:
    """Cancel workflow records via their cancel action — non-destructive alternative to delete.

    Tries the model's standard cancel methods in order (`action_cancel`, then
    `button_cancel`). Use for workflow documents (invoices/bills, sales/purchase orders,
    pickings, etc.) that should be voided rather than deleted. This is a state transition —
    only call it when the user explicitly asks to cancel the specific record(s).

    Args:
        model: Odoo model name, e.g. "account.move", "sale.order".
        ids: Record IDs to cancel.

    Returns: {"method": <method that worked>, "result": <return value>}.
    Raises if no cancel method is available on the model.
    """
    last_err: Exception | None = None
    for method in ("action_cancel", "button_cancel"):
        try:
            result = _execute(model, method, [ids])
            return {"method": method, "result": result}
        except RuntimeError as e:
            last_err = e
    raise RuntimeError(
        f"No cancel method worked for {model} (tried action_cancel, button_cancel). "
        f"Last error: {last_err}. If this model isn't a workflow document, consider "
        "odoo_archive instead."
    )


_REPORT_EXT = {"pdf": "pdf", "html": "html", "text": "txt"}


@mcp.tool()
def odoo_render_report(report_ref: str, ids: list[int], converter: str = "pdf") -> dict[str, Any]:
    """Render an Odoo QWeb report (`ir.actions.report`) to a file and return it as base64.

    This is the **native export** path: Odoo's rendered reports come from HTTP controllers,
    not XML-RPC, so this logs in over HTTP (using the connected user's credentials) and
    downloads `/report/<converter>/<report_name>/<ids>`. The client decodes `content_base64`
    and saves it locally.

    Works for QWeb reports — invoices, vendor bills, customer/partner statements, aged-partner
    PDFs, and any custom `ir.actions.report` of type pdf/html/text. It does NOT cover the
    Enterprise *financial* report engine (`account.report`: Balance Sheet, P&L, GL, Trial
    Balance) — those use a different controller; build those from data instead.

    Args:
        report_ref: The report's `report_name` (e.g. "account.report_invoice_with_payments")
                    OR the integer id of its `ir.actions.report` record (resolved automatically).
        ids: Record ids the report runs over (e.g. the account.move ids for invoices).
        converter: "pdf" (default), "html", or "text".

    Returns: {report_name, converter, ids, filename, mimetype, size, content_base64}.
    """
    if converter not in _REPORT_EXT:
        raise RuntimeError(f"converter must be one of {sorted(_REPORT_EXT)}; got {converter!r}.")
    s = _session()

    # Resolve an action id or xmlid to the report_name the URL expects.
    report_name = report_ref
    if isinstance(report_ref, int) or str(report_ref).isdigit():
        rec = _execute("ir.actions.report", "read", [[int(report_ref)]], {"fields": ["report_name"]})
        if not rec:
            raise RuntimeError(f"No ir.actions.report with id {report_ref}.")
        report_name = rec[0]["report_name"]

    opener = _web_opener(s["url"], s["db"], s["username"], s["api_key"])
    ids_str = ",".join(str(int(i)) for i in ids)
    dl_url = f"{s['url']}/report/{converter}/{report_name}/{ids_str}"
    try:
        resp = opener.open(urllib.request.Request(dl_url), timeout=180)
        raw = resp.read()
        mimetype = resp.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Report download failed ({e.code}) for {report_name} ids={ids_str}. "
            "Check the report_name and that the ids exist for this report's model."
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the report endpoint: {e}") from e

    # An HTML body on a PDF request usually means an Odoo error page, not a report.
    if converter == "pdf" and "text/html" in mimetype.lower():
        raise RuntimeError(
            f"Expected a PDF but got HTML for {report_name} — likely an Odoo error page "
            "(wrong report_name, or the records aren't valid for this report)."
        )

    safe = report_name.replace(".", "_").replace("/", "_")
    return {
        "report_name": report_name, "converter": converter, "ids": ids,
        "filename": f"{safe}_{ids_str}.{_REPORT_EXT[converter]}",
        "mimetype": mimetype, "size": len(raw),
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }


@mcp.tool()
def odoo_execute(
    model: str,
    method: str,
    args: list | None = None,
    kwargs: dict | None = None,
) -> Any:
    """Escape hatch — call any method on any model via execute_kw.

    Use only when no specialized tool fits. State-transition methods (`action_post`,
    `button_confirm`, `button_draft`, `action_cancel`) must only be invoked with explicit
    per-record user confirmation.

    Policy controls still apply: `unlink` is blocked everywhere (deletion disabled — use
    odoo_archive / odoo_cancel), and writes to technical/structural models (ir.model*,
    ir.module*, ir.ui.view, ir.actions*, ir.cron, base.automation, …) are blocked
    (model-tampering disabled). Both can be overridden in odoo_config.json.

    Args:
        model: Odoo model name.
        method: Method to invoke, e.g. "action_post", "button_draft".
        args: Positional args. For most state methods this is [[record_id]].
        kwargs: Keyword args, often {}.
    """
    return _execute(model, method, args or [], kwargs or {})


def _transport_security():
    """Configure DNS-rebinding/Host-header protection for HTTP transports.

    The MCP SDK defaults to allowing only localhost Host headers, which rejects a server
    reached through a reverse proxy / Cloudflare tunnel (the Host is the public domain) with
    "421 Misdirected Request". Behaviour via MCP_ALLOWED_HOSTS:

      - unset or "*"  → DNS-rebinding host checks DISABLED. Intended for a server whose network
                        boundary is a trusted proxy/tunnel. (Content-Type is still validated.)
      - "a.com,b.com" → checks ENABLED, limited to those exact hosts (+ any-port variants and
                        matching http/https origins).
    """
    from mcp.server.transport_security import TransportSecuritySettings

    allowed = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if allowed and allowed != "*":
        hosts: list[str] = []
        origins: list[str] = []
        for h in (x.strip() for x in allowed.split(",") if x.strip()):
            hosts += [h, f"{h}:*"]                       # exact + any-port
            origins += [f"https://{h}", f"http://{h}", f"https://{h}:*", f"http://{h}:*"]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins,
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _run() -> None:
    """Launch the server. Transport is chosen by the MCP_TRANSPORT env var:

      - unset / "stdio"            → stdio (default; for local subprocess clients)
      - "http" / "streamable-http" → streamable-HTTP service (for hosted/Docker use)

    For HTTP, bind address and port come from MCP_HOST (default 0.0.0.0) and
    MCP_PORT (default 8000). The MCP endpoint is served at MCP_PATH (default /mcp).
    Host-header protection is controlled by MCP_ALLOWED_HOSTS (see _transport_security).
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8000"))
        mcp.settings.streamable_http_path = os.environ.get("MCP_PATH", "/mcp")
        mcp.settings.transport_security = _transport_security()
        mcp.run(transport="streamable-http")
    elif transport == "sse":
        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8000"))
        mcp.settings.transport_security = _transport_security()
        mcp.run(transport="sse")
    else:
        mcp.run()


if __name__ == "__main__":
    _run()
