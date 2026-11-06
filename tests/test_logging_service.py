from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import logging_service


class LoggingServiceTestCase(unittest.TestCase):
    @staticmethod
    def _close_logging_handlers():
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, logging_service._HANDLER_MARKER, False):
                root.removeHandler(handler)
                handler.close()

    def tearDown(self):
        self._close_logging_handlers()

    def test_resolve_log_path_ignores_legacy_setting(self):
        with patch.object(
            logging_service,
            "DEFAULT_LOG_PATH",
            Path("/fixed/data_store/pillar_tracker.log"),
        ):
            self.assertEqual(
                logging_service.resolve_log_path(
                    {"log_path": "/tmp/legacy/pillar_tracker.log"}
                ),
                Path("/fixed/data_store/pillar_tracker.log"),
            )

    def test_read_log_tail_uses_fixed_data_store_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixed_path = root / "data_store" / "pillar_tracker.log"
            fixed_path.parent.mkdir()
            fixed_path.write_text(
                "old diagnostic\nnew diagnostic\n",
                encoding="utf-8",
            )

            with patch.object(logging_service, "DEFAULT_LOG_PATH", fixed_path):
                result = logging_service.read_log_tail(
                    {"log_path": str(root / "legacy" / "pillar_tracker.log")},
                )

            self.assertEqual(result["path"], str(fixed_path))
            self.assertTrue(result["exists"])
            self.assertEqual(result["lines"], ["new diagnostic", "old diagnostic"])
            self.assertNotIn("configured_path", result)

    def test_configure_logging_always_uses_fixed_data_store_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                root = Path(temp_dir)
                fixed_path = root / "data_store" / "pillar_tracker.log"
                ensured_paths = []

                def ensure(path: Path) -> None:
                    ensured_paths.append(path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch(exist_ok=True)

                with (
                    patch.object(logging_service, "DEFAULT_LOG_PATH", fixed_path),
                    patch.object(
                        logging_service,
                        "_ensure_log_file",
                        side_effect=ensure,
                    ),
                ):
                    result = logging_service.configure_logging(
                        {"log_path": str(root / "legacy" / "pillar_tracker.log")},
                    )

                self.assertTrue(result["file_enabled"])
                self.assertEqual(result["path"], str(fixed_path))
                self.assertEqual(ensured_paths, [fixed_path])
                self.assertTrue(fixed_path.exists())
            finally:
                # Windows cannot remove a file while RotatingFileHandler still
                # owns it. TemporaryDirectory cleanup happens before tearDown.
                self._close_logging_handlers()


if __name__ == "__main__":
    unittest.main()


class LogRangeTestCase(unittest.TestCase):
    def test_reads_rotated_files_for_a_period(self):
        from datetime import datetime, timezone
        import tempfile
        from pathlib import Path
        from services.logging_service import read_log_range

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pillar_tracker.log"
            Path(str(path) + ".1").write_text(
                "2026-11-04T10:00:00+0000 INFO [x] old one\n"
                "2026-11-05T10:00:00+0000 INFO [x] yesterday\n",
                encoding="utf-8",
            )
            path.write_text(
                "2026-11-06T08:00:00+0000 ERROR [x] today failed\n"
                "Traceback (most recent call last):\n"
                "2026-11-06T09:00:00+0000 INFO [x] today ok\n",
                encoding="utf-8",
            )
            result = read_log_range(
                datetime(2026, 11, 5, tzinfo=timezone.utc),
                datetime(2026, 11, 6, 8, 30, tzinfo=timezone.utc),
                path=path,
            )
            self.assertEqual(result["files"], 2)
            self.assertEqual(result["oldest_available_at"], "2026-11-04T10:00:00+00:00")
            self.assertEqual(len(result["lines"]), 2)
            self.assertIn("today failed\nTraceback", result["lines"][0])
            self.assertIn("yesterday", result["lines"][1])
            limited = read_log_range(None, None, lines=1, path=path)
            self.assertTrue(limited["truncated"])
            self.assertIn("today ok", limited["lines"][0])
