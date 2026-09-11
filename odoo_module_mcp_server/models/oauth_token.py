"""OAuth access and refresh tokens issued by the MCP server.

Tokens are opaque random strings. We store a SHA-256 of each one — the
plaintext only ever lives in the response body that goes back to the
client. On verification we recompute the hash and look it up.
"""
import base64
import hashlib
import secrets

from datetime import timedelta

from odoo import api, fields, models


def _hash(value):
    digest = hashlib.sha256(value.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


class McpOauthToken(models.Model):
    _name = 'custom.mcp.oauth.token'
    _description = 'MCP OAuth Token'
    _order = 'create_date desc'

    token_hash = fields.Char(required=True, index=True)
    kind = fields.Selection([
        ('access', 'Access'),
        ('refresh', 'Refresh'),
    ], required=True, default='access')
    user_id = fields.Many2one('res.users', required=True, ondelete='cascade',
                               index=True)
    client_id = fields.Char(required=True, index=True)
    scope = fields.Char(default='mcp')
    expires_at = fields.Datetime(required=True)
    last_used = fields.Datetime()

    @api.model
    def _issue(self, kind, user_id, client_id, scope, ttl_seconds):
        """Mint a token, persist its hash, return ``(plaintext, expires_in)``."""
        plaintext = secrets.token_urlsafe(32)
        self.sudo().create({
            'token_hash': _hash(plaintext),
            'kind': kind,
            'user_id': user_id,
            'client_id': client_id,
            'scope': scope or 'mcp',
            'expires_at': fields.Datetime.now() + timedelta(seconds=ttl_seconds),
        })
        return plaintext, ttl_seconds

    @api.model
    def _check_access_token(self, token):
        """Verify an access token; return the ``res.users`` id or ``False``.

        Updates ``last_used`` on a hit.
        """
        if not token:
            return False
        record = self.sudo().search([
            ('token_hash', '=', _hash(token)),
            ('kind', '=', 'access'),
        ], limit=1)
        if not record:
            return False
        if fields.Datetime.now() > record.expires_at:
            return False
        record.last_used = fields.Datetime.now()
        return record.user_id.id

    @api.model
    def _consume_refresh(self, refresh_token, client_id):
        """Verify and revoke a refresh token; return ``(user_id, scope)``."""
        if not refresh_token:
            raise ValueError('invalid_grant: missing refresh_token')
        record = self.sudo().search([
            ('token_hash', '=', _hash(refresh_token)),
            ('kind', '=', 'refresh'),
        ], limit=1)
        if not record:
            raise ValueError('invalid_grant: unknown refresh_token')
        if record.client_id != client_id:
            raise ValueError('invalid_grant: client_id mismatch')
        if fields.Datetime.now() > record.expires_at:
            raise ValueError('invalid_grant: refresh_token expired')
        user_id, scope = record.user_id.id, record.scope
        # Rotate — single-use refresh tokens.
        record.sudo().unlink()
        return user_id, scope

    @api.model
    def _gc(self):
        """Best-effort cleanup of expired tokens (cron-callable)."""
        self.sudo().search([
            ('expires_at', '<', fields.Datetime.now() - timedelta(hours=1)),
        ]).unlink()
