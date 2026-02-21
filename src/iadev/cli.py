"""CLI for IA-First iadev operations."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from .config import get_workspace
from .core.errors import MCPError, SecurityViolationError, ValidationError
from .core.logging_utils import get_logger
from .core.security import guard_text, redact
from .core.state import (
    STAGES,
    advance_stage,
    append_command_log,
    invalidate_artifact,
    load_state,
    record_artifact,
    require_stage,
    reset_stage,
    save_state,
    set_repo_branch,
    set_repo_skipped,
    validate_jira_key,
)
from .git import create_branch, delete_local_branch, list_local_branches, parse_branch_table, push_branch
from .git.auth import build_auth_provider
from .platform import build_platform_provider

STATE_FILE_NAME = ".state.json"
LOGS_DIR_NAME = "logs"


def _demand_dir(workspace, jira_key: str) -> Path:
    return Path(workspace.root) / workspace.demands_dir / jira_key


def _state_path(workspace, jira_key: str) -> Path:
    return _demand_dir(workspace, jira_key) / STATE_FILE_NAME


def _logs_dir(workspace, jira_key: str) -> Path:
    return _demand_dir(workspace, jira_key) / LOGS_DIR_NAME


def _repo_order(workspace) -> list[str]:
    return list(workspace.repos.keys())


def _pr_doc_suffix(repo_name: str, workspace) -> str:
    return workspace.pr_doc_suffix_map.get(repo_name, repo_name)


def _command_string(args: argparse.Namespace) -> str:
    parts = [args.command]
    if getattr(args, "type", None):
        parts.append(args.type)
    if getattr(args, "jira_key", None):
        parts.append(args.jira_key)
    if getattr(args, "to_stage", None):
        parts.extend(["--to", args.to_stage])
    if getattr(args, "artifact", None):
        parts.append(args.artifact)
    if getattr(args, "workspace", None):
        parts.extend(["--workspace", args.workspace])
    return " ".join(parts)


def _warn_missing_artifact(logger, path: Path) -> None:
    logger.warn(f"Artifact missing: {path}")


def _set_azure_defaults_from_prs(pr_results: list[dict], workspace) -> None:
    org_env = workspace.platform_config.org_env or "AZURE_DEVOPS_ORG"
    project_env = workspace.platform_config.project_env or "AZURE_DEVOPS_PROJECT"
    if os.environ.get(org_env) and os.environ.get(project_env):
        return
    for pr in pr_results:
        web_url = pr.get("web_url")
        if not web_url:
            continue
        parsed = urlparse(web_url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            continue
        org = parts[0]
        project = parts[1]
        if not os.environ.get(org_env):
            os.environ[org_env] = f"{parsed.scheme}://{parsed.netloc}/{org}/"
        if not os.environ.get(project_env):
            os.environ[project_env] = project
        break


def _repo_name_from_pr(pr: dict, workspace) -> str | None:
    repo_name = pr.get("repo_name")
    if repo_name:
        return repo_name
    repo_suffix = pr.get("repo")
    if not repo_suffix:
        return None
    for name in workspace.repos.keys():
        if _pr_doc_suffix(name, workspace) == repo_suffix:
            return name
    return repo_suffix


def _delete_clean_local_branches(pr_results: list[dict], *, workspace, dry_run: bool, logger) -> None:
    conflict_by_branch: dict[tuple[str, str], bool] = {}
    for pr in pr_results:
        repo_name = _repo_name_from_pr(pr, workspace)
        branch = pr.get("source_branch")
        if not repo_name or not branch:
            continue
        key = (repo_name, branch)
        conflict_flag = pr.get("has_conflict")
        if conflict_flag is None:
            conflict_flag = True
        conflict_by_branch[key] = conflict_by_branch.get(key, False) or bool(conflict_flag)
    for (repo_name, branch), has_conflict in conflict_by_branch.items():
        if has_conflict:
            continue
        try:
            delete_local_branch(repo_name, branch, workspace=workspace, dry_run=dry_run, logger=logger)
        except ValidationError as exc:
            if logger:
                logger.warn(f"Unable to delete branch {branch} in {repo_name}: {exc}")


def _collect_repo_conflicts(pr_results: list[dict], workspace) -> tuple[dict[str, bool], dict[str, set[str]]]:
    conflict_by_repo: dict[str, bool] = {}
    branches_by_repo: dict[str, set[str]] = {}
    for pr in pr_results:
        repo_name = _repo_name_from_pr(pr, workspace)
        branch = pr.get("source_branch")
        if repo_name and branch:
            branches_by_repo.setdefault(repo_name, set()).add(branch)
        if not repo_name:
            continue
        conflict_flag = pr.get("has_conflict")
        if conflict_flag is None:
            conflict_flag = True
        conflict_by_repo[repo_name] = conflict_by_repo.get(repo_name, False) or bool(conflict_flag)
    return conflict_by_repo, branches_by_repo


def _build_conflict_error_message(pr_results: list[dict], workspace) -> str:
    lines = [
        "Conflicts still present after conflict-solved. Pushes NOT performed.",
        "",
        "Repos with unresolved conflicts:",
    ]
    for pr in pr_results:
        if not pr.get("has_conflict"):
            continue
        repo_name = _repo_name_from_pr(pr, workspace) or pr.get("repo", "unknown")
        source = pr.get("source_branch", "?")
        target = pr.get("target_branch", "?")
        url = pr.get("web_url", "")
        entry = f"  - {repo_name:<20} | branch: {source}  ->  {target}"
        if url:
            entry += f"\n    PR: {url}"
        lines.append(entry)
    lines += [
        "",
        "Action required:",
        "  1. Resolve the conflicts in the listed repos manually",
        "  2. Re-run: iadev conflict-solved <JIRA-KEY>",
    ]
    return "\n".join(lines)


def _load_state(workspace, jira_key: str, dry_run: bool):
    return load_state(
        jira_key,
        _state_path(workspace, jira_key),
        jira_base_url=workspace.jira_base_url,
        repo_names=_repo_order(workspace),
        create=not dry_run,
    )


def handle_context_approved(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    demand_dir = _demand_dir(workspace, args.jira_key)
    context_path = demand_dir / "01-context-00.md"
    if not context_path.exists():
        _warn_missing_artifact(logger, context_path)
    else:
        record_artifact(state, "01-context-00.md", demand_dir=demand_dir)
    changed = advance_stage(state, "context_approved")
    if not changed:
        logger.warn("Stage already ahead; keeping current stage.")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("context-approved recorded.")
    return 0


def _ensure_repo_pending(state: dict, repo_name: str) -> None:
    repos = state.setdefault("repos", {})
    repo_state = repos.setdefault(repo_name, {"status": "pending"})
    if repo_state.get("status") == "skipped":
        repo_state["status"] = "pending"


def handle_plan_approved(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    demand_dir = _demand_dir(workspace, args.jira_key)
    plan_path = demand_dir / "02-plan-00.md"

    if not plan_path.exists():
        _warn_missing_artifact(logger, plan_path)
    else:
        branch_map = parse_branch_table(plan_path)
        unknown = [name for name in branch_map.keys() if name not in workspace.repos]
        if unknown:
            raise ValidationError(f"Unknown repo(s) in Branch de Trabalho table: {', '.join(unknown)}")

        all_repos = _repo_order(workspace)
        impacted = [r for r in all_repos if r in branch_map]
        skipped = [r for r in all_repos if r not in branch_map]

        for repo_name in impacted:
            set_repo_branch(state, repo_name, branch_map[repo_name])
            _ensure_repo_pending(state, repo_name)
        for repo_name in skipped:
            set_repo_skipped(state, repo_name)

        for repo_name in impacted:
            branch_name = branch_map[repo_name]
            logger.info(f"Creating branch '{branch_name}' in {repo_name}...")
            create_branch(
                repo_name,
                branch_name,
                workspace,
                auth,
                pull_first=True,
                dry_run=args.dry_run,
                logger=logger,
            )

        record_artifact(state, "02-plan-00.md", demand_dir=demand_dir)

    changed = advance_stage(state, "plan_approved")
    if not changed:
        logger.warn("Stage already ahead; keeping current stage.")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("plan-approved recorded. Branches created in impacted repos.")
    return 0


def handle_build_passed(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    changed = advance_stage(state, "build_passed")
    if not changed:
        logger.warn("Stage already ahead; keeping current stage.")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("build-passed recorded.")
    return 0


def handle_change_approved(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    try:
        require_stage(state, "build_passed")
    except MCPError as exc:
        raise ValidationError(
            f"{exc} Run: iadev build-passed {args.jira_key} after validating local build."
        ) from exc
    changed = advance_stage(state, "change_approved")
    if not changed:
        logger.warn("Stage already ahead; keeping current stage.")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("change-approved recorded.")
    return 0


def _refresh_pr_results(pr_results: list[dict], *, platform, workspace, dry_run: bool, logger) -> list[dict]:
    refreshed: list[dict] = []
    for pr in pr_results:
        pr_id = pr.get("pull_request_id") or pr.get("pr_id")
        if not pr_id:
            refreshed.append(pr)
            continue
        repo_name = _repo_name_from_pr(pr, workspace)
        status = platform.get_pr_status(
            str(pr_id),
            repo_name=repo_name,
            dry_run=dry_run,
            logger=logger,
        )
        updated = dict(pr)
        updated["merge_status"] = status.merge_status
        updated["has_conflict"] = status.has_conflict
        refreshed.append(updated)
    return refreshed


def handle_conflict_solved(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    require_stage(state, "conflict-resolution")
    pr_results = state.get("prs")
    if not pr_results:
        raise ValidationError("No PRs recorded. Run iadev-devops pr-create before resolving conflicts.")

    platform = build_platform_provider(workspace)
    _set_azure_defaults_from_prs(pr_results, workspace)
    refreshed = _refresh_pr_results(pr_results, platform=platform, workspace=workspace, dry_run=args.dry_run, logger=logger)
    state["prs"] = refreshed

    conflict_by_repo, branches_by_repo = _collect_repo_conflicts(refreshed, workspace)
    has_conflict = any(conflict_by_repo.values())
    repos_state = state.setdefault("repos", {})

    for repo_name, status in repos_state.items():
        if repo_name not in conflict_by_repo:
            continue
        if conflict_by_repo[repo_name]:
            status["status"] = "conflict-resolution"

    if has_conflict:
        error_msg = _build_conflict_error_message(refreshed, workspace)
        logger.error(error_msg)
        append_command_log(state, _command_string(args), user=getpass.getuser())
        save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
        return 1

    for repo_name, status in repos_state.items():
        if status.get("status") not in ("pending", "conflict-resolution"):
            continue
        branches = branches_by_repo.get(repo_name, set())
        if not branches:
            logger.warn(f"No branches found for repo {repo_name}; marking done.")
            status["status"] = "done"
            continue
        local_branches = set(list_local_branches(repo_name, workspace=workspace, dry_run=args.dry_run, logger=logger))
        for branch in branches:
            if branch not in local_branches:
                logger.warn(f"Branch {branch} not found in {repo_name}; skipping push.")
                continue
            push_branch(
                repo_name,
                branch,
                state=state,
                workspace=workspace,
                auth=auth,
                dry_run=args.dry_run,
                logger=logger,
            )
        status["status"] = "done"

    _delete_clean_local_branches(refreshed, workspace=workspace, dry_run=args.dry_run, logger=logger)
    advance_stage(state, "done")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("conflict-solved recorded.")
    return 0


def handle_rerun(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    pr_docs = [_pr_doc_name(name, workspace) for name in _repo_order(workspace)]
    rerun_artifacts = {
        "rfc": ["00-rfc.md"],
        "plan": ["02-plan-00.md"],
        "impl": [],
        "prdoc": pr_docs,
    }
    artifacts = rerun_artifacts.get(args.type, [])
    for artifact in artifacts:
        invalidate_artifact(state, artifact)
    notes = f"READY FOR CODEX: rerun {args.type} for {args.jira_key}"
    append_command_log(state, _command_string(args), user=getpass.getuser(), notes=notes)
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info(notes)
    return 0


def _pr_doc_name(repo_name: str, workspace) -> str:
    suffix = _pr_doc_suffix(repo_name, workspace)
    return f"{workspace.pr_doc_prefix}-{suffix}.md"


def handle_reset(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    reset_stage(state, args.to_stage)
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info(f"Stage reset to {args.to_stage}.")
    return 0


def handle_invalidate(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    invalidate_artifact(state, args.artifact)
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info(f"Artifact invalidated: {args.artifact}")
    return 0


class SecureArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        safe = redact(message)
        self.print_usage(sys.stderr)
        raise ValidationError(safe)


def build_parser() -> argparse.ArgumentParser:
    parser = SecureArgumentParser(prog="iadev")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without executing git/az")
    parser.add_argument("--workspace", default=None, help="Workspace name (default: auto-detect by CWD)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_jira_arg(subparser):
        subparser.add_argument("jira_key")

    context_parser = subparsers.add_parser("context-approved")
    add_jira_arg(context_parser)
    context_parser.set_defaults(func=handle_context_approved, command="context-approved")

    plan_parser = subparsers.add_parser("plan-approved")
    add_jira_arg(plan_parser)
    plan_parser.set_defaults(func=handle_plan_approved, command="plan-approved")

    build_passed_parser = subparsers.add_parser("build-passed")
    add_jira_arg(build_passed_parser)
    build_passed_parser.set_defaults(func=handle_build_passed, command="build-passed")

    change_parser = subparsers.add_parser("change-approved")
    add_jira_arg(change_parser)
    change_parser.set_defaults(func=handle_change_approved, command="change-approved")

    conflict_parser = subparsers.add_parser("conflict-solved")
    add_jira_arg(conflict_parser)
    conflict_parser.set_defaults(func=handle_conflict_solved, command="conflict-solved")

    rerun_parser = subparsers.add_parser("rerun")
    rerun_parser.add_argument("type", choices=["rfc", "plan", "impl", "prdoc"])
    add_jira_arg(rerun_parser)
    rerun_parser.set_defaults(func=handle_rerun, command="rerun")

    reset_parser = subparsers.add_parser("reset")
    add_jira_arg(reset_parser)
    reset_parser.add_argument("--to", dest="to_stage", required=True, choices=[
        *STAGES,
    ])
    reset_parser.set_defaults(func=handle_reset, command="reset")

    invalidate_parser = subparsers.add_parser("invalidate")
    add_jira_arg(invalidate_parser)
    invalidate_parser.add_argument("artifact")
    invalidate_parser.set_defaults(func=handle_invalidate, command="invalidate")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = get_workspace(args.workspace)

    if getattr(args, "jira_key", None):
        validate_jira_key(args.jira_key, pattern=workspace.jira_key_pattern)

    auth = build_auth_provider(workspace)
    logs_dir = _logs_dir(workspace, args.jira_key)
    logger = get_logger(args.jira_key, args.command, logs_dir=logs_dir)

    try:
        return args.func(args, logger, workspace, auth)
    except MCPError as exc:
        try:
            message = redact(str(exc))
            guard_text(message)
        except SecurityViolationError:
            message = "Security violation."
        print(f"ERROR: {message}", file=sys.stderr)
        return 1
    except Exception:
        print("ERROR: Unexpected failure.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
