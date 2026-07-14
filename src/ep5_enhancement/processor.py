from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import torch
except ImportError:  # pragma: no cover - production dependency is optional for demo mode.
    torch = None

from .ep5_enhancement import UNetGANConfig, batch_process_progressive, load_generator


def load_model(model_path: str, config: UNetGANConfig | None = None):
    if config is None:
        config = UNetGANConfig()

    model_file = Path(model_path)
    if not model_file.is_absolute():
        raise ValueError("please provide an absolute path for the model.")
    if not model_file.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    global_stats_path = model_file.parent / "global_stats.pt"
    if (
        torch is not None
        and config.USE_NORMALIZE
        and config.NORMALIZE_MEAN == "global_zsocre"
        and global_stats_path.exists()
        and global_stats_path.stat().st_size > 0
    ):
        global_stats = torch.load(global_stats_path, map_location="cpu")
        config.GLOBAL_STATS = global_stats
        config.GLOBAL_MEAN = global_stats["mean"].numpy().squeeze()
        config.GLOBAL_STD = global_stats["std"].numpy().squeeze()

    return load_generator(model_file, config=config), config


def batch_process(
    input_paths: list[str],
    output_path: str,
    model: Any = None,
    gamma: float | None = None,
    config: UNetGANConfig | None = None,
) -> list[Path]:
    if not input_paths:
        return []
    if model is None:
        raise RuntimeError("Model must be provided for batch processing.")

    if config is None:
        config = getattr(model, "config", UNetGANConfig())
    if gamma is not None:
        config.GAMMA = gamma

    image_suffix = (".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff")
    files = [Path(path) for path in input_paths]
    unsupported = [
        str(path)
        for path in files
        if path.suffix.lower() not in image_suffix
    ]
    if unsupported:
        raise ValueError(f"Unsupported image suffix for super-resolution: {unsupported}")

    input_root = os.path.dirname(str(files[0]))
    return batch_process_progressive(
        input_root,
        files,
        output_path,
        image_suffix,
        model,
        config=config,
    )
