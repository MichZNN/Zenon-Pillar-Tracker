from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from models.database import Database
from controllers.web_controller import DashboardHandler


class WebAppTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "tracker.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dashboard_assets_exist(self):
        templates_dir = Path(__file__).parents[1] / "templates"
        index = (templates_dir / "index.html").read_text()
        self.assertIn("Zenon Pillar Tracker", index)
        self.assertIn('/static/icons/favicon-180.png', index)
        self.assertIn('/static/icons/favicon-32.png', index)
        self.assertIn('/static/icons/favicon-16.png', index)
        self.assertIn('/static/icons/site.webmanifest', index)
        static_dir = templates_dir.parents[0] / "static" / "icons"
        for filename in (
            "favicon-180.png",
            "favicon-16.png",
            "favicon-32.png",
            "favicon.ico",
            "site.webmanifest",
        ):
            self.assertTrue((static_dir / filename).exists())
        styles = (templates_dir / "styles.css").read_text().lower()
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
        self.assertIn("font-size: 1.4rem;", styles)
        self.assertIn("flex: 0 0 42px;", styles)
        self.assertNotIn(".narrow-shell", styles)
        self.assertIn("min-height: 100svh;", styles)
        self.assertIn("padding: 20px 20px 60px;", styles)
        app = (templates_dir / "app.js").read_text()
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
        self.assertIn("epoch.epoch_start_inferred", app)
        self.assertIn("epoch-estimated", app)
        self.assertNotIn("epoch.announcement_at || epoch.last_seen_at", app)
        self.assertIn("renderPerformanceChart", app)
        self.assertIn("/api/collector-status", app)
        self.assertIn("/api/performance?days=30", app)
        self.assertIn("&performance=0", app)
        self.assertNotIn('healthy: "Node online"', app)
        index_content = (templates_dir / "index.html").read_text()
        self.assertIn('class="site-header dashboard-header"', index_content)
        self.assertIn('href="/portal"', index_content)
        self.assertIn(">Login<", index_content)
        self.assertNotIn('href="/account"', index_content)
        self.assertNotIn('href="/admin"', index_content)
        for page_name in ("login.html", "setup.html", "portal.html"):
            page = (templates_dir / page_name).read_text()
            self.assertIn('/static/icons/favicon-32.png', page)
            self.assertIn('/static/icons/site.webmanifest', page)
        for page_name in ("epochs.html", "events.html", "pillars.html"):
            page = (templates_dir / page_name).read_text()
            self.assertIn('/static/icons/favicon-32.png', page)
            self.assertIn('/static/icons/site.webmanifest', page)
        self.assertNotIn('class="page-shell narrow-shell history-page"', (templates_dir / "epochs.html").read_text())
        self.assertNotIn('class="page-shell narrow-shell history-page"', (templates_dir / "events.html").read_text())
        self.assertIn('href="/epochs"', index_content)
        self.assertIn('href="/events"', index_content)
        self.assertIn('fa-circle-arrow-right', index_content)
        self.assertIn('id="pillars-link"', index_content)
        self.assertIn('id="pillars-link" class="section-link section-icon-link"', index_content)
        self.assertNotIn('>View all <', index_content)
        self.assertIn('data-history-kind="epochs"', (templates_dir / "epochs.html").read_text())
        self.assertIn('data-history-kind="events"', (templates_dir / "events.html").read_text())
        epochs_js = (templates_dir / "history.js").read_text()
        self.assertNotIn("ZNN reward", epochs_js)
        self.assertNotIn("QSR reward", epochs_js)
        self.assertNotIn("Last seen", epochs_js)
        self.assertNotIn("First seen", epochs_js)
        self.assertNotIn("Momentum height", epochs_js)
        self.assertNotIn("Transition evidence", epochs_js)
        self.assertNotIn("Reward announcement", epochs_js)
        self.assertIn("Epoch transition", epochs_js)
        self.assertIn("Epoch timeline", (templates_dir / "epochs.html").read_text())
        self.assertNotIn("The transition time is", (templates_dir / "epochs.html").read_text())
        self.assertIn("fa-circle-arrow-left", (templates_dir / "epochs.html").read_text())
        pillars_page = (templates_dir / "pillars.html").read_text()
        self.assertIn('href="/pillars?status=active"', pillars_page)
        self.assertIn('href="/pillars?status=inactive"', pillars_page)
        history_js = (templates_dir / "history.js").read_text()
        self.assertIn("offset", history_js)
        self.assertIn("pagination", history_js)
        pillars_js = (templates_dir / "pillars.js").read_text()
        self.assertIn("history.pushState", pillars_js)
        setup = (templates_dir / "setup.html").read_text()
        self.assertIn('id="setup-form"', setup)
        self.assertIn("/api/setup/admin", (templates_dir / "setup.js").read_text())
        portal = (templates_dir / "portal.html").read_text()
        self.assertIn('class="site-header portal-header"', portal)
        self.assertIn('id="user-menu-toggle"', portal)
        self.assertIn('id="user-menu-dropdown"', portal)
        self.assertIn('id="account-menu-link"', portal)
        self.assertIn('id="account-modal" class="modal-backdrop" hidden', portal)
        self.assertIn('id="close-account-modal"', portal)
        self.assertIn('aria-modal="true"', portal)
        self.assertIn('id="account-form" class="form-grid compact-form account-form" autocomplete="off"', portal)
        self.assertIn('name="current_password" type="password" autocomplete="off"', portal)
        self.assertIn('id="subscription-form" class="form-grid subscription-form"', portal)
        self.assertIn('id="admin-subscription-form" class="form-grid subscription-form"', portal)
        self.assertIn('class="subscription-table subscription-table-own"', portal)
        self.assertIn('class="subscription-table subscription-table-admin"', portal)
        self.assertIn('id="admin-area"', portal)
        self.assertIn('id="collector-start"', portal)
        self.assertIn('id="collector-stop"', portal)
        self.assertIn('id="collector-restart"', portal)
        self.assertIn('id="collector-container-log"', portal)
        self.assertIn('id="open-logs-modal"', portal)
        self.assertIn('id="logs-modal"', portal)
        self.assertIn('id="log-source-filter"', portal)
        self.assertIn('id="log-level-filter"', portal)
        self.assertIn('id="log-search"', portal)
        self.assertIn('id="refresh-modal-logs"', portal)
        self.assertIn('id="collector-data-status"', portal)
        self.assertIn('id="collector-last-error"', portal)
        self.assertIn('id="user-subscriptions-section" class="section-block" hidden', portal)
        self.assertIn("My subscriptions", portal)
        self.assertIn('name="discord_webhook"', portal)
        self.assertIn('class="admin-layout"', portal)
        self.assertIn('class="admin-menu"', portal)
        self.assertIn('id="admin-menu-toggle"', portal)
        self.assertIn('id="admin-menu-links"', portal)
        self.assertIn('data-admin-nav', portal)
        section_ids = (
            "runtime-settings",
            "subscription-settings",
            "user-settings",
            "session-settings-card",
            "log-settings-card",
            "operations-settings",
        )
        for section_id in section_ids:
            self.assertIn(f'id="{section_id}"', portal)
        menu_positions = [portal.index(f'href="#{section_id}"') for section_id in section_ids]
        self.assertEqual(menu_positions, sorted(menu_positions))
        self.assertIn('/static/vendor/font-awesome/7.3.1/css/all.min.css', portal)
        self.assertIn('id="settings-form"', portal)
        self.assertIn('id="logging-settings-form"', portal)
        self.assertIn("Fixed location: <code>data_store/pillar_tracker.log</code>", portal)
        self.assertNotIn('name="log_path"', portal)
        self.assertIn('id="session-settings-form"', portal)
        self.assertIn('id="toast-container"', portal)
        self.assertIn("Save runtime settings", portal)
        self.assertIn("Save log settings", portal)
        self.assertIn("Save session settings", portal)
        self.assertIn('name="node_rpc_urls"', portal)
        self.assertIn("http://127.0.0.1:35997", portal)
        self.assertIn('name="auth_session_hours"', portal)
        self.assertIn('value="pillar_inactive"', portal)
        self.assertIn('value="reward_shares_changed"', portal)
        self.assertNotIn('id="settings-json"', portal)
        self.assertNotIn("Settings are stored in SQLite", portal)
        portal_js = (templates_dir / "portal.js").read_text()
        login = (templates_dir / "login.html").read_text()
        self.assertIn('placeholder="Username"', login)
        self.assertIn('placeholder="Password"', login)
        self.assertIn('id="toggle-login-password"', login)
        self.assertIn("form-stack .auth-input-wrap input", styles)
        self.assertIn("--control-height: 42px", styles)
        self.assertIn("--control-radius: 9px", styles)
        self.assertIn(".health-badge { height: var(--control-height);", styles)
        self.assertIn(".user-menu-chevron { flex: 0 0 auto;", styles)
        self.assertIn(".user-name { flex: 0 1 auto;", styles)
        self.assertIn("text-align: left", styles)
        self.assertIn("left: 0;", styles)
        self.assertIn("width: max-content", styles)
        self.assertIn(".portal-header .user-menu { justify-self: start;", styles)
        self.assertIn("border-radius: var(--control-radius)", styles)
        self.assertIn("initialiseUserMenu", portal_js)
        self.assertIn("initialiseAccountModal", portal_js)
        self.assertIn("setAccountModalOpen(true)", portal_js)
        self.assertIn("requireSubscriptionDestination", portal_js)
        self.assertIn("function destinationTypes", portal_js)
        self.assertIn("function eventSummary", portal_js)
        self.assertIn("class=\"table-info", portal_js)
        self.assertIn('data-label="Events"', portal_js)
        self.assertIn("/api/admin/collector-control", portal_js)
        self.assertIn("/api/admin/collector-logs?tail=${LOG_FETCH_LIMIT}", portal_js)
        self.assertIn("/api/admin/logs?limit=${LOG_FETCH_LIMIT}&lines=${LOG_FETCH_LIMIT}", portal_js)
        self.assertIn("LOG_REFRESH_INTERVAL_MS = 10000", portal_js)
        self.assertIn("window.setInterval", portal_js)
        self.assertIn("parseCollectorLogs", portal_js)
        self.assertIn("parseAuditLogs", portal_js)
        self.assertIn("controlCollector", portal_js)
        self.assertIn("last_error", portal_js)
        self.assertIn("renderCollectorDiagnostics", portal_js)
        self.assertIn("selectedEvents", portal_js)
        self.assertIn("readSettingsForm", portal_js)
        self.assertIn("initialiseAdminNavigation", portal_js)
        self.assertIn("setMenuOpen", portal_js)
        self.assertIn('aria-expanded', portal_js)
        self.assertIn('$("#user-subscriptions-section").hidden = false', portal_js)
        self.assertIn("IntersectionObserver", portal_js)
        self.assertIn("showToast", portal_js)
        self.assertIn("validationNotified", portal_js)
        self.assertIn("TOAST_DURATION_MS = 10000", portal_js)
        self.assertIn("container.append(toast)", portal_js)
        self.assertNotIn("container.replaceChildren(toast)", portal_js)
        self.assertIn('step="0.1"', portal)
        self.assertIn(".admin-menu", styles)
        self.assertIn(".admin-layout", styles)
        self.assertIn(".admin-menu-toggle", styles)
        self.assertIn(".subscription-table .table-info::after", styles)
        self.assertIn("width: max-content", styles)
        self.assertIn(".subscription-table thead", styles)
        self.assertIn(".dashboard-header", styles)
        self.assertIn(".portal-header", styles)
        self.assertIn("justify-content: flex-end", styles)
        self.assertIn("transition: transform 10s linear", styles)
        self.assertEqual(portal.count("Save runtime settings"), 2)
        self.assertEqual(portal.count("Save log settings"), 1)
        self.assertEqual(portal.count("Save session settings"), 1)
        restart_note = portal.index('class="muted settings-restart-note"')
        runtime_bottom_save = portal.index('form="settings-form"', restart_note)
        self.assertLess(restart_note, runtime_bottom_save)
        self.assertIn('src="/portal.js"', portal)
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

    def test_successful_read_access_is_not_logged(self):
        handler = object.__new__(DashboardHandler)
        handler.address_string = lambda: "127.0.0.1"
        with patch("controllers.web_controller.logger") as logger:
            handler.log_message('"%s" %s %s', "GET /app.js HTTP/1.1", "200", "42")
            handler.log_message('"%s" %s %s', "POST /api/auth/login HTTP/1.1", "200", "42")
            logger.info.assert_called_once()
            logger.warning.assert_not_called()
            logger.error.assert_not_called()

    def test_chrome_devtools_probe_is_handled_without_a_not_found(self):
        handler = object.__new__(DashboardHandler)
        handler.path = "/.well-known/appspecific/com.chrome.devtools.json"
        handler._send_json = lambda payload: setattr(handler, "probe_payload", payload)

        handler.do_GET()

        self.assertEqual(handler.probe_payload, {})


if __name__ == "__main__":
    unittest.main()
