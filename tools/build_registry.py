#!/usr/bin/env python3
"""
Sync the `version` field of each connector entry in registry.json
from the connector's manifest.yaml.

This script does NOT regenerate the entire registry — descriptions
in registry.json are hand-curated one-liners, not the verbose
multi-line `description:` in each manifest. We only touch fields
that have a canonical source in the manifest: currently just
`version` and `name` (renames are rare but should propagate).

Usage:
    python3 tools/build_registry.py            # write registry.json
    python3 tools/build_registry.py --check    # exit 1 if registry
                                               # is out of sync (CI gate)

Why this exists: bumping a manifest's version without bumping the
matching entry in registry.json silently breaks the Forge marketplace
sync UI ("no update available" forever). This script + a CI check
makes that class of bug impossible.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "registry.json"


def parse_manifest_top(path: Path) -> dict[str, str]:
    """
    Extract top-level scalar keys from a manifest.yaml — we only care
    about `id`, `name`, `version`. Stops at the first nested section
    (settings: / tools: / etc.) or a block scalar continuation. No
    PyYAML dep (some manifest scripts have non-printable chars).
    """
    out: dict[str, str] = {}
    interesting = {"id", "name", "version"}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            # Strip newline; preserve leading spaces so we can detect
            # indented (= nested) lines.
            line = line.rstrip("\n")
            if not line.strip():
                continue
            # Indented line → we've descended into a nested block; stop.
            if line[0] in (" ", "\t"):
                continue
            m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)$', line)
            if not m:
                continue
            key, raw = m.group(1), m.group(2).strip()
            if key not in interesting:
                continue
            # Block-scalar marker (description: |) is not what we want
            # for these single-line fields; skip if it appears.
            if raw in ("|", ">", "|-", ">-"):
                continue
            # Strip surrounding quotes.
            if raw.startswith('"') and raw.endswith('"'):
                raw = raw[1:-1]
            elif raw.startswith("'") and raw.endswith("'"):
                raw = raw[1:-1]
            out[key] = raw
    return out


def sync_registry() -> tuple[dict, list[str]]:
    """Returns (new_registry_dict, list_of_change_descriptions)."""
    existing = json.loads(REGISTRY_PATH.read_text())
    changes: list[str] = []
    new_connectors = []
    seen_ids: set[str] = set()

    for entry in existing.get("connectors", []):
        cid = entry.get("id")
        if not cid:
            new_connectors.append(entry)
            continue
        seen_ids.add(cid)
        manifest = ROOT / cid / "manifest.yaml"
        if not manifest.exists():
            print(f"warning: registry entry {cid} has no manifest at {manifest}; keeping as-is",
                  file=sys.stderr)
            new_connectors.append(entry)
            continue
        top = parse_manifest_top(manifest)
        merged = dict(entry)
        for field in ("version", "name"):
            mv = top.get(field)
            if mv and entry.get(field) != mv:
                changes.append(f"{cid}.{field}: {entry.get(field)!r} → {mv!r}")
                merged[field] = mv
        new_connectors.append(merged)

    # Surface any manifest that's on disk but not in the registry —
    # we don't auto-add (descriptions need a human) but the warning
    # tells the maintainer to insert an entry.
    for child in sorted(ROOT.iterdir()):
        if not (child / "manifest.yaml").exists():
            continue
        if child.name not in seen_ids:
            print(f"warning: {child.name}/manifest.yaml exists but has no registry entry "
                  f"— add one to registry.json manually (description is hand-curated)",
                  file=sys.stderr)

    new_registry = {**existing, "connectors": new_connectors}
    return new_registry, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero if registry.json is out of sync.")
    args = ap.parse_args()

    new, changes = sync_registry()
    new_text = json.dumps(new, indent=2) + "\n"
    current = REGISTRY_PATH.read_text()

    if args.check:
        if new_text != current:
            print("registry.json is out of sync with manifests:", file=sys.stderr)
            for c in changes:
                print(f"  - {c}", file=sys.stderr)
            print("Run `python3 tools/build_registry.py` and commit.",
                  file=sys.stderr)
            return 1
        print("registry.json is up to date.")
        return 0

    if new_text == current:
        print("registry.json already in sync — no changes.")
        return 0

    REGISTRY_PATH.write_text(new_text)
    print(f"updated registry.json:")
    for c in changes:
        print(f"  {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
