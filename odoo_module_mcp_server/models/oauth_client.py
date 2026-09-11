"""OAuth 2.1 Dynamic Client Registration store (RFC 7591).

Public clients only — Claude and other agents register themselves via
POST /oauth/register, get a ``client_id`` back, and use PKCE on every
authorization request. No ``client_secret`` is issued.
"""
import secrets

from odoo import api, fields, models


class McpOauthClient(models.Model):
    _name = 'custom.mcp.oauth.client'
    _description = 'MCP OAuth Client'
    _order = 'create_date desc'

    client_id = fields.Char(required=True, index=True, copy=False, readonly=True)
    name = fields.Char(string='Client Name')
    redirect_uris = fields.Text(
        required=True,
        help='One redirect URI per line. Claude.ai uses '
             'https://claude.ai/api/mcp/auth_callback.')
    last_seen = fields.Datetime()

    _sql_constraints = [
        ('client_id_uniq', 'unique(client_id)', 'client_id must be unique'),
    ]

    @api.model
    def _generate_client_id(self):
        # Public clients (no secret) — 32-byte random URL-safe id.
        return 'mcp_' + secrets.token_urlsafe(24)

    @api.model
    def _register_client(self, name, redirect_uris):
        """Create a new public client; returns the saved record.

        Note: this is named ``_register_client`` (not ``_register``) because
        ``_register`` is a reserved boolean class attribute on Odoo's
        ``BaseModel`` — shadowing it breaks the model.
        """
        record = self.sudo().create({
            'client_id': self._generate_client_id(),
            'name': (name or '')[:200],
            'redirect_uris': '\n'.join(redirect_uris or []),
        })
        return record

    def _allows_redirect(self, uri):
        self.ensure_one()
        allowed = [u.strip() for u in (self.redirect_uris or '').splitlines() if u.strip()]
        return uri in allowed
