"""Custom auth method for the MCP endpoint.

Also stamps ``request.mcp_auth_method`` so the activity log can record *how* a
call authenticated, and records rejected bearer tokens as ``auth_failure``
entries — a run of those is how a leaked or revoked key shows up.

``auth='mcp'`` authenticates the ``Authorization: Bearer <token>`` header and
runs the tool call as that user (record rules + ir.model.access apply). It
accepts, in order:

1. an **OAuth 2.1 access token** issued by this server — only when OAuth is
   enabled (``odoo_module_mcp_server.oauth_enabled``); and
2. a standard Odoo **API key** (scope ``rpc``) — always available.

When OAuth is off, only the API-key path is tried.
"""
import logging
import re

from odoo import models
from odoo.http import request

_logger = logging.getLogger(__name__)


def _log_auth_failure(reason):
    """Record a rejected bearer token. Never raises — auth must not depend on it."""
    try:
        request.env['custom.mcp.log'].sudo().log_event(
            'auth_failure', 'MCP authentication rejected: %s' % reason,
            outcome='denied')
    except Exception:
        _logger.exception("MCP: logging an auth failure failed")


def _read_bearer_token():
    header = request.httprequest.headers.get('Authorization', '')
    match = re.match(r'^bearer\s+(.+)$', header, re.IGNORECASE)
    return match.group(1) if match else None


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _auth_method_mcp(cls):
        from werkzeug.exceptions import Unauthorized
        token = _read_bearer_token()
        if not token:
            _log_auth_failure('missing Bearer token')
            raise Unauthorized('Missing Bearer token')

        # 1. OAuth 2.1 access token — only when OAuth is enabled.
        params = request.env['ir.config_parameter'].sudo()
        if params.get_param('odoo_module_mcp_server.oauth_enabled') == 'True':
            user_id = request.env['custom.mcp.oauth.token']._check_access_token(token)
            if user_id:
                request.update_env(user=user_id)
                request.session.can_save = False
                request.mcp_auth_method = 'oauth'
                return

        # 2. Fall back to a standard Odoo API key (the built-in bearer flow).
        user_id = request.env['res.users.apikeys']._check_credentials(
            scope='rpc', key=token)
        if not user_id:
            # Deliberately does NOT log the token — a rejected credential is
            # still a credential.
            _log_auth_failure('invalid Bearer token')
            raise Unauthorized('Invalid Bearer token')
        request.update_env(user=user_id)
        request.session.can_save = False
        request.mcp_auth_method = 'api_key'
