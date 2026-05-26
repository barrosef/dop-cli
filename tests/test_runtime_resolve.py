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
        "optum-support-be": AppConfig(
            name="optum-support-be", repo="optum-support-be", kind="java",
            port=8080, debug_port=5006, aliases=["osb"],
            lifesupport_url_env="LIFESUPPORT_API_URL",
        ),
        "optum-support-fe": AppConfig(
            name="optum-support-fe", repo="optum-support-fe", kind="vite",
            port=5173, aliases=["osf"],
        ),
    }
    fe_deps = [FeDependency(fe="optum-support-fe", be="optum-support-be")]
    aliases = {"ls": "lifesupport-api", "osb": "optum-support-be", "osf": "optum-support-fe"}
    runtime = RuntimeConfig(apps=apps, fe_deps=fe_deps, aliases=aliases)
    return WorkspaceConfig(name="test", root="/tmp/test", runtime=runtime)

def test_expand_aliases():
    ws = _ws()
    result = expand_apps(ws, ["osf", "osb"])
    assert result == {"optum-support-fe", "optum-support-be"}

def test_expand_auto_deps():
    ws = _ws()
    result = expand_apps(ws, ["osf"])
    assert "optum-support-be" in result
    assert "optum-support-fe" in result

def test_expand_no_deps():
    ws = _ws()
    result = expand_apps(ws, ["osf"], no_deps=True)
    assert result == {"optum-support-fe"}

def test_infer_urls_local_lifesupport(monkeypatch):
    monkeypatch.setenv("AZURE_LIFESUPPORT_URL", "https://azure.example.com")
    ws = _ws()
    requested = {"lifesupport-api", "optum-support-be"}
    env = infer_urls(ws, requested)
    assert env["LIFESUPPORT_URL"] == "http://lifesupport-api:8082"

def test_infer_urls_remote_lifesupport(monkeypatch):
    monkeypatch.setenv("AZURE_LIFESUPPORT_URL", "https://azure.example.com")
    ws = _ws()
    requested = {"optum-support-be"}
    env = infer_urls(ws, requested)
    assert env["LIFESUPPORT_URL"] == "https://azure.example.com"

def test_expand_unknown_app_raises():
    ws = _ws()
    import pytest
    with pytest.raises(Exception):
        expand_apps(ws, ["unknown-app"])
