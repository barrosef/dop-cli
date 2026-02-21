"""State management for iadev CLI."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError
from .fs import ensure_dir, read_json, write_json
from .hashing import sha256_file

STAGES = [
    "initialized",
    "rfc_generated",
    "context_approved",
    "plan_generated",
    "plan_approved",
    "implemented",
    "build_passed",
    "change_approved",
    "pr_docs_generated",
    "prs_created",
    "conflict-resolution",
    "done",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_jira_key(jira_key: str, *, pattern: str = r"^[A-Z][A-Z0-9]+-\d+$") -> None:
    if not re.match(pattern, jira_key):
        raise ValidationError(f"Invalid JIRA key format: {jira_key}")


def stage_index(stage: str) -> int:
    if stage not in STAGES:
        raise ValidationError(f"Invalid stage: {stage}")
    return STAGES.index(stage)


def default_state(jira_key: str, *, jira_base_url: str, repo_names: list[str]) -> dict[str, Any]:
    return {
        "jiraKey": jira_key,
        "jiraUrl": f"{jira_base_url}/{jira_key}",
        "stage": "initialized",
        "artifacts": {},
        "commands_log": [],
        "repos": {name: {"status": "pending"} for name in repo_names},
    }


def ensure_state_defaults(
    state: dict[str, Any],
    jira_key: str,
    *,
    jira_base_url: str,
    repo_names: list[str],
) -> dict[str, Any]:
    state.setdefault("jiraKey", jira_key)
    state.setdefault("jiraUrl", f"{jira_base_url}/{jira_key}")
    state.setdefault("stage", "initialized")
    state.setdefault("artifacts", {})
    state.setdefault("commands_log", [])
    if "repos" not in state or not isinstance(state["repos"], dict):
        state["repos"] = {}
    for name in repo_names:
        state["repos"].setdefault(name, {"status": "pending"})
    return state


def load_state(
    jira_key: str,
    state_path: Path,
    *,
    jira_base_url: str,
    repo_names: list[str],
    create: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_jira_key(jira_key)
    if not state_path.exists():
        state = default_state(jira_key, jira_base_url=jira_base_url, repo_names=repo_names)
        original = copy.deepcopy(state)
        if create:
            ensure_dir(state_path.parent)
            write_json(state_path, state)
        return state, original
    state = read_json(state_path) or default_state(
        jira_key, jira_base_url=jira_base_url, repo_names=repo_names
    )
    state = ensure_state_defaults(
        state, jira_key, jira_base_url=jira_base_url, repo_names=repo_names
    )
    original = copy.deepcopy(state)
    return state, original


def save_state(
    state_path: Path,
    state: dict[str, Any],
    original_state: dict[str, Any],
    *,
    dry_run: bool = False,
) -> None:
    ensure_state_defaults(
        state,
        state.get("jiraKey", ""),
        jira_base_url=state.get("jiraUrl", "").rsplit("/", 1)[0],
        repo_names=list(state.get("repos", {}).keys()),
    )
    assert_commands_log_append_only(
        original_state.get("commands_log", []), state.get("commands_log", [])
    )
    write_json(state_path, state, dry_run=dry_run)


def assert_commands_log_append_only(original: list[Any], new: list[Any]) -> None:
    if len(new) < len(original):
        raise StateError("commands_log cannot be truncated")
    if new[: len(original)] != original:
        raise StateError("commands_log is append-only; existing entries cannot change")


def append_command_log(
    state: dict[str, Any],
    command: str,
    *,
    user: str | None = None,
    notes: str | None = None,
) -> None:
    entry: dict[str, Any] = {"command": command, "at": now_iso()}
    if user:
        entry["user"] = user
    if notes:
        entry["notes"] = notes
    state.setdefault("commands_log", []).append(entry)


def advance_stage(state: dict[str, Any], new_stage: str) -> bool:
    current = state.get("stage", "initialized")
    current_index = stage_index(current)
    new_index = stage_index(new_stage)
    if new_index < current_index:
        return False
    state["stage"] = new_stage
    return True


def reset_stage(state: dict[str, Any], new_stage: str) -> None:
    stage_index(new_stage)
    state["stage"] = new_stage


def is_stage_at_least(state: dict[str, Any], required_stage: str) -> bool:
    return stage_index(state.get("stage", "initialized")) >= stage_index(required_stage)


def require_stage(state: dict[str, Any], required_stage: str) -> None:
    if not is_stage_at_least(state, required_stage):
        raise StateError(f"Stage gate not met. Required: {required_stage}.")


def record_artifact(state: dict[str, Any], artifact_name: str, *, demand_dir: Path) -> bool:
    path = demand_dir / artifact_name
    if not path.exists():
        return False
    entry = {
        "generatedAt": now_iso(),
        "hash": sha256_file(path),
        "invalidated": False,
    }
    state.setdefault("artifacts", {})[artifact_name] = entry
    return True


def invalidate_artifact(state: dict[str, Any], artifact_name: str) -> None:
    entry = state.setdefault("artifacts", {}).get(artifact_name, {})
    entry["invalidated"] = True
    entry["invalidatedAt"] = now_iso()
    state["artifacts"][artifact_name] = entry


def set_repo_branch(state: dict[str, Any], repo_name: str, branch_name: str) -> None:
    state.setdefault("repos", {}).setdefault(repo_name, {})["branch"] = branch_name


def set_repo_skipped(state: dict[str, Any], repo_name: str) -> None:
    state.setdefault("repos", {}).setdefault(repo_name, {})["status"] = "skipped"
