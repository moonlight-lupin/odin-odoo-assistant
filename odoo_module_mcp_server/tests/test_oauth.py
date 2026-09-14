"""Tests for the OAuth 2.1 credential logic.

This is the part of the module where a mistake hands someone else's Odoo
session to an attacker, and it had no tests. What is pinned here:

* **PKCE is really PKCE** — S256 computed per RFC 7636, checked against the
  spec's own test vector, and a wrong verifier is rejected.
* **Authorization codes are single-use** — a replayed code is refused even
  when everything else about it is valid.
* **Codes and tokens expire**, and an expired one buys nothing.
* **client_id and redirect_uri must match** the ones the code was issued for.
* **Redirect URIs match exactly** — no prefix matching, which is how open
  redirects get in.
* **Secrets are stored hashed**, never in plaintext.

The ORM is faked down to an in-memory store; the decision logic is the real
code. Under a real Odoo this module is skipped.
"""
import base64
import hashlib
import types
import unittest
from datetime import datetime, timedelta

from . import _fake_odoo

if _fake_odoo.real_odoo_present():                          # pragma: no cover
    raise unittest.SkipTest("fake-Odoo suite; skipped when a real Odoo is present")

_fake_odoo.install()
oauth_authorization = _fake_odoo.load('models.oauth_authorization')
oauth_token = _fake_odoo.load('models.oauth_token')
oauth_client = _fake_odoo.load('models.oauth_client')

NOW = datetime(2026, 9, 14, 10, 0, 0)


# ---------------------------------------------------------------------------
# A tiny in-memory store standing in for the ORM
# ---------------------------------------------------------------------------

class Row:
    """One stored record. Many2one values are exposed as objects with `.id`."""

    def __init__(self, store, values):
        self._store = store
        self.__dict__.update(values)
        if isinstance(getattr(self, 'user_id', None), int):
            self.user_id = types.SimpleNamespace(id=self.user_id)
        self.__dict__.setdefault('used_on', False)
        self.__dict__.setdefault('last_used', False)

    def sudo(self):
        return self

    def unlink(self):
        if self in self._store.rows:
            self._store.rows.remove(self)
        return True

    def __bool__(self):
        return True


class FakeStore:
    """Supports the narrow slice of ORM the OAuth models use."""

    def __init__(self):
        self.rows = []

    def sudo(self):
        return self

    def create(self, values):
        row = Row(self, dict(values))
        self.rows.append(row)
        return row

    def search(self, domain, limit=None):
        matches = [row for row in self.rows if self._matches(row, domain)]
        if limit:
            matches = matches[:limit]
        return matches[0] if (limit == 1 and matches) else (matches or _EMPTY)

    @staticmethod
    def _matches(row, domain):
        for clause in domain:
            if not isinstance(clause, (list, tuple)) or len(clause) != 3:
                continue                       # '|' and friends: not needed here
            field, operator, value = clause
            actual = getattr(row, field, None)
            if operator == '=' and actual != value:
                return False
            if operator == '<' and not (actual and actual < value):
                return False
        return True


class _Empty:
    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())


_EMPTY = _Empty()


def _s256(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()


class OauthCase(unittest.TestCase):
    def setUp(self):
        _fake_odoo._Datetime._now = NOW
        self.addCleanup(setattr, _fake_odoo._Datetime, '_now', None)
        self.store = FakeStore()


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

class TestPkceComputation(unittest.TestCase):
    def test_matches_the_rfc_7636_test_vector(self):
        # RFC 7636 Appendix B — if this drifts, every client breaks (or worse,
        # the check silently passes for the wrong verifier).
        verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
        expected = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'
        self.assertEqual(oauth_authorization._sha256_b64url(verifier), expected)

    def test_the_challenge_is_url_safe_and_unpadded(self):
        for verifier in ('a', 'b' * 43, 'ünïcødé', '~-._'):
            with self.subTest(verifier=verifier):
                challenge = oauth_authorization._sha256_b64url(verifier)
                self.assertNotIn('=', challenge)
                self.assertNotIn('+', challenge)
                self.assertNotIn('/', challenge)

    def test_different_verifiers_give_different_challenges(self):
        self.assertNotEqual(oauth_authorization._sha256_b64url('one'),
                            oauth_authorization._sha256_b64url('two'))


class TestAuthorizationCodeExchange(OauthCase):
    VERIFIER = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
    CLIENT = 'mcp_client_1'
    REDIRECT = 'https://claude.ai/api/mcp/auth_callback'

    def issue(self, **overrides):
        model = oauth_authorization.McpOauthAuthorization
        kwargs = dict(client_id=self.CLIENT, user_id=7,
                      redirect_uri=self.REDIRECT, scope='mcp',
                      code_challenge=_s256(self.VERIFIER),
                      code_challenge_method='S256')
        kwargs.update(overrides)
        return model._issue(self.store, **kwargs)

    def consume(self, code, **overrides):
        model = oauth_authorization.McpOauthAuthorization
        kwargs = dict(client_id=self.CLIENT, redirect_uri=self.REDIRECT,
                      code_verifier=self.VERIFIER)
        kwargs.update(overrides)
        return model._consume(self.store, code, **kwargs)

    # -- happy path ------------------------------------------------------
    def test_a_valid_code_yields_the_user_and_scope(self):
        code = self.issue()
        self.assertEqual(self.consume(code), (7, 'mcp'))

    def test_the_code_is_stored_hashed_not_in_plaintext(self):
        code = self.issue()
        stored = self.store.rows[0]
        self.assertNotEqual(stored.code_hash, code)
        self.assertEqual(stored.code_hash, oauth_authorization._sha256_b64url(code))
        self.assertFalse(any(getattr(row, 'code', None) for row in self.store.rows))

    def test_each_issued_code_is_unique(self):
        self.assertNotEqual(self.issue(), self.issue())

    # -- the refusals ----------------------------------------------------
    def test_a_replayed_code_is_refused(self):
        code = self.issue()
        self.consume(code)
        with self.assertRaises(ValueError) as caught:
            self.consume(code)
        self.assertIn('already used', str(caught.exception))

    def test_a_wrong_verifier_fails_the_pkce_check(self):
        code = self.issue()
        with self.assertRaises(ValueError) as caught:
            self.consume(code, code_verifier='not-the-verifier')
        self.assertIn('PKCE', str(caught.exception))

    def test_a_missing_verifier_is_refused(self):
        code = self.issue()
        for verifier in (None, ''):
            with self.subTest(verifier=verifier):
                with self.assertRaises(ValueError) as caught:
                    self.consume(code, code_verifier=verifier)
                self.assertIn('code_verifier', str(caught.exception))

    def test_an_unknown_code_is_refused(self):
        self.issue()
        with self.assertRaises(ValueError) as caught:
            self.consume('a-code-that-was-never-issued')
        self.assertIn('unknown code', str(caught.exception))

    def test_a_missing_code_is_refused(self):
        for code in (None, ''):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.consume(code)

    def test_a_code_from_another_client_is_refused(self):
        code = self.issue()
        with self.assertRaises(ValueError) as caught:
            self.consume(code, client_id='mcp_someone_else')
        self.assertIn('client_id mismatch', str(caught.exception))

    def test_a_different_redirect_uri_is_refused(self):
        code = self.issue()
        with self.assertRaises(ValueError) as caught:
            self.consume(code, redirect_uri='https://evil.example/callback')
        self.assertIn('redirect_uri mismatch', str(caught.exception))

    def test_an_expired_code_is_refused(self):
        code = self.issue()
        _fake_odoo._Datetime._now = NOW + timedelta(
            minutes=oauth_authorization.CODE_TTL_MINUTES + 1)
        with self.assertRaises(ValueError) as caught:
            self.consume(code)
        self.assertIn('expired', str(caught.exception))

    def test_a_code_just_inside_its_ttl_still_works(self):
        code = self.issue()
        _fake_odoo._Datetime._now = NOW + timedelta(
            minutes=oauth_authorization.CODE_TTL_MINUTES - 1)
        self.assertEqual(self.consume(code)[0], 7)

    def test_a_failed_exchange_does_not_burn_the_code(self):
        # A client that mistypes the verifier should be able to retry; only a
        # SUCCESSFUL exchange consumes the code.
        code = self.issue()
        with self.assertRaises(ValueError):
            self.consume(code, code_verifier='wrong')
        self.assertEqual(self.consume(code), (7, 'mcp'))


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

class TestAccessTokens(OauthCase):
    def issue(self, kind='access', ttl=3600, client_id='mcp_client_1'):
        return oauth_token.McpOauthToken._issue(
            self.store, kind, 7, client_id, 'mcp', ttl)

    def test_a_fresh_access_token_resolves_to_its_user(self):
        token, expires_in = self.issue()
        self.assertEqual(expires_in, 3600)
        self.assertEqual(
            oauth_token.McpOauthToken._check_access_token(self.store, token), 7)

    def test_the_token_is_stored_hashed_not_in_plaintext(self):
        token, _ = self.issue()
        stored = self.store.rows[0]
        self.assertNotEqual(stored.token_hash, token)
        self.assertEqual(stored.token_hash, oauth_token._hash(token))

    def test_an_expired_access_token_is_rejected(self):
        token, _ = self.issue(ttl=60)
        _fake_odoo._Datetime._now = NOW + timedelta(seconds=61)
        self.assertFalse(
            oauth_token.McpOauthToken._check_access_token(self.store, token))

    def test_an_unknown_token_is_rejected(self):
        self.issue()
        self.assertFalse(
            oauth_token.McpOauthToken._check_access_token(self.store, 'made-up'))

    def test_an_empty_token_is_rejected(self):
        for token in (None, ''):
            with self.subTest(token=token):
                self.assertFalse(
                    oauth_token.McpOauthToken._check_access_token(self.store, token))

    def test_a_refresh_token_is_not_accepted_as_an_access_token(self):
        token, _ = self.issue(kind='refresh')
        self.assertFalse(
            oauth_token.McpOauthToken._check_access_token(self.store, token))

    def test_use_is_recorded(self):
        token, _ = self.issue()
        oauth_token.McpOauthToken._check_access_token(self.store, token)
        self.assertEqual(self.store.rows[0].last_used, NOW)


class TestRefreshTokens(OauthCase):
    def issue(self, ttl=86400, client_id='mcp_client_1'):
        return oauth_token.McpOauthToken._issue(
            self.store, 'refresh', 7, client_id, 'mcp', ttl)[0]

    def consume(self, token, client_id='mcp_client_1'):
        return oauth_token.McpOauthToken._consume_refresh(self.store, token, client_id)

    def test_a_valid_refresh_token_yields_the_user_and_scope(self):
        self.assertEqual(self.consume(self.issue()), (7, 'mcp'))

    def test_refresh_tokens_are_single_use(self):
        # Rotation: replaying a refresh token must not mint a second session.
        token = self.issue()
        self.consume(token)
        with self.assertRaises(ValueError) as caught:
            self.consume(token)
        self.assertIn('unknown refresh_token', str(caught.exception))

    def test_another_client_cannot_use_it(self):
        with self.assertRaises(ValueError) as caught:
            self.consume(self.issue(), client_id='mcp_someone_else')
        self.assertIn('client_id mismatch', str(caught.exception))

    def test_an_expired_refresh_token_is_refused(self):
        token = self.issue(ttl=60)
        _fake_odoo._Datetime._now = NOW + timedelta(seconds=61)
        with self.assertRaises(ValueError) as caught:
            self.consume(token)
        self.assertIn('expired', str(caught.exception))

    def test_a_missing_refresh_token_is_refused(self):
        for token in (None, ''):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    self.consume(token)


# ---------------------------------------------------------------------------
# Client registration
# ---------------------------------------------------------------------------

class TestClientRegistration(OauthCase):
    """_register_client calls _generate_client_id on itself, so the store needs it."""

    def setUp(self):
        super().setUp()
        self.store._generate_client_id = (
            lambda: oauth_client.McpOauthClient._generate_client_id(self.store))

    def register(self, name, uris):
        client = oauth_client.McpOauthClient._register_client(self.store, name, uris)
        client.ensure_one = lambda: True
        return client

    def test_redirect_uris_match_exactly_not_by_prefix(self):
        # Prefix matching here is how open redirects get in.
        client = self.register('Claude', ['https://claude.ai/api/mcp/auth_callback'])
        allows = oauth_client.McpOauthClient._allows_redirect
        self.assertTrue(allows(client, 'https://claude.ai/api/mcp/auth_callback'))
        for hostile in (
            'https://claude.ai/api/mcp/auth_callback/../../evil',
            'https://claude.ai/api/mcp/auth_callback?next=https://evil.example',
            'https://claude.ai.evil.example/api/mcp/auth_callback',
            'https://claude.ai/api/mcp/auth_callbackX',
            'http://claude.ai/api/mcp/auth_callback',     # scheme downgrade
        ):
            with self.subTest(uri=hostile):
                self.assertFalse(allows(client, hostile))

    def test_every_registered_uri_is_allowed(self):
        client = self.register('Multi', ['https://a.example/cb', 'https://b.example/cb'])
        allows = oauth_client.McpOauthClient._allows_redirect
        self.assertTrue(allows(client, 'https://a.example/cb'))
        self.assertTrue(allows(client, 'https://b.example/cb'))
        self.assertFalse(allows(client, 'https://c.example/cb'))

    def test_client_ids_are_prefixed_and_unguessable(self):
        generate = oauth_client.McpOauthClient._generate_client_id
        ids = {generate(self.store) for _ in range(50)}
        self.assertEqual(len(ids), 50, 'client ids must not collide')
        for client_id in ids:
            self.assertTrue(client_id.startswith('mcp_'))
            self.assertGreaterEqual(len(client_id), 20)

    def test_a_long_client_name_is_truncated(self):
        client = self.register('N' * 500, ['https://a.example/cb'])
        self.assertLessEqual(len(client.name), 200)

    def test_no_client_secret_is_ever_issued(self):
        # Public clients only — PKCE is the proof, not a shared secret.
        client = self.register('Claude', ['https://a.example/cb'])
        self.assertFalse(getattr(client, 'client_secret', None))


if __name__ == '__main__':
    unittest.main()
