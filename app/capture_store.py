from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any


class CaptureStore:
    def __init__(self, capture_dir: Path) -> None:
        self.capture_dir = capture_dir
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.latest_path = self.capture_dir / "latest.json"

    def read_latest(self) -> dict[str, Any] | None:
        if not self.latest_path.exists():
            return None
        try:
            return json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def write_latest(self, metadata: dict[str, Any]) -> None:
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.latest_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.latest_path)

    def file_for_id(self, capture_id: str) -> Path | None:
        latest = self.read_latest()
        if latest and latest.get("id") == capture_id:
            file_path = latest.get("file_path")
            if isinstance(file_path, str) and Path(file_path).exists():
                return Path(file_path)

        candidates = sorted(
            (
                path
                for path in self.capture_dir.iterdir()
                if path.is_file()
                and capture_id in path.stem
                and path.name != self.latest_path.name
            ),
            key=lambda path: _file_preference(path),
        )
        return candidates[0] if candidates else None


def media_type_for(path: Path) -> str:
    media_type, _ = mimetypes.guess_type(path.name)
    return media_type or "application/octet-stream"


def _file_preference(path: Path) -> tuple[int, str]:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return (0, path.name)
    if suffix == ".nef":
        return (1, path.name)
    return (2, path.name)
