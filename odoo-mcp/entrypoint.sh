#!/bin/sh
# Entrypoint for the Odoo MCP container.
#
# On a fresh run, if no config exists at ODOO_CONFIG_PATH we seed it from the
# baked-in example template so the operator has a file to edit. The server then
# starts; tool calls will return a clear "fill in url/db" error until the config
# is completed. (username/api_key are supplied at runtime via the odoo_connect
# tool, not stored here.)
set -eu

CONFIG_PATH="${ODOO_CONFIG_PATH:-/config/odoo_config.json}"
EXAMPLE_PATH="/app/odoo_config.example.json"

CONFIG_DIR="$(dirname "$CONFIG_PATH")"
mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_PATH" ]; then
    cp "$EXAMPLE_PATH" "$CONFIG_PATH"
    echo "------------------------------------------------------------------"
    echo "No config found — created an example at:"
    echo "    $CONFIG_PATH"
    echo "Edit it (set your real 'url' and 'db'), then restart this container."
    echo "Credentials (username/api_key) are provided per-user at runtime via"
    echo "the odoo_connect tool — they are NOT stored in this file."
    echo "------------------------------------------------------------------"
fi

# exec so the server is PID 1 and receives signals (clean shutdown).
exec python server.py
