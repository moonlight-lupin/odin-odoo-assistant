# Integration harness — Odoo-context tools

Runs every acceptance scenario from the spec through **both** servers against a real, throwaway
Odoo 18 (Docker), and checks the two routes return the same answers.

* `addons/knowledge/` — a **test stand-in** for Odoo's Enterprise Knowledge app (the parts the tools
  touch: tree, body with Odoo's default HTML sanitiser, properties defined on the parent, members and
  permissions as record rules, chatter). Never deploy it.
* `scripts/seed.py` — production-shaped companies, journals, users and the workspace.
* `run.py` — route A = the in-Odoo module over `/mcp/v1`; route B = `odoo-mcp/server.py` over XML-RPC.

```bash
./reset.sh        # fresh DB, install the module, seed (a few minutes)
python run.py     # 85 checks; exit code 1 on any failure
docker compose -p ctxtest down -v
```

To run against a real Enterprise build instead of the stand-in (done on 11 Nov 2026 against
`odoo-18.0+e.20261029` with the real Knowledge app — 85/85 passed), install `knowledge` and the
module into a scratch database on that stack, seed it with `CTX_BASE_URL` set to the URL you will
serve it on, then run with `CTX_URL`, `CTX_DB` and `CTX_SEED` pointing at it. On Windows use
`127.0.0.1`, not `localhost` — the IPv6 fallback adds ~2 s per XML-RPC call.

What the stand-in alone cannot cover: the real Knowledge app's own behaviour. The Enterprise run
above covers it; production-specific data and permissions still deserve a staging pass.
