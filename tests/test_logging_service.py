from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import logging_service


class LoggingServiceTestCase(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, logging_service._HANDLER_MARKER, False):
                root.removeHandler(handler)
                handler.close()

    def test_read_log_tail_uses_data_directory_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured = root / "legacy" / "pillar_tracker.log"
            fallback = root / "data_store" / "pillar_tracker.log"
            fallback.parent.mkdir()
            fallback.write_text("fallback diagnostic\n", encoding="utf-8")

            with patch.object(logging_service, "DEFAULT_LOG_PATH", fallback):
                result = logging_service.read_log_tail(
                    {"log_path": str(configured)},
                )

            self.assertEqual(result["path"], str(fallback))
            self.assertEqual(result["configured_path"], str(configured))
            self.assertTrue(result["exists"])
            self.assertEqual(result["lines"], ["fallback diagnostic"])
            self.assertIn("fallback", result["error"])

    def test_configure_logging_falls_back_when_configured_path_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured = root / "legacy" / "pillar_tracker.log"
            fallback = root / "data_store" / "pillar_tracker.log"
            real_ensure = logging_service._ensure_log_file

            def ensure(path: Path) -> None:
                if path == configured:
                    raise OSError("read-only test path")
                real_ensure(path)

            with (
                patch.object(logging_service, "DEFAULT_LOG_PATH", fallback),
                patch.object(
                    logging_service,
                    "_ensure_log_file",
                    side_effect=ensure,
                ),
            ):
                result = logging_service.configure_logging(
                    {"log_path": str(configured)},
                )

            self.assertTrue(result["file_enabled"])
            self.assertEqual(result["path"], str(fallback))
            self.assertIn("using fallback", result["error"])
            self.assertTrue(fallback.exists())


if __name__ == "__main__":
    unittest.main()
