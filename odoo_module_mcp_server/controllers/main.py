import datetime
import json
import logging
import time

from odoo import http
from odoo.http import request

from .. import log_utils
from ..mcp_registry import McpRegistry

_logger = logging.getLogger(__name__)

# JSON-RPC 2.0 standard error codes (https://www.jsonrpc.org/specification)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_ERROR = -32000

# MCP protocol version this server speaks.
PROTOCOL_VERSION = '2024-11-05'

SERVER_INFO = {
    'name': 'Odoo MCP Server',
    'version': '18.0.2.0.0',
}


class McpController(http.Controller):
    """MCP (Model Context Protocol) endpoint speaking JSON-RPC 2.0 over HTTP.

    Authenticated via the ``Authorization: Bearer <api-key>`` header. The
    custom ``auth='mcp'`` method (see ``models/ir_http.py``) validates a
    standard Odoo API key (scope ``rpc``). (OAuth is not included in this
    build.) Every tool call runs as the key owner, so record rules and
    ir.model.access enforce what the caller can see and do.

    Every call is also recorded in ``custom.mcp.log`` (Settings → Technical →
    MCP Activity Log): who, which tool, the redacted arguments, the outcome
    and the duration. Logging is best-effort by construction — it is written
    on its own cursor and never propagates a failure into the response.
    """

    @http.route('/mcp/v1', type='http', auth='mcp', methods=['POST'],
                csrf=False, save_session=False)
    def mcp_endpoint(self, **kwargs):
        try:
            raw = request.httprequest.data.decode('utf-8') or '{}'
            payload = json.loads(raw)
        except Exception as error:
            return self._json_error(None, PARSE_ERROR,
                                    'Parse error: %s' % error)

        # MCP also allows batched requests (a JSON array). Handle both.
        if isinstance(payload, list):
            responses = [self._handle_message(msg) for msg in payload]
            responses = [r for r in responses if r is not None]
            return self._json(responses)
        return self._json(self._handle_message(payload))

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------
    def _handle_message(self, message):
        """Return the JSON-RPC response dict, or None for a notification."""
        req_id = message.get('id')
        method = message.get('method')
        params = message.get('params') or {}

        # MCP notifications have no `id` and don't need a response.
        is_notification = req_id is None

        try:
            if method == 'initialize':
                result = self._handle_initialize(params)
            elif method == 'tools/list':
                result = self._handle_tools_list(params)
            elif method == 'tools/call':
                result = self._handle_tools_call(params)
            elif method in ('notifications/initialized',
                            'notifications/cancelled'):
                # Acknowledged-and-ignored.
                return None
            elif method == 'ping':
                result = {}
            else:
                if is_notification:
                    return None
                return self._error(req_id, METHOD_NOT_FOUND,
                                   'Unknown method: %s' % method)
        except McpError as error:
            if is_notification:
                return None
            return self._error(req_id, error.code, error.message, error.data)
        except Exception as error:
            _logger.exception("MCP %s failed", method)
            self._log_event('protocol_error', 'MCP %s failed' % method, error=error)
            if is_notification:
                return None
            return self._error(req_id, INTERNAL_ERROR,
                               'Internal error: %s' % error)

        if is_notification:
            return None
        return {'jsonrpc': '2.0', 'id': req_id, 'result': result}

    def _handle_initialize(self, params):
        return {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {
                'tools': {'listChanged': False},
            },
            'serverInfo': SERVER_INFO,
        }

    def _handle_tools_list(self, params):
        tools = []
        for name, tool in sorted(McpRegistry.tools().items()):
            tools.append({
                'name': name,
                'description': tool['description'],
                'inputSchema': tool['input_schema'],
                # The four behavioural hints. The registry guarantees all four
                # are present and boolean, so this never advertises a partial
                # set — a missing destructiveHint defaults to *true* per spec,
                # which would mislabel every read tool.
                'annotations': tool['annotations'],
            })
        return {'tools': tools}

    def _handle_tools_call(self, params):
        name = params.get('name')
        if not name:
            raise McpError(INVALID_PARAMS, 'Missing tool name')
        tool = McpRegistry.get(name)
        if not tool:
            raise McpError(INVALID_PARAMS, 'Unknown tool: %s' % name)
        args = params.get('arguments') or {}
        started = time.monotonic()
        try:
            value = tool['fn'](request.env, args)
        except Exception as error:
            # Tool execution errors are part of the MCP result, not a
            # protocol error — the client can show the message to the user.
            _logger.info("MCP tool %s raised: %s", name, error)
            # A guardrail refusal is classified as 'blocked', not 'error', so an
            # operator can tell "the agent tried and was stopped" apart from
            # "Odoo errored".
            self._log_call(name, args, log_utils.classify_error(error),
                           started, error=error)
            return {
                'content': [{'type': 'text', 'text': str(error)}],
                'isError': True,
            }
        self._log_call(name, args, 'ok', started, result=value)
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, default=self._json_default, indent=2,
                              ensure_ascii=False)
        return {
            'content': [{'type': 'text', 'text': text}],
            'isError': False,
        }

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _log_call(name, args, outcome, started, result=None, error=None):
        """Record one tool call. Swallows everything — see custom.mcp.log."""
        try:
            request.env['custom.mcp.log'].sudo().log_call(
                tool=name, args=args, outcome=outcome,
                duration_ms=(time.monotonic() - started) * 1000,
                result=result, error=error)
        except Exception:
            _logger.exception("MCP: logging tool call %s failed", name)

    @staticmethod
    def _log_event(kind, summary, error=None, detail=None):
        try:
            request.env['custom.mcp.log'].sudo().log_event(
                kind, summary, error=error, detail=detail)
        except Exception:
            _logger.exception("MCP: logging event %s failed", kind)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _json_default(value):
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()
        if isinstance(value, bytes):
            import base64
            return base64.b64encode(value).decode('ascii')
        if hasattr(value, '_name'):  # Odoo recordset
            return [{'id': record.id, 'display_name': record.display_name}
                    for record in value]
        if hasattr(value, '__iter__'):
            return list(value)
        return str(value)

    def _json(self, body):
        return request.make_response(
            json.dumps(body, default=self._json_default),
            headers=[('Content-Type', 'application/json')])

    def _error(self, req_id, code, message, data=None):
        err = {'code': code, 'message': message}
        if data is not None:
            err['data'] = data
        return {'jsonrpc': '2.0', 'id': req_id, 'error': err}

    def _json_error(self, req_id, code, message):
        return self._json(self._error(req_id, code, message))


class McpError(Exception):
    """Raise inside a handler to return a JSON-RPC error to the client."""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
