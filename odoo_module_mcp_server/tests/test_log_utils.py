"""Tests for the MCP log's redaction / truncation / gating rules.

These are the properties an audit trail is worthless without, and they must
hold identically here and in the external server (``odoo-mcp/audit.py``) — an
audit record should read the same whichever server produced it.

Deliberately Odoo-free: ``log_utils`` imports nothing from odoo, so this runs
both under Odoo's test runner and under a bare
``python -m pytest odoo_module_mcp_server/tests``.
"""
import json
import unittest

try:                                    # under Odoo's runner
    from odoo.addons.odoo_module_mcp_server import log_utils
except ImportError:                     # under bare pytest
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import log_utils


class TestRedaction(unittest.TestCase):
    """Credentials must never reach the log table."""

    SECRET_KEYS = [
        'api_key', 'apiKey', 'API_KEY', 'password', 'passwd', 'secret',
        'token', 'access_token', 'refresh_token', 'Authorization',
        'client_secret', 'credentials',
    ]

    def test_secret_keys_are_masked(self):
        for key in self.SECRET_KEYS:
            with self.subTest(key=key):
                out = log_utils.redact({key: 'hunter2'})
                self.assertEqual(out[key], log_utils.REDACTED)
                self.assertNotIn('hunter2', json.dumps(out))

    def test_redaction_is_recursive(self):
        out = log_utils.redact({'outer': [{'inner': {'api_key': 's3cret'}}],
                                'ok': 'keep'})
        self.assertNotIn('s3cret', json.dumps(out))
        self.assertEqual(out['ok'], 'keep')

    def test_ordinary_keys_survive(self):
        payload = {'model': 'account.move', 'keyword': 'x', 'values': {'ref': 'INV-1'}}
        self.assertEqual(log_utils.redact(payload), payload)


class TestTruncation(unittest.TestCase):
    """Payloads stay bounded, and a reader can tell "short" from "shortened"."""

    def test_long_strings_are_capped(self):
        out = log_utils.redact({'note': 'x' * 5000}, max_string=100)
        self.assertLess(len(out['note']), 200)
        self.assertTrue(out['note'].startswith('x' * 100))
        self.assertIn('more chars', out['note'])

    def test_long_lists_are_capped(self):
        out = log_utils.redact({'ids': list(range(500))}, max_items=10)
        self.assertEqual(len(out['ids']), 11)
        self.assertIn('more items', str(out['ids'][-1]))

    def test_deep_nesting_is_capped(self):
        deep = current = {}
        for _ in range(50):
            current['next'] = {}
            current = current['next']
        self.assertTrue(json.dumps(log_utils.redact(deep, max_depth=5)))

    def test_bytes_are_summarised_not_embedded(self):
        out = log_utils.redact({'content': b'\x00' * 4096})
        self.assertEqual(out['content'], '<4096 bytes>')


class TestResultSummary(unittest.TestCase):
    def test_records_collapse_to_count_and_ids(self):
        rows = [{'id': i, 'name': 'row %d' % i} for i in range(200)]
        summary = log_utils.summarize_result(rows)
        self.assertEqual(summary['type'], 'records')
        self.assertEqual(summary['count'], 200)
        self.assertLessEqual(len(summary['ids']), log_utils.DEFAULT_MAX_ITEMS)

    def test_scalars_pass_through(self):
        self.assertIs(log_utils.summarize_result(True), True)
        self.assertEqual(log_utils.summarize_result(42), 42)
        self.assertIsNone(log_utils.summarize_result(None))

    def test_a_created_id_is_kept_verbatim(self):
        # odoo_create returns the new id — the single most audit-relevant value.
        self.assertEqual(log_utils.summarize_result(1841), 1841)


class TestSerialisation(unittest.TestCase):
    def test_dump_never_raises_on_an_opaque_object(self):
        class Opaque:
            def __repr__(self):
                return '<opaque>'
        text = log_utils.dump({'result': Opaque()})
        self.assertIn('opaque', text)
        json.loads(text)                # still valid JSON

    def test_dump_is_stable_for_plain_payloads(self):
        self.assertEqual(json.loads(log_utils.dump({'a': 1})), {'a': 1})


class TestLevelGating(unittest.TestCase):
    def test_off_records_nothing(self):
        for outcome in ('ok', 'error', 'blocked'):
            self.assertFalse(log_utils.should_log('off', outcome))

    def test_error_keeps_only_failures_and_blocks(self):
        self.assertFalse(log_utils.should_log('error', 'ok'))
        self.assertTrue(log_utils.should_log('error', 'error'))
        self.assertTrue(log_utils.should_log('error', 'blocked'))

    def test_all_keeps_everything(self):
        for outcome in ('ok', 'error', 'blocked'):
            self.assertTrue(log_utils.should_log('all', outcome))

    def test_unknown_level_is_treated_as_all(self):
        # A garbled config parameter must not silently switch the trail off.
        self.assertTrue(log_utils.should_log('loud', 'ok'))
        self.assertTrue(log_utils.should_log(None, 'ok'))
        self.assertTrue(log_utils.should_log('', 'ok'))


class TestErrorClassification(unittest.TestCase):
    """A policy refusal is not a fault — operators need to tell them apart."""

    def test_guardrail_refusals_are_blocked(self):
        for message in (
            "Refusing to modify structural model 'ir.model' — MCP is in "
            "transactional mode.",
            "Refusing to delete records — MCP record deletion is disabled.",
        ):
            self.assertEqual(log_utils.classify_error(ValueError(message)), 'blocked')

    def test_other_failures_are_errors(self):
        self.assertEqual(log_utils.classify_error(ValueError('Unknown model: x')), 'error')
        self.assertEqual(log_utils.classify_error(KeyError('id')), 'error')

    def test_access_errors_are_denied(self):
        class AccessError(Exception):
            pass
        self.assertEqual(log_utils.classify_error(AccessError('not allowed')), 'denied')


class TestCallSummary(unittest.TestCase):
    """The one-line summary shown in the log list view."""

    def test_summary_names_model_and_method(self):
        text = log_utils.describe_call('odoo_write', {'model': 'account.move', 'ids': [1, 2]})
        self.assertIn('account.move', text)
        self.assertIn('odoo_write', text)

    def test_summary_survives_missing_arguments(self):
        self.assertIn('odoo_whoami', log_utils.describe_call('odoo_whoami', {}))
        self.assertIn('odoo_whoami', log_utils.describe_call('odoo_whoami', None))

    def test_summary_is_bounded(self):
        text = log_utils.describe_call('odoo_write', {'model': 'x' * 500})
        self.assertLessEqual(len(text), log_utils.SUMMARY_MAX_CHARS)


class TestArgumentFacets(unittest.TestCase):
    """Model / method / record ids are lifted into columns so the log is filterable."""

    def test_model_and_ids_are_extracted(self):
        facets = log_utils.extract_facets('odoo_write',
                                          {'model': 'account.move', 'ids': [1, 2, 3]})
        self.assertEqual(facets['model_name'], 'account.move')
        self.assertEqual(facets['record_ids'], '1,2,3')
        self.assertEqual(facets['record_count'], 3)

    def test_execute_lifts_the_method(self):
        facets = log_utils.extract_facets(
            'odoo_execute', {'model': 'account.move', 'method': 'action_post'})
        self.assertEqual(facets['method'], 'action_post')

    def test_record_ids_are_bounded(self):
        facets = log_utils.extract_facets('odoo_write', {'model': 'x', 'ids': list(range(500))})
        self.assertEqual(facets['record_count'], 500)
        self.assertLessEqual(len(facets['record_ids']), log_utils.SUMMARY_MAX_CHARS)

    def test_missing_facets_are_empty_not_absent(self):
        facets = log_utils.extract_facets('odoo_whoami', {})
        self.assertEqual(facets['model_name'], '')
        self.assertEqual(facets['method'], '')
        self.assertEqual(facets['record_count'], 0)




class TestMappingsStayBounded(unittest.TestCase):
    """Every container type must be capped. An unbounded one is not cosmetic:
    ``odoo_fields_get`` on account.move returns ~350 field definitions, and
    storing that whole dict in the Text column on every call bloats the table
    for no audit value."""

    def test_wide_mappings_are_truncated_with_a_marker(self):
        schema = {'field_%03d' % i: {'type': 'char'} for i in range(350)}
        out = log_utils.redact(schema)
        self.assertEqual(len(out), log_utils.DEFAULT_MAX_KEYS + 1)  # +1: marker
        self.assertEqual(out[log_utils.TRUNCATED_KEY],
                         '[+%d more keys]' % (350 - log_utils.DEFAULT_MAX_KEYS))

    def test_a_realistic_write_payload_is_kept_whole(self):
        # The cap must not cost audit value: a create/write payload's breadth
        # IS the record of what the agent wrote.
        values = {'field_%02d' % i: i for i in range(40)}
        out = log_utils.redact(values)
        self.assertEqual(out, values)
        self.assertNotIn(log_utils.TRUNCATED_KEY, out)

    def test_nested_mappings_are_capped_too(self):
        out = log_utils.redact({'values': {'f%03d' % i: i for i in range(200)}})
        self.assertIn(log_utils.TRUNCATED_KEY, out['values'])

    def test_secrets_are_still_masked_in_a_truncated_mapping(self):
        payload = {'api_key': 'hunter2'}
        payload.update({'f%03d' % i: i for i in range(200)})
        self.assertNotIn('hunter2', json.dumps(log_utils.redact(payload)))

    def test_summarised_results_are_bounded_as_well(self):
        out = log_utils.summarize_result({'f%03d' % i: {'x': i} for i in range(200)})
        self.assertIn(log_utils.TRUNCATED_KEY, out)


class TestPolicyRefusalsAreClassifiedAsBlocked(unittest.TestCase):
    """'blocked' vs 'error' is the whole point of the outcome column: an
    operator needs to tell "the agent tried and the guardrail stopped it" from
    "Odoo errored". A guardrail missing from _POLICY_MARKERS silently drops out
    of the "Blocked by policy" filter. The messages below are generic_tools';
    test_generic_tools cross-checks them against what it actually raises."""

    GUARDRAIL_MESSAGES = [
        "Refusing to modify structural model 'ir.ui.view' — MCP is in "
        "transactional mode",
        "Refusing to delete records — MCP record deletion is disabled",
        "Refusing to call private method: '_write'",
    ]

    def test_every_guardrail_message_classifies_as_blocked(self):
        for message in self.GUARDRAIL_MESSAGES:
            with self.subTest(message=message[:40]):
                self.assertEqual(log_utils.classify_error(ValueError(message)),
                                 'blocked')

    def test_an_ordinary_failure_is_still_an_error(self):
        for message in ("Unknown model: 'nope.nope'",
                        "Model 'account.move' has no method 'frobnicate'",
                        'Report not found: account.report_invoice'):
            with self.subTest(message=message):
                self.assertEqual(log_utils.classify_error(ValueError(message)),
                                 'error')


if __name__ == '__main__':
    unittest.main()
