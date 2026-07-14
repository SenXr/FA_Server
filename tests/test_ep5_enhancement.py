from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

import bootstrap
from ep5_enhancement import UNetGANConfig, batch_process, load_model


class EP5EnhancementTests(unittest.TestCase):
    def test_load_model_requires_absolute_existing_model_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir).resolve() / "best_model.pth"
            model_path.write_bytes(b"demo")

            model, config = load_model(str(model_path), config=UNetGANConfig())

            self.assertEqual(model_path, model.model_path)
            self.assertIsInstance(config, UNetGANConfig)

    def test_batch_process_accepts_three_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_path = root.resolve() / "best_model.pth"
            model_path.write_bytes(b"demo")
            model, config = load_model(str(model_path), config=UNetGANConfig())

            paths = []
            for index in range(3):
                path = root / f"image_{index}.bmp"
                Image.new("L", (8, 8), color=120 + index).save(path)
                paths.append(str(path.resolve()))

            outputs = batch_process(
                input_paths=paths,
                output_path=str(root / "Super_Resolution"),
                model=model,
                config=config,
            )

            self.assertEqual(3, len(outputs))
            self.assertEqual(
                ["image_0_sr.bmp", "image_1_sr.bmp", "image_2_sr.bmp"],
                [path.name for path in outputs],
            )
            self.assertTrue(all(path.exists() for path in outputs))


if __name__ == "__main__":
    unittest.main()
