from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentFilesTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    def test_docker_image_is_unprivileged_and_health_checked(self) -> None:
        dockerfile = self.read("Dockerfile")
        self.assertIn("python:3.14.5-slim-bookworm", dockerfile)
        self.assertIn("USER tracker", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/app/data_store", dockerfile)

    def test_compose_runs_web_and_collector_with_persistent_data(self) -> None:
        compose = self.read("compose.yaml")
        self.assertIn("web:", compose)
        self.assertIn("collector:", compose)
        self.assertIn("${DATA_DIR:-./data_store}:/app/data_store", compose)
        self.assertIn("./control:/run/zenon-control", compose)
        self.assertIn("restart: unless-stopped", compose)
        self.assertIn("max-size: 10m", compose)

    def test_systemd_unit_manages_compose_without_ubuntu_specific_paths(self) -> None:
        unit = self.read("deploy/systemd/zenon-pillar-tracker.service")
        self.assertIn("docker compose", unit)
        self.assertIn(".deploy-image.env", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertNotIn("ubuntu", unit.lower())

    def test_deploy_script_persists_the_selected_image_tag(self) -> None:
        script = self.read("deploy/bin/deploy.sh")
        self.assertIn(".deploy-image.env", script)
        self.assertIn("printf 'IMAGE=%s", script)
        self.assertIn("docker compose ps --status running --services", script)
        self.assertIn("docker compose logs --no-color --tail=200 web collector", script)

    def test_collector_control_bridge_is_allowlisted_and_installed_separately(self) -> None:
        bridge = self.read("deploy/bin/collector_control_bridge.py")
        self.assertIn('"status", "logs", "start", "stop", "restart"', bridge)
        self.assertNotIn("shell=True", bridge)
        unit = self.read("deploy/systemd/zenon-pillar-tracker-control.service")
        self.assertIn("collector_control_bridge.py", unit)
        self.assertIn("Group=10001", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        dev_unit = self.read("deploy/systemd/zenon-pillar-tracker-dev-control.service")
        self.assertIn("/srv/zenon-pillar-tracker-dev", dev_unit)

    def test_development_deployment_is_isolated(self) -> None:
        environment = self.read("deploy/examples/development.env.example")
        unit = self.read("deploy/systemd/zenon-pillar-tracker-dev.service")
        nginx = self.read("deploy/nginx/pillartracker.turmin.com.conf")
        self.assertIn("WEB_PORT=8081", environment)
        self.assertIn("/srv/zenon-pillar-tracker-dev/data_store", environment)
        self.assertIn("WorkingDirectory=/srv/zenon-pillar-tracker-dev", unit)
        self.assertIn("127.0.0.1:8081", nginx)
        self.assertIn("server_name pillartracker.turmin.com", nginx)

    def test_workflow_tests_both_platforms_builds_arm64_and_deploys_main(self) -> None:
        workflow = self.read(".github/workflows/ci-cd.yml")
        self.assertIn("Tests (Windows)", workflow)
        self.assertIn("Tests (Debian ARM64 container)", workflow)
        self.assertIn('python-version: "3.14.5"', workflow)
        self.assertIn("python:3.14.5-slim-bookworm", workflow)
        self.assertIn("channel=development", workflow)
        self.assertIn("environment: ${{ github.ref == 'refs/heads/development'", workflow)
        self.assertIn("zenon-pillar-tracker-dev.service", workflow)
        self.assertIn("linux/arm64", workflow)
        self.assertIn("refs/heads/main", workflow)
        self.assertIn("deploy/bin/deploy.sh", workflow)
        self.assertIn("deploy/bin/collector_control_bridge.py", workflow)
        self.assertIn("zenon-pillar-tracker-control.service", workflow)

    def test_requirements_are_utf8(self) -> None:
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("requests==", requirements)


if __name__ == "__main__":
    unittest.main()
