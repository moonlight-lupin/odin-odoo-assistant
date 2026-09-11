"""Custom auth method for the MCP endpoint.

``auth='mcp'`` authenticates the ``Authorization: Bearer <token>`` header and
runs the tool call as that user (record rules + ir.model.access apply). It
accepts, in order:

1. an **OAuth 2.1 access token** issued by this server — only when OAuth is
   enabled (``odoo_module_mcp_server.oauth_enabled``); and
2. a standard Odoo **API key** (scope ``rpc``) — always available.

When OAuth is off, only the API-key path is tried.
"""
import re

from odoo import models
from odoo.http import request


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
            raise Unauthorized('Missing Bearer token')

        # 1. OAuth 2.1 access token — only when OAuth is enabled.
        params = request.env['ir.config_parameter'].sudo()
        if params.get_param('odoo_module_mcp_server.oauth_enabled') == 'True':
            user_id = request.env['custom.mcp.oauth.token']._check_access_token(token)
            if user_id:
                request.update_env(user=user_id)
                request.session.can_save = False
                return

        # 2. Fall back to a standard Odoo API key (the built-in bearer flow).
        user_id = request.env['res.users.apikeys']._check_credentials(
            scope='rpc', key=token)
        if not user_id:
            raise Unauthorized('Invalid Bearer token')
        request.update_env(user=user_id)
        request.session.can_save = False
