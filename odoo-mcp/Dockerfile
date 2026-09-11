# Odoo MCP server — HTTP (streamable-http) service image.
FROM python:3.12-slim

# Don't write .pyc, unbuffered stdout for live logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_PATH=/mcp \
    ODOO_CONFIG_PATH=/config/odoo_config.json

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code + example config template + entrypoint.
COPY server.py odoo_config.example.json ./
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Run as a non-root user; ensure /config is writable for the seed-on-first-run.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /config \
    && chown -R appuser:appuser /app /config
USER appuser

# Config is provided via a mounted volume; declare it so it persists by default.
VOLUME ["/config"]

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
