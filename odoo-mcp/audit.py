"""Logging interface for the Odoo MCP server: an audit trail plus diagnostics.

Two streams, both structured, both on **stderr-safe** paths (stdout is the MCP
channel when the server runs on stdio — writing a single stray byte there
corrupts the protocol):

1. **Audit trail** — one JSON object per tool call, appended to a rotating
   JSONL file and mirrored to stderr. This is the "what did the agent actually
   do in our books" record: who was connected, which tool, which arguments,
   whether it succeeded / failed / was refused by a guardrail, and how long it
   took. Read it back with :meth:`AuditLog.tail` or the ``odoo_audit_tail``
   tool.

2. **Diagnostics** — the ordinary ``logging`` logger ``odoo_mcp``, for
   connection, auth, transport and configuration events. Operators tail this
   in ``docker logs``.

Safety properties this module is built around, in priority order:

* **Credentials never land in the log.** Any mapping key that looks like a
  secret (``api_key``, ``password``, ``token``, ``authorization``, …) is
  replaced with ``***redacted***`` before serialisation, at every depth.
* **Logging never breaks a tool call.** Every sink write is wrapped: an
  unwritable path, a full disk or an unserialisable payload degrades the
  record, never the call.
* **Payloads stay bounded.** Long strings, long lists, wide mappings and deep
  nesting are truncated, and result sets are summarised to a count plus ids
  rather than embedded whole. No single record can grow without limit.

Configuration (env var wins over the ``odoo_config.json`` key):

===========================  =========================  ==========================
Env var                      config key                 default
===========================  =========================  ==========================
``MCP_AUDIT_LOG``            ``audit_log``              ``<config dir>/mcp-audit.jsonl``
                                                        (``off`` disables the file)
``MCP_AUDIT_LEVEL``          ``audit_level``            ``all`` (``off`` / ``error`` / ``all``)
``MCP_AUDIT_PAYLOADS``       ``audit_payloads``         ``true``
``MCP_AUDIT_MAX_BYTES``      ``audit_max_bytes``        ``5242880`` (5 MiB)
``MCP_AUDIT_BACKUPS``        ``audit_backups``          ``3``
``MCP_LOG_LEVEL``            ``log_level``              ``INFO`` (diagnostics)
===========================  =========================  ==========================

The redaction and truncation rules here are mirrored 1:1 by the drop-in Odoo
module (``odoo_module_mcp_server/log_utils.py``) so an audit trail reads the
same whichever server produced it.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

LOGGER_NAME = "odoo_mcp"
AUDIT_LOGGER_NAME = "odoo_mcp.audit"

REDACTED = "***redacted***"

# The key a truncated mapping carries its "…and N more" marker under. Not a
# legal Odoo field name, so it can never collide with a real payload key.
TRUNCATED_KEY = "…"

DEFAULT_MAX_STRING = 512
DEFAULT_MAX_ITEMS = 20
# Mappings get their own, more generous cap. A write payload's breadth IS the
# audit-relevant part — truncating `values` at 20 fields would hide what an
# agent wrote — but it still has to be bounded: `odoo_fields_get` on
# account.move returns ~350 field definitions, and writing that whole dict as
# one record churns a 5 MiB trail and evicts the write records it exists for.
# 64 renders any realistic create/write payload whole and caps schema dumps.
DEFAULT_MAX_KEYS = 64
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUPS = 3

LEVELS = ("off", "error", "all")

# Substrings that mark a mapping key as carrying a secret. Matched
# case-insensitively against the whole key, so `apiKey`, `X-Authorization` and
# `client_secret` are all caught.
_SECRET_HINTS = (
    "api_key", "apikey", "password", "passwd", "secret", "token",
    "authorization", "credential", "private_key", "session_id",
)

_logger = logging.getLogger(LOGGER_NAME)
_audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)

# Exception types that mean "refused by policy" rather than "went wrong" —
# registered by server.py so this module stays import-independent of it.
_POLICY_ERRORS: tuple[type[BaseException], ...] = ()

# Callable returning the session block to attach to each record (never the key).
_CONTEXT_PROVIDER: Callable[[], dict] | None = None


# ---------------------------------------------------------------------------
# Redaction & truncation
# ---------------------------------------------------------------------------


def _is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def redact(
    value: Any,
    *,
    max_string: int = DEFAULT_MAX_STRING,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_keys: int = DEFAULT_MAX_KEYS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    _depth: int = 0,
) -> Any:
    """Return a copy of ``value`` safe to persist: secrets masked, size bounded.

    Recurses through dicts/lists/tuples. Strings longer than ``max_string``,
    sequences longer than ``max_items`` and mappings wider than ``max_keys`` are
    truncated with an explicit marker so a reader can tell the difference
    between "short" and "shortened". Nesting deeper than ``max_depth``
    collapses to ``"…"``.
    """
    if _depth > max_depth:
        return "…"
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:max_keys]:
            if _is_secret_key(key):
                out[key] = REDACTED
            else:
                out[key] = redact(item, max_string=max_string, max_items=max_items,
                                  max_keys=max_keys, max_depth=max_depth,
                                  _depth=_depth + 1)
        if len(value) > max_keys:
            out[TRUNCATED_KEY] = "[+%d more keys]" % (len(value) - max_keys)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        kept = [redact(item, max_string=max_string, max_items=max_items,
                       max_keys=max_keys, max_depth=max_depth, _depth=_depth + 1)
                for item in items[:max_items]]
        if len(items) > max_items:
            kept.append("…[+%d more items]" % (len(items) - max_items))
        return kept
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<%d bytes>" % len(bytes(value))
    if isinstance(value, str):
        if len(value) > max_string:
            return "%s…[+%d more chars]" % (value[:max_string], len(value) - max_string)
        return value
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    # Anything else (recordset proxies, dates, opaque objects) — repr it, bounded.
    return redact(repr(value), max_string=max_string, max_items=max_items,
                  max_keys=max_keys, max_depth=max_depth, _depth=_depth)


def summarize_result(
    value: Any,
    *,
    max_string: int = DEFAULT_MAX_STRING,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_keys: int = DEFAULT_MAX_KEYS,
) -> Any:
    """Condense a tool's return value for the log.

    A list of records collapses to ``{"type": "records", "count": N, "ids": [...]}``
    — the ids are what an auditor needs to pull the records back up; the field
    values are already in Odoo. Everything else goes through :func:`redact`.
    """
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        items = list(value)
        if items and all(isinstance(item, dict) for item in items):
            ids = [item.get("id") for item in items[:max_items] if isinstance(item.get("id"), int)]
            return {"type": "records", "count": len(items), "ids": ids}
    return redact(value, max_string=max_string, max_items=max_items,
                  max_keys=max_keys)


def classify_error(error: BaseException) -> str:
    """``blocked`` for a policy refusal, ``error`` for anything else."""
    if _POLICY_ERRORS and isinstance(error, _POLICY_ERRORS):
        return "blocked"
    return "error"


def register_policy_errors(*types: type[BaseException]) -> None:
    """Tell the audit trail which exceptions mean "refused by policy"."""
    global _POLICY_ERRORS
    _POLICY_ERRORS = tuple(types)


def set_context_provider(provider: Callable[[], dict] | None) -> None:
    """Install a callable returning the session block for each record."""
    global _CONTEXT_PROVIDER
    _CONTEXT_PROVIDER = provider


# ---------------------------------------------------------------------------
# The audit log
# ---------------------------------------------------------------------------


class AuditLog:
    """Append-only JSONL audit trail with size-based rotation.

    ``path=None`` means no file sink — records still go to stderr, so an
    ``AuditLog()`` constructed with no arguments touches no disk at all.

    Note this is NOT what an unconfigured server does: :func:`configure` with
    neither ``MCP_AUDIT_LOG`` nor ``audit_log`` set defaults to a real file
    beside ``odoo_config.json``, on the principle that an audit trail you have
    to remember to switch on is one that will be off when it matters. Set
    ``MCP_AUDIT_LOG=off`` for stderr only.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        level: str = "all",
        payloads: bool = True,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backups: int = DEFAULT_BACKUPS,
        stderr: bool = True,
        max_string: int = DEFAULT_MAX_STRING,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_keys: int = DEFAULT_MAX_KEYS,
    ):
        self.file_path = Path(path) if path else None
        self.level = level if level in LEVELS else "all"
        self.payloads = payloads
        self.max_bytes = max_bytes
        self.backups = backups
        self.stderr = stderr
        self.max_string = max_string
        self.max_items = max_items
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._seq = 0
        self._warned = False

    # -- gating ----------------------------------------------------------

    def enabled_for(self, outcome: str) -> bool:
        if self.level == "off":
            return False
        if self.level == "error":
            return outcome != "ok"
        return True

    # -- writing ---------------------------------------------------------

    def _rotate(self) -> None:
        """Shift ``a.jsonl`` → ``a.jsonl.1`` → … dropping anything past ``backups``."""
        path = self.file_path
        if path is None:
            return
        if self.backups <= 0:
            path.unlink(missing_ok=True)
            return
        oldest = path.with_name(path.name + ".%d" % self.backups)
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            src = path.with_name(path.name + ".%d" % index)
            if src.exists():
                src.replace(path.with_name(path.name + ".%d" % (index + 1)))
        path.replace(path.with_name(path.name + ".1"))

    def _write(self, line: str) -> None:
        """Append one already-serialised line, rotating first if it would overflow."""
        path = self.file_path
        if path is None:
            return
        # Bytes, not characters: the file is UTF-8, so a record full of
        # non-ASCII (a French customer name, a ¥ amount) takes more room on disk
        # than len() suggests, and the cap has to measure what is written.
        size = len(line.encode("utf-8"))
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if (self.max_bytes and path.exists()
                    and path.stat().st_size + size > self.max_bytes):
                self._rotate()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def _serialise(self, event: dict) -> str:
        try:
            return json.dumps(event, default=str, ensure_ascii=False) + "\n"
        except Exception:  # pragma: no cover — default=str makes this near-impossible
            return json.dumps({"ts": _now(), "kind": "audit_error",
                               "message": "record could not be serialised"}) + "\n"

    def record(self, event: dict) -> None:
        """Serialise and emit one record. Never raises."""
        line = self._serialise(event)
        # A record too big for the rotation window would otherwise rotate the
        # history away and still be written oversized — three such calls would
        # consume all three backups and leave the trail holding almost nothing.
        # Drop the payload instead and keep the fact of the call: who, which
        # tool, what outcome — the part an auditor cannot reconstruct elsewhere.
        if self.max_bytes and len(line.encode("utf-8")) > self.max_bytes:
            trimmed = {key: value for key, value in event.items()
                       if key not in ("args", "result")}
            trimmed["payload_dropped"] = (
                "payload omitted — the record exceeded max_bytes (%d)"
                % self.max_bytes)
            line = self._serialise(trimmed)
        if self.stderr:
            try:
                _audit_logger.info("%s", line.rstrip("\n"))
            except Exception:
                pass
        try:
            self._write(line)
        except Exception as error:
            # A broken sink must never break a tool call — say so once, then stay quiet.
            if not self._warned:
                self._warned = True
                _logger.warning("Audit log sink unavailable (%s): %s — continuing "
                                "with stderr only.", self.file_path, error)

    # -- record builders -------------------------------------------------

    def _base(self, kind: str) -> dict:
        # Under HTTP transport FastMCP runs sync tools in a thread pool, so two
        # calls can be in _base at once. `+= 1` is a read-modify-write: without
        # the lock both threads can read the same value and emit two records
        # sharing a seq, and an auditor reconciling the trail cannot then tell a
        # duplicate from a lost record.
        with self._lock:
            self._seq += 1
            seq = self._seq
        event = {"ts": _now(), "kind": kind, "seq": seq, "pid": os.getpid()}
        if _CONTEXT_PROVIDER is not None:
            try:
                context = _CONTEXT_PROVIDER()
            except Exception:
                context = None
            if context:
                event["session"] = context
        return event

    def tool_call(self, tool: str, args: dict | None, outcome: str,
                  duration_ms: float, result: Any = None,
                  error: BaseException | None = None) -> None:
        """Record one MCP tool invocation."""
        if not self.enabled_for(outcome):
            return
        event = self._base("tool_call")
        event["tool"] = tool
        event["outcome"] = outcome
        event["duration_ms"] = round(duration_ms, 3)
        if self.payloads:
            if args is not None:
                event["args"] = redact(args, max_string=self.max_string,
                                       max_items=self.max_items,
                                       max_keys=self.max_keys)
            if error is None:
                event["result"] = summarize_result(result, max_string=self.max_string,
                                                   max_items=self.max_items,
                                                   max_keys=self.max_keys)
        if error is not None:
            event["error"] = {
                "type": type(error).__name__,
                "message": redact(str(error), max_string=self.max_string),
            }
        self.record(event)

    def event(self, kind: str, **fields: Any) -> None:
        """Record a non-tool event (connect, auth failure, startup, …)."""
        if self.level == "off":
            return
        event = self._base(kind)
        event.update(redact(fields, max_string=self.max_string,
                            max_items=self.max_items, max_keys=self.max_keys))
        self.record(event)

    # -- reading ---------------------------------------------------------

    def tail(self, limit: int = 50, tool: str | None = None,
             outcome: str | None = None) -> list[dict]:
        """Return up to ``limit`` recent records, newest first. Never raises."""
        if self.file_path is None or not self.file_path.exists():
            return []
        try:
            lines = self.file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            _logger.warning("Could not read audit log %s: %s", self.file_path, error)
            return []
        rows: list[dict] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if tool and row.get("tool") != tool:
                continue
            if outcome and row.get("outcome") != outcome:
                continue
            rows.append(row)
            if len(rows) >= max(1, limit):
                break
        return rows

    def describe(self) -> dict:
        """Operator-facing summary of where the trail is and what it captures."""
        if self.level == "off":
            status = "disabled (audit level 'off')"
        elif self.file_path is None:
            status = "stderr only — no file sink configured (set MCP_AUDIT_LOG)"
        else:
            status = "active"
        return {
            "status": status,
            "path": str(self.file_path) if self.file_path else None,
            "level": self.level,
            "payloads": self.payloads,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Module-level active log
# ---------------------------------------------------------------------------

# Until configure() runs: stderr only. Merely importing this module never
# creates a file — the file sink is configure()'s doing, and _run() is the only
# caller. (configure() itself DOES default to a real path; see AuditLog.)
_ACTIVE: AuditLog | None = None


def get() -> AuditLog:
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = AuditLog()
    return _ACTIVE


def record_tool_call(tool: str, args: dict | None, outcome: str,
                     duration_ms: float, result: Any = None,
                     error: BaseException | None = None) -> None:
    get().tool_call(tool, args, outcome, duration_ms, result=result, error=error)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _logger.warning("%s=%r is not an integer — using %d.", name, raw, default)
        return default


def _is_off(raw: Any) -> bool:
    if raw is False:
        return True
    if isinstance(raw, str):
        return raw.strip().lower() in ("", "off", "none", "false", "0", "-")
    return False


def setup_diagnostics(level: str | None = None) -> None:
    """Install the stderr handler for the diagnostic + audit-mirror loggers.

    Idempotent, and hard-wired to ``sys.stderr``: on stdio transport, stdout
    carries the MCP protocol and must stay byte-clean.

    ``MCP_LOG_LEVEL`` moves the DIAGNOSTIC logger only. The audit mirror stays
    pinned at INFO: what the trail records is ``MCP_AUDIT_LEVEL``'s business,
    and quietening connection chatter must never silently switch the audit
    trail off — with ``MCP_AUDIT_LOG=off`` (a supported setup) stderr is the
    only sink there is.
    """
    level = (level or os.environ.get("MCP_LOG_LEVEL") or "INFO").upper()
    for logger in (_logger, _audit_logger):
        logger.setLevel(logging.INFO if logger is _audit_logger
                        else getattr(logging, level, logging.INFO))
        logger.propagate = False
        if any(getattr(handler, "_odoo_mcp", False) for handler in logger.handlers):
            continue
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._odoo_mcp = True  # type: ignore[attr-defined]
        logger.addHandler(handler)


def configure(cfg: dict | None = None, default_dir: Path | None = None,
              install: bool = True) -> AuditLog:
    """Build an :class:`AuditLog` from env vars + ``odoo_config.json``.

    Env vars win over config keys. ``default_dir`` is where the trail lands when
    neither names a path (the folder holding ``odoo_config.json``).
    """
    cfg = cfg or {}
    setup_diagnostics(os.environ.get("MCP_LOG_LEVEL") or cfg.get("log_level"))

    raw_path = os.environ.get("MCP_AUDIT_LOG")
    if raw_path is None:
        raw_path = cfg.get("audit_log")
    if raw_path is None:
        base = default_dir or Path.cwd()
        path: Path | None = Path(base) / "mcp-audit.jsonl"
    elif _is_off(raw_path):
        path = None
    else:
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute():
            # A relative path is relative to the CONFIG, not the process cwd —
            # on stdio the cwd is whatever the MCP client happened to launch from.
            path = Path(default_dir or Path.cwd()) / path

    level = (os.environ.get("MCP_AUDIT_LEVEL") or cfg.get("audit_level") or "all").lower()
    if level not in LEVELS:
        _logger.warning("Unknown audit level %r — falling back to 'all' "
                        "(valid: %s).", level, ", ".join(LEVELS))
        level = "all"

    log = AuditLog(
        path=path,
        level=level,
        payloads=_env_flag("MCP_AUDIT_PAYLOADS", bool(cfg.get("audit_payloads", True))),
        max_bytes=_env_int("MCP_AUDIT_MAX_BYTES",
                           int(cfg.get("audit_max_bytes", DEFAULT_MAX_BYTES))),
        backups=_env_int("MCP_AUDIT_BACKUPS", int(cfg.get("audit_backups", DEFAULT_BACKUPS))),
        stderr=_env_flag("MCP_AUDIT_STDERR", True),
    )
    if install:
        global _ACTIVE
        _ACTIVE = log
    return log


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------


def audit_tool(fn: Callable) -> Callable:
    """Wrap an MCP tool so every invocation lands in the audit trail.

    ``functools.wraps`` keeps ``__doc__``, ``__name__`` and — via
    ``__wrapped__`` — the signature FastMCP introspects to build the tool
    schema, so wrapping is invisible to the protocol.

    One thing ``wraps`` cannot copy is ``__globals__``: it is a read-only
    attribute of the function object. That matters because ``server.py`` uses
    ``from __future__ import annotations``, so every annotation reaches FastMCP
    as a STRING, and FastMCP resolves those forward references against
    ``wrapper.__globals__`` — which is this module's namespace, not
    ``server.py``'s. Anything a tool annotates that happens not to exist here
    would fail to resolve, and FastMCP resolves leniently: the NameError is
    swallowed and the parameter silently loses its schema.

    So resolve the annotations HERE, in ``fn``'s own namespace, and publish the
    result as ``__signature__``. ``inspect.signature`` prefers that over
    re-deriving from the annotations, so FastMCP sees real type objects and
    never has to guess a namespace. If a tool carries an annotation that cannot
    be resolved even in its own module (a ``TYPE_CHECKING``-only import, say),
    fall back to the unresolved signature and say so rather than failing the
    import — a tool with a degraded schema still beats a server that won't start.
    """
    signature = inspect.signature(fn)
    try:
        resolved = inspect.signature(fn, eval_str=True)
    except Exception as error:
        _logger.warning(
            "Could not resolve type annotations for tool %s (%s) — its MCP "
            "schema may be incomplete.", getattr(fn, "__name__", fn), error)
        resolved = None

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            bound = signature.bind_partial(*args, **kwargs)
            captured = dict(bound.arguments)
        except TypeError:
            captured = {"__args__": list(args), "__kwargs__": dict(kwargs)}
        try:
            result = fn(*args, **kwargs)
        except BaseException as error:
            _safe_record(fn.__name__, captured, classify_error(error),
                         (time.perf_counter() - started) * 1000, error=error)
            raise
        _safe_record(fn.__name__, captured, "ok",
                     (time.perf_counter() - started) * 1000, result=result)
        return result

    if resolved is not None:
        wrapper.__signature__ = resolved  # type: ignore[attr-defined]
    return wrapper


def _safe_record(tool: str, args: dict, outcome: str, duration_ms: float,
                 result: Any = None, error: BaseException | None = None) -> None:
    """Record, swallowing anything the audit path itself throws."""
    try:
        get().tool_call(tool, args, outcome, duration_ms, result=result, error=error)
    except Exception as audit_error:  # pragma: no cover — belt and braces
        try:
            _logger.warning("Audit record for %s failed: %s", tool, audit_error)
        except Exception:
            pass


__all__: Iterable[str] = (
    "AuditLog", "audit_tool", "classify_error", "configure", "get", "redact",
    "record_tool_call", "register_policy_errors", "set_context_provider",
    "setup_diagnostics", "summarize_result", "REDACTED", "TRUNCATED_KEY",
    "LEVELS", "DEFAULT_MAX_ITEMS", "DEFAULT_MAX_KEYS", "DEFAULT_MAX_STRING",
)

# Install the stderr handlers at import so nothing is ever lost before
# configure() runs (and so a stdio server never writes to stdout).
setup_diagnostics()
