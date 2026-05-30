import argparse
from unittest.mock import MagicMock, patch
import pytest
from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, AppConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
)
from dop.runtime import handlers


def _ws() -> WorkspaceConfig:
    apps = {
        "optum-support-be": AppConfig(name="optum-support-be", service="optum-support-be",
                                      role="backend", port=8080,
                                      url_env="OPTUM_SUPPORT_BE_URL"),
        "optum-support-fe": AppConfig(name="optum-support-fe", service="optum-support-fe",
                                      role="frontend", port=5173, aliases=["osf"],
                                      depends_on=["optum-support-be"],
                                      e2e_suite="optum-support-fe"),
    }
    dc = DockerComposeConfig(
        ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"),
        clean={"maven": ["m2-cache"]},
    )
    rt = RuntimeConfig(apps=apps, aliases={"osf": "optum-support-fe"},
                       infra=["mongodb", "allure"], docker_compose=dc)
    return WorkspaceConfig(name="t", root="/tmp/ws", runtime=rt)


def _args(**kw):
    return argparse.Namespace(**kw)


def test_handle_stop_delegates_to_provider():
    ws = _ws()
    provider = MagicMock()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_stop(ws, _args(apps=["osf"]), dry_run=True)
    assert rc == 0
    provider.stop.assert_called_once()


def test_handle_clean_maps_flags_to_categories():
    ws = _ws()
    provider = MagicMock()
    provider.clean.return_value = ["m2-cache"]
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_clean(ws, _args(m2=True, node_modules=False, allure=False, all=False), dry_run=True)
    assert rc == 0
    provider.clean.assert_called_once_with(["maven"], dry_run=True, logger=None)


def test_handle_clean_no_flags_raises():
    ws = _ws()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=MagicMock()):
        with pytest.raises(Exception):
            handlers.handle_clean(ws, _args(m2=False, node_modules=False, allure=False, all=False))


def test_suite_urls_from_config():
    ws = _ws()
    assert handlers._suite_base_url(ws, "optum-support-fe") == "http://localhost:5173"
    assert handlers._suite_api_url(ws, "optum-support-fe") == "http://localhost:8080"
