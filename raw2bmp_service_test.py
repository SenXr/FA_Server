from pathlib import Path

import bootstrap
from fa_server.services.raw2bmp_service import Raw2BmpService


# Modify only these values for a manual test.
INPUT_FOLDER = Path("/private/ep5_service/data/rsync_data/V1_B.D_23_123")
RAW_FILE_NAME = "test_001.raw"


def main() -> None:
    raw_path = INPUT_FOLDER / RAW_FILE_NAME
    if not raw_path.is_file():
        raise RuntimeError(f"RAW file does not exist: {raw_path}")

    print(f"Input RAW: {raw_path}")
    final_bmp_path = Raw2BmpService().transcode_and_rename_raw(raw_path)

    print(f"Output BMP: {final_bmp_path}")
    print(f"Exists: {final_bmp_path.is_file()}")


if __name__ == "__main__":
    main()
