from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

import bootstrap
from fa_server.services.sync_service import (
    cleanup_redundant_files,
    cleanup_source_artifacts,
    conversion_source_extensions,
    discover_data_files,
)
from fa_server.services.raw2bmp_service import Raw2BmpService


class Raw2BmpIntegrationTests(unittest.TestCase):
    def test_raw2bmp_task_session_loads_config_once_and_summarizes_once(self):
        class FakeProcessor:
            def __init__(self):
                self.load_count = 0
                self.summary_count = 0
                self.result = {
                    "success_files": [],
                    "failed_files": [],
                    "input_files": [],
                    "total_count": 0,
                    "success_count": 0,
                    "failed_count": 0,
                }

            def load_folder_config(self, input_folder_path: str) -> dict:
                self.load_count += 1
                return {"xml_path": str(Path(input_folder_path) / "raw_file_manifest.xml")}

            def process_raw_file_with_config(
                self,
                raw_path: str,
                xml_config: dict,
                folder_name: str,
            ) -> bool:
                del xml_config, folder_name
                source = Path(raw_path)
                output = source.with_name(f"{source.stem}T.bmp")
                output.write_bytes(b"bmp")
                self.result["success_files"].append({"output_path": str(output)})
                self.result["success_count"] += 1
                self.result["total_count"] += 1
                return True

            def log_processing_summary(self) -> None:
                self.summary_count += 1

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            processor = FakeProcessor()
            session = Raw2BmpService(lambda: processor).create_task_session(root)
            first = root / "image_001.raw"
            second = root / "image_002.raw"
            first.write_bytes(b"raw")
            second.write_bytes(b"raw")

            session.transcode_and_rename_raw(first)
            session.transcode_and_rename_raw(second)
            session.finish()
            session.finish()

            self.assertEqual(1, processor.load_count)
            self.assertEqual(1, processor.summary_count)
            self.assertEqual(2, processor.result["success_count"])

    def test_raw_conversion_uses_folder_manifest_and_returns_final_bmp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "image.raw"
            raw_path.write_bytes(bytes(range(64)))
            write_manifest(root, "image.raw")

            final_path = Raw2BmpService().transcode_and_rename_raw(raw_path)

            self.assertEqual("imageT.bmp", final_path.name)
            self.assertTrue(final_path.exists())

    def test_raw_conversion_preserves_encoded_image_saved_with_raw_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "image.raw"
            source = Image.new("L", (4, 4))
            source.putdata(list(range(16)))
            source.save(raw_path, format="BMP")
            write_manifest(root, "image.raw")

            final_path = Raw2BmpService().transcode_and_rename_raw(raw_path)
            with Image.open(BytesIO(final_path.read_bytes())) as opened:
                size = opened.size
                converted = opened.convert("L")
                try:
                    pixels = list(converted.tobytes())
                finally:
                    converted.close()

            self.assertEqual((4, 4), size)
            self.assertEqual(list(range(16)), pixels)

    def test_raw_conversion_preserves_encoded_color_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "color.raw"
            source = Image.new("RGB", (2, 2))
            expected_pixels = [
                (255, 0, 0),
                (0, 255, 0),
                (0, 0, 255),
                (255, 255, 0),
            ]
            source.putdata(expected_pixels)
            source.save(raw_path, format="BMP")
            write_manifest(root, "color.raw")

            final_path = Raw2BmpService().transcode_and_rename_raw(raw_path)
            with Image.open(BytesIO(final_path.read_bytes())) as opened:
                size = opened.size
                converted = opened.convert("RGB")
                try:
                    data = converted.tobytes()
                    pixels = [
                        tuple(data[index : index + 3])
                        for index in range(0, len(data), 3)
                    ]
                finally:
                    converted.close()

            self.assertEqual((2, 2), size)
            self.assertEqual(expected_pixels, pixels)

    def test_raw_conversion_cleanup_leaves_manifest_and_final_t_bmp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "raw_test_001.raw"
            redundant_bmp = root / "raw_test_001.bmp"
            raw_path.write_bytes(bytes(range(64)))
            redundant_bmp.write_bytes(b"bmp")
            write_manifest(root, "raw_test_001.raw")

            cleanup_redundant_files(root)
            final_path = Raw2BmpService().transcode_and_rename_raw(raw_path)
            cleanup_source_artifacts(raw_path, final_path)

            self.assertEqual(
                ["raw_file_manifest.xml", "raw_test_001T.bmp"],
                sorted(path.name for path in root.iterdir()),
            )

    def test_cleanup_removes_resynced_sources_when_final_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "raw_test_001.raw").write_bytes(b"raw")
            (root / "raw_test_001.bmp").write_bytes(b"bmp")
            (root / "raw_test_001T.bmp").write_bytes(b"final")
            (root / "raw_test_001_convertedT.bmp").write_bytes(b"bad")

            cleanup_redundant_files(root)

            self.assertEqual(["raw_test_001T.bmp"], sorted(path.name for path in root.iterdir()))

    def test_cleanup_removes_tmp_files_and_discovery_skips_them(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tmp_path = root / "test_001_abcd.tmp"
            raw_path = root / "test_001.raw"
            tmp_path.write_bytes(b"partial")
            raw_path.write_bytes(b"raw")

            before_cleanup = discover_data_files(root, (".raw", ".tmp"))
            cleanup_redundant_files(root)

            self.assertEqual([raw_path], before_cleanup)
            self.assertFalse(tmp_path.exists())
            self.assertEqual([raw_path], discover_data_files(root, (".raw", ".tmp")))

    def test_discovery_skips_manifest_files_even_if_extension_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "test_001.raw"
            manifest_path = root / "raw_file_manifest.xml"
            manifest_artifact = root / "raw_file_manifestT.bmp"
            typo_manifest_artifact = root / "raw_file_mainifestT.bmp"
            raw_path.write_bytes(b"raw")
            manifest_path.write_text("<raw_image_manifest />", encoding="utf-8")
            manifest_artifact.write_bytes(b"bad")
            typo_manifest_artifact.write_bytes(b"bad")

            discovered = discover_data_files(root, (".raw", ".xml", ".bmp"))

            self.assertEqual([raw_path], discovered)

    def test_cleanup_removes_manifest_bmp_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "raw_file_manifest.xml"
            manifest_artifact = root / "raw_file_manifestT.bmp"
            typo_manifest_artifact = root / "raw_file_mainifestT.bmp"
            final_image = root / "raw_test_001T.bmp"
            manifest_path.write_text("<raw_image_manifest />", encoding="utf-8")
            manifest_artifact.write_bytes(b"bad")
            typo_manifest_artifact.write_bytes(b"bad")
            final_image.write_bytes(b"final")

            cleanup_redundant_files(root)

            self.assertEqual(
                ["raw_file_manifest.xml", "raw_test_001T.bmp"],
                sorted(path.name for path in root.iterdir()),
            )

    def test_conversion_source_extensions_keep_only_raw(self):
        self.assertEqual((".raw",), conversion_source_extensions((".raw", ".bmp", ".xml")))
        self.assertEqual((".raw",), conversion_source_extensions((".bmp", ".xml")))


def write_manifest(root: Path, raw_name: str) -> None:
    (root / "raw_file_manifest.xml").write_text(
        f"""<?xml version='1.0' encoding='utf-8'?>
<raw_image_manifest total="1">
  <files><file index="1" name="{raw_name}" /></files>
</raw_image_manifest>
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
