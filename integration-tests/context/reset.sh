#!/bin/sh
# Rebuild the throwaway database from scratch, install the module, seed it.
set -e
cd "$(dirname "$0")"
docker compose -p ctxtest down -v >/dev/null 2>&1 || true
docker compose -p ctxtest up -d db
sleep 6
ADDONS=--addons-path=/mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons
MSYS_NO_PATHCONV=1 docker compose -p ctxtest run --rm odoo odoo -d ctx -i odoo_module_mcp_server \
  --without-demo=all --stop-after-init $ADDONS > install.log 2>&1
grep -E "ERROR|CRITICAL" install.log | grep -v "Unexpected indentation" && exit 1 || true
MSYS_NO_PATHCONV=1 docker compose -p ctxtest run --rm -T odoo sh -c \
  "odoo shell -d ctx --db_host=db --db_user=odoo --db_password=odoo $ADDONS --no-http < /mnt/scripts/seed.py" > seed.log 2>&1
grep '^SEED' seed.log | sed 's/^SEED//' > seed.json
test -s seed.json || { tail -30 seed.log; exit 1; }
docker compose -p ctxtest up -d odoo
sleep 8
echo ready
