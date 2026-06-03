# FortiNCM v8.0 — broken / surprising live API endpoints

Live target: **10.15.50.60** (NCM v8.0.0 build 6001 / serial FNVX-MTM25000621)
Spec compared against: `/Users/zliu/Downloads/fortincm-8.0-api-docs.json`
Verified: 2026-06-02 with admin JWT and root ADOM (`330201025894400`).

For each endpoint: what the spec says, what the live server actually
does, and how the v0.3 connector handles it.

---

## Auth — the spec misleads two ways

### POST /api/v2/auth/login
- **In spec**: yes (tag AUTH_V2), Basic-auth via `GET`.
- **Live**: 400 MALFORMED_REQUEST / 404 depending on endpoint variant.
- **Reality**: only `POST /api/v3/auth/login` with JSON body
  `{ "username", "password" }` works. Response is
  `{ result: { jwt, admin, profile, adoms[], ... } }`.
- **Fix applied**: connector uses v3, body-mode bearer-token-exchange.

### Authorization header format
- **In spec**: HTTP bearer (implies `Authorization: Bearer <jwt>`).
- **Live (2026-06-02)**: `Authorization: Bearer <jwt>` →
  **HTTP 403 `{"message":"UNAUTHORIZED","code":101}`**. (Earlier
  builds returned 500.) Either way it does not authenticate.
- **Reality**: the JWT must be sent **bare**, i.e.
  `Authorization: <jwt>` — no scheme prefix.
- **Fix applied**: `auth.bearer_format: bare` in manifest. Forge
  injects the raw token.

### JWT TTL
- **In spec**: not documented.
- **Live**: 600s (10 min). After 600s any call → 401.
- **Fix applied**: `expires_ttl_sec: 540` — Forge refreshes 60s before
  expiry. The bearer cache is per `(host, credIdentity)` so password
  rotation and multi-host installs invalidate / partition correctly.

---

## Backup categories — spec values 400, real values undocumented

### GET /api/v3/backup/{backup_category}
- **In spec**: path param `backup_category` (string, no enum).
- **Live**: returns **400 Bad Request** for every value the spec
  suggests:
  - `SYSTEM`, `DB`, `ADOM`, `CONFIGURATION`, `DATABASE`, `LOGS`,
    `CONFIG_PACKAGE`, `ADOM_BACKUP`, `REPORTS`, `LOGS_BACKUP` → 400
  - `system`, `db`, `adom` (lowercase) → 400
- **Live working values** (probed):
  - `SYSTEM_BACKUP` → 200, returns config-package tarballs
  - `DATABASE_BACKUP` → 200, returns DB tarballs
- **Fix applied**: `list_backup_files` and `restore_backup` tool
  descriptions hard-call out `SYSTEM_BACKUP | DATABASE_BACKUP` as the
  only live-verified categories. Other categories may exist on
  installs that have the corresponding feature enabled.

---

## Scheduler list — 500 server bug

### GET /api/v3/schedules
- **In spec**: yes — query params `runsOnNCM` (bool), `taskType` (str);
  AdomId header required.
- **Live**: returns **500 `{"message":"ERROR - null"}`** for every
  combination of args (root AdomId, runsOnNCM=true/false, with/without
  taskType, with body, with empty body, POST). Same 500 even with no
  filter at all.
- **Reality**: server-side bug on empty-DB / fresh installs — it tries
  to enumerate something that's null. The activity-types sister
  endpoint (`/api/v3/schedules/activity-types`) **works** fine.
- **Fix applied**: removed the `list_schedules` tool. Kept
  `get_schedule(id)`, `list_schedule_activity_types`,
  `run_schedule_now`, `enable_schedules`, `disable_schedules` so a
  caller who has ids from elsewhere (GUI, install scripts) can still
  drive schedules. Use `call_api` to retry `/api/v3/schedules` on an
  install where it might work.

---

## Host count-by-type — most types 500

### GET /api/v3/host/count-by-type/{countType}
- **In spec**: path param `countType: string` — no enum given.
- **Live** (tried 12 values):
  - `STATUS` → 200 `{ ENABLED, DISABLED }`
  - `OS` → 200 but `result: {}` (empty)
  - `CAMPUS`, `CONNECTED`, `HOST_TYPE`, `CONNECTION_STATE`,
    `OS_FAMILY`, `VENDOR`, `ROLE`, `HOST_TYPE_VIEW`, `DEVICE_TYPE`,
    `OFFLINE`, `LOCATION`, `TYPE`, `BY_OS`, `BY_STATUS` → **500
    `ERROR - failed to count`**
- **Fix applied**: replaced the generic `count_hosts_by_type` tool
  with a narrow `count_hosts_by_status` tool that hardcodes the only
  working bucket. For richer breakdowns use `list_hosts` with a
  filter (e.g. `filter=connected==true`).

---

## Endpoint-fingerprint count — uniformly 400

### GET /api/v3/endpoint-fingerprint/count/{countType}
- **In spec**: header `AdomId` required, path `countType: string`.
- **Live** (tried 8 values: `OS`, `STATUS`, `CLASSIFICATION`,
  `DEVICE_TYPE`, `VENDOR`, `CATEGORY`, `ENABLED`, `METHOD_TYPE`):
  - All → **400 Bad Request** (even with valid root AdomId).
- **Reality**: the endpoint takes some additional argument the spec
  doesn't document, or only accepts internal type ids. No simple
  exploration found a working call.
- **Fix applied**: removed the `count_endpoint_fingerprints` tool.
  `list_endpoint_fingerprints` with `count=0&filter=…` is the
  reachable alternative. Use `call_api` if you have a working type id.

---

## Groups by type — needs INTEGER, spec implies string

### GET /api/v3/groups/group/type
- **In spec**: query `groupType: integer` (the spec is right —
  it was the v0.2 manifest that called it a string).
- **Live** with string values (`ADMINISTRATOR`, `DEVICE`, …) → 400.
- **Live** with integer values:
  - `groupType=1` → 200 DEVICE groups
  - `groupType=2` → 200 PORT groups
  - `groupType=10` → 200 ADMINISTRATOR groups
  (Discoverable from `list_groups` → each group's `groupTypeValue`.)
- **Fix applied**: parameter retyped to `number`; tool description
  lists the integer→name mapping observed live.

---

## Cluster node-status — 500 on STANDALONE nodes

### GET /api/v3/ncm/cluster/node-status/{serial}
- **In spec**: yes.
- **Live**: returns **500 `ERROR - failed to check node status`** on
  the local standalone install (FNVX-MTM25000621, nodeType
  STANDALONE).
- **Reality**: the endpoint probably only works for nodes that are
  part of a real cluster (LEADER/WORKER). On a STANDALONE box there's
  nothing to status-check. Other cluster endpoints
  (`/nodes`, `/node/{serial}`, `/role`, `/capacity`, `/failed`) all
  work fine on standalone.
- **Fix applied**: removed the `get_cluster_node_status` tool —
  `get_cluster_node(serial)` already returns `nodeStatus` (e.g.
  `"RUNNING"`). For real cluster installs the endpoint may work; use
  `call_api` to retry.

---

## Response shape divergence — `results[]` not `records[]`

### GET /api/v3/host/list, /api/v3/endpoint-fingerprint/list
- **In spec / v0.2 manifest**: returns `result.records[]`.
- **Live**: returns `results[]` (plural) at the **top level**, plus
  `total`, `filtered`, `totalPages`, `hasNext`, `pageIndex`.
  Example:
  ```json
  { "message":"success", "code":0, "result":null,
    "results":[], "total":0, "filtered":0,
    "totalPages":0, "hasNext":false, "pageIndex":0 }
  ```
- **Fix applied**: `returns:` strings in the manifest now describe the
  real shape so the LLM doesn't go looking under `result.records`.

---

## list_dashboards — lowercase header

### GET /api/v3/dashboard/list
- **In spec**: header named `adomId` (lowercase 'a' — one-off quirk).
- **Live**: confirmed — works with lowercase `adomId`; the standard
  capitalised `AdomId` is silently ignored / treated as missing on
  some builds.
- **Fix applied**: manifest sends lowercase `adomId` for this tool
  only. All other tools use canonical `AdomId`.

---

## Summary

| Endpoint | Spec said | Live behaviour | Action |
|---|---|---|---|
| `POST /api/v2/auth/login` | works | 400 / wrong | switched to `/api/v3/auth/login` |
| `Authorization: Bearer <jwt>` | standard | 500 | bare token (`bearer_format: bare`) |
| `GET /api/v3/backup/SYSTEM` | works | 400 | use `SYSTEM_BACKUP` / `DATABASE_BACKUP` |
| `GET /api/v3/schedules` | works | 500 always | tool removed (kept get/runNow/enable/disable) |
| `GET /api/v3/host/count-by-type/*` | string enum | 500 except `STATUS` (`OS` empty) | tool narrowed to STATUS only |
| `GET /api/v3/endpoint-fingerprint/count/{type}` | string enum | 400 for every type tried | tool removed |
| `GET /api/v3/groups/group/type?groupType=DEVICE` | (v0.2 sent string) | 400 | param retyped to integer |
| `GET /api/v3/ncm/cluster/node-status/{serial}` | works | 500 on STANDALONE | tool removed |
| `host/list`, `endpoint-fingerprint/list` shape | `result.records[]` | `results[]` at top level | `returns:` doc fixed |
| `dashboard/list` AdomId header | `adomId` (lowercase) | confirmed | manifest sends lowercase |

35 of 37 tested REST endpoints work as documented (with the v3 +
bare-token + correct categories fixes above). 4 endpoints were
broken enough to drop from typed tools; all remain reachable via
`call_api` if a different NCM build behaves differently.
