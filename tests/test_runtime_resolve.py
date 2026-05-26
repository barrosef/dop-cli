from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, AppConfig, FeDependency,
)
from dop.runtime.resolve import expand_apps, infer_urls

def _ws() -> WorkspaceConfig:
    apps = {
        "lifesupport-api": AppConfig(
            name="lifesupport-api", repo="lifesupport-api", kind="java",
            port=8082, debug_port=5005, aliases=["ls"],
        ),
        "optumsupport-be": AppConfig(
            name="optumsupport-be", repo="optumsupport-be", kind="java",
            port=8080, debug_port=5006, aliases=["osb"],
            lifesupport_url_env="LIFESUPPORT_API_URL",
        ),
        "optumsupport-fe": AppConfig(
            name="optumsupport-fe", repo="optumsupport-fe", kind="vite",
            port=5173, aliases=["osf"],
        ),
    }
    fe_deps = [FeDependency(fe="optumsupport-fe", be="optumsupport-be")]
    aliases = {"ls": "lifesupport-api", "osb": "optumsupport-be", "osf": "optumsupport-fe"}
    runtime = RuntimeConfig(apps=apps, fe_deps=fe_deps, aliases=aliases)
    return WorkspaceConfig(name="test", root="/tmp/test", runtime=runtime)

def test_expand_aliases():
    ws = _ws()
    result = expand_apps(ws, ["osf", "osb"])
    assert result == {"optumsupport-fe", "optumsupport-be"}

def test_expand_auto_deps():
    ws = _ws()
    result = expand_apps(ws, ["osf"])
    assert "optumsupport-be" in result
    assert "optumsupport-fe" in result

def test_expand_no_deps():
    ws = _ws()
    result = expand_apps(ws, ["osf"], no_deps=True)
    assert result == {"optumsupport-fe"}

def test_infer_urls_local_lifesupport(monkeypatch):
    monkeypatch.setenv("AZURE_LIFESUPPORT_URL", "https://azure.example.com")
    ws = _ws()
    requested = {"lifesupport-api", "optumsupport-be"}
    env = infer_urls(ws, requested)
    assert env["LIFESUPPORT_URL"] == "http://lifesupport-api:8082"

def test_infer_urls_remote_lifesupport(monkeypatch):
    monkeypatch.setenv("AZURE_LIFESUPPORT_URL", "https://azure.example.com")
    ws = _ws()
    requested = {"optumsupport-be"}
    env = infer_urls(ws, requested)
    assert env["LIFESUPPORT_URL"] == "https://azure.example.com"

def test_expand_unknown_app_raises():
    ws = _ws()
    import pytest
    with pytest.raises(Exception):
        expand_apps(ws, ["unknown-app"])
