from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


def valid_workspace_manifest(value: Any, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, dict) or value.get("complete") is not True:
        return False
    files = value.get("files")
    if not isinstance(files, list) or (not allow_empty and not files) or len(files) > 2000:
        return False
    if type(value.get("file_count")) is not int or value["file_count"] != len(files):
        return False
    if type(value.get("total_size_bytes")) is not int:
        return False
    captured_at = value.get("captured_at_unix")
    if not isinstance(captured_at, (int, float)) or not math.isfinite(captured_at) or captured_at <= 0:
        return False
    if value.get("errors") not in ([], None):
        return False

    total = 0
    seen_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            return False
        path = item.get("path")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or len(path) > 2048
            or path.startswith("/")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or path in seen_paths
            or type(size) is not int
            or size < 0
            or size > 10 * 1024 * 1024
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            return False
        seen_paths.add(path)
        total += size
    return total <= 100 * 1024 * 1024 and value["total_size_bytes"] == total


def build_workspace_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return path/hash changes between two complete, validated workspace snapshots."""
    before_files = {entry["path"]: entry for entry in before["files"]}
    after_files = {entry["path"]: entry for entry in after["files"]}

    created = [after_files[path] for path in sorted(after_files.keys() - before_files.keys())]
    deleted = [before_files[path] for path in sorted(before_files.keys() - after_files.keys())]
    modified = []
    for path in sorted(before_files.keys() & after_files.keys()):
        old = before_files[path]
        new = after_files[path]
        if old["sha256"] != new["sha256"] or old["size_bytes"] != new["size_bytes"]:
            modified.append({
                "path": path,
                "before_size_bytes": old["size_bytes"],
                "before_sha256": old["sha256"],
                "after_size_bytes": new["size_bytes"],
                "after_sha256": new["sha256"],
            })

    delta_content = {
        "created": created,
        "modified": modified,
        "deleted": deleted,
        "baseline_captured_at_unix": before["captured_at_unix"],
        "verified_captured_at_unix": after["captured_at_unix"],
    }
    sha256_delta_digest = hashlib.sha256(
        json.dumps(delta_content, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return {
        "complete": True,
        "baseline_captured_at_unix": before["captured_at_unix"],
        "verified_captured_at_unix": after["captured_at_unix"],
        "created": created,
        "modified": modified,
        "deleted": deleted,
        "counts": {
            "created": len(created),
            "modified": len(modified),
            "deleted": len(deleted),
        },
        "sha256_delta_digest": sha256_delta_digest,
        "scope": "path and SHA-256 delta; file contents are not included",
    }
