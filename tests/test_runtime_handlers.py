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


def test_publish_allure_project_generates(tmp_path):
    results = tmp_path / "proj" / ".allure-results"
    results.mkdir(parents=True)
    (results / "x-result.json").write_text("{}")
    reports_root = tmp_path / "reports"
    reports_root.mkdir()
    with patch("dop.runtime.handlers.run_command") as rc:
        handlers._publish_allure_project(
            project="aaa-demo", results_dir=results, reports_root=reports_root,
        )
    rc.assert_called_once()
    cmd = rc.call_args.args[0]
    assert cmd[:2] == ["allure", "generate"]
    assert str(reports_root / "aaa-demo") in cmd
    assert "--report-name" in cmd


def test_publish_allure_project_dry_run_skips(tmp_path):
    results = tmp_path / "p" / ".allure-results"
    results.mkdir(parents=True)
    (results / "r.json").write_text("{}")
    with patch("dop.runtime.handlers.run_command") as rc:
        handlers._publish_allure_project(
            project="it-demo", results_dir=results, reports_root=tmp_path / "rep",
            dry_run=True,
        )
    rc.assert_not_called()


def test_publish_allure_project_skips_when_no_results(tmp_path):
    # results_dir does not exist -> generate is skipped, run_command never called
    with patch("dop.runtime.handlers.run_command") as rc:
        handlers._publish_allure_project(
            project="aaa-empty",
            results_dir=tmp_path / "missing" / ".allure-results",
            reports_root=tmp_path / "reports",
        )
    rc.assert_not_called()


def test_handle_report_clean_uses_test_root(tmp_path):
    ws = _ws()
    ws.root = str(tmp_path)
    ws.test_root = "test/e2e"
    runs = tmp_path / "test/e2e/reports/SUOPT-1/optum-support-fe"
    for n in range(1, 8):
        (runs / f"run-{n}").mkdir(parents=True)
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=MagicMock()):
        rc = handlers.handle_report(ws, _args(report_action="clean", keep=5), dry_run=False)
    assert rc == 0
    # 7 runs, keep last 5 -> 2 oldest removed -> 5 remain (only works if clean
    # resolved the path under test/e2e/reports, i.e. honored ws.test_root)
    assert sum(1 for _ in runs.iterdir()) == 5
