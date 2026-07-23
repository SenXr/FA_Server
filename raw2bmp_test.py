from pathlib import Path

from src.raw2bmp import Raw2BmpProcessor


# Modify only these values for a manual test.
INPUT_FOLDER = Path("/private/ep5_service/data/rsync_data/V1_B.D_23_123")
OUTPUT_FOLDER = Path("/private/ep5_service/data/V1_B.D_23_123")
MAX_FILES = 3


def log_callback(message: str) -> None:
    print(f"[raw2bmp] {message}")


def main() -> None:
    raw_files = sorted(INPUT_FOLDER.rglob("*.raw"))
    if MAX_FILES:
        raw_files = raw_files[:MAX_FILES]

    if not raw_files:
        raise RuntimeError(f"No RAW files found in: {INPUT_FOLDER}")

    print(f"Input folder : {INPUT_FOLDER}")
    print(f"Output folder: {OUTPUT_FOLDER}")
    print(f"RAW files    : {[str(path) for path in raw_files]}")

    processor = Raw2BmpProcessor(log_callback=log_callback)
    result = processor.process_raw_files(
        [str(path) for path in raw_files],
        str(OUTPUT_FOLDER),
    )

    print("\nResult:")
    print(result)

    print("\nOutput check:")
    for item in result.get("success_files", []):
        output_path = Path(item["output_path"])
        print(f"{output_path} exists={output_path.is_file()}")


if __name__ == "__main__":
    main()
