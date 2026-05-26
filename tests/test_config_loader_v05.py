import tomllib
from dop.config.loader import _parse_workspace

TOML_WITH_RUNTIME = """
[workspaces.test]
root = "/tmp/test"

[workspaces.test.runtime]
compose_file = "docker-compose.yml"
env_file = "docker/.env"
default_max_strikes = 5
compose_timeout = 180

[workspaces.test.runtime.apps.lifesupport-api]
repo = "lifesupport-api"
kind = "java"
port = 8082
debug_port = 5005
aliases = ["ls"]
lifesupport_url_env = ""

[workspaces.test.runtime.apps.optumsupport-be]
repo = "optumsupport-be"
kind = "java"
port = 8080
debug_port = 5006
aliases = ["osb"]
lifesupport_url_env = "LIFESUPPORT_API_URL"

[workspaces.test.runtime.apps.optumsupport-fe]
repo = "optumsupport-fe"
kind = "vite"
port = 5173
aliases = ["osf"]

[[workspaces.test.runtime.fe_deps]]
fe = "optumsupport-fe"
be = "optumsupport-be"
"""


def test_parse_runtime_section():
    raw = tomllib.loads(TOML_WITH_RUNTIME)
    ws_data = raw["workspaces"]["test"]
    ws = _parse_workspace("test", ws_data)

    assert ws.runtime.default_max_strikes == 5
    assert ws.runtime.compose_timeout == 180
    assert "lifesupport-api" in ws.runtime.apps
    assert ws.runtime.apps["lifesupport-api"].port == 8082
    assert ws.runtime.apps["lifesupport-api"].aliases == ["ls"]
    assert ws.runtime.apps["optumsupport-be"].lifesupport_url_env == "LIFESUPPORT_API_URL"
    assert len(ws.runtime.fe_deps) == 1
    assert ws.runtime.fe_deps[0].fe == "optumsupport-fe"
    assert ws.runtime.aliases == {"ls": "lifesupport-api", "osb": "optumsupport-be", "osf": "optumsupport-fe"}


def test_parse_without_runtime_section():
    raw = tomllib.loads('[workspaces.test]\nroot = "/tmp/test"')
    ws = _parse_workspace("test", raw["workspaces"]["test"])
    assert ws.runtime.default_max_strikes == 3
    assert ws.runtime.apps == {}
