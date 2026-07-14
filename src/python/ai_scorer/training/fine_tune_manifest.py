from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from typing import Any


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(paths: list[str]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.encode("utf-8"))
        h.update(file_sha256(path).encode("utf-8"))
    return h.hexdigest()


def collect_jsonl_paths(dataset_dir: str) -> list[str]:
    names = ["train.jsonl", "val.jsonl"]
    paths: list[str] = []
    for name in names:
        path = os.path.join(dataset_dir, name)
        if os.path.isfile(path):
            paths.append(path)
    return paths


def current_git_sha() -> str:
    try:
        output = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        return output
    except Exception:
        return "unknown"


def write_manifest(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def now_epoch() -> int:
    return int(time.time())
