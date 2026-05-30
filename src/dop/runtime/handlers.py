# src/dop/runtime/handlers.py
from __future__ import annotations
import os
from pathlib import Path

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError
from ..core.process import run_command
from .resolve import expand_apps, infer_urls
from .compose import write_env_runtime
from .orchestrator import build_runtime_provider


# --------------------------------------------------------------------------
# Path / env helpers
# --------------------------------------------------------------------------
def _ws_root(ws: WorkspaceConfig) -> Path:
    return Path(ws.root)


def _dc(ws: WorkspaceConfig):
    dc = ws.runtime.docker_compose
    if dc is None:
        raise ValidationError("Missing [runtime.docker_compose] config.")
    return dc


def _primary_env_file(ws: WorkspaceConfig) -> Path:
    return _ws_root(ws) / _dc(ws).env_files[0]


def _env_runtime_file(ws: WorkspaceConfig) -> Path:
    return _primary_env_file(ws).parent / ".env.runtime"


def _validate_env_file(ws: WorkspaceConfig) -> None:
    ef = _primary_env_file(ws)
    if not ef.exists():
        example = ef.parent / ".env.example"
        raise ValidationError(
            f"Environment file not found: {ef}\n"
            f"Copy {example} -> {ef} and fill in credentials."
        )


# --------------------------------------------------------------------------
# E2E suite URL helpers (derived from config)
# --------------------------------------------------------------------------
def _fe_for_suite(ws: WorkspaceConfig, suite: str):
    return next((a for a in ws.runtime.apps.values() if a.e2e_suite == suite), None)


def _suite_base_url(ws: WorkspaceConfig, suite: str) -> str:
    fe = _fe_for_suite(ws, suite)
    return f"http://localhost:{fe.port if fe else 5173}"


def _suite_api_url(ws: WorkspaceConfig, suite: str) -> str:
    fe = _fe_for_suite(ws, suite)
    if fe:
        for dep in fe.depends_on:
            be = ws.runtime.apps.get(dep)
            if be and be.role == "backend":
                return f"http://localhost:{be.port}"
    return "http://localhost:8080"


# --------------------------------------------------------------------------
# Front-end build (host)
# --------------------------------------------------------------------------
def _build_frontends(ws: WorkspaceConfig, requested: set[str], *, dry_run: bool = False, logger=None) -> None:
    for app_name in sorted(requested):
        app = ws.runtime.apps.get(app_name)
        if not app or app.build is None:
            continue
        app_path = _ws_root(ws) / app.build.dir
        artifact_path = app_path / app.build.artifact
        if not (app_path / "node_modules").is_dir():
            print(f"  ⚠ {app_name}: node_modules missing, running npm install first...")
            run_command(["npm", "install"], cwd=app_path, dry_run=dry_run, logger=logger)
        print(f"  Building {app_name}...")
        run_command(app.build.command.split(), cwd=app_path, dry_run=dry_run, logger=logger)
        if not dry_run and artifact_path.is_dir():
            print(f"  ✔ {app_name}: built → {artifact_path}")
        elif not dry_run:
            raise ValidationError(
                f"{app_name}: build succeeded but {app.build.artifact}/ not found at {artifact_path}"
            )


def _check_port_available(port: int) -> None:
    import subprocess
    result = subprocess.run(["lsof", "-i", f":{port}", "-t"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        raise ValidationError(f"Port {port} already in use (PIDs: {result.stdout.strip()})")


# --------------------------------------------------------------------------
# Lifecycle handlers
# --------------------------------------------------------------------------
def handle_start(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    _validate_env_file(ws)
    requested = expand_apps(ws, args.apps, no_deps=getattr(args, "no_deps", False))

    for app_name in requested:
        app = ws.runtime.apps[app_name]
        _check_port_available(app.port)
        if app.debug_port:
            _check_port_available(app.debug_port)

    _build_frontends(ws, requested, dry_run=dry_run, logger=logger)

    env = infer_urls(ws, requested)
    write_env_runtime(_env_runtime_file(ws), env)

    if logger:
        logger.info(f"Starting: {', '.join(sorted(requested))}")
    provider.up(sorted(requested), wait=True, dry_run=dry_run, logger=logger)

    print(f"\nApps up: {', '.join(sorted(requested))}")
    for k, v in env.items():
        print(f"  {k} -> {v}")
    return 0


def handle_stop(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    apps: list[str] = []
    if getattr(args, "apps", None):
        apps = sorted(expand_apps(ws, args.apps, no_deps=True))
    provider.stop(apps, dry_run=dry_run, logger=logger)
    print(f"Stopped: {', '.join(apps) if apps else 'all'}")
    return 0


def handle_log(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    apps = sorted(expand_apps(ws, args.apps, no_deps=True))
    provider.logs(
        apps,
        follow=getattr(args, "follow", True),
        tail=getattr(args, "tail", None),
        since=getattr(args, "since", None),
        dry_run=dry_run, logger=logger,
    )
    return 0


def handle_rebuild(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    requested = expand_apps(ws, args.apps, no_deps=True)
    fe_apps = {a for a in requested if ws.runtime.apps[a].build is not None}
    if not fe_apps:
        raise ValidationError("No frontend apps to rebuild (apps without a [build] section).")
    _build_frontends(ws, fe_apps, dry_run=dry_run, logger=logger)
    provider.restart(sorted(fe_apps), dry_run=dry_run, logger=logger)
    print(f"✔ Rebuilt and restarted: {', '.join(sorted(fe_apps))}")
    return 0


def handle_status(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    statuses = provider.status(dry_run=dry_run, logger=logger)
    if dry_run:
        return 0

    app_names = set(ws.runtime.apps)
    print("Apps:")
    for s in statuses:
        if s.name not in app_names:
            continue
        if s.up:
            label = (s.state or "up") + (f" ({s.health})" if s.health else "")
            print(f"  ✔ {s.name:<25} {label:<15} :{s.port}")
        else:
            print(f"  ✗ {s.name:<25} down")

    print("\nInfra:")
    for s in statuses:
        if s.name in app_names:
            continue
        mark = "✔" if s.up else "✗"
        label = (s.state or ("up" if s.up else "down"))
        print(f"  {mark} {s.name:<25} {label}")
    return 0


def handle_restart(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    handle_stop(ws, args, dry_run=dry_run, logger=logger)
    handle_start(ws, args, dry_run=dry_run, logger=logger)
    return 0


# --------------------------------------------------------------------------
# E2E / codegen / report / clean
# --------------------------------------------------------------------------
def handle_e2e(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from .e2e import resolve_e2e_target, find_suites_for_jira, next_run_number, suite_order

    provider = build_runtime_provider(ws)
    e2e_root = _ws_root(ws) / "e2e"
    reports_root = e2e_root / "reports"
    order = suite_order(ws)
    known_suites = [
        d.name for d in e2e_root.iterdir()
        if d.is_dir() and (d / "tests").is_dir()
    ] if e2e_root.is_dir() else []

    targets = getattr(args, "targets", []) or []
    if not targets:
        raise ValidationError("No e2e target specified. Use: dop e2e <suite|JIRA|file>")

    jira_key = None
    suites_to_run: list[str] = []
    pytest_filter = getattr(args, "k", None)
    extra_pytest = list(getattr(args, "extra", []) or [])

    for token in targets:
        resolved = resolve_e2e_target(token, known_suites=known_suites)
        if resolved["kind"] == "suite":
            suites_to_run.extend(resolved["suites"])
        elif resolved["kind"] == "jira":
            jira_key = token.upper()
            jira_filter = resolved["filter"]
            found = find_suites_for_jira(jira_filter, e2e_root=e2e_root, suite_order=order)
            if not found:
                raise ValidationError(f"No tests found for {jira_key} in suites: {', '.join(order)}")
            suites_to_run.extend(found)
            if not pytest_filter:
                pytest_filter = jira_filter
        elif resolved["kind"] == "file":
            extra_pytest.append(resolved.get("path", token))

    # Deduplicate, preserve config suite order
    seen: set[str] = set()
    ordered: list[str] = []
    for s in order:
        if s in suites_to_run and s not in seen:
            ordered.append(s)
            seen.add(s)
    for s in suites_to_run:
        if s not in seen:
            ordered.append(s)
            seen.add(s)

    headed = getattr(args, "headed", False)
    all_green = True
    for suite in ordered:
        pytest_args: list[str] = []
        if pytest_filter:
            pytest_args += ["-k", pytest_filter]
        extra_env = {
            "E2E_BASE_URL": _suite_base_url(ws, suite),
            "E2E_API_URL": _suite_api_url(ws, suite),
        }
        if headed:
            pytest_args.append("--headed")
            extra_env["E2E_SHARED_CONTEXT"] = "1"
            extra_env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
        pytest_args += extra_pytest

        report_jira = jira_key or "manual"
        report_dir = reports_root / report_jira / suite
        report_dir.mkdir(parents=True, exist_ok=True)
        run_n = next_run_number(report_dir)
        results_path = f"/e2e/reports/{report_jira}/{suite}/run-{run_n}/results"
        pytest_args += [f"--alluredir={results_path}"]

        if logger:
            logger.info(f"E2E suite: {suite} (run-{run_n})")
        code = provider.run_ephemeral(
            [f"/e2e/{suite}"] + pytest_args, env=extra_env, dry_run=dry_run, logger=logger,
        )
        if code == 0:
            print(f"  ✔ {suite}: green (run-{run_n})")
        else:
            print(f"  ✘ {suite}: red (run-{run_n})")
            all_green = False

    print(f"\nResult: {'green' if all_green else 'red'}")
    _generate_allure3_reports(e2e_root, suites=ordered, dry_run=dry_run)
    return 0 if all_green else 1


def _generate_allure3_reports(e2e_root: Path, *, suites: list, dry_run: bool = False) -> None:
    import shutil
    reports_root = e2e_root / "reports"
    for suite in suites:
        suite_dir = e2e_root / suite
        results_dir = suite_dir / ".allure-results"
        report_dir = reports_root / suite
        config_file = suite_dir / "allurerc.yml"
        if not results_dir.is_dir():
            continue
        if dry_run:
            print(f"  [dry-run] Would regenerate Allure report for {suite}")
            continue
        try:
            if report_dir.is_dir():
                shutil.rmtree(report_dir)
            cmd = ["allure", "generate", ".allure-results", "--output", str(report_dir), "--report-name", suite]
            if config_file.is_file():
                cmd += ["--config", "allurerc.yml"]
            run_command(cmd, cwd=suite_dir)
            print(f"  Allure: http://localhost:5252/{suite}/index.html")
        except Exception as e:
            print(f"  ⚠ Allure generate failed for {suite}: {e}")


def handle_codegen(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from ..core.state import now_iso
    provider = build_runtime_provider(ws)
    suite = args.suite
    url = getattr(args, "url", None) or _suite_base_url(ws, suite)
    out = getattr(args, "out", None) or f"tests/recordings/recording_{now_iso()[:10]}.py"
    print(f"Starting codegen for suite '{suite}' → {url}")
    provider.run_ephemeral(
        ["playwright", "codegen", url, "-o", f"/e2e/{suite}/{out}"],
        env={"DISPLAY": os.environ.get("DISPLAY", ":0")},
        dry_run=dry_run, logger=logger, exec_replace=True,
    )
    return 0  # pragma: no cover


def handle_report(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    action = getattr(args, "report_action", None)
    if not action:
        raise ValidationError("Use: dop report serve|open|clean")

    if action == "serve":
        provider = build_runtime_provider(ws)
        provider.up([], wait=True, dry_run=dry_run, logger=logger)  # infra only (mongodb+allure)
        print("✔ Allure serving at http://localhost:5050")
        return 0

    if action == "open":
        import webbrowser
        suite = getattr(args, "suite", None) or ""
        jira = getattr(args, "jira", None) or ""
        url = f"http://localhost:5050/projects/{jira}/{suite}" if jira else "http://localhost:5050"
        webbrowser.open(url)
        return 0

    if action == "clean":
        import shutil
        keep = getattr(args, "keep", 5)
        reports_root = _ws_root(ws) / "e2e" / "reports"
        cleaned = 0
        if reports_root.is_dir():
            for jira_dir in reports_root.iterdir():
                if not jira_dir.is_dir() or jira_dir.name.startswith(("_", ".")):
                    continue
                for suite_dir in jira_dir.iterdir():
                    if not suite_dir.is_dir():
                        continue
                    runs = sorted(
                        [d for d in suite_dir.iterdir()
                         if d.is_dir() and d.name.startswith("run-") and d.name.split("-")[1].isdigit()],
                        key=lambda d: int(d.name.split("-")[1]),
                    )
                    for r in (runs[:-keep] if len(runs) > keep else []):
                        if not dry_run:
                            shutil.rmtree(r)
                        cleaned += 1
        print(f"✔ Cleaned {cleaned} old runs (keeping last {keep} per suite)")
        return 0
    return 1


def handle_clean(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    categories: list[str] = []
    if getattr(args, "all", False):
        categories = ["all"]
    else:
        if getattr(args, "m2", False):
            categories.append("maven")
        if getattr(args, "node_modules", False):
            categories.append("node_modules")
        if getattr(args, "allure", False):
            categories.append("allure")
    if not categories:
        raise ValidationError("Specify --m2, --node-modules, --allure, or --all")
    removed = provider.clean(categories, dry_run=dry_run, logger=logger)
    print(f"✔ Cleaned {len(removed)} volumes")
    return 0
