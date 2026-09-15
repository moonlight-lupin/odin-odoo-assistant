"""Tests for the MCP settings round-trip.

One theme: **a falsy setting must survive being saved.**

``config_parameter=`` looks like it stores a field for you, and for a truthy
value it does. For a falsy one it does not: core ``res.config.settings``
normalises the value to Python ``False`` before calling ``set_param``, and
``set_param`` treats ``False`` as "delete this parameter". ``get_values`` then
finds nothing and falls back to the field default, so the setting springs back
to its default and the code that reads the parameter never sees what the
administrator actually chose.

That is a silent failure with teeth in both directions here:

* ``protect_structural`` / ``log_payloads`` — Booleans defaulting True, which
  would spring back ON, so a deliberate "off" never takes effect;
* ``log_retention_days`` — where **0 means "keep the trail forever"**, the
  setting an instance picks precisely because its auditors require an unbroken
  record. Lost, it reverts to 90 days and the daily cron starts deleting the
  trail it was told to keep.

So all three are stored as explicit strings. These tests pin that, against a
``set_param`` that reproduces core's real falsy behaviour — a fake that simply
stored whatever it was handed would pass even with the bug present.

Runs against a fake Odoo (see ``_fake_odoo``). Under a real Odoo it is skipped.
"""
import unittest

from . import _fake_odoo

if _fake_odoo.real_odoo_present():                          # pragma: no cover
    raise unittest.SkipTest("fake-Odoo suite; skipped when a real Odoo is present")

_fake_odoo.install()
settings = _fake_odoo.load('models.res_config_settings')
mcp_log = _fake_odoo.load('models.mcp_log')


class OdooParams:
    """``ir.config_parameter`` with core's real set_param semantics.

    The bit that matters: a falsy value DELETES the parameter rather than
    storing it. Everything downstream of that is the bug under test.
    """

    def __init__(self, stored=None):
        self.stored = dict(stored or {})

    def sudo(self):
        return self

    def get_param(self, key, default=None):
        return self.stored.get(key, default)

    def set_param(self, key, value):
        if value is False or value is None or value == '':
            self.stored.pop(key, None)
        else:
            self.stored[key] = str(value)
        return True


class CoreSettings:
    """Stands in for core res.config.settings at the end of the MRO.

    ``super().set_values()`` in the module's override lands here. Core would
    also write every ``config_parameter=`` field at this point — which is
    exactly the path the three settings under test deliberately avoid.
    """

    def set_values(self):
        self.core_set_values_ran = True

    def get_values(self):
        return {}


class FakeSettings(settings.ResConfigSettings, CoreSettings):
    def __init__(self, params, **field_values):
        self._params = params
        self.__dict__.update(field_values)
        self.env = {'ir.config_parameter': params}

    def ensure_one(self):
        return self


def _save(**field_values):
    """Run set_values with these field values; return the stored parameters."""
    params = OdooParams()
    values = dict(mcp_protect_structural=True, mcp_log_payloads=True,
                  mcp_log_retention_days=90)
    values.update(field_values)
    FakeSettings(params, **values).set_values()
    return params


class TestRetentionRoundTrip(unittest.TestCase):

    def test_zero_is_stored_rather_than_deleting_the_parameter(self):
        params = _save(mcp_log_retention_days=0)
        self.assertEqual(params.get_param(settings.ResConfigSettings.LOG_RETENTION_PARAM),
                         '0',
                         "0 did not survive the save — 'keep the trail forever' "
                         "will silently revert to the 90-day default")

    def test_zero_reads_back_as_zero_not_the_default(self):
        params = _save(mcp_log_retention_days=0)
        values = FakeSettings(params, mcp_log_retention_days=0).get_values()
        self.assertEqual(values['mcp_log_retention_days'], 0)

    def test_an_ordinary_window_round_trips(self):
        params = _save(mcp_log_retention_days=30)
        values = FakeSettings(params, mcp_log_retention_days=30).get_values()
        self.assertEqual(values['mcp_log_retention_days'], 30)

    def test_an_absent_parameter_falls_back_to_the_default(self):
        values = FakeSettings(OdooParams()).get_values()
        self.assertEqual(values['mcp_log_retention_days'],
                         settings.DEFAULT_LOG_RETENTION_DAYS)

    def test_a_garbled_parameter_falls_back_rather_than_raising(self):
        params = OdooParams({settings.ResConfigSettings.LOG_RETENTION_PARAM: 'soon'})
        values = FakeSettings(params).get_values()
        self.assertEqual(values['mcp_log_retention_days'],
                         settings.DEFAULT_LOG_RETENTION_DAYS)

    def test_the_stored_zero_actually_stops_the_cron_deleting(self):
        """The end of the chain, and the reason any of this matters."""
        params = _save(mcp_log_retention_days=0)

        class Log(mcp_log.McpLog):
            def __init__(self):
                self.env = type('E', (), {'__getitem__': staticmethod(
                    lambda key: params)})()

            def sudo(self):
                raise AssertionError(
                    "_gc_logs searched for rows to delete despite a retention "
                    "of 0 — the audit trail is being trimmed after all")

        self.assertEqual(Log()._gc_logs(), 0)


class TestFalsyBooleansRoundTrip(unittest.TestCase):
    """The same quirk, on the two Booleans that default True."""

    def test_structural_protection_can_be_switched_off(self):
        params = _save(mcp_protect_structural=False)
        self.assertEqual(
            params.get_param(settings.ResConfigSettings.PROTECT_STRUCTURAL_PARAM),
            'False')
        values = FakeSettings(params).get_values()
        self.assertFalse(values['mcp_protect_structural'])

    def test_payload_logging_can_be_switched_off(self):
        params = _save(mcp_log_payloads=False)
        self.assertEqual(
            params.get_param(settings.ResConfigSettings.LOG_PAYLOADS_PARAM),
            'False')
        values = FakeSettings(params).get_values()
        self.assertFalse(values['mcp_log_payloads'])

    def test_the_defaults_are_the_safe_ones(self):
        values = FakeSettings(OdooParams()).get_values()
        self.assertTrue(values['mcp_protect_structural'])
        self.assertTrue(values['mcp_log_payloads'])


if __name__ == '__main__':
    unittest.main()
