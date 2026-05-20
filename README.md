# forge-connectors

Connector manifests for [Forge](https://github.com/aiwatching/forge).

Forge pulls `registry.json` from this repo on startup (and every hour
afterwards) to populate its **Connectors Marketplace**. Each connector
lives in its own directory:

```
registry.json                # listing of all connectors (light)
<id>/
  manifest.yaml              # full declarative manifest
  README.md                  # optional, surfaced in the marketplace
```

## How Forge consumes this

- `https://raw.githubusercontent.com/aiwatching/forge-connectors/main/registry.json`
- `https://raw.githubusercontent.com/aiwatching/forge-connectors/main/<id>/manifest.yaml`

Override the base URL via `settings.connectorsRepoUrl` in Forge if you
want to host a private fork.

## Manifest schema

See [`docs/Connector-DeclarativeExtract-Spec.md`](https://github.com/aiwatching/forge/blob/main/docs/Connector-DeclarativeExtract-Spec.md)
in the main Forge repo for the full schema. In short, each manifest
declares:

- `id`, `name`, `version` — identity (semver)
- `icon`, `description`, `author`
- `min_forge_version` — gates install on Forge versions that have the
  necessary runtime features
- `runner` — `main` (permissive-CSP sites) or `isolated` (Teams, GitHub)
- `settings` — fields the user fills in (host URL, PAT, etc.). `type:
  secret` is auto-encrypted at rest with AES-256-GCM
- `tools` — callable surface for the LLM. Each tool declares a
  `protocol` of `browser` (script in user's tab), `http` (Forge issues
  the request), or `shell` (Forge spawns a command)

## Versioning rules

- Bump `version` on **every** material change to a manifest. The Forge
  extension uses the version string to detect upgrades.
- Major: breaking schema or tool removal
- Minor: new tools / settings fields
- Patch: bug fixes inside an existing tool script

## Adding a new connector

1. Create `<id>/manifest.yaml` with `id`, `name`, `version`, `tools`,
   `settings` (if needed)
2. Add the entry to `registry.json` (id, name, version, icon,
   description, min_forge_version)
3. Open a PR. The Forge maintainers review the manifest before merge —
   especially the `script` bodies, since they execute in the user's
   browser tab.
