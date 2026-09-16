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
        annotations={
            'readOnlyHint': True, 'destructiveHint': False,
            'idempotentHint': True, 'openWorldHint': False,
        },
    )
    def echo(env, args):
        return {'echoed': args['message']}

The MCP controller advertises every registered tool via ``tools/list`` and
dispatches ``tools/call`` to the registered function with ``(env, args)``.
The function should return a JSON-serialisable value (str, number, bool,
None, list, dict, or an Odoo recordset — recordsets get auto-serialised).

``annotations`` are MCP's four behavioural hints, and all four are REQUIRED —
registration raises if any is missing or non-boolean. They are what a host
shows the user before it runs a tool, and the spec's defaults are pessimistic
(``destructiveHint`` defaults to *true*), so a tool that omits them is not
quietly unlabelled — it is advertised as able to destroy data. Making them
mandatory is the only way a bridge module cannot reintroduce that by omission.
"""

#: The four hints every tool must declare. See
#: https://modelcontextprotocol.io/specification — Tool Annotations.
REQUIRED_HINTS = ('readOnlyHint', 'destructiveHint',
                  'idempotentHint', 'openWorldHint')


def _validated_hints(name, annotations):
    """Return the hints as a plain dict, or raise if the set is not complete."""
    hints = dict(annotations or {})
    missing = [h for h in REQUIRED_HINTS if h not in hints]
    if missing:
        raise ValueError(
            "Tool %r must declare every MCP behavioural hint; missing: %s"
            % (name, ', '.join(missing)))
    for hint in REQUIRED_HINTS:
        if not isinstance(hints[hint], bool):
            raise ValueError(
                "Tool %r: %s must be true or false, got %r"
                % (name, hint, hints[hint]))
    unknown = [h for h in hints if h not in REQUIRED_HINTS and h != 'title']
    if unknown:
        raise ValueError(
            "Tool %r declares unknown annotations: %s" % (name, ', '.join(unknown)))
    return hints


class McpRegistry:
    _tools = {}

    @classmethod
    def register(cls, name, description, input_schema, annotations, fn):
        # Validate BEFORE touching _tools, so a rejected tool leaves no
        # half-registered entry behind for tools/list to advertise.
        hints = _validated_hints(name, annotations)
        cls._tools[name] = {
            'description': description,
            'input_schema': input_schema,
            'annotations': hints,
            'fn': fn,
        }

    @classmethod
    def tools(cls):
        return dict(cls._tools)

    @classmethod
    def get(cls, name):
        return cls._tools.get(name)


def register_tool(name, description, input_schema, annotations):
    """Decorator form of :meth:`McpRegistry.register`."""
    def decorator(fn):
        McpRegistry.register(name, description, input_schema, annotations, fn)
        return fn
    return decorator
