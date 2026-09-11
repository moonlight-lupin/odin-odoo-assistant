"""Odoo MCP — Config GUI.

A tiny zero-dependency (stdlib Tkinter only) GUI for editing the **URL** and
**database** in the `odoo_config.json` that the MCP server reads.

By design this GUI manages ONLY the two shared instance settings — `url` and
`db`. The `username` and `api_key` are personal credentials that each user
supplies themselves (added to `odoo_config.json` directly, or prompted for by
the skill). The GUI never displays or edits them, and Save preserves whatever
credentials are already in the file.

Run it directly — no virtualenv or `mcp` package required:

    python config_gui.py

By default it reads/writes `odoo_config.json` in the PARENT folder of this
script (matching server.py's default). Override with ODOO_CONFIG_PATH, or
use File → Open to point at a different config.
"""

from __future__ import annotations

import json
import os
import ssl
import threading
import xmlrpc.client
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Mirror server.py: config lives in the parent folder of this script by default.
_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "odoo_config.json"
_CONFIG_PATH = Path(os.environ.get("ODOO_CONFIG_PATH", str(_DEFAULT_CONFIG)))

# The GUI edits only these. username/api_key are user-supplied and preserved on save.
_EDITABLE = ("url", "db")
_CREDENTIALS = ("username", "api_key")


# ---------- XML-RPC helpers (self-contained, no dependency on server.py) ----------

def _make_proxies(url: str):
    """Return (common, db_service, models) proxies, TLS-tolerant for self-signed certs."""
    url = url.rstrip("/")
    if url.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        transport = xmlrpc.client.SafeTransport(context=ctx)
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", transport=transport)
        db_service = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/db", transport=transport)
        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", transport=transport)
    else:
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        db_service = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/db")
        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return common, db_service, models


def list_databases(url: str) -> list[str]:
    """Ask the Odoo server for its database list. May be disabled server-side."""
    _, db_service, _ = _make_proxies(url)
    return list(db_service.list())


def test_connection(url: str, db: str, username: str, api_key: str) -> tuple[bool, str]:
    """Authenticate and, on success, fetch the visible company names."""
    common, _, models = _make_proxies(url)
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        return False, (
            "Authentication failed — check the username and api_key in your "
            "config, plus the db name.\nOdoo API keys require developer mode to be enabled."
        )
    companies = models.execute_kw(
        db, uid, api_key, "res.company", "search_read", [[]],
        {"fields": ["id", "name"], "order": "id asc"},
    )
    names = ", ".join(f"[{c['id']}] {c['name']}" for c in companies) or "(none visible)"
    return True, f"Connected. UID {uid}.\nCompanies: {names}"


# ---------- GUI ----------

class ConfigApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config_path = _CONFIG_PATH
        self.vars = {f: tk.StringVar() for f in _EDITABLE}
        # Credentials read from disk, kept in memory only — never shown or edited.
        self._creds: dict[str, str] = {}
        self._build_ui()
        self._load(self.config_path, announce=False)

    # ---- layout ----
    def _build_ui(self) -> None:
        self.root.title("Odoo MCP — Config (URL & Database)")
        self.root.minsize(560, 0)

        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open…", command=self._open_dialog)
        filemenu.add_command(label="Save", command=self._save, accelerator="Ctrl+S")
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=filemenu)
        self.root.config(menu=menubar)
        self.root.bind("<Control-s>", lambda _e: self._save())

        frm = ttk.Frame(self.root, padding=14)
        frm.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frm, text="Server URL").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.vars["url"]).grid(
            row=row, column=1, sticky="ew", pady=4, padx=(8, 6))
        ttk.Button(frm, text="Fetch DBs", command=self._fetch_dbs, width=10).grid(
            row=row, column=2, pady=4)

        row += 1
        ttk.Label(frm, text="Database").grid(row=row, column=0, sticky="w", pady=4)
        self.db_combo = ttk.Combobox(frm, textvariable=self.vars["db"], values=[])
        self.db_combo.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4, padx=(8, 0))

        row += 1
        self.cred_label = ttk.Label(frm, text="", foreground="#666")
        self.cred_label.grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 0))

        row += 1
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 6))
        ttk.Button(btns, text="Test Connection", command=self._test).pack(side="left")
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=8)
        self.spinner = ttk.Label(btns, text="")
        self.spinner.pack(side="left", padx=8)

        row += 1
        self.status = tk.Text(frm, height=6, wrap="word", state="disabled",
                              relief="solid", borderwidth=1)
        self.status.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        frm.rowconfigure(row, weight=1)

        row += 1
        self.path_label = ttk.Label(frm, text="", foreground="#666")
        self.path_label.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))

    # ---- helpers ----
    def _set_status(self, msg: str, ok: bool | None = None) -> None:
        self.status.config(state="normal")
        self.status.delete("1.0", "end")
        self.status.insert("1.0", msg)
        color = {True: "#0a7d28", False: "#b00020", None: "#000"}[ok]
        self.status.tag_add("all", "1.0", "end")
        self.status.tag_config("all", foreground=color)
        self.status.config(state="disabled")

    def _refresh_cred_label(self) -> None:
        have = [c for c in _CREDENTIALS if self._creds.get(c)]
        if len(have) == len(_CREDENTIALS):
            self.cred_label.config(
                text="Credentials: username + api_key found in config (managed by you, "
                     "not editable here).", foreground="#0a7d28")
        else:
            missing = ", ".join(c for c in _CREDENTIALS if not self._creds.get(c))
            self.cred_label.config(
                text=f"Credentials: missing {missing} — add them to the config file "
                     "yourself (the GUI doesn't manage credentials).", foreground="#b00020")

    def _busy(self, on: bool) -> None:
        self.spinner.config(text="working…" if on else "")
        self.root.update_idletasks()

    def _run_bg(self, fn, on_done) -> None:
        """Run fn() in a worker thread; deliver result to on_done on the UI thread."""
        self._busy(True)

        def worker():
            try:
                result = ("ok", fn())
            except Exception as exc:  # noqa: BLE001 — surface anything to the user
                result = ("err", exc)
            self.root.after(0, lambda: self._finish(result, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, result, on_done) -> None:
        self._busy(False)
        on_done(result)

    # ---- actions ----
    def _open_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Open odoo_config.json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if path:
            self._load(Path(path), announce=True)

    def _load(self, path: Path, announce: bool) -> None:
        self.config_path = path
        self.path_label.config(text=f"Config: {path}")
        self._creds = {}
        if path.exists():
            try:
                cfg = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                self._set_status(f"Could not read config:\n{exc}", ok=False)
                self._refresh_cred_label()
                return
            for f in _EDITABLE:
                self.vars[f].set(str(cfg.get(f, "")))
            # Keep credentials in memory for Test Connection; never display them.
            for c in _CREDENTIALS:
                if cfg.get(c):
                    self._creds[c] = str(cfg[c])
            if announce:
                self._set_status(f"Loaded {path}", ok=None)
        else:
            for f in _EDITABLE:
                self.vars[f].set("")
            if announce:
                self._set_status(f"No file at {path} — fill URL/Database and Save.", ok=None)
        self._refresh_cred_label()

    def _save(self) -> None:
        url = self.vars["url"].get().strip()
        db = self.vars["db"].get().strip()
        missing = [f for f, v in (("url", url), ("db", db)) if not v]
        if missing:
            if not messagebox.askyesno(
                "Empty fields",
                f"These fields are empty: {', '.join(missing)}.\nSave anyway?",
            ):
                return

        # Merge into the existing file so user-supplied credentials survive.
        existing: dict[str, str] = {}
        if self.config_path.exists():
            try:
                existing = json.loads(self.config_path.read_text())
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing["url"] = url
        existing["db"] = db

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(existing, indent=2) + "\n")
        except OSError as exc:
            self._set_status(f"Save failed:\n{exc}", ok=False)
            return

        # Refresh our in-memory view of credentials from what's now on disk.
        self._creds = {c: str(existing[c]) for c in _CREDENTIALS if existing.get(c)}
        self._refresh_cred_label()
        note = "" if len(self._creds) == len(_CREDENTIALS) else (
            "\nNote: username/api_key are not set in this file yet — add them yourself.")
        self._set_status(f"Saved URL and database to {self.config_path}.{note}", ok=True)

    def _fetch_dbs(self) -> None:
        url = self.vars["url"].get().strip()
        if not url:
            self._set_status("Enter a Server URL first.", ok=False)
            return
        self._set_status(f"Fetching databases from {url} …", ok=None)
        self._run_bg(lambda: list_databases(url), self._fetch_dbs_done)

    def _fetch_dbs_done(self, result) -> None:
        kind, payload = result
        if kind == "err":
            self._set_status(
                f"Could not fetch databases:\n{payload}\n\n"
                "The server may have the database list disabled "
                "(list_db = False). Type the database name manually.",
                ok=False,
            )
            return
        dbs = payload
        self.db_combo["values"] = dbs
        if not dbs:
            self._set_status("Server returned no databases.", ok=None)
        elif not self.vars["db"].get().strip():
            self.vars["db"].set(dbs[0])
        self._set_status(f"Found {len(dbs)} database(s): {', '.join(dbs)}", ok=True)

    def _test(self) -> None:
        url = self.vars["url"].get().strip()
        db = self.vars["db"].get().strip()
        if not url or not db:
            self._set_status("Fill URL and Database first.", ok=False)
            return
        missing = [c for c in _CREDENTIALS if not self._creds.get(c)]
        if missing:
            self._set_status(
                f"Cannot test: {', '.join(missing)} not found in the config file.\n"
                "The GUI doesn't manage credentials — add your username and api_key to "
                f"{self.config_path} (or have the skill prompt you), then Test again.",
                ok=False,
            )
            return
        self._set_status("Testing connection …", ok=None)
        username, api_key = self._creds["username"], self._creds["api_key"]
        self._run_bg(
            lambda: test_connection(url, db, username, api_key),
            self._test_done,
        )

    def _test_done(self, result) -> None:
        kind, payload = result
        if kind == "err":
            self._set_status(f"Connection error:\n{payload}", ok=False)
            return
        ok, msg = payload
        self._set_status(msg, ok=ok)


def main() -> None:
    root = tk.Tk()
    ConfigApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
