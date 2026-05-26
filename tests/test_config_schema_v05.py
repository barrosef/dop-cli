from dop.config.schema import (
    RuntimeConfig, AppConfig, FeDependency,
    WorkspaceConfig, RepoConfig,
)

def test_app_config_defaults():
    app = AppConfig(
        name="lifesupport-api",
        repo="lifesupport-api",
        kind="java",
        port=8082,
        debug_port=5005,
    )
    assert app.aliases == []
    assert app.dev_cmd is None
    assert app.lifesupport_url_env is None

def test_fe_dependency():
    dep = FeDependency(fe="optum-support-fe", be="optum-support-be")
    assert dep.fe == "optum-support-fe"

def test_runtime_config_defaults():
    rc = RuntimeConfig()
    assert rc.compose_file == "docker-compose.yml"
    assert rc.env_file == "docker/.env"
    assert rc.default_max_strikes == 3
    assert rc.compose_timeout == 300
    assert rc.apps == {}
    assert rc.fe_deps == []
    assert rc.aliases == {}

def test_workspace_config_has_runtime():
    ws = WorkspaceConfig(name="test", root="/tmp/test")
    assert ws.runtime is not None
    assert isinstance(ws.runtime, RuntimeConfig)
