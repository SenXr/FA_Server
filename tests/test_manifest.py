from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bootstrap
from fa_server.services.raw2bmp_service import read_raw_manifest


class ManifestTests(unittest.TestCase):
    def test_reads_raw_file_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "raw_file_manifest.xml"
            manifest_path.write_text(
                """<?xml version='1.0' encoding='utf-8'?>
<raw_image_manifest total="2">
  <files>
    <file index="1" name="test_001.raw" />
    <file index="2" name="test_002.raw" />
  </files>
</raw_image_manifest>
""",
                encoding="utf-8",
            )

            manifest = read_raw_manifest(root)

            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(2, manifest.expected_count)
            self.assertEqual(
                {"test_001T.bmp", "test_002T.bmp"},
                manifest.expected_final_bmp_names,
            )


if __name__ == "__main__":
    unittest.main()
