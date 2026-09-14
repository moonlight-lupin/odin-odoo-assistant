"""A fake ``odoo`` just large enough to import and exercise this addon.

The module's guardrails, JSON-RPC dispatch and OAuth code-exchange logic are
plain Python wrapped in a thin ORM layer. Standing up a real database to test
them costs minutes per run; standing up enough of ``odoo`` to import them costs
milliseconds — and the logic under test is the same either way.

What this fake is NOT: a substitute for running the addon against a real Odoo.
It cannot catch a bad view arch, a missing xmlid, an ORM method that does not
exist, or a field the database rejects. It pins *decision logic* — which models
are refused, which methods are private, whether a reused authorization code is
rejected — and those are the parts that carry security weight.

Under a real Odoo (its own test runner) ``install()`` is a no-op and callers
skip: the behaviours are exercised live there instead.

Usage::

    from . import _fake_odoo
    _fake_odoo.install()
    generic_tools = _fake_odoo.load('generic_tools')
    env = _fake_odoo.FakeEnv()
"""
import importlib
import sys
import types
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent.parent
ADDON_NAME = ADDON_DIR.name


def real_odoo_present():
    """True when a genuine Odoo is importable — the fake must stand aside."""
    try:
        import odoo
    except ImportError:
        return False
    return hasattr(odoo, 'addons')


# ---------------------------------------------------------------------------
# The fake package
# ---------------------------------------------------------------------------

class _Field:
    """Stands in for fields.Char/Many2one/…: only needs to be constructible."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _Datetime:
    """fields.Datetime — both a field (constructible) and a namespace.

    Odoo's real Datetime is both: `fields.Datetime(required=True)` declares a
    column, while `fields.Datetime.now()` is a helper. Tests that care about
    time set `_Datetime._now`.
    """

    _now = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    @classmethod
    def now(cls):
        from datetime import datetime
        return cls._now or datetime(2026, 9, 14, 10, 0, 0)

    @staticmethod
    def subtract(value, **kwargs):
        from datetime import timedelta
        return value - timedelta(**kwargs)

    @staticmethod
    def to_datetime(value):
        return value


class _Model:
    """Base for models.Model / TransientModel / AbstractModel."""
    _name = None
    _inherit = None


def _call_kw(model, method, args, kwargs):
    """Stand-in for odoo.api.call_kw — record it and delegate to the fake model."""
    return model._record_call_kw(method, args, kwargs)


def install():
    """Install the fake ``odoo`` into sys.modules. Idempotent; no-op under real Odoo."""
    if 'odoo' in sys.modules:
        return sys.modules['odoo']

    odoo = types.ModuleType('odoo')
    odoo._ = lambda text: text
    odoo.SUPERUSER_ID = 1

    api_mod = types.ModuleType('odoo.api')
    api_mod.model = lambda fn: fn
    api_mod.depends = lambda *a, **k: (lambda fn: fn)
    api_mod.call_kw = _call_kw
    api_mod.Environment = lambda cr, uid, ctx: cr.env

    fields_mod = types.ModuleType('odoo.fields')
    for name in ('Char', 'Text', 'Integer', 'Float', 'Boolean', 'Selection',
                 'Many2one', 'One2many', 'Many2many', 'Binary', 'Date', 'Html'):
        setattr(fields_mod, name, _Field)
    fields_mod.Datetime = _Datetime

    models_mod = types.ModuleType('odoo.models')
    models_mod.Model = _Model
    models_mod.TransientModel = _Model
    models_mod.AbstractModel = _Model

    exceptions_mod = types.ModuleType('odoo.exceptions')

    class UserError(Exception):
        pass

    class AccessError(Exception):
        pass

    class ValidationError(Exception):
        pass
    exceptions_mod.UserError = UserError
    exceptions_mod.AccessError = AccessError
    exceptions_mod.ValidationError = ValidationError

    http_mod = types.ModuleType('odoo.http')

    class Controller:
        pass

    def route(*route_args, **route_kwargs):
        def decorator(fn):
            fn.routing = route_kwargs
            return fn
        return decorator

    http_mod.Controller = Controller
    http_mod.route = route
    http_mod.request = None
    http_mod.Response = type('Response', (), {})

    odoo.api = api_mod
    odoo.fields = fields_mod
    odoo.models = models_mod
    odoo.exceptions = exceptions_mod
    odoo.http = http_mod

    sys.modules.update({
        'odoo': odoo, 'odoo.api': api_mod, 'odoo.fields': fields_mod,
        'odoo.models': models_mod, 'odoo.exceptions': exceptions_mod,
        'odoo.http': http_mod,
    })
    return odoo


def load(dotted):
    """Import ``odoo_module_mcp_server.<dotted>`` with relative imports intact.

    Registering a path-only stand-in for the addon package (rather than
    executing its real ``__init__.py``, which imports controllers and models
    eagerly) lets importlib resolve submodules from disk while ``from .x import``
    and ``from ..y import`` keep working.
    """
    if ADDON_NAME not in sys.modules:
        stub = types.ModuleType(ADDON_NAME)
        stub.__path__ = [str(ADDON_DIR)]
        sys.modules[ADDON_NAME] = stub
    return importlib.import_module('%s.%s' % (ADDON_NAME, dotted))


# ---------------------------------------------------------------------------
# A fake ORM: env → model → recordset
# ---------------------------------------------------------------------------

class FakeRecordset:
    """A browsed recordset. Records every mutation for assertions."""

    def __init__(self, model, ids):
        self._model = model
        self.ids = list(ids)

    @property
    def id(self):
        return self.ids[0] if self.ids else False

    def __bool__(self):
        return bool(self.ids)

    def __len__(self):
        return len(self.ids)

    def write(self, values):
        self._model.calls.append(('write', self.ids, values))
        return True

    def unlink(self):
        self._model.calls.append(('unlink', self.ids))
        return True

    def __getattr__(self, name):
        # Workflow methods the model was told it has (e.g. button_cancel).
        if name.startswith('_'):
            raise AttributeError(name)
        model = object.__getattribute__(self, '_model')
        if name in model.workflow_methods:
            def workflow(*args, **kwargs):
                model.calls.append((name, self.ids, args, kwargs))
                return {'called': name, 'ids': self.ids}
            return workflow
        raise AttributeError(name)


class FakeModel:
    """One model in the fake env. Configure returns; assert on `calls`."""

    #: Methods a browsed recordset answers to unless a test clears them.
    DEFAULT_WORKFLOW_METHODS = ('action_cancel', 'button_cancel')

    def __init__(self, name, env, results=None, workflow_methods=None,
                 params=None, access_denied=()):
        self._name = name
        self.env = env
        self.calls = []
        self.results = results or {}
        self.workflow_methods = set(
            self.DEFAULT_WORKFLOW_METHODS if workflow_methods is None
            else workflow_methods)
        self.params = params if params is not None else {}
        self.access_denied = set(access_denied)
        self.company = None

    # -- recording helpers ------------------------------------------------
    def _record(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        return self.results.get(method, True)

    @property
    def last(self):
        assert self.calls, 'expected at least one call on %s' % self._name
        return self.calls[-1]

    # -- ORM surface ------------------------------------------------------
    def with_company(self, company_id):
        clone = FakeModel(self._name, self.env, self.results,
                          self.workflow_methods, self.params, self.access_denied)
        clone.calls = self.calls          # share the recorder
        clone.company = company_id
        self.company = company_id         # ...and visible on the model the test holds
        self.calls.append(('with_company', company_id))
        return clone

    def sudo(self):
        return self

    def browse(self, ids):
        self.calls.append(('browse', list(ids)))
        return FakeRecordset(self, ids)

    def create(self, values):
        self.calls.append(('create', values))
        if isinstance(values, list):
            return FakeRecordset(self, list(range(1, len(values) + 1)))
        return FakeRecordset(self, [self.results.get('create_id', 1841)])

    def search_read(self, domain=None, fields=None, **kwargs):
        self.calls.append(('search_read', domain, fields, kwargs))
        return self.results.get('search_read', [])

    def search(self, domain=None, **kwargs):
        self.calls.append(('search', domain, kwargs))
        return FakeRecordset(self, self.results.get('search', []))

    def search_count(self, domain=None, **kwargs):
        self.calls.append(('search_count', domain, kwargs))
        return self.results.get('search_count', 0)

    def read_group(self, domain=None, fields=None, groupby=None, **kwargs):
        self.calls.append(('read_group', domain, fields, groupby, kwargs))
        return self.results.get('read_group', [])

    def name_search(self, name='', args=None, operator='ilike', limit=100):
        self.calls.append(('name_search', name, args, operator, limit))
        return self.results.get('name_search', [])

    def fields_get(self, attributes=None):
        self.calls.append(('fields_get', attributes))
        return self.results.get('fields_get', {})

    def check_access(self, operation):
        self.calls.append(('check_access', operation))
        if self._name in self.access_denied or operation in self.access_denied:
            raise sys.modules['odoo'].exceptions.AccessError(
                'no access to %s' % self._name)
        return True

    # -- ir.config_parameter ---------------------------------------------
    def get_param(self, key, default=None):
        self.calls.append(('get_param', key, default))
        return self.params.get(key, default)

    def set_param(self, key, value):
        self.params[key] = value
        return True

    # -- odoo_execute path ------------------------------------------------
    def __contains__(self, item):
        return False

    def _record_call_kw(self, method, args, kwargs):
        self.calls.append(('call_kw', method, args, kwargs))
        return self.results.get(method, {'called': method})

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if name in object.__getattribute__(self, 'workflow_methods'):
            def workflow(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return self.results.get(name, True)
            return workflow
        raise AttributeError(name)


class FakeCursor:
    def __init__(self, env=None, dbname='testdb'):
        self.env = env
        self.dbname = dbname
        self.committed = False
        self.closed = False

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.committed = True
        self.closed = True
        return False


class FakeEnv:
    """Dict-like Odoo environment over FakeModel instances.

    Unknown model names raise KeyError on access but report False for `in`,
    which is what ``generic_tools._model`` checks.
    """

    def __init__(self, models=None, params=None, user=None, known=None):
        self.params = params if params is not None else {}
        self._models = {}
        self.known = set(known) if known is not None else None
        for name, config in (models or {}).items():
            self._models[name] = FakeModel(name, self, **config)
        self.user = user or types.SimpleNamespace(
            id=7, login='agent@example.test', name='Agent',
            company_ids=types.SimpleNamespace(ids=[1]))
        self.cr = FakeCursor(self)
        self.registry = types.SimpleNamespace(cursor=lambda: FakeCursor(self))
        self.refs = {}

    def __contains__(self, name):
        if self.known is not None:
            return name in self.known
        return True

    def __getitem__(self, name):
        if name not in self._models:
            config = {'params': self.params} if name == 'ir.config_parameter' else {}
            self._models[name] = FakeModel(name, self, **config)
        return self._models[name]

    def ref(self, xmlid, raise_if_not_found=True):
        if xmlid in self.refs:
            return self.refs[xmlid]
        if raise_if_not_found:
            raise ValueError('no such xmlid: %s' % xmlid)
        return None

    def model(self, name):
        return self[name]
