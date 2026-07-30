from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from raw2bmp import Raw2BmpProcessor


DEFAULT_MANIFEST_FILENAME = "raw_file_manifest.xml"


@dataclass(frozen=True)
class RawFileManifest:
    path: Path
    expected_files: tuple[str, ...]

    @property
    def expected_count(self) -> int:
        return len(self.expected_files)

    @property
    def expected_final_bmp_names(self) -> set[str]:
        names: set[str] = set()
        for file_name in self.expected_files:
            source = Path(file_name)
            if source.suffix.lower() == ".bmp" and source.stem.endswith("T"):
                names.add(source.name)
            else:
                names.add(f"{source.stem}T.bmp")
        return names


class Raw2BmpService:
    def __init__(
        self,
        processor_factory: Callable[[], Raw2BmpProcessor] = Raw2BmpProcessor,
    ):
        self.processor_factory = processor_factory

    def create_task_session(self, folder: Path) -> "Raw2BmpTaskSession":
        return Raw2BmpTaskSession(self.processor_factory(), folder)

    def transcode_and_rename_raw(self, raw_path: Path) -> Path:
        session = self.create_task_session(raw_path.parent)
        try:
            return session.transcode_and_rename_raw(raw_path)
        finally:
            session.finish()


class Raw2BmpTaskSession:
    def __init__(self, processor: Raw2BmpProcessor, folder: Path):
        self.processor = processor
        self.folder = folder
        self.xml_config: dict | None = None
        self.folder_name = folder.name
        self.processed_count = 0
        self.finished = False

    def transcode_and_rename_raw(self, raw_path: Path) -> Path:
        self._ensure_config()
        assert self.xml_config is not None

        try:
            success_before = len(self.processor.result.get("success_files", []))
            failed_before = len(self.processor.result.get("failed_files", []))
            processed = self.processor.process_raw_file_with_config(
                str(raw_path),
                self.xml_config,
                self.folder_name,
            )
            self.processed_count += 1

            if not processed:
                failed_files = self.processor.result.get("failed_files", [])
                if len(failed_files) > failed_before:
                    raise RuntimeError(
                        failed_files[-1].get(
                            "error",
                            "RAW to BMP conversion failed",
                        )
                    )
                raise RuntimeError("RAW to BMP conversion failed")

            success_files = self.processor.result.get("success_files", [])
            if len(success_files) <= success_before:
                raise RuntimeError("RAW to BMP conversion produced no output")

            output_path = Path(success_files[-1]["output_path"])
            if not output_path.exists():
                raise RuntimeError(
                    f"RAW to BMP output file not found: {output_path}"
                )
            return output_path
        finally:
            self._clear_file_details()

    def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        if self.processed_count:
            self.processor.log_processing_summary()

    def _ensure_config(self) -> None:
        if self.xml_config is not None:
            return

        self.folder.mkdir(parents=True, exist_ok=True)
        self.xml_config = self.processor.load_folder_config(str(self.folder))
        assert self.xml_config is not None, (
            f"RAW to BMP XML configuration was not loaded from {self.folder}"
        )
        if not self.xml_config:
            raise RuntimeError(f"No XML configuration file found in {self.folder}")

    def _clear_file_details(self) -> None:
        for key in ("success_files", "failed_files", "input_files"):
            values = self.processor.result.get(key)
            if isinstance(values, list):
                values.clear()


def read_raw_manifest(folder: Path) -> RawFileManifest | None:
    manifest_path = folder / DEFAULT_MANIFEST_FILENAME
    if not manifest_path.exists():
        return None

    try:
        root = ET.parse(manifest_path).getroot()
    except ET.ParseError:
        return None

    file_names: list[str] = []
    for element in root.findall(".//file"):
        name = element.attrib.get("name")
        if name:
            file_names.append(Path(name).name)

    if not file_names:
        total = root.attrib.get("total")
        if total and total.isdigit():
            file_names = [f"{index:06d}.raw" for index in range(1, int(total) + 1)]

    return RawFileManifest(path=manifest_path, expected_files=tuple(file_names))


def sync_manifest_complete(root: Path, manifest: RawFileManifest) -> bool:
    if manifest.expected_count == 0:
        return False
    existing = {
        path.name
        for path in root.glob("*.bmp")
        if path.is_file() and path.stem.endswith("T")
    }
    return manifest.expected_final_bmp_names.issubset(existing)
