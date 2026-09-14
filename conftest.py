"""Repo-root pytest configuration.

Lets ``python -m pytest`` at the repository root run both suites:

* ``odoo-mcp/tests/`` — the external server (its own conftest stubs the mcp SDK)
* ``odoo_module_mcp_server/tests/`` — the drop-in module's Odoo-free unit tests

The module is an Odoo addon, so importing its package executes
``from odoo import ...`` — unavailable outside an Odoo installation. pytest
derives a test module's import name from the ``__init__.py`` chain, so
collecting the module's tests would import the addon package and fail on that.
Registering a **path-only** stand-in for the addon package first fixes it: the
stand-in's ``__path__`` is the real directory, so ``odoo_module_mcp_server.tests``
still resolves, but the addon's own ``__init__.py`` (and therefore odoo) is
never imported.

Under Odoo's own test runner none of this applies — Odoo imports the addon
properly and the tests import ``odoo.addons.odoo_module_mcp_server.log_utils``.
"""
import sys
import types
from pathlib import Path

_ADDON = Path(__file__).resolve().parent / "odoo_module_mcp_server"

if _ADDON.is_dir() and _ADDON.name not in sys.modules:
    _stub = types.ModuleType(_ADDON.name)
    _stub.__path__ = [str(_ADDON)]  # a package for import purposes, nothing more
    _stub.__doc__ = "Path-only stand-in installed by conftest.py; see its docstring."
    sys.modules[_ADDON.name] = _stub
