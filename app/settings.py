from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


VALID_CAPTURE_FORMATS = {"jpeg", "nef", "nef+jpeg"}


@dataclass(frozen=True)
class Settings:
    capture_dir: Path
    capture_format: str
    camera_model: str
    gphoto2: str
    sips: str
    host: str
    port: int
    gphoto_timeout: int

    @classmethod
    def from_env(cls) -> "Settings":
        capture_format = os.getenv("D3500_CAPTURE_FORMAT", "jpeg").strip().lower()
        if capture_format not in VALID_CAPTURE_FORMATS:
            raise ValueError(
                "D3500_CAPTURE_FORMAT must be one of: "
                + ", ".join(sorted(VALID_CAPTURE_FORMATS))
            )

        return cls(
            capture_dir=Path(os.getenv("D3500_CAPTURE_DIR", "./captures"))
            .expanduser()
            .resolve(),
            capture_format=capture_format,
            camera_model=os.getenv("D3500_CAMERA_MODEL", "Nikon DSC D3500"),
            gphoto2=os.getenv(
                "D3500_GPHOTO2",
                _first_existing("/opt/homebrew/bin/gphoto2", "gphoto2"),
            ),
            sips=os.getenv("D3500_SIPS", _first_existing("/usr/bin/sips", "sips")),
            host=os.getenv("D3500_HOST", "127.0.0.1"),
            port=int(os.getenv("D3500_PORT", "8000")),
            gphoto_timeout=int(os.getenv("D3500_GPHOTO_TIMEOUT", "60")),
        )

    def as_dict(self) -> dict[str, str | int]:
        return {
            "capture_dir": str(self.capture_dir),
            "capture_format": self.capture_format,
            "camera_model": self.camera_model,
            "gphoto2": self.gphoto2,
            "sips": self.sips,
            "host": self.host,
            "port": self.port,
            "gphoto_timeout": self.gphoto_timeout,
        }


def _first_existing(preferred: str, fallback: str) -> str:
    if Path(preferred).exists():
        return preferred
    return shutil.which(fallback) or fallback
