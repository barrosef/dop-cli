import argparse
from unittest.mock import MagicMock, patch

from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, AppConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
)
from dop.runtime import handlers
from dop.runtime.handlers import _merge_allure_results


def _ws(root) -> WorkspaceConfig:
    apps = {
        "optum-support-be": AppConfig(name="optum-support-be", service="optum-support-be",
                                      role="backend", port=8080),
        "optum-support-fe": AppConfig(name="optum-support-fe", service="optum-support-fe",
                                      role="frontend", port=5173,
                                      depends_on=["optum-support-be"],
                                      e2e_suite="optum-support-fe"),
    }
    dc = DockerComposeConfig(
        ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"),
    )
    rt = RuntimeConfig(apps=apps, infra=["mongodb", "allure"], docker_compose=dc)
    return WorkspaceConfig(name="t", root=str(root), runtime=rt)


# ---- P1: merge/aggregate allure results ----

def test_merge_accumulates(tmp_path):
    src = tmp_path / "run-1" / "results"
    src.mkdir(parents=True)
    (src / "b-result.json").write_text("{}")
    dest = tmp_path / ".allure-results"
    dest.mkdir()
    (dest / "a-result.json").write_text("{}")

    n = _merge_allure_results(src, dest, fresh=False)
    assert n == 1
    assert {f.name for f in dest.iterdir()} == {"a-result.json", "b-result.json"}


def test_merge_fresh_resets(tmp_path):
    src = tmp_path / "run-1" / "results"
    src.mkdir(parents=True)
    (src / "b-result.json").write_text("{}")
    dest = tmp_path / ".allure-results"
    dest.mkdir()
    (dest / "a-result.json").write_text("{}")

    n = _merge_allure_results(src, dest, fresh=True)
    assert n == 1
    assert {f.name for f in dest.iterdir()} == {"b-result.json"}


def test_merge_missing_source(tmp_path):
    src = tmp_path / "nope" / "results"
    dest = tmp_path / ".allure-results"
    n = _merge_allure_results(src, dest, fresh=False)
    assert n == 0
    assert dest.is_dir()  # criado mesmo sem fonte


# ---- P3: host pre-creates run dir (host-owned) ----

def test_handle_e2e_precreates_host_run_dir(tmp_path):
    ws = _ws(tmp_path)
    (tmp_path / "e2e" / "optum-support-fe" / "tests").mkdir(parents=True)

    provider = MagicMock()
    provider.run_ephemeral.return_value = 0
    args = argparse.Namespace(targets=["optum-support-fe"], k=None, headed=False, fresh_report=False)

    # dry_run=False para exercitar a criação real do dir; allure não está instalado,
    # mas como não há resultados a geração é pulada (sem chamar o binário).
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_e2e(ws, args, dry_run=False)

    assert rc == 0
    run_results = tmp_path / "e2e" / "reports" / "manual" / "optum-support-fe" / "run-1" / "results"
    assert run_results.is_dir(), "host deve pré-criar run-N/results (P3)"


def test_handle_e2e_aggregates_run_into_suite(tmp_path):
    """P1 ponta-a-ponta: o resultado da run é copiado para <suite>/.allure-results."""
    ws = _ws(tmp_path)
    (tmp_path / "e2e" / "optum-support-fe" / "tests").mkdir(parents=True)

    def fake_run(args, *, env, dry_run=False, logger=None):
        # simula o container gravando um result no alluredir (run-1/results)
        rd = tmp_path / "e2e" / "reports" / "manual" / "optum-support-fe" / "run-1" / "results"
        (rd / "x-result.json").write_text("{}")
        return 0

    provider = MagicMock()
    provider.run_ephemeral.side_effect = fake_run
    args = argparse.Namespace(targets=["optum-support-fe"], k=None, headed=False, fresh_report=False)

    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_e2e(ws, args, dry_run=False)

    assert rc == 0
    aggregated = tmp_path / "e2e" / "optum-support-fe" / ".allure-results" / "x-result.json"
    assert aggregated.is_file(), "P1: resultado da run deve ser agregado no .allure-results da suíte"
