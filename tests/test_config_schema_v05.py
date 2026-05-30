from dop.config.schema import (
    RuntimeConfig, AppConfig, AppBuildConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
    WorkspaceConfig,
)


def test_app_config_minimal():
    app = AppConfig(name="optum-support-be", service="optum-support-be",
                    role="backend", port=8080)
    assert app.aliases == []
    assert app.depends_on == []
    assert app.url_env is None
    assert app.fallback_url_env is None
    assert app.e2e_suite is None
    assert app.build is None
    assert app.debug_port is None


def test_app_build_config():
    b = AppBuildConfig(dir="repos/optum-support-fe", command="npx vite build")
    assert b.artifact == "dist"


def test_runtime_config_defaults():
    rc = RuntimeConfig()
    assert rc.orchestrator == "docker_compose"
    assert rc.infra == []
    assert rc.default_max_strikes == 3
    assert rc.apps == {}
    assert rc.aliases == {}
    assert rc.docker_compose is None


def test_docker_compose_config_defaults():
    dc = DockerComposeConfig()
    assert dc.compose_file == "docker-compose.yml"
    assert dc.env_files == ["docker/.env"]
    assert dc.project_name == ""
    assert dc.ephemeral_runner is None
    assert dc.clean == {}


def test_ephemeral_runner_config():
    er = EphemeralRunnerConfig(service="playwright-env", profile="e2e")
    assert er.service == "playwright-env"
    assert er.profile == "e2e"


def test_workspace_config_has_runtime():
    ws = WorkspaceConfig(name="test", root="/tmp/test")
    assert isinstance(ws.runtime, RuntimeConfig)
