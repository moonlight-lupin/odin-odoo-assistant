"""Short-lived OAuth authorization codes.

Created when the user clicks Approve on the consent page; consumed by the
token endpoint in exchange for an access token. PKCE-verified (RFC 7636,
S256 only).
"""
import base64
import hashlib
import secrets

from datetime import datetime, timedelta

from odoo import api, fields, models

# Authorization codes live a few minutes — long enough to round-trip
# through the client, short enough to limit replay risk.
CODE_TTL_MINUTES = 5


def _sha256_b64url(value):
    digest = hashlib.sha256(value.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


class McpOauthAuthorization(models.Model):
    _name = 'custom.mcp.oauth.authorization'
    _description = 'MCP OAuth Authorization Code'
    _order = 'create_date desc'

    code_hash = fields.Char(required=True, index=True)
    client_id = fields.Char(required=True, index=True)
    user_id = fields.Many2one('res.users', required=True, ondelete='cascade')
    redirect_uri = fields.Char(required=True)
    scope = fields.Char(default='mcp')
    code_challenge = fields.Char(required=True)
    code_challenge_method = fields.Selection(
        [('S256', 'S256')], default='S256', required=True)
    expires_at = fields.Datetime(required=True)
    used_on = fields.Datetime()

    @api.model
    def _issue(self, client_id, user_id, redirect_uri, scope,
               code_challenge, code_challenge_method):
        """Generate a fresh code, store its hash, return the plaintext."""
        code = secrets.token_urlsafe(32)
        self.sudo().create({
            'code_hash': _sha256_b64url(code),
            'client_id': client_id,
            'user_id': user_id,
            'redirect_uri': redirect_uri,
            'scope': scope or 'mcp',
            'code_challenge': code_challenge,
            'code_challenge_method': code_challenge_method or 'S256',
            'expires_at': fields.Datetime.now() + timedelta(minutes=CODE_TTL_MINUTES),
        })
        return code

    @api.model
    def _consume(self, code, client_id, redirect_uri, code_verifier):
        """Verify and burn an authorization code.

        :return: the ``res.users`` id on success.
        :raise ValueError: on any mismatch (invalid code, expired, reused,
            PKCE mismatch, …) — the caller maps that to an OAuth error.
        """
        if not code:
            raise ValueError('Missing code')
        record = self.sudo().search([
            ('code_hash', '=', _sha256_b64url(code)),
        ], limit=1)
        if not record:
            raise ValueError('invalid_grant: unknown code')
        if record.used_on:
            raise ValueError('invalid_grant: code already used')
        if fields.Datetime.now() > record.expires_at:
            raise ValueError('invalid_grant: code expired')
        if record.client_id != client_id:
            raise ValueError('invalid_grant: client_id mismatch')
        if record.redirect_uri != redirect_uri:
            raise ValueError('invalid_grant: redirect_uri mismatch')
        # PKCE — recompute the challenge from the verifier and compare.
        if not code_verifier:
            raise ValueError('invalid_grant: missing code_verifier')
        recomputed = _sha256_b64url(code_verifier)
        if recomputed != record.code_challenge:
            raise ValueError('invalid_grant: PKCE check failed')
        record.used_on = fields.Datetime.now()
        return record.user_id.id, record.scope

    @api.model
    def _gc(self):
        """Best-effort cleanup of expired or used codes (cron-callable)."""
        cutoff = fields.Datetime.now() - timedelta(hours=1)
        self.sudo().search([
            '|', ('expires_at', '<', cutoff),
                 ('used_on', '<', cutoff),
        ]).unlink()
