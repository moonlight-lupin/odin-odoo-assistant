# MCP Server — OAuth 2.1 setup & troubleshooting (team guide)

How to connect Claude (Desktop / claude.ai) to this Odoo MCP module using **OAuth** — so each
teammate logs in as **themselves** (no shared API keys), with Odoo's record rules and access rights
applying per user.

There are **three responsibilities**, usually three different people:

| # | Who | What |
|---|-----|------|
| 1 | **Odoo admin** | Install/upgrade the module, turn on OAuth, set the Issuer URL |
| 2 | **Infra / hosting** | Make the reverse proxy forward two `/.well-known/…` paths to Odoo *(the usual sticking point)* |
| 3 | **Each user** | Add the custom connector in Claude and approve the login |

Do them in that order. **Until step 2 is done, step 3 cannot work** — Claude can't discover the OAuth
endpoints and falls back to a wrong URL (see Troubleshooting → *"redirects to /authorize → 404"*).

---

## How it works (30-second mental model)

When you add the connector, Claude performs a standard OAuth 2.1 discovery + login:

```
1. Claude → GET  /.well-known/oauth-protected-resource      → "the auth server is <issuer>"
2. Claude → GET  /.well-known/oauth-authorization-server     → endpoints: /oauth/authorize, /token, /register
3. Claude → POST /oauth/register   (Dynamic Client Registration, PKCE)   → gets a client_id (mcp_…)
4. Claude → GET  /oauth/authorize  → you log into Odoo + click Approve     → auth code
5. Claude → POST /oauth/token      → access token (+ refresh token)
6. Claude → POST /mcp/v1   Authorization: Bearer <access token>            → tools run as you
```

Steps 3–6 are normal Odoo controller routes and work out of the box. **Steps 1–2 are the only ones
that commonly fail** — not because of Odoo, but because many reverse proxies don't forward
`/.well-known/` to the backend.

---

## Step 1 — Odoo admin: enable OAuth

1. Install or upgrade the **MCP Server** module (this build, **v18.0.3.0.0+**, deployed to the addons
   path — *not* via the in-app "Import Module" uploader, which doesn't run custom Python on managed
   hosts). After deploy: **Apps → MCP Server → Upgrade**, then restart the Odoo service.
2. Enable developer mode, then **Settings → General Settings → MCP Server**:
   - Turn **OAuth 2.1 (Claude-compatible)** **ON**.
   - **Issuer URL** = the public base URL clients use, **exactly**, no trailing slash:
     `https://odoo.example.com`
   - (Optional) Access-token TTL (default 60 min) / Refresh-token TTL (default 30 days).
   - **Save.**

That's all on the Odoo side. The module's `/oauth/*` and `/.well-known/oauth-*` routes are now live
*in Odoo* — but the proxy still has to let the `/.well-known` ones through (Step 2).

---

## Step 2 — Infra: forward two `/.well-known/…` paths to Odoo  ⚠️ the critical step

OAuth discovery uses two fixed URLs that **must reach Odoo**:
```
/.well-known/oauth-protected-resource
/.well-known/oauth-authorization-server
```
Many proxies (nginx in front of Odoo, including on AWS/EC2 deployments) answer `/.well-known/`
themselves — for ACME/SSL or via a dot-path deny rule — so these never reach Odoo and return a
generic 404. That breaks discovery.

**Confirm the layer first (run on the EC2 instance).** Hitting Odoo directly, bypassing nginx,
should return JSON — proving Odoo serves it and nginx is the blocker:
```bash
curl -s http://127.0.0.1:8069/.well-known/oauth-authorization-server     # → JSON (Odoo serves it)
curl -s https://<public-host>/.well-known/oauth-authorization-server    # → nginx 404 (blocked at the proxy)
```
(If the first returns the nginx page too, Odoo isn't reachable on 8069 on that host — but if
`/oauth/register` works publicly, Odoo is fine and nginx is the layer to adjust.)

**The likely cause is one of these in the nginx config:**
1. A **dot-path deny rule** that also catches `/.well-known/`, e.g. `location ~ /\. { deny all; }`
   → fix: exclude well-known — `location ~ /\.(?!well-known) { deny all; }`.
2. A **`/.well-known/` block scoped to a webroot** (for Let's Encrypt/certbot) that 404s anything
   other than `acme-challenge` → fix: keep `acme-challenge` on the webroot, but pass the OAuth
   paths to Odoo.

> **On managed hosting (e.g. `*.run-odoo.com` on AWS EC2), nginx belongs to the provider** — this
> whole step is a support request to them. Send them this section; the ask is exactly the location
> block below plus `sudo nginx -t && sudo systemctl reload nginx`.

**Add an nginx location that proxies just these two paths to the Odoo upstream**, placed so it takes
precedence over any existing `/.well-known/` or dot-path rule:

```nginx
location ~ ^/\.well-known/(oauth-protected-resource|oauth-authorization-server)$ {
    proxy_pass http://127.0.0.1:8069;          # the Odoo upstream
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
}
```
Then `sudo nginx -t && sudo systemctl reload nginx`.

If a dot-path deny rule exists (`location ~ /\. { deny all; }`), either keep the block above *ahead*
of it, or exclude well-known: `location ~ /\.(?!well-known) { deny all; }`.

> If there's also a CDN/WAF in front (Cloudflare, an AWS ALB/CloudFront), confirm it isn't
> special-casing `/.well-known/` either — but if `curl` of these paths returns **nginx's** 404 page,
> the request is reaching the instance and being stopped there, so the nginx rule is what's needed.

### Verify Step 2 (anyone can run this)
```bash
curl -s https://odoo.example.com/.well-known/oauth-authorization-server
```
- ✅ **Fixed:** returns JSON containing `"authorization_endpoint": ".../oauth/authorize"`.
- ❌ **Not fixed:** returns an HTML page ending in `<center>nginx</center>` (the proxy is still
  intercepting it).

Sanity check that Odoo/the module is healthy regardless (this should already return `201`):
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://odoo.example.com/oauth/register \
     -H "Content-Type: application/json" -d '{"redirect_uris":["https://claude.ai/api/mcp/auth_callback"]}'
```

---

## Step 3 — Each user: add the connector in Claude

Once Step 2 verifies green:

**Claude Desktop / claude.ai → Settings → Connectors → Add custom connector**
- **Name:** Odoo (or anything)
- **Server URL / Remote MCP URL:** `https://odoo.example.com/mcp/v1`
- **Authentication:** **OAuth** — leave Client ID / Secret **blank** (Claude registers itself
  automatically via Dynamic Client Registration).
- Click **Connect** → you're redirected to the Odoo login (if not already signed in) → an
  **Authorize MCP access** consent screen → **Approve**.
- Done — the `odoo_*` tools appear, running as your Odoo user. Ask Odin to run `odoo_whoami` to
  confirm.

> Do **not** type your email (or anything) into the Client ID field. If you do, Claude skips
> auto-registration and the login breaks — leave it blank.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Login redirects to **`https://…/authorize?…` → 404**, and `client_id` looks like your **email** | Discovery failed → Claude fell back to a default endpoint + you (or it) supplied a manual client_id | **Step 2 not done.** Verify the two `/.well-known/…` return JSON, not nginx's 404. Re-add the connector with Client ID **blank**. |
| `curl /.well-known/oauth-authorization-server` → page ending in `nginx` | Proxy intercepts `/.well-known/` | Step 2 nginx rule. |
| Discovery JSON returns but points at the **wrong host** | Issuer URL mismatch | Set Issuer URL to the exact public base (`https://odoo.example.com`, no trailing slash) and Save. |
| `/oauth/*` returns **404** (not 400/201) | OAuth toggle off, or Issuer URL empty | Step 1 — enable OAuth + set Issuer URL. |
| Consent screen → Approve → error | redirect_uri not registered | Claude uses `https://claude.ai/api/mcp/auth_callback`; DCR registers it automatically — re-add with Client ID blank so registration happens. |
| Tools never appear after Approve | token/endpoint reachability | Confirm `/oauth/token` (POST) is reachable; check the Claude Desktop MCP logs. |

**One-shot health check (run after Step 2):**
```bash
base=https://odoo.example.com
curl -s $base/.well-known/oauth-protected-resource      | head -c 200; echo
curl -s $base/.well-known/oauth-authorization-server    | head -c 200; echo
```
Both should be JSON. If they are, the connector will go straight through.

---

## Security & operations

- **Per-user identity.** Each login runs as that Odoo user — record rules, `ir.model.access`, and the
  audit trail (`create_uid`/`write_uid`) all apply per person. No shared keys.
- **Tokens.** Access tokens are short-lived (default 60 min); refresh tokens are single-use (rotated
  on every refresh) and stored **SHA-256 hashed** — plaintext only ever exists in the HTTP response to
  the client. PKCE (S256) is required on every authorization.
- **Revoking a user / client.** Settings → Technical → **MCP OAuth Tokens** / **MCP OAuth Clients** —
  delete the relevant records (there's also a GC cron for expired tokens).
- **Least privilege.** Give MCP users only the Odoo groups they need; the module additionally blocks
  deletions and structural/system writes by default (see the main README).
- **HTTPS only.** The Issuer URL must be `https://` — OAuth tokens must never travel in clear.

---

## Fallback (no OAuth): API key via `mcp-remote`

If you need a connection **before** Step 2 is sorted, Claude Desktop can reach the module with a plain
**API key** using the `mcp-remote` bridge (needs Node.js). This uses only `/mcp/v1` — no `.well-known`,
no OAuth. In `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "odoo": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://odoo.example.com/mcp/v1",
               "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "Bearer <YOUR_ODOO_API_KEY>" }
    }
  }
}
```
Per-user keys still give per-user identity, but it's a manual per-machine setup — OAuth (above) is the
cleaner team experience once the proxy rule is in place.
