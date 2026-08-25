from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import Database


class WebAppTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "tracker.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dashboard_assets_exist(self):
        web_dir = Path(__file__).parents[1] / "web"
        index = (web_dir / "index.html").read_text()
        self.assertIn("Zenon Pillar Tracker", index)
        self.assertIn('/static/icons/apple-touch-icon.png', index)
        self.assertIn('/static/icons/favicon-32x32.png', index)
        self.assertIn('/static/icons/favicon-16x16.png', index)
        self.assertIn('/static/icons/site.webmanifest', index)
        static_dir = web_dir.parents[0] / "static" / "icons"
        for filename in (
            "apple-touch-icon.png",
            "favicon-16x16.png",
            "favicon-32x32.png",
            "favicon.ico",
            "site.webmanifest",
        ):
            self.assertTrue((static_dir / filename).exists())
        styles = (web_dir / "styles.css").read_text().lower()
        self.assertIn("@media (min-width: 680px)", styles)
        self.assertIn("nth-child(n + 3)", styles)
        self.assertIn(".skeleton-card { min-height: 380px; }", styles)
        self.assertIn(".loading-skeleton-grid { grid-column: 1 / -1; }", styles)
        self.assertIn("height: 60px;", styles)
        self.assertIn("min-height: 60px;", styles)
        self.assertIn("margin: 18px 0;", styles)
        self.assertIn(".content-grid > .section-block { margin-top: 0; }", styles)
        self.assertIn("--syrius-green", styles)
        self.assertNotIn("--sirius-green", styles)
        app = (web_dir / "app.js").read_text()
        self.assertIn("/api/overview", app)
        self.assertIn("Inactive for", app)
        self.assertIn("missed checks", app)
        self.assertIn("Performance (30 days)", app)
        self.assertIn("class=\"stat-item\"", app)
        self.assertIn("class=\"stat-item performance-stat\"", app)
        self.assertIn("Produced / expected", app)
        self.assertIn("Momentum / delegate", app)
        self.assertIn("formatEpoch", app)
        self.assertIn("epoch.epoch_start_at", app)
        self.assertNotIn("epoch.announcement_at || epoch.last_seen_at", app)
        self.assertIn("renderPerformanceChart", app)
        index_content = (web_dir / "index.html").read_text()
        self.assertIn("skeleton-stat-grid", index_content)
        self.assertEqual(index_content.count('class="skeleton-card"'), 4)
        self.assertIn("Total pillars", index_content)
        self.assertIn("Active pillars", index_content)
        self.assertIn("Inactive pillars", index_content)
        self.assertLess(
            index_content.index('id="epoch-list"'),
            index_content.index('id="pillar-list"'),
        )
        self.assertLess(
            index_content.index('id="event-list"'),
            index_content.index('id="pillar-list"'),
        )

    def test_overview_contract_is_json_serializable(self):
        overview = self.database.get_overview()
        self.assertEqual(overview["pillar_counts"]["total"], 0)
        self.assertIn("node", overview)
        self.assertIn("recent_events", overview)


if __name__ == "__main__":
    unittest.main()
