from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from utils.env_loader import load_env_file


class EnvLoaderTestCase(unittest.TestCase):
    def test_loads_comments_quotes_and_export_entries(self):
        variable_names = {
            "ZENON_TEST_TOKEN",
            "ZENON_TEST_QUOTED",
            "ZENON_TEST_EXPORTED",
        }
        previous = {name: os.environ.get(name) for name in variable_names}
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                env_path = Path(temp_dir) / ".env"
                env_path.write_text(
                    "# comment\n"
                    "ZENON_TEST_TOKEN=abc123\n"
                    "ZENON_TEST_QUOTED=\"hello world\"\n"
                    "export ZENON_TEST_EXPORTED='enabled'\n",
                    encoding="utf-8",
                )

                load_env_file(env_path)

                self.assertEqual(os.environ["ZENON_TEST_TOKEN"], "abc123")
                self.assertEqual(os.environ["ZENON_TEST_QUOTED"], "hello world")
                self.assertEqual(os.environ["ZENON_TEST_EXPORTED"], "enabled")
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
