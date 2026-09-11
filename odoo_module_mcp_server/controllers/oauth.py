"""OAuth 2.1 endpoints for the MCP server.

Spec-compliant subset for MCP / Claude.ai custom connectors:

* ``/.well-known/oauth-protected-resource`` — points clients at the AS.
* ``/.well-known/oauth-authorization-server`` — AS metadata.
* ``/oauth/register`` — Dynamic Client Registration (RFC 7591), public
   clients only.
* ``/oauth/authorize`` — user consent (requires an Odoo session).
* ``/oauth/token`` — authorization_code (PKCE) and refresh_token grants.

All of these return 404 when ``odoo_module_mcp_server.oauth_enabled`` is off
or the issuer URL is unset — so a deployment that only wants Odoo API
keys is unaffected.
"""
import json
import logging
import secrets
import urllib.parse

from odoo import http
from odoo.http import request


_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _oauth_config():
    """Return ``(enabled, issuer)``; enabled is False when not usable."""
    params = request.env['ir.config_parameter'].sudo()
    enabled = params.get_param('odoo_module_mcp_server.oauth_enabled') == 'True'
    issuer = (params.get_param('odoo_module_mcp_server.oauth_issuer_url') or '').rstrip('/')
    return (enabled and bool(issuer)), issuer


def _access_ttl():
    params = request.env['ir.config_parameter'].sudo()
    return int(params.get_param('odoo_module_mcp_server.oauth_access_ttl_min') or 60) * 60


def _refresh_ttl():
    params = request.env['ir.config_parameter'].sudo()
    return int(params.get_param('odoo_module_mcp_server.oauth_refresh_ttl_day') or 30) * 86400


def _json(body, status=200, headers=None):
    out_headers = [
        ('Content-Type', 'application/json'),
        ('Cache-Control', 'no-store'),
        ('Pragma', 'no-cache'),
    ]
    if headers:
        out_headers.extend(headers)
    return request.make_response(json.dumps(body), status=status,
                                 headers=out_headers)


def _oauth_error(error, description=None, redirect_uri=None, state=None,
                 status=400):
    """OAuth error: redirect back to the client when possible, else JSON."""
    if redirect_uri:
        params = {'error': error}
        if description:
            params['error_description'] = description
        if state:
            params['state'] = state
        sep = '&' if '?' in redirect_uri else '?'
        return request.redirect(redirect_uri + sep + urllib.parse.urlencode(params),
                                code=302, local=False)
    body = {'error': error}
    if description:
        body['error_description'] = description
    return _json(body, status=status)


def _require_enabled():
    enabled, issuer = _oauth_config()
    if not enabled:
        return None, request.not_found()
    return issuer, None


# ---------------------------------------------------------------------------
# .well-known
# ---------------------------------------------------------------------------

class OauthDiscovery(http.Controller):

    @http.route('/.well-known/oauth-protected-resource',
                type='http', auth='public', methods=['GET'], csrf=False)
    def protected_resource(self, **kwargs):
        issuer, fallthrough = _require_enabled()
        if fallthrough is not None:
            return fallthrough
        return _json({
            'resource': issuer + '/mcp/v1',
            'authorization_servers': [issuer],
            'scopes_supported': ['mcp'],
            'bearer_methods_supported': ['header'],
        })

    @http.route('/.well-known/oauth-authorization-server',
                type='http', auth='public', methods=['GET'], csrf=False)
    def authorization_server(self, **kwargs):
        issuer, fallthrough = _require_enabled()
        if fallthrough is not None:
            return fallthrough
        return _json({
            'issuer': issuer,
            'authorization_endpoint': issuer + '/oauth/authorize',
            'token_endpoint': issuer + '/oauth/token',
            'registration_endpoint': issuer + '/oauth/register',
            'response_types_supported': ['code'],
            'grant_types_supported': ['authorization_code', 'refresh_token'],
            'code_challenge_methods_supported': ['S256'],
            'token_endpoint_auth_methods_supported': ['none'],
            'scopes_supported': ['mcp'],
        })


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591) — public clients, no secret.
# ---------------------------------------------------------------------------

class OauthRegistration(http.Controller):

    @http.route('/oauth/register', type='http', auth='public',
                methods=['POST'], csrf=False)
    def register(self, **kwargs):
        issuer, fallthrough = _require_enabled()
        if fallthrough is not None:
            return fallthrough
        try:
            payload = json.loads(request.httprequest.data or b'{}')
        except Exception as error:
            return _oauth_error('invalid_request',
                                'malformed JSON: %s' % error, status=400)
        redirect_uris = payload.get('redirect_uris') or []
        if not isinstance(redirect_uris, list) or not redirect_uris:
            return _oauth_error('invalid_redirect_uri',
                                'redirect_uris is required', status=400)
        for uri in redirect_uris:
            if not isinstance(uri, str) or not uri.startswith(('http://', 'https://')):
                return _oauth_error(
                    'invalid_redirect_uri',
                    'redirect_uri must be http(s): %r' % uri, status=400)
        client = request.env['custom.mcp.oauth.client'].sudo()._register_client(
            name=payload.get('client_name') or 'mcp-client',
            redirect_uris=redirect_uris,
        )
        return _json({
            'client_id': client.client_id,
            'client_name': client.name,
            'redirect_uris': redirect_uris,
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'],
            'token_endpoint_auth_method': 'none',
            'scope': 'mcp',
        }, status=201)


# ---------------------------------------------------------------------------
# Authorize — user consent (needs an Odoo login session).
# ---------------------------------------------------------------------------

class OauthAuthorize(http.Controller):

    @http.route('/oauth/authorize', type='http', auth='public',
                methods=['GET'], csrf=False)
    def authorize(self, **params):
        issuer, fallthrough = _require_enabled()
        if fallthrough is not None:
            return fallthrough
        client_id = params.get('client_id')
        redirect_uri = params.get('redirect_uri')
        response_type = params.get('response_type')
        scope = params.get('scope') or 'mcp'
        state = params.get('state')
        challenge = params.get('code_challenge')
        challenge_method = params.get('code_challenge_method') or 'S256'

        client = request.env['custom.mcp.oauth.client'].sudo().search([
            ('client_id', '=', client_id)], limit=1)
        if not client:
            return _oauth_error('invalid_client',
                                'unknown client_id', status=400)
        if not redirect_uri or not client._allows_redirect(redirect_uri):
            return _oauth_error(
                'invalid_redirect_uri',
                'redirect_uri not registered for this client', status=400)
        if response_type != 'code':
            return _oauth_error('unsupported_response_type',
                                response_type, redirect_uri, state)
        if challenge_method != 'S256':
            return _oauth_error('invalid_request',
                                'only S256 PKCE is supported',
                                redirect_uri, state)
        if not challenge:
            return _oauth_error('invalid_request',
                                'code_challenge is required',
                                redirect_uri, state)

        # Require an authenticated Odoo session.
        if not request.session.uid:
            login_url = '/web/login?redirect=' + urllib.parse.quote(
                request.httprequest.full_path)
            return request.redirect(login_url, code=302, local=True)

        # Render the consent page; Approve will POST back to /oauth/authorize.
        return request.render('odoo_module_mcp_server.oauth_consent', {
            'client': client,
            'user_id': request.env.user,
            'scope': scope,
            'redirect_uri': redirect_uri,
            'state': state or '',
            'code_challenge': challenge,
            'code_challenge_method': challenge_method,
        })

    @http.route('/oauth/authorize', type='http', auth='user',
                methods=['POST'], csrf=False)
    def authorize_decision(self, **form):
        issuer, fallthrough = _require_enabled()
        if fallthrough is not None:
            return fallthrough
        client_id = form.get('client_id')
        redirect_uri = form.get('redirect_uri')
        state = form.get('state')
        decision = form.get('decision')

        client = request.env['custom.mcp.oauth.client'].sudo().search([
            ('client_id', '=', client_id)], limit=1)
        if not client or not client._allows_redirect(redirect_uri or ''):
            return _oauth_error('invalid_request', 'bad client / redirect',
                                status=400)
        if decision != 'approve':
            return _oauth_error('access_denied', 'user denied access',
                                redirect_uri, state)

        code = request.env['custom.mcp.oauth.authorization'].sudo()._issue(
            client_id=client_id,
            user_id=request.env.user.id,
            redirect_uri=redirect_uri,
            scope=form.get('scope') or 'mcp',
            code_challenge=form.get('code_challenge'),
            code_challenge_method=form.get('code_challenge_method') or 'S256',
        )
        sep = '&' if '?' in redirect_uri else '?'
        params = {'code': code}
        if state:
            params['state'] = state
        return request.redirect(redirect_uri + sep + urllib.parse.urlencode(params),
                                code=302, local=False)


# ---------------------------------------------------------------------------
# Token — authorization_code (PKCE) and refresh_token grants.
# ---------------------------------------------------------------------------

class OauthToken(http.Controller):

    @http.route('/oauth/token', type='http', auth='public',
                methods=['POST'], csrf=False)
    def token(self, **form):
        issuer, fallthrough = _require_enabled()
        if fallthrough is not None:
            return fallthrough
        grant = form.get('grant_type')
        if grant == 'authorization_code':
            return self._grant_code(form)
        if grant == 'refresh_token':
            return self._grant_refresh(form)
        return _oauth_error('unsupported_grant_type',
                            grant or '(missing)', status=400)

    def _grant_code(self, form):
        try:
            user_id, scope = request.env['custom.mcp.oauth.authorization'].sudo()._consume(
                code=form.get('code'),
                client_id=form.get('client_id'),
                redirect_uri=form.get('redirect_uri'),
                code_verifier=form.get('code_verifier'),
            )
        except ValueError as error:
            return _oauth_error('invalid_grant', str(error), status=400)
        return self._issue_token_pair(user_id, form.get('client_id'), scope)

    def _grant_refresh(self, form):
        try:
            user_id, scope = request.env['custom.mcp.oauth.token'].sudo()._consume_refresh(
                refresh_token=form.get('refresh_token'),
                client_id=form.get('client_id'),
            )
        except ValueError as error:
            return _oauth_error('invalid_grant', str(error), status=400)
        return self._issue_token_pair(user_id, form.get('client_id'), scope)

    def _issue_token_pair(self, user_id, client_id, scope):
        tokens = request.env['custom.mcp.oauth.token'].sudo()
        access, access_ttl = tokens._issue(
            'access', user_id, client_id, scope, _access_ttl())
        refresh, _refresh_ttl_seconds = tokens._issue(
            'refresh', user_id, client_id, scope, _refresh_ttl())
        return _json({
            'access_token': access,
            'token_type': 'Bearer',
            'expires_in': access_ttl,
            'refresh_token': refresh,
            'scope': scope or 'mcp',
        })
