"""Process-wide registry of MCP tools.

A module declares its tools at import time::

    from odoo.addons.odoo_module_mcp_server.mcp_registry import register_tool

    @register_tool(
        name='example.echo',
        description='Return whatever was passed in.',
        input_schema={
            'type': 'object',
            'properties': {'message': {'type': 'string'}},
            'required': ['message'],
        },
    )
    def echo(env, args):
        return {'echoed': args['message']}

The MCP controller advertises every registered tool via ``tools/list`` and
dispatches ``tools/call`` to the registered function with ``(env, args)``.
The function should return a JSON-serialisable value (str, number, bool,
None, list, dict, or an Odoo recordset — recordsets get auto-serialised).
"""


class McpRegistry:
    _tools = {}

    @classmethod
    def register(cls, name, description, input_schema, fn):
        cls._tools[name] = {
            'description': description,
            'input_schema': input_schema,
            'fn': fn,
        }

    @classmethod
    def tools(cls):
        return dict(cls._tools)

    @classmethod
    def get(cls, name):
        return cls._tools.get(name)


def register_tool(name, description, input_schema):
    """Decorator form of :meth:`McpRegistry.register`."""
    def decorator(fn):
        McpRegistry.register(name, description, input_schema, fn)
        return fn
    return decorator
