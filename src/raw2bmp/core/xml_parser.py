from __future__ import annotations

import math
import os
from io import BytesIO
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


class XMLParser:
    def parse_xml(self, xml_path: str, logger=None) -> dict[str, Any] | None:
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as exc:
            if logger:
                logger.error("Failed to parse XML %s: %s", xml_path, exc)
            return None

        files: dict[str, dict[str, str]] = {}
        for node in root.findall(".//file"):
            name = node.attrib.get("name")
            if not name:
                continue
            files[name] = dict(node.attrib)

        try:
            gds_num = int(root.attrib.get("gds_num", root.attrib.get("gds", "1")))
        except ValueError:
            gds_num = 1

        return {
            "xml_path": xml_path,
            "root_tag": root.tag,
            "gds_num": gds_num,
            "files": files,
        }


class ImageProcessor:
    def extract_image_metadata(self, raw_data: bytes, logger=None) -> dict[str, Any]:
        if not raw_data:
            if logger:
                logger.error("RAW data is empty.")
            return {}

        pixel_count = len(raw_data)
        width = int(math.sqrt(pixel_count))
        if width <= 0:
            width = 1
        height = math.ceil(pixel_count / width)
        return {
            "scan_width": width,
            "scan_height": height,
            "scan_dir": "forward",
            "dtype": "uint8",
        }

    def determine_scan_mode(
        self,
        metadata: dict[str, Any],
        xml_config: dict[str, Any],
        logger=None,
    ) -> str:
        del metadata, xml_config, logger
        return "normal"

    def process_raw_data(
        self,
        raw_data: bytes,
        scan_width: int,
        scan_dir: str,
        scan_mode: str,
    ) -> np.ndarray:
        del scan_dir, scan_mode
        encoded_image = self._try_decode_encoded_image(raw_data)
        if encoded_image is not None:
            return encoded_image

        width = max(1, int(scan_width))
        height = math.ceil(len(raw_data) / width)
        padded = raw_data.ljust(width * height, b"\x00")
        return np.frombuffer(padded, dtype=np.uint8).reshape((height, width))

    def _try_decode_encoded_image(self, raw_data: bytes) -> np.ndarray | None:
        try:
            with Image.open(BytesIO(raw_data)) as image:
                return np.asarray(image.convert("RGB"))
        except Exception:
            return None


class FileNameBuilder:
    def extract_naming_component(
        self,
        raw_path: str,
        xml_config: dict[str, Any],
        metadata: dict[str, Any],
        folder_name: str,
        logger=None,
    ) -> dict[str, Any]:
        del metadata, folder_name, logger
        raw_name = os.path.basename(raw_path)
        raw_stem = Path(raw_name).stem
        file_config = xml_config.get("files", {}).get(raw_name, {})
        return {
            "raw_name": raw_name,
            "raw_stem": raw_stem,
            "file_config": file_config,
        }

    def build_filename_gds1(
        self,
        naming_component: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        del metadata
        return self._build_default_filename(naming_component)

    def build_filename_gds0(
        self,
        naming_component: dict[str, Any],
        metadata: dict[str, Any],
        xml_config: dict[str, Any],
    ) -> str:
        del metadata, xml_config
        return self._build_default_filename(naming_component)

    def _build_default_filename(self, naming_component: dict[str, Any]) -> str:
        raw_stem = naming_component["raw_stem"]
        file_config = naming_component.get("file_config", {})
        output_name = file_config.get("output") or file_config.get("bmp_name")
        if output_name:
            return output_name

        x = file_config.get("x")
        y = file_config.get("y")
        if x is not None and y is not None:
            return f"{raw_stem}_x{x}_y{y}.bmp"

        return f"{raw_stem}T.bmp"
