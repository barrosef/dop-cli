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

    env = infer_urls(ws, requested)
    rt_file = _env_runtime_file(ws)
    write_env_runtime(rt_file, env)

    service_names = sorted(requested)
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
            print(f"  up   {app_name:<25} {status_str:<15} :{app_cfg.port}")
        else:
            print(f"  down {app_name:<25}")

    allure_match = next(
        (c for c in containers if c.get("Name") == "allure" or c.get("Service") == "allure"),
        None,
    )
    print("\nInfra:")
    if allure_match:
        print(f"  up   allure                    {allure_match.get('State', 'up'):<15} :5050")
    else:
        print(f"  down allure")
    print(f"  down playwright-env            (on-demand)")
    return 0


def handle_restart(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    handle_stop(ws, args, dry_run=dry_run, logger=logger)
    handle_start(ws, args, dry_run=dry_run, logger=logger)
    return 0
