from __future__ import annotations

from pathlib import Path


class FolderMonitor:
    """Small helper for deployment code that needs to list local RAW inputs."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def list_raw_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(path for path in self.root.rglob("*.raw") if path.is_file())
