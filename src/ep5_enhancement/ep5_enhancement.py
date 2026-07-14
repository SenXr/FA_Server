from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter


@dataclass
class UNetGANConfig:
    USE_NORMALIZE: bool = False
    NORMALIZE_MEAN: str = "none"
    GAMMA: float = 1.0
    GLOBAL_STATS: Any = None
    GLOBAL_MEAN: float = 0.0
    GLOBAL_STD: float = 0.0


@dataclass
class DemoEnhancementModel:
    model_path: Path
    config: UNetGANConfig


def load_generator(model_path: str | Path, config: UNetGANConfig) -> DemoEnhancementModel:
    return DemoEnhancementModel(model_path=Path(model_path), config=config)


def batch_process_progressive(
    input_root: str | Path,
    files: list[str | Path],
    output_path: str | Path,
    image_suffix: tuple[str, ...],
    model: DemoEnhancementModel,
    *,
    config: UNetGANConfig,
) -> list[Path]:
    del input_root, image_suffix, model
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    for source in files:
        source_path = Path(source)
        target = output_dir / f"{source_path.stem}_sr{source_path.suffix}"
        _enhance_image(source_path, target, gamma=config.GAMMA)
        outputs.append(target)
    return outputs


def _enhance_image(source_path: Path, target_path: Path, *, gamma: float) -> None:
    with Image.open(source_path) as image:
        enhanced = image.convert("RGB")
        enhanced = enhanced.filter(ImageFilter.SHARPEN)
        enhanced = ImageEnhance.Contrast(enhanced).enhance(max(0.1, gamma))
        enhanced.save(target_path)
