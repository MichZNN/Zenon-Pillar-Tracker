from __future__ import annotations

import unittest

from tools.epoch_schedule import calculate_epoch_start


class EpochScheduleTestCase(unittest.TestCase):
    def test_reference_epoch_start_is_preserved(self):
        self.assertEqual(
            calculate_epoch_start(1627),
            "2026-05-10T13:30:00+00:00",
        )

    def test_epoch_1732_is_105_days_after_epoch_1627(self):
        self.assertEqual(
            calculate_epoch_start(1732),
            "2026-08-23T13:30:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
