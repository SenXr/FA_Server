import os
from typing import Dict, Any, List, Optional
from PIL import Image
import io

from .core.xml_parser import XMLParser, ImageProcessor, FileNameBuilder
from .core.logger_setup import LoggerManager


class Raw2BmpProcessor:

    def __init__(self, log_callback=None):
        self.logger = LoggerManager.setup_logger(
            "Raw2BmpProcessor",
            {},
            log_callback=log_callback,
            log_file_name="process_raw_folder",
            format_type="default",
        )

        self.xml_parser = XMLParser()
        self.image_processor = ImageProcessor()
        self.filename_builder = FileNameBuilder()

        self.result = self._init_result_stats()

    def _log_error(self, message: str):
        self.logger.error(message)

    def _log_info(self, message: str):
        self.logger.info(message)

    def _init_result_stats(self) -> Dict[str, Any]:
        return {
            "success_count": 0,
            "failed_count": 0,
            "total_count": 0,
            "success_files": [],
            "failed_files": [],
            "input_files": [],
            "output_files": "",
        }

    def _find_xml_config(self, input_folder_path: str) -> Optional[str]:

        for root, dirs, files in os.walk(input_folder_path):
            for file in files:
                if file.lower().endswith(".xml"):
                    return os.path.join(root, file)
        return None

    def _find_raw_files(self, input_folder_path: str) -> List[str]:
        raw_files = []
        for root, dirs, files in os.walk(input_folder_path):
            for file in files:
                if file.lower().endswith(".raw"):
                    raw_files.append(os.path.join(root, file))
        return raw_files

    def _load_xml_config(self, input_file_path: str) -> Optional[Dict]:
        xml_path = self._find_xml_config(input_file_path)
        if not xml_path:
            self._log_error(f"No XML configuration file found in {input_file_path}")
            return None

        xml_config = self.xml_parser.parse_xml(xml_path, self.logger)
        if xml_config:
            self._log_info(f"XML configuration loaded from {xml_path}")
        else:
            error_msg = f"Failed to load XML configuration from {xml_path}"
            self._log_error(error_msg)

        return xml_config

    def load_folder_config(self, input_folder_path: str) -> Optional[Dict]:
        return self._load_xml_config(input_folder_path)

    def process_raw_file_with_config(
        self,
        raw_path: str,
        xml_config: Dict,
        folder_name: str,
    ) -> bool:
        self.result["total_count"] += 1
        if raw_path not in self.result["input_files"]:
            self.result["input_files"].append(raw_path)
        return self._process_raw_file(raw_path, xml_config, folder_name)

    def log_processing_summary(self) -> None:
        self._log_info(
            f"Processing completed. Success: {self.result['success_count']}, Failed: {self.result['failed_count']}"
        )

    def _process_raw_file(
        self, raw_path: str, xml_config: Dict, folder_name: str
    ) -> bool:
        try:
            with open(raw_path, "rb") as f:
                raw_data = f.read()

            metadata = self.image_processor.extract_image_metadata(
                raw_data, self.logger
            )
            if not metadata:
                raise ValueError(f"Failed to extract metadata from {raw_path}")

            scan_mode = self.image_processor.determine_scan_mode(
                metadata, xml_config, self.logger
            )

            processed_array = self.image_processor.process_raw_data(
                raw_data, metadata["scan_width"], metadata["scan_dir"], scan_mode
            )

            if processed_array is None:
                raise ValueError(f"Failed to process raw data from {raw_path}")

            naming_component = self.filename_builder.extract_naming_component(
                raw_path, xml_config, metadata, folder_name, self.logger
            )

            gds_num = xml_config.get("gds_num", 1)
            tartget_filename = (
                self.filename_builder.build_filename_gds1(naming_component, metadata)
                if gds_num == 1
                else self.filename_builder.build_filename_gds0(
                    naming_component, metadata, xml_config
                )
            )

            img_buffer = io.BytesIO()
            img = Image.fromarray(processed_array)
            img.save(img_buffer, format="BMP")
            img_bytes = img_buffer.getvalue()
            img_buffer.close()

            output_path = os.path.join(os.path.dirname(raw_path), tartget_filename)
            with open(output_path, "wb") as f:
                f.write(img_bytes)

            self.result["success_count"] += 1
            self.result["success_files"].append(
                {
                    "input_path": raw_path,
                    "output_path": output_path,
                    "filename": tartget_filename,
                }
            )

            self._log_info(f"Successfully processed {tartget_filename} from {raw_path}")

            return True

        except Exception as e:
            self.result["failed_count"] += 1
            self.result["failed_files"].append(
                {"input_path": raw_path, "error": str(e)}
            )
            self._log_error(f"Failed to process {raw_path}: {e}")
            return False

    def process_folder(
        self, input_folder_path: str, output_folder_path: str
    ) -> Dict[str, Any]:
        raw_files = self._find_raw_files(input_folder_path)
        if not raw_files:
            error_msg = f"No RAW files found in {input_folder_path}"
            self._log_error(error_msg)
            self.result["error"] = error_msg
            return self.result

        return self.process_raw_files(raw_files, output_folder_path)

    def process_raw_files(
        self, raw_file_paths: List[str], output_folder_path: str
    ) -> Dict[str, Any]:

        self.result = self._init_result_stats()
        self.result["input_files"] = raw_file_paths
        self.result["output_folder"] = output_folder_path

        if not raw_file_paths:
            error_msg = "No RAW files provided for processing."
            self._log_error(error_msg)
            self.result["error"] = error_msg
            return self.result

        try:
            os.makedirs(output_folder_path, exist_ok=True)
        except Exception as e:
            error_msg = f"Failed to create output folder {output_folder_path}: {e}"
            self._log_error(error_msg)
            self.result["error"] = error_msg
            return self.result

        input_folder_path = os.path.dirname(raw_file_paths[0])

        xml_config = self._load_xml_config(input_folder_path)
        if not xml_config:
            self.result["error"] = (
                f"No XML configuration file found in {input_folder_path}"
            )
            return self.result

        folder_name = os.path.basename(os.path.normpath(input_folder_path))

        for raw_path in raw_file_paths:
            self.process_raw_file_with_config(raw_path, xml_config, folder_name)

        self.log_processing_summary()

        return self.result
