# src/dop/runtime/handlers.py
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError
from ..core.process import run_command
from .resolve import expand_apps, infer_urls
from .compose import (
    build_up_command, build_stop_command, build_logs_command,
    build_ps_command, write_env_runtime,
)


def _ws_root(ws: WorkspaceConfig) -> Path:
    return Path(ws.root)


def _compose_file(ws: WorkspaceConfig) -> Path:
    return _ws_root(ws) / ws.runtime.compose_file


def _env_file(ws: WorkspaceConfig) -> Path:
    return _ws_root(ws) / ws.runtime.env_file


def _env_runtime_file(ws: WorkspaceConfig) -> Path:
    return _env_file(ws).parent / ".env.runtime"


def _validate_env_file(ws: WorkspaceConfig) -> None:
    ef = _env_file(ws)
    if not ef.exists():
        example = ef.parent / ".env.example"
        raise ValidationError(
            f"Environment file not found: {ef}\n"
            f"Copy {example} -> {ef} and fill in credentials."
        )



_SUITE_FE_PORT = {
    "optum-support-fe": 5173,
    "providers-front-end": 5174,
    "Canal-empresa-fe": 5175,
}

_SUITE_BE_PORT = {
    "optum-support-fe": 8080,
    "providers-front-end": 8083,
    "Canal-empresa-fe": 8084,
}


def _suite_base_url(suite: str, ws: WorkspaceConfig) -> str:
    port = _SUITE_FE_PORT.get(suite, 5173)
    return f"http://localhost:{port}"


def _suite_api_url(suite: str) -> str:
    port = _SUITE_BE_PORT.get(suite, 8080)
    return f"http://localhost:{port}"


_FE_BUILD_COMMANDS = {
    "optum-support-fe": ("repos/optum-support-fe", "npx vite build"),
    "providers-front-end": ("repos/providers-front-end", "npx vue-cli-service build"),
    "canal-empresa-fe": ("repos/Canal-empresa-fe", "npx vue-cli-service build"),
}


def _build_frontends(ws: WorkspaceConfig, requested: set[str], *, dry_run: bool = False, logger=None) -> None:
    for app_name in sorted(requested):
        if app_name not in _FE_BUILD_COMMANDS:
            continue
        repo_dir, build_cmd = _FE_BUILD_COMMANDS[app_name]
        app_path = _ws_root(ws) / repo_dir
        dist_path = app_path / "dist"
        if not (app_path / "node_modules").is_dir():
            print(f"  ⚠ {app_name}: node_modules missing, running npm install first...")
            run_command(["npm", "install"], cwd=app_path, dry_run=dry_run, logger=logger)
        print(f"  Building {app_name}...")
        run_command(build_cmd.split(), cwd=app_path, dry_run=dry_run, logger=logger)
        if not dry_run and dist_path.is_dir():
            print(f"  ✔ {app_name}: built → {dist_path}")
        elif not dry_run:
            raise ValidationError(f"{app_name}: build succeeded but dist/ not found at {dist_path}")


def _check_port_available(port: int) -> None:
    result = subprocess.run(
        ["lsof", "-i", f":{port}", "-t"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        raise ValidationError(
            f"Port {port} already in use (PIDs: {result.stdout.strip()})"
        )


def handle_start(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    _validate_env_file(ws)
    requested = expand_apps(ws, args.apps, no_deps=getattr(args, "no_deps", False))

    for app_name in requested:
        app = ws.runtime.apps[app_name]
        _check_port_available(app.port)
        if app.debug_port:
            _check_port_available(app.debug_port)

    _build_frontends(ws, requested, dry_run=dry_run, logger=logger)

    env = infer_urls(ws, requested)
    rt_file = _env_runtime_file(ws)
    write_env_runtime(rt_file, env)

    _INFRA_SERVICES = ["mongodb", "allure"]
    service_names = sorted(requested) + _INFRA_SERVICES
    cmd = build_up_command(
        compose_file=_compose_file(ws),
        env_files=[_env_file(ws), rt_file],
        services=service_names,
        wait=True,
    )

    if logger:
        logger.info(f"Starting: {', '.join(service_names)}")

    run_command(cmd, cwd=_ws_root(ws), dry_run=dry_run, logger=logger)
    print(f"\nApps up: {', '.join(sorted(requested))}")
    for k, v in env.items():
        print(f"  {k} -> {v}")
    return 0


def handle_stop(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    services: list[str] = []
    if getattr(args, "apps", None):
        resolved = expand_apps(ws, args.apps, no_deps=True)
        services = sorted(resolved)
    cmd = build_stop_command(compose_file=_compose_file(ws), services=services)
    run_command(cmd, cwd=_ws_root(ws), dry_run=dry_run, logger=logger)
    print(f"Stopped: {', '.join(services) if services else 'all'}")
    return 0


def handle_log(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    resolved = expand_apps(ws, args.apps, no_deps=True)
    services = sorted(resolved)
    cmd = build_logs_command(
        compose_file=_compose_file(ws),
        services=services,
        follow=getattr(args, "follow", True),
        tail=getattr(args, "tail", None),
        since=getattr(args, "since", None),
    )
    if dry_run:
        if logger:
            logger.info(f"WOULD RUN: {' '.join(cmd)}")
        return 0
    os.execvp(cmd[0], cmd)
    # execvp replaces the process; this line is never reached
    return 0  # pragma: no cover


def handle_rebuild(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    requested = expand_apps(ws, args.apps, no_deps=True)
    fe_apps = {a for a in requested if a in _FE_BUILD_COMMANDS}
    if not fe_apps:
        raise ValidationError("No frontend apps to rebuild. Use: dop rebuild osf|pfe|cef")
    _build_frontends(ws, fe_apps, dry_run=dry_run, logger=logger)
    for app_name in sorted(fe_apps):
        cmd = ["docker", "compose", "-f", str(_compose_file(ws)), "restart", app_name]
        run_command(cmd, cwd=_ws_root(ws), dry_run=dry_run, logger=logger)
    print(f"✔ Rebuilt and restarted: {', '.join(sorted(fe_apps))}")
    return 0


def handle_status(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    cmd = build_ps_command(compose_file=_compose_file(ws))
    if dry_run:
        if logger:
            logger.info(f"WOULD RUN: {' '.join(cmd)}")
        return 0

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ws_root(ws)))

    containers: list[dict] = []
    if result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    print("Apps:")
    for app_name, app_cfg in ws.runtime.apps.items():
        match = next(
            (c for c in containers if c.get("Name") == app_name or c.get("Service") == app_name),
            None,
        )
        if match:
            state = match.get("State", "unknown")
            health = match.get("Health", "")
            status_str = state + (f" ({health})" if health else "")
            print(f"  ✔ {app_name:<25} {status_str:<15} :{app_cfg.port}")
        else:
            print(f"  ✗ {app_name:<25} down")

    allure_match = next(
        (c for c in containers if c.get("Name") == "allure" or c.get("Service") == "allure"),
        None,
    )
    print("\nInfra:")
    if allure_match:
        print(f"  ✔ allure                    {allure_match.get('State', 'up'):<15} :5050")
    else:
        print(f"  ✗ allure                    down")
    print(f"  ✗ playwright-env            (sob demanda)")
    return 0


def handle_restart(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    handle_stop(ws, args, dry_run=dry_run, logger=logger)
    handle_start(ws, args, dry_run=dry_run, logger=logger)
    return 0


def handle_e2e(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from .e2e import resolve_e2e_target, find_suites_for_jira, next_run_number, E2E_SUITE_ORDER
    from .compose import build_run_command
    from ..core.errors import ProcessError

    e2e_root = _ws_root(ws) / "e2e"
    reports_root = e2e_root / "reports"
    known_suites = [d.name for d in e2e_root.iterdir()
                    if d.is_dir() and (d / "tests").is_dir()]

    targets = getattr(args, "targets", []) or []
    if not targets:
        raise ValidationError("No e2e target specified. Use: dop e2e <suite|JIRA|file>")

    jira_key = None
    suites_to_run = []
    pytest_filter = getattr(args, "k", None)
    extra_pytest = getattr(args, "extra", []) or []

    for token in targets:
        resolved = resolve_e2e_target(token, known_suites=known_suites)
        if resolved["kind"] == "suite":
            suites_to_run.extend(resolved["suites"])
        elif resolved["kind"] == "jira":
            jira_key = token.upper()
            jira_filter = resolved["filter"]
            found = find_suites_for_jira(jira_filter, e2e_root=e2e_root, suites=known_suites)
            if not found:
                raise ValidationError(f"No tests found for {jira_key} in suites: {', '.join(known_suites)}")
            suites_to_run.extend(found)
            if not pytest_filter:
                pytest_filter = jira_filter
        elif resolved["kind"] == "file":
            extra_pytest.append(resolved.get("path", token))

    # Deduplicate, preserve E2E_SUITE_ORDER
    seen = set()
    ordered = []
    for s in E2E_SUITE_ORDER:
        if s in suites_to_run and s not in seen:
            ordered.append(s)
            seen.add(s)
    for s in suites_to_run:
        if s not in seen:
            ordered.append(s)
            seen.add(s)

    max_strikes = getattr(args, "max_strikes", None) or ws.runtime.default_max_strikes
    max_strikes = max(1, min(10, max_strikes))

    headed = getattr(args, "headed", False)
    all_green = True
    run_n = 1
    for suite in ordered:
        pytest_args = []
        if pytest_filter:
            pytest_args += ["-k", pytest_filter]
        suite_fe_url = _suite_base_url(suite, ws)
        suite_api = _suite_api_url(suite)
        extra_env = {"E2E_BASE_URL": suite_fe_url, "E2E_API_URL": suite_api}
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

        cmd = build_run_command(
            compose_file=_compose_file(ws),
            env_files=[_env_file(ws)],
            service="playwright-env",
            args=[f"/e2e/{suite}"] + pytest_args,
            profile="e2e",
            extra_env=extra_env,
        )

        if logger:
            logger.info(f"E2E suite: {suite} (run-{run_n})")

        try:
            run_command(cmd, cwd=_ws_root(ws), dry_run=dry_run, logger=logger)
            print(f"  ✔ {suite}: green (run-{run_n})")
        except Exception:
            print(f"  ✘ {suite}: red (run-{run_n})")
            all_green = False

    status = "green" if all_green else "red"
    print(f"\nResult: {status}")
    if jira_key and ordered:
        report_jira = jira_key
        print(f"Report: http://localhost:5050/projects/{report_jira}/{ordered[-1]}/run-{run_n}")
    return 0 if all_green else 1


def handle_codegen(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from .compose import build_run_command
    from ..core.state import now_iso

    suite = args.suite
    url = getattr(args, "url", None) or "http://localhost:5173"
    out = getattr(args, "out", None) or f"tests/recordings/recording_{now_iso()[:10]}.py"

    cmd = build_run_command(
        compose_file=_compose_file(ws),
        service="playwright-env",
        args=["playwright", "codegen", url, "-o", f"/e2e/{suite}/{out}"],
        profile="e2e",
        extra_env={"DISPLAY": os.environ.get("DISPLAY", ":0")},
    )
    print(f"Starting codegen for suite '{suite}' → {url}")
    os.execvp(cmd[0], cmd)
    return 0  # pragma: no cover


def handle_report(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from .compose import build_up_command

    action = getattr(args, "report_action", None)
    if not action:
        raise ValidationError("Use: dop report serve|open|clean")

    if action == "serve":
        cmd = build_up_command(
            compose_file=_compose_file(ws),
            env_files=[_env_file(ws)],
            services=["allure"],
            wait=True,
        )
        run_command(cmd, cwd=_ws_root(ws), dry_run=dry_run, logger=logger)
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
                    to_remove = runs[:-keep] if len(runs) > keep else []
                    for r in to_remove:
                        if not dry_run:
                            shutil.rmtree(r)
                        cleaned += 1
        print(f"✔ Cleaned {cleaned} old runs (keeping last {keep} per suite)")
        return 0

    return 1


def handle_clean(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    targets = []
    if getattr(args, "m2", False) or getattr(args, "all", False):
        targets.append("m2-cache")
    if getattr(args, "node_modules", False) or getattr(args, "all", False):
        targets += ["optum-fe-node_modules", "providers-fe-node_modules", "canal-fe-node_modules"]
    if getattr(args, "allure", False) or getattr(args, "all", False):
        targets += ["allure-results", "allure-reports"]
    if getattr(args, "all", False):
        targets += ["lifesupport-target", "optum-be-target", "providers-be-target", "canal-be-target"]

    if not targets:
        raise ValidationError("Specify --m2, --node-modules, --allure, or --all")

    for vol in targets:
        cmd = ["docker", "volume", "rm", "-f", f"optum-dev_{vol}"]
        if not dry_run:
            subprocess.run(cmd, capture_output=True)
        print(f"  Removed volume: {vol}")

    print(f"✔ Cleaned {len(targets)} volumes")
    return 0
