# fortincm connector — design notes (v0.1.0)

Sibling of the `nac` connector. Read `nac/NOTES.md` first — most of
the conventions (caller-explicit token threading, SSH upgrade flow,
per-call host param, password-from-settings) are identical here.

Living reference for the bits that diverge from NAC.

---

## 1. Source

- OpenAPI: `/Users/zliu/Downloads/fortincm-8.0-api-docs.json`
  (OpenAPI 3.0.1, 1062 paths, 80 tags, base URL
  `https://10.15.50.60:8889`).
- No live probing yet — schema-only.

---

## 2. Tool inventory (74 tools)

| Group | Count | Notes |
|---|---|---|
| SSH (CLI) | 4 | upgrade · get_version · run_command · reboot (verbatim from NAC; get_version regex broadened to match `FortiNCM` and `FortiNAC`) |
| Auth | 5 | login · refresh_token · logout · get_current_user · list_user_tokens |
| NCM basics | 2 | get_ncm_version · get_ncm_serial |
| NCM cluster | 9 | get_cluster_nodes/node/node_status/role/capacity · get_failed_ncm_count · change_to_leader · add/delete_cluster_worker |
| Firmware (REST) | 6 | list_local_firmware_images · list_fortiguard_firmware · list_firmware_image_used_by · start/get_firmware_upgrade · delete_firmware_images |
| ADOM | 3 | list_adoms · get_adom_admin · delete_adom_admin |
| Host | 10 | list/get · health · health_history · applications · users · count_by_type · enable_disable · rescan_with_profile · delete |
| Group | 8 | list/get · by_type · hierarchy · create · update · clone · delete |
| Backup/Restore | 5 | list_backup_files · list_remote_backup_settings · backup_and_clean · db_backup_and_clean · restore_backup |
| Policy Package | 6 | list · assignments · revisions · current_revision · restore_revision · delete |
| Policy Block | 3 | list · get · list_by_package |
| Scheduler | 6 | list · get · activity_types · run_now · enable · disable |
| NetworkDevice | 4 | list · get · add · delete |
| EndpointFingerprint | 2 | list · count |
| **Escape hatch** | 1 | `call_api(path, method, query_params, body_json)` |

**25 tools are destructive** — flagged `destructive: true` so chat
asks for confirmation.

---

## 3. Auth model — divergence from NAC

NAC uses `POST /actions/user/current-session/login` with form body →
`sessionKey` + `JSESSIONID` cookie, both threaded into every call.

NCM uses **`GET /api/v2/auth/login` with HTTP Basic** → returns
`RestResultLoginResult { result: { loginStatus, jwt, admin, profile,
adoms, ... } }`. Caller threads `result.jwt` as `Authorization:
<jwt>` (bare, no `Bearer`/`Basic` prefix) into every subsequent v3
call.

```bash
RESP=$(curl -sk -u "$USER:$PASS" "https://$HOST:8889/api/v2/auth/login")
TOKEN=$(echo "$RESP" | jq -r '.result.jwt')
curl -sk -H "Authorization: $TOKEN" -H "AdomId: 1" \
     "https://$HOST:8889/api/v3/ncm/cluster/nodes"
```

Implementation note: the `login` tool sets
`Authorization: Basic {basic:{settings.username}:{settings.password}}`.
This uses Forge core's `{basic:user:pass}` template helper to
base64-encode at request time. If your Forge core doesn't have that
helper, fall back to a pre-encoded secret setting.

### Bare vs Bearer prefix

The OpenAPI spec only documents the header as "Authorization token"
without specifying a prefix. We mirror NAC's bare-token convention
because:
- The OpenAPI examples in the JFrog-generated spec show no prefix.
- NAC v7.6 (same codebase family) uses bare tokens.

If a NCM build rejects the bare form, retry via `call_api` with an
explicit `Bearer ` prefix; flip the convention here and document.

---

## 4. AdomId header — the multi-tenant tax

Most v3 endpoints require an `AdomId: <int>` header (NAC has nothing
like this). Typed tools take `adom_id` as a string parameter
defaulting to `"1"` (root ADOM on most installs). After login, call
`list_adoms` (or read `result.adoms[]` from the login response) to
discover the real ids for this admin's scope.

Endpoints that do **not** require AdomId:
- `/api/v2/auth/login`
- `/api/v3/auth/*` (logout, refresh_token, getCurrentUser, tokens)
- `/api/v3/version`, `/api/v3/serialNo`
- `/api/v3/ncm/cluster/*` (cluster scope is global)
- `/api/v3/backup/remote-settings`
- `/api/v3/firmware/*` (mostly)

For these, leaving `AdomId: 1` set is harmless — the server ignores
unused headers.

---

## 5. URL pattern divergence

- **NAC**: `https://{host}:8443/actions/<path>?resetUserTimeout=false&APIDEBUG=false&NAC_SERVER=&<args>`
  (boilerplate query params mandatory).
- **NCM**: `https://{host}:8889/api/v3/<path>?<args>`
  (no boilerplate; just standard query string).

NCM is the simpler URL shape — clean REST with no `/actions` prefix
and no compulsory query params.

---

## 6. What's intentionally NOT covered

| Area | Reason |
|---|---|
| Most settings WRITES (LDAP/MDM/SAML create/update/delete) | Rare path; use `call_api`. |
| Browser-side fallbacks | Schema covers everything; no need for chrome MCP yet. |
| Cluster certificate management | Niche; use `call_api` with `/api/v3/ncm/cluster/get-managed-ca/*` if needed. |
| MFA / RoamingGuest / Reporting / Dashboard / Auditlog / Event / Alarm / Monitor / ... ~50 other tags | The ~990 endpoints not typed are reachable via `call_api`. Add typed tools when a clear usage pattern emerges. |

---

## 7. Roadmap

| Version | Content |
|---|---|
| v0.1 | Initial 74 tools (this) |
| v0.2 | Live-probe against a real NCM (10.15.50.60) — verify token prefix, AdomId requirements per endpoint, fix any wrong assumptions |
| v0.3 | Add typed tools for top 10 most-used `call_api` patterns (track via usage) |
| v0.4 | Audit log / Event / Alarm read tools (debugging "what did NCM do at time X") |
| v0.5 | LogReceiver / Reporting reads |

---

## 8. Validation

```bash
cd ~/IdeaProjects/forge-connectors
python3 -c "import yaml; yaml.safe_load(open('fortincm/manifest.yaml'))"
python3 -c "import json; json.load(open('registry.json'))"
```

Both pass as of v0.1.0.
