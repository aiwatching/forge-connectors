# nac connector — design notes

Living reference for picking up FortiNAC connector work. Captures
the non-obvious stuff that took painful probing to discover and isn't
in the OpenAPI schema. Read this before adding tools or touching auth.

Current shipped version: **v0.12.0** · 72 tools (4 ssh + 56 http reads
+ 16 http destructive + 1 escape hatch). See `manifest.yaml`.

---

## 1. Why one connector, not two

Earlier history: there was a separate `fortinac` http-only connector
alongside `nac` (ssh-only). Merged at v0.4.0 — one device, one
credential pair, one connector. Tools mix `protocol: ssh` and
`protocol: http` freely; Forge framework supports this.

Settings hold `port` (SSH, default 22), `username` (default admin),
`password`. Same admin credential drives both surfaces. NAC's mgmt
HTTPS port (default 8443) is a per-call tool parameter, not a
setting.

---

## 2. REST auth — what NAC v7.6 actually wants

The OpenAPI schema (`docs/fnac-rest-schema-7.6.json`) describes the
logical surface but **not the real deployment shape**. The schema's
`/host/*`, `/user/*`, etc. paths return 401/404 directly. The GUI
hits everything at `/actions/<same-path>` with extra boilerplate.

This was verified against a live 7.6 install via Chrome DevTools
network sniffing (see §6 for the recipe).

### The dance

```
1.  POST /actions/user/current-session/login
    Content-Type: application/x-www-form-urlencoded
    user=admin&password=...
    →  200 OK
       Set-Cookie: JSESSIONID=<value>; Path=/; Secure; HttpOnly
       body:  { status:"success", sessionKey:"<32-char token>",
                userRecord:{...}, ... }

2.  Every subsequent call:
    GET/POST  /actions/<path>?resetUserTimeout=false&APIDEBUG=false&NAC_SERVER=&<args>
    Cookie:        JSESSIONID=<from step 1>
    Authorization: <sessionKey>           ← bare token, NO Bearer/Basic
    Accept:        application/json
    → 200 OK { status:"success", results:[...], total:N, ... }
```

### What goes wrong if you skip pieces

| Missing | Result |
|---|---|
| `/actions/` prefix | 404 + SPA HTML (`<title>Gui</title>`) |
| `?resetUserTimeout=...&APIDEBUG=...&NAC_SERVER=` | 400 Bad Request |
| `JSESSIONID` cookie | 400 |
| `Authorization` header | 401 |
| Both cookie + token | 200 ✓ |

The schema's `bfSecKey` (apiKey in query) and `httpBasic` security
schemes are inert in the v7.6 deployment we tested — they parse but
don't authenticate.

### TLS

NAC ships a self-signed cert (`CN=at16-fortinac` style). Manifest
top-level `http: { verify_tls: false }` disables Node fetch's strict
cert check. Requires Forge core ≥ 0.10.26 (we added the
`capture_response_headers` and `verify_tls` knobs for this connector
in `lib/chat/protocols/http.ts`).

### Session lifecycle

Sessions expire ~30 min idle. There's no long-lived API-user concept
in v7.6 (unlike FortiGate's `config system api-user`). Pipelines do
**login once per run** and thread (jsessionid, session_key) into
every subsequent tool. For runs > 30 min, re-login.

`login` tool's response preamble carries `set-cookie:` (via
manifest's `capture_response_headers: [set-cookie]`). Parse with:

```bash
SK=$(echo "$RESP" | jq -r '.content' | sed -n '/^$/,$p' | tail -n +2 | jq -r '.sessionKey')
JS=$(echo "$RESP" | jq -r '.content' | sed -n 's/^set-cookie: JSESSIONID=\([^;]*\).*/\1/p')
```

---

## 3. Tool inventory

72 tools across these groups (versions track when each was added):

| Group | Count | Versions | Notes |
|---|---|---|---|
| SSH (CLI) | 4 | v0.3 | upgrade · get_version · run_command · reboot |
| REST auth | 3 | v0.5 | login · logout · get_session_info |
| Host queries | 11 | v0.5–v0.6 | by_mac/ip/user/port · detail (id) · policy · health · applications · list/count/recent |
| Host destructive | 5 | v0.7 | enable/disable_by_mac · set_role · rescan_with_profile · validate_dpc |
| Network device queries | 6 | v0.7 | list/count/get_device · list_device_ports · get_port · list_port_changes_for_port |
| Network ops (destructive) | 5 | v0.8 | set_port_properties · start/stop_l3_scan · start/stop_device_discovery |
| Logging | 2 | v0.6 | list_connection_events · list_events |
| Policy reads | 1 | v0.6 | list_access_policies |
| Policy writes (destructive) | 6 | v0.9 | enable/disable · swap/set_rank · update/delete |
| User reads | 4 | v0.10 | list/get/policy · search_user_directory |
| System reads | 7 | v0.10 | cluster · software · hardware · license · HA · background_tasks · groups |
| Integration reads | 12 | v0.10–v0.11 | LDAP · AAA · MDM · SAML · syslog parsers · vuln scanners · firewall tags · email · FSSO · SNMP · proxy · recent_syslog |
| Group writes (destructive) | 5 | v0.12 | add/remove_devices · copy · update · delete |
| **Escape hatch** | 1 | v0.6 | `call_api(path, method, extra_query, body_json)` — any `/actions/*` endpoint |

**16 tools are destructive** — flagged `destructive: true` so chat
asks confirmation before firing.

`call_api` covers the remaining ~1200 endpoints in the OpenAPI
schema. Use a typed tool first if one fits — better param schemas,
clearer return docs to the LLM.

---

## 4. What's intentionally NOT covered

| Area | Reason |
|---|---|
| NCM (Network Configuration Manager) | Will be a **separate `fortinac-ncm` connector**. Different mgmt surface, different deployments. Don't pollute this one. |
| Most settings WRITES (LDAP/MDM/SAML create/update/delete) | Admin config — rare path for Mantis bug verification. Use `call_api` if needed. |
| Browser-side fallbacks (chrome MCP) | Deferred to v0.14+. REST covers 95% of UI flows; browser-side breaks unattended pipelines (no Chrome → no run). |
| Host/Port/User `add-members` (typed) | The `system/group/add-members` endpoint requires a `type` byte (host=1, port=2, user=0, device=3 — convention unverified). v0.12 only types `add_devices_to_group` because it has a clean `/{id}/add-devices` endpoint that doesn't need the discriminator. Use `call_api` for host/port/user group membership. |
| Bulk array form bodies (`elemID=1&elemID=2`) | http.ts's `body_form` currently serialises arrays as one CSV string. NAC may accept CSV (Spring `@RequestParam` does); if not, enhance http.ts to repeat keys or fall back to `call_api` with hand-built body. |

---

## 5. Roadmap (when you pick it back up)

Loose priority — confirm with real usage before committing time:

| Version | Content | Effort |
|---|---|---|
| v0.13 | (reserved for fortinac-ncm split) | new connector |
| v0.14 | Browser-side fallbacks for UI-only flows (chrome MCP probed) | M, value depends on actual gaps |
| v0.15 | Scheduler / backup writes (scheduled tasks, on-demand backup) | S, ops-only |
| v0.16 | Scan / compliance result reads (`/policy/remediation-configuration`, scan history) | S, useful for endpoint compliance bugs |
| v0.17 | Host group membership for host/port/user (need to verify the `type` byte mapping) | S after probing |
| v0.18 | Authentication / endpoint compliance policy writes | M, dangerous — defer until clear pipeline need |

---

## 6. How to probe new endpoints

Two-track approach when adding tools, especially for ones the
OpenAPI schema doesn't pin down well.

### Track A — OpenAPI scan (cheap, first pass)

```bash
python3 << 'PY'
import json
s = json.load(open('docs/fnac-rest-schema-7.6.json'))
# find GET endpoints in a subsystem
for p, ops in sorted(s['paths'].items()):
    if p.startswith('/<your-prefix>') and 'get' in ops:
        summ = (ops['get'].get('summary') or '')[:80]
        params = [q.get('name') for q in ops['get'].get('parameters',[]) if q.get('in') in ('query','path')]
        print(f'  GET  {p:55} [{",".join(params)[:30]}]  {summ}')
PY
```

Schema lives in `/Users/zliu/IdeaProjects/FortiNAC-v3/docs/fnac-rest-schema-7.6.json`.

### Track B — Live GUI sniff (when schema is ambiguous)

1. Start Chrome with debug port:
   `~/IdeaProjects/my-workflow/scripts/chrome-mcp.sh restart`
2. Log into NAC GUI (https://10.15.52.152:8443/gui/...), navigate
   to the page that exercises the tool you want to wrap.
3. Find the NAC tab id:
   ```bash
   curl -s http://localhost:9222/json/list \
     | python3 -c "import sys,json; print([t for t in json.load(sys.stdin) if '10.15' in t['url']][0]['webSocketDebuggerUrl'])"
   ```
4. Sniff `/actions/<subsystem>/*` requests via CDP — see the
   one-off scripts referenced in the v0.5–v0.6 commits
   (`/tmp/cdp-sniff.mjs`, `/tmp/cdp-probe*.mjs` patterns). Reload
   the page in the tab to trigger fresh requests, capture
   `Network.requestWillBeSent` for URL + headers, dump them.

The first probe of any new subsystem should confirm: actual base
path (`/actions/...`), required query params, request body shape
(form vs JSON), and any non-obvious headers.

---

## 7. Adding a typed tool — pattern

```yaml
  tool_name:
    description: |
      One-line summary + when to use vs alternatives.
      Multi-line: param notes, common filter syntax, gotchas.
    destructive: true   # only if write op
    protocol: http
    parameters:
      host:        { type: string, required: true }
      port:        { type: number, default: 8443 }
      jsessionid:  { type: string, required: true }
      session_key: { type: string, required: true }
      # tool-specific args here…
    request:
      method: GET
      url: "https://{args.host}:{args.port}/actions/<path>?resetUserTimeout=false&APIDEBUG=false&NAC_SERVER=&<args>"
      headers:
        Cookie: "JSESSIONID={args.jsessionid}"
        Authorization: "{args.session_key}"
        Accept: "application/json"
        # Content-Type: "application/json"   # for JSON-body POSTs
      # body_form: { … }                      # for form POSTs
      # body: "{args.something_json}"         # for JSON POSTs
    returns: "{ status, results: [Shape{ fields… }], total }"
```

Boilerplate to copy:

- Every URL needs `?resetUserTimeout=false&APIDEBUG=false&NAC_SERVER=` (chain extra args after).
- Every non-login tool needs the Cookie + Authorization headers (must spell `JSESSIONID=` literally — only the value is templated).
- `host` / `port` / `jsessionid` / `session_key` always required for non-login tools.
- Mark `destructive: true` on any write op.

After editing:

```bash
cd ~/IdeaProjects/forge-connectors
python3 -c "import yaml; yaml.safe_load(open('nac/manifest.yaml'))"  # parse check
# bump version in manifest.yaml AND in registry.json's nac entry
git add nac registry.json && git commit -m "nac vX.Y.Z — what's new" && git push
```

User then **Reinstalls** in Forge web Marketplace.

---

## 8. Forge core dependencies

This connector relies on three features added to my-workflow during
its v0.5 / v0.6 development:

- `http.verify_tls: false` knob (lib/chat/protocols/http.ts) — uses
  undici.fetch + Agent to skip TLS cert check. Required for NAC's
  self-signed cert.
- `capture_response_headers` (HttpRequestSpec) — surfaces named
  response headers in the truncated preamble so callers can parse
  Set-Cookie. Required for login's JSESSIONID extraction.
- undici 8.x as a direct dep — provides the dispatcher used when
  verify_tls=false.

Minimum compatible Forge version is set in this manifest as
`min_forge_version: 0.9.17` but practically needs 0.10.26+ for the
above. Update both as Forge versions roll forward.

---

## 9. Known quirks

- **Password in chat history** — connector settings store username/password
  encrypted, but pipelines that pass them via the curl probe paste
  the literal value into shell history. Rotate the NAC admin
  password if it leaks.
- **`/actions/host/by-mac/...` returns `{results: [HostRecord]}`** —
  not bare HostRecord. Subscript `[0]` to get the host. Same shape
  for all `by-*` endpoints.
- **Filter syntax** — verified working: `physAddress==00:50:56:B5:9E:70`,
  `connected==true`, `status==1`. Combining with `&` chains filter
  clauses but ANDing semantics aren't documented; verify with
  list_hosts before relying on a compound filter.
- **call_api `body_json`** — must be a JSON-encoded STRING (the LLM
  has to construct + stringify). Forge templates `{args.body_json}`
  into the request body as-is, with `Content-Type: application/json`
  header set explicitly in the tool spec.
- **set_role takes CSV `id`** — `host_ids: "1234,5678"`. Server-side
  splits to int[]. Other writers documented as taking arrays mostly
  accept CSV too, but unverified per-endpoint.
