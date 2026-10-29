"""Small helpers shared across the pipeline."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path


def hms(seconds: float) -> str:
    """00:00:00 -- used for every timestamp shown to the reader."""
    s = int(max(seconds, 0))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def subtitle_ts(seconds: float, sep: str) -> str:
    ms = int(round(max(seconds, 0) * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d}{sep}{ms % 1000:03d}"


def human_duration(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def safe_filename(text: str, limit: int = 80) -> str:
    """Windows-safe file/folder name that still reads like the original title."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:limit].rstrip(" .") or "untitled"


def free_bytes(path: Path) -> int:
    path = Path(path)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def gib(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"


def write_json(path: Path, data) -> None:
    """Write atomically so an interrupted run never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
