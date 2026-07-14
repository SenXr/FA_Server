from __future__ import annotations

from os import PathLike
from pathlib import Path


class InvalidFolderName(ValueError):
    pass


def validate_folder_name(folder_name: str) -> str:
    value = (folder_name or "").strip().replace("\\", "/")
    if not value:
        raise InvalidFolderName("folder_name is required")

    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise InvalidFolderName("folder_name must be a relative folder name")

    return value.strip("/")


def normalize_path_text(value: str | PathLike[str]) -> str:
    return str(value).strip().replace("\\", "/")


def path_from_user_input(value: str | PathLike[str]) -> Path:
    if isinstance(value, Path):
        return value
    return Path(normalize_path_text(value))


def folder_dir(local_root: Path, folder_name: str) -> Path:
    safe_name = validate_folder_name(folder_name)
    return local_root / safe_name


def folder_database_path(local_root: Path, folder_name: str, database_filename: str) -> Path:
    return folder_dir(local_root, folder_name) / database_filename
