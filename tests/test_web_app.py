from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Thread
from urllib.request import urlopen
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader, select_autoescape

from models.database import Database
from controllers.web_controller import (
    ApiRateLimiter,
    DashboardHandler,
    DashboardServer,
    PAGE_TEMPLATE_CONTEXT,
)


class WebAppTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "tracker.sqlite3")
        self.templates_dir = Path(__file__).parents[1] / "templates"
        self.template_environment = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def render_page(self, page_name: str) -> str:
        return self.template_environment.get_template(page_name).render(
            **PAGE_TEMPLATE_CONTEXT[page_name]
        )

    def test_dashboard_assets_exist(self):
        templates_dir = self.templates_dir
        base = (templates_dir / "base.html").read_text()
        index = self.render_page("index.html")
        self.assertIn("Zenon Pillar Tracker", index)
        self.assertIn('/static/icons/favicon-180.png', index)
        self.assertIn('/static/icons/favicon-32.png', index)
        self.assertIn('/static/icons/favicon-16.png', index)
        self.assertIn('/static/icons/site.webmanifest', index)
        for favicon in (
            '/static/icons/favicon-16.png',
            '/static/icons/favicon-32.png',
            '/static/icons/favicon-48.png',
            '/static/icons/favicon-180.png',
            '/static/icons/favicon.ico',
            '/static/icons/site.webmanifest',
        ):
            self.assertEqual(base.count(favicon), 1)
        self.assertIn('{% include "header.html" %}', base)
        self.assertIn('{% include "footer.html" %}', base)
        self.assertIn('fa-brands fa-github', index)
        self.assertIn('fa-brands fa-telegram', index)
        styles = (templates_dir / "styles.css").read_text().lower()
        self.assertIn(".site-footer-link:first-child:hover", styles)
        self.assertIn(".site-footer-link:last-child:hover", styles)
        self.assertIn("color: #0fbf3e;", styles)
        self.assertIn("color: #0088cc;", styles)
        self.assertIn('fa-solid fa-floppy-disk', self.render_page("portal.html"))
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
        index_content = index
        self.assertIn('class="site-header dashboard-header"', index_content)
        self.assertIn('href="/portal"', index_content)
        self.assertIn(">Login<", index_content)
        self.assertNotIn('href="/account"', index_content)
        self.assertNotIn('href="/admin"', index_content)
        for page_name in ("login.html", "setup.html", "portal.html"):
            page = self.render_page(page_name)
            self.assertIn('/static/icons/favicon-32.png', page)
            self.assertIn('/static/icons/site.webmanifest', page)
        for page_name in ("epochs.html", "events.html", "pillars.html"):
            page = self.render_page(page_name)
            self.assertIn('/static/icons/favicon-32.png', page)
            self.assertIn('/static/icons/site.webmanifest', page)
        for page_name in (
            "index.html", "login.html", "setup.html", "portal.html",
            "epochs.html", "events.html", "pillars.html",
        ):
            raw_page = (templates_dir / page_name).read_text()
            self.assertNotIn('/static/icons/favicon-', raw_page)
        self.assertNotIn('class="page-shell narrow-shell history-page"', self.render_page("epochs.html"))
        self.assertNotIn('class="page-shell narrow-shell history-page"', self.render_page("events.html"))
        self.assertIn('href="/epochs"', index_content)
        self.assertIn('href="/events"', index_content)
        self.assertIn('fa-circle-arrow-right', index_content)
        self.assertIn('id="pillars-link"', index_content)
        self.assertIn('id="pillars-link" class="section-link section-icon-link"', index_content)
        self.assertNotIn('>View all <', index_content)
        self.assertIn('" pillar" + (total === 1 ? "" : "s")', app)
        self.assertIn('data-history-kind="epochs"', self.render_page("epochs.html"))
        self.assertIn('data-history-kind="events"', self.render_page("events.html"))
        epochs_js = (templates_dir / "history.js").read_text()
        self.assertNotIn("ZNN reward", epochs_js)
        self.assertNotIn("QSR reward", epochs_js)
        self.assertNotIn("Last seen", epochs_js)
        self.assertNotIn("First seen", epochs_js)
        self.assertNotIn("Momentum height", epochs_js)
        self.assertNotIn("Transition evidence", epochs_js)
        self.assertNotIn("Reward announcement", epochs_js)
        self.assertIn("Epoch transition", epochs_js)
        epochs_page = self.render_page("epochs.html")
        self.assertIn("Epoch timeline", epochs_page)
        self.assertNotIn("The transition time is", epochs_page)
        self.assertIn("fa-circle-arrow-left", epochs_page)
        pillars_page = self.render_page("pillars.html")
        self.assertNotIn("Use the status links", pillars_page)
        self.assertIn("Pillar directory", pillars_page)
        self.assertIn('href="/pillars?status=active"', pillars_page)
        self.assertIn('href="/pillars?status=inactive"', pillars_page)
        history_js = (templates_dir / "history.js").read_text()
        self.assertIn("offset", history_js)
        self.assertIn("pagination", history_js)
        pillars_js = (templates_dir / "pillars.js").read_text()
        self.assertIn("history.pushState", pillars_js)
        setup = self.render_page("setup.html")
        self.assertIn('id="setup-form"', setup)
        self.assertIn("/api/setup/admin", (templates_dir / "setup.js").read_text())
        portal = self.render_page("portal.html")
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
        self.assertIn('class="collector-status-card"', portal)
        self.assertIn('id="logs-preview-viewer"', portal)
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
        login = self.render_page("login.html")
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
        self.assertIn("/api/admin/logs?limit=${LOG_FETCH_LIMIT}&lines=${LOG_FETCH_LIMIT}", portal_js)
        self.assertIn("LOG_REFRESH_INTERVAL_MS = 10000", portal_js)
        self.assertIn("window.setInterval", portal_js)
        self.assertIn("parseAuditLogs", portal_js)
        self.assertIn("latest entries", portal_js)
        self.assertNotIn("latest ${LOG_FETCH_LIMIT}", portal_js)
        self.assertIn("last_error", portal_js)
        self.assertIn("renderCollectorDiagnostics", portal_js)
        self.assertIn("settings-live-note", portal)
        self.assertNotIn("collector-start", portal)
        self.assertNotIn("collector-stop", portal)
        self.assertNotIn("collector-restart", portal)
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
        live_settings_note = portal.index('class="muted settings-live-note"')
        runtime_bottom_save = portal.index('form="settings-form"', live_settings_note)
        self.assertLess(live_settings_note, runtime_bottom_save)
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

    def test_web_server_renders_html_templates_and_keeps_assets_static(self):
        try:
            server = DashboardServer(
                ("127.0.0.1", 0),
                DashboardHandler,
                self.database,
                self.templates_dir,
                self.templates_dir.parent / "static",
                ApiRateLimiter(max_requests=0),
                {},
            )
        except PermissionError as exc:
            self.skipTest(f"Local socket creation is unavailable: {exc}")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            with urlopen(f"http://{host}:{port}/epochs", timeout=5) as response:
                rendered = response.read().decode("utf-8")
                self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
                self.assertIn("Epoch history", rendered)
                self.assertIn('/static/icons/favicon-48.png', rendered)
                self.assertIn('fa-brands fa-github', rendered)
                self.assertIn('fa-brands fa-telegram', rendered)
            with urlopen(f"http://{host}:{port}/app.js", timeout=5) as response:
                javascript_content_type = response.headers["Content-Type"].split(";", 1)[0]
                self.assertIn(
                    javascript_content_type,
                    {"application/javascript", "text/javascript"},
                )
                self.assertIn("/api/overview", response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

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
