# fortincm connector — design notes (v0.3.0)

Sibling of the `nac` connector. Read `nac/NOTES.md` first — most of
the shape (per-call host param, password-from-settings, SSH upgrade
flow) is identical here.

Living reference for the bits that diverge from NAC.

---

## 1. Source

- OpenAPI: `/Users/zliu/Downloads/fortincm-8.0-api-docs.json`
  (OpenAPI 3.0.1, 1062 paths, 108 tags).
- Live target: 10.15.50.60 (NCM v8.0.0 build 6001 / FNVX-MTM25000621).
  v0.3 re-validated against this box. The OpenAPI `servers` entry
  advertises port 8889 but the live appliance answers REST on
  **443** — we go with 443.
- Endpoints that are genuinely broken / surprising on live are
  documented in `BROKEN-APIS.md` (kept next to the manifest).

---

## 2. Tool inventory (v0.3 — 75 tools)

| Group | Count | Notes |
|---|---|---|
| SSH (CLI) | 4 | upgrade · get_version · run_command · reboot |
| Auth (probes) | 2 | get_current_user · list_user_tokens (no login/logout/refresh — auto) |
| NCM basics | 2 | get_ncm_version · get_ncm_serial |
| NCM cluster | 7 | get_cluster_nodes/node/role/capacity · get_failed_ncm_count · change_to_leader · add/delete_cluster_worker (cluster_node_status removed — 500 on standalone) |
| Firmware (REST) | 6 | list_local_firmware_images · list_fortiguard_firmware · list_firmware_image_used_by · start/get_firmware_upgrade · delete_firmware_images |
| ADOM | 6 | list_adoms · get_adom · get_root_adom · list_adom_admins · get_adom_admin · delete_adom_admins |
| Host | 9 | list · get · health · health_history · applications · users · count_by_status · enable_disable · rescan_with_profile · delete (count_by_type collapsed → status-only; richer counts via list_hosts filter) |
| Group | 8 | list · get · by_type (int!) · hierarchy · create · update · clone · delete |
| Backup/Restore | 5 | list_backup_files · list_remote_backup_settings · backup_and_clean · db_backup_and_clean · restore_backup |
| Policy Package | 6 | list · assignments · revisions · current_revision · restore_revision · delete |
| Policy Block | 3 | list · get · list_by_package |
| Scheduler | 5 | get · activity_types · run_now · enable · disable (list removed — server 500) |
| NetworkDevice | 5 | list · get · add · update · delete |
| EndpointFingerprint | 1 | list (count removed — server 400) |
| Dashboard | 3 | list_dashboards · system_info · system_performance |
| **Escape hatch** | 1 | `call_api(path, method, query_params, body_json)` |

**24 tools are destructive** — flagged `destructive: true` so chat
asks for confirmation.

The ~990 endpoints not typed are reachable via `call_api`.

---

## 3. Auth model — v0.3 fully auto

The v0.2 manifest exposed `login` / `refresh_token` / `logout` tools
and every other REST tool took a `token` arg that callers (LLMs,
pipelines) had to thread. **Gone in v0.3.** Forge core now ships a
connector-level `bearer-token-exchange` auth scheme; the fortincm
manifest declares:

```yaml
auth:
  type: bearer-token-exchange
  exchange_url: "https://{args.host}/api/v3/auth/login"
  exchange_method: POST
  exchange_body:
    username: "{settings.username}"
    password: "{settings.password}"
  bearer_path: result.jwt
  bearer_format: bare        # NCM rejects "Bearer <jwt>" with 500
  expires_ttl_sec: 540       # 9 min, 1 min safety on the 10 min server TTL
```

How it works at runtime (`lib/chat/protocols/http.ts`):

1. First REST tool call per `(credIdentity, exchange_url)` triggers
   a POST to `https://<host>/api/v3/auth/login` with the credentials
   body templated from settings + args.
2. Forge pulls `result.jwt` out of the response and caches it in
   memory keyed by `<JSON-of-resolved-body> | <resolved-exchange-url>`
   — so multi-host installs cache per host, and rotating the password
   invalidates the cache.
3. The JWT is injected as `Authorization: <jwt>` (bare — no `Bearer`
   prefix) on every subsequent REST tool call.
4. Forge refreshes 60s before the cached TTL (`expires_ttl_sec`
   minus 60s of headroom).

LLM-visible effect: no `token` parameter on any tool, no concept of
"sessions" exposed to the chat. `get_current_user` and
`list_user_tokens` are kept as **auth probes** — they hit the real
auth endpoints with the auto-injected JWT, useful for sanity-checking
credentials.

### Quirks worth remembering

- **Port 443, not 8889.** The OpenAPI `servers:` entry lies. All REST
  URLs are `https://{host}/api/v3/...` (no port).
- **Bearer prefix returns 500.** Use the bare JWT in the
  `Authorization` header (`bearer_format: bare`).
- **JWT TTL is 600 s.** Forge refreshes at 540 s. Long-running
  pipelines stop having to call `refresh_token` themselves.
- **Login is `/api/v3/auth/login`, not `/api/v2/`.** The OpenAPI spec
  shows the v2 path but the v3 path is what works live.

---

## 4. AdomId header — the multi-tenant tax

Most v3 endpoints require an `AdomId: <int>` header (NAC has nothing
like this). AdomId is only set on endpoints whose `parameters:` block
declares it.

**AdomId NOT required** (verified per-endpoint in spec):
- `/api/v3/auth/*`
- `/api/v3/version`, `/api/v3/serialNo`
- `/api/v3/ncm/cluster/*` — cluster scope is global
- `/api/v3/firmware/*`
- `/api/v3/schedules/{id}`, `/api/v3/schedules/runNow`,
  `/api/v3/schedules/enable`, `/api/v3/schedules/disable`
- `/api/v3/backup/db/backup`
- `/api/v3/adoms`, `/api/v3/adoms/{id}`, `/api/v3/adoms/root`

**AdomId quirk:** `/api/v3/dashboard/list` uses lowercase `adomId`
(spec quirk — handled in the manifest).

**Default AdomId in tool params** is now `"330201025894400"` (the root
ADOM id observed on 10.15.50.60). The root id is a long random
integer that **varies per install** — generated at install time, NOT
hard-coded server-side. Callers should call `list_adoms` /
`get_root_adom` once and use the returned `id` for their site.

---

## 5. Body vs query — already correct in v0.2, kept

| Endpoint | Shape |
|---|---|
| `DELETE /api/v3/host/delete` | JSON body `DeleteHostReq` |
| `DELETE /api/v3/ncm/cluster/delete` | JSON body `DeleteWorkerRequest` |
| `DELETE /api/v3/firmware/local-images/delete` | JSON body `DeleteImagesRequest` |
| `DELETE /api/v3/adoms/admin` | JSON body `DeleteUserRequest` |
| `POST /api/v3/schedules/runNow` | JSON array of ids |
| `PUT /api/v3/schedules/enable` | JSON array of ids |
| `PUT /api/v3/schedules/disable` | JSON array of ids |
| `POST /api/v3/backup/db/backup` | only `fileRetentionDays` (no AdomId, no backupStorage) |

Tools that still legitimately use query-CSV for `ids`:
- `DELETE /api/v3/groups/group?id={ids}` (note: param is `id`, not `ids`)
- `DELETE /api/v3/network-device?ids={ids}`

---

## 6. What's intentionally NOT covered

| Area | Reason |
|---|---|
| Most settings WRITES (LDAP/MDM/SAML create/update/delete) | Rare path; use `call_api`. |
| Cluster validate/edit/open-worker | Niche internal ops — call_api if needed. |
| Campus firmware validation (`/api/v3/firmware/upgrade/campus-validation`) | Use call_api when you need pre-flight. |
| `host/list-filters`, `host/persistent-agent/*` etc. | Add when usage justifies. |
| MFA / RoamingGuest / Reporting / Auditlog / Event / Alarm / Monitor / ~50 other tags | Reachable via `call_api`. |

---

## 7. Validation

```bash
cd ~/IdeaProjects/forge-connectors
python3 -c "import yaml; yaml.safe_load(open('fortincm/manifest.yaml'))"
python3 -c "import json; json.load(open('registry.json'))"

# Live JWT round-trip probe — should print HTTP 200 + JSON
URL=https://10.15.50.60
JWT=$(curl -sk -X POST "$URL/api/v3/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YAMS"}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['result']['jwt'])")
curl -sk "$URL/api/v3/dashboard/system-info" \
  -H "Authorization: $JWT" -H "AdomId: 330201025894400" \
  -w '\nHTTP %{http_code}\n'
```

All pass as of v0.3.0.

---

## 8. Changelog

- **v0.3.0 (2026-06-02)** — connector-level `bearer-token-exchange`
  auth replaces caller-threaded JWT. login/logout/refresh_token tools
  removed. count_endpoint_fingerprints, list_schedules,
  get_cluster_node_status, count_hosts_by_type removed (all broken on
  live NCM — see BROKEN-APIS.md). Added get_adom, get_root_adom,
  count_hosts_by_status. group_type retyped to integer. Backup-category
  docs corrected to SYSTEM_BACKUP / DATABASE_BACKUP. Default AdomId
  changed to the live root id.
- **v0.2.0** — auth corrected to v3 + bare JWT + port 443, DELETE/PUT
  body shapes fixed, AdomId added per spec.
- **v0.1.0** — initial cut from OpenAPI (broken auth assumptions).
