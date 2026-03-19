"""CLI for IA-First dop operations."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .config import get_workspace
from .core.errors import MCPError, SecurityViolationError, ValidationError
from .core.fs import read_text, write_text
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
from .git import (
    checkout_branch,
    checkout_new_branch_from_remote,
    commit_changes,
    create_branch,
    current_branch,
    delete_local_branch,
    fetch_origin,
    force_push_branch,
    get_conflict_files,
    has_pending_merge,
    has_pending_rebase,
    has_uncommitted_changes,
    list_local_branches,
    log_diff,
    merge_remote_branch,
    parse_branch_table,
    pull_branch,
    push_branch,
    push_branch_simple,
    rebase_on_base,
    repo_path,
)
from .git.auth import build_auth_provider
from .platform import PRResult, build_platform_provider

STATE_FILE_NAME = ".state.json"
LOGS_DIR_NAME = "logs"

DESENV_BRANCH = "desenv"
MERGE_CONFLICTS_BRANCH = "merge-conflicts-desenv-from-OG-GLOBAL"

_BRANCH_PATTERN = re.compile(r"^\s*[-*]?\s*Branch\s*:\s*(\S+)", re.IGNORECASE)


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
    if getattr(args, "repo", None):
        parts.extend(["--repo", args.repo])
    if getattr(args, "branch", None):
        parts.extend(["--branch", args.branch])
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


def _switch_repos_to_base_branches(pr_results: list[dict], *, workspace, dry_run: bool, logger) -> None:
    """Switch all impacted repos back to their base branches (keep local feature branches)."""
    switched: set[str] = set()
    for pr in pr_results:
        repo_name = _repo_name_from_pr(pr, workspace)
        if not repo_name or repo_name in switched:
            continue
        repo_cfg = workspace.repos.get(repo_name)
        if not repo_cfg:
            continue
        try:
            checkout_branch(repo_name, repo_cfg.base_branch, workspace=workspace, dry_run=dry_run, logger=logger)
        except Exception:
            if logger:
                logger.warn(f"Could not switch {repo_name} back to {repo_cfg.base_branch}.")
        switched.add(repo_name)


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
        "  2. Re-run: dop conflict-solved <JIRA-KEY>",
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


# ---------------------------------------------------------------------------
# PR doc & git helpers (unified from dop-devops)
# ---------------------------------------------------------------------------

def _pr_doc_glob(repo_name: str, workspace) -> str:
    suffix = _pr_doc_suffix(repo_name, workspace)
    prefix = workspace.pr_doc_prefix
    base = prefix[:-2] if len(prefix) > 2 else prefix
    return f"{base}*-{suffix}.md"


def _pr_doc_paths(jira_key: str, repo_name: str, workspace) -> list[Path]:
    demand_dir = _demand_dir(workspace, jira_key)
    pattern = _pr_doc_glob(repo_name, workspace)
    return sorted(demand_dir.glob(pattern))


def _parse_pr_doc(path: Path) -> tuple[str, str, str | None]:
    content = read_text(path)
    if content is None:
        raise ValidationError(f"PR doc not found: {path}")
    title = ""
    for line in content.splitlines():
        if line.strip():
            title = line.lstrip("#").strip()
            break
    if not title:
        raise ValidationError(f"Missing PR title in {path}")
    branch = None
    for line in content.splitlines():
        match = _BRANCH_PATTERN.match(line)
        if match:
            branch = match.group(1).strip("`")
            break
    return title, content, branch


def _branches_from_docs(jira_key: str, repo_name: str, workspace, logger) -> list[str]:
    branches: list[str] = []
    for doc_path in _pr_doc_paths(jira_key, repo_name, workspace):
        _, _, branch = _parse_pr_doc(doc_path)
        if branch:
            branches.append(branch)
    unique = []
    for branch in branches:
        if branch not in unique:
            unique.append(branch)
    if not unique:
        fallback = current_branch(repo_name, workspace, logger=logger)
        if not fallback:
            raise ValidationError(f"Unable to infer branch for {repo_name}.")
        logger.warn(f"Branch not found in PR docs for {repo_name}; using {fallback}.")
        unique.append(fallback)
    return unique


def _is_jira_branch(branch: str, jira_key: str) -> bool:
    return branch == jira_key or branch.startswith(f"{jira_key}-")


def _branches_for_jira(repo_name: str, jira_key: str, *, workspace, dry_run: bool, logger) -> list[str]:
    branches = list_local_branches(repo_name, workspace, dry_run=False, logger=logger)
    matched = [branch for branch in branches if _is_jira_branch(branch, jira_key)]
    if matched and logger:
        logger.info(f"Found branches for {jira_key} in {repo_name}: {', '.join(matched)}")
    return matched


def _repo_is_skipped(state: dict, repo_name: str) -> bool:
    repo_state = state.get("repos", {}).get(repo_name, {})
    return repo_state.get("status") == "skipped"


def _pr_result_to_dict(result: PRResult, workspace) -> dict:
    return {
        "repo": _pr_doc_suffix(result.repo_name, workspace),
        "repo_name": result.repo_name,
        "source_branch": result.source_branch,
        "target_branch": result.target_branch,
        "web_url": result.web_url,
        "pull_request_id": result.pr_id,
        "merge_status": result.merge_status,
        "has_conflict": result.has_conflict,
    }


def _update_repo_statuses_from_results(state: dict, pr_results: list[dict], workspace) -> None:
    repos_state = state.setdefault("repos", {})
    conflict_by_repo: dict[str, bool] = {}
    for pr in pr_results:
        repo_name = _repo_name_from_pr(pr, workspace)
        if not repo_name:
            continue
        conflict_flag = pr.get("has_conflict")
        if conflict_flag is None:
            conflict_flag = True
        conflict_by_repo[repo_name] = conflict_by_repo.get(repo_name, False) or bool(conflict_flag)
    for repo_name, has_conflict in conflict_by_repo.items():
        repo_state = repos_state.setdefault(repo_name, {"status": "pending"})
        if repo_state.get("status") != "pending":
            continue
        repo_state["status"] = "conflict-resolution" if has_conflict else "done"


def format_teams_message(jira_key: str, jira_url: str | None, pr_results: list[dict]) -> str:
    link = jira_url or ""
    lines = [f"PRs - {jira_key} - {link}"]
    for index, pr in enumerate(pr_results, start=1):
        link = pr.get("web_url") or "<link indisponivel>"
        conflict = "Sim" if pr.get("has_conflict") else "Não"
        lines.extend(
            [
                f"PR {index}",
                f"App: {pr.get('repo')}",
                f"From {pr.get('source_branch')} to {pr.get('target_branch')}",
                f"Link: {link}",
                f"Conflito: {conflict}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def write_teams_message(jira_key: str, jira_url: str | None, pr_results: list[dict], *, workspace, dry_run: bool) -> str:
    message = format_teams_message(jira_key, jira_url, pr_results)
    path = _demand_dir(workspace, jira_key) / "03-pr-team-message.md"
    write_text(path, message + "\n", dry_run=dry_run)
    return str(path)


def _create_prs_for_repo(
    *,
    repo_name: str,
    source_branch: str,
    title: str,
    description: str,
    state: dict,
    workspace,
    platform,
    dry_run: bool,
    logger,
) -> list[dict]:
    require_stage(state, "change_approved")
    repo_cfg = workspace.repos.get(repo_name)
    if not repo_cfg:
        raise ValidationError(f"Missing repo config for {repo_name}")
    results: list[dict] = []
    for target in repo_cfg.pr_targets:
        pr = platform.create_pr(
            repo_name=repo_name,
            source_branch=source_branch,
            target_branch=target,
            title=title,
            description=description,
            reviewers=None,
            dry_run=dry_run,
            logger=logger,
        )
        results.append(_pr_result_to_dict(pr, workspace))
    return results


def _commit_message_from_title(title: str, repo_name: str, workspace) -> str:
    """Derive semantic commit message from PR doc title by stripping repo suffix."""
    suffix = f" - {_pr_doc_suffix(repo_name, workspace)}"
    if title.endswith(suffix):
        return title[: -len(suffix)]
    suffix2 = f" - {repo_name}"
    if title.endswith(suffix2):
        return title[: -len(suffix2)]
    return title


# ---------------------------------------------------------------------------
# Gate / Stage handlers
# ---------------------------------------------------------------------------

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
            f"{exc} Run: dop build-passed {args.jira_key} after validating local build."
        ) from exc
    changed = advance_stage(state, "change_approved")
    if not changed:
        logger.warn("Stage already ahead; keeping current stage.")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("change-approved recorded.")
    return 0


# ---------------------------------------------------------------------------
# Git/DevOps handlers (unified from dop-devops)
# ---------------------------------------------------------------------------

def handle_git_pull(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    repos = [args.repo] if args.repo else _repo_order(workspace)
    for repo in repos:
        if _repo_is_skipped(state, repo):
            continue
        branch = getattr(args, "branch", None)
        if not branch:
            repo_cfg = workspace.repos.get(repo)
            if not repo_cfg:
                raise ValidationError(f"Missing repo config for {repo}")
            branch = repo_cfg.base_branch
        pull_branch(repo, branch_name=branch, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("git-pull completed.")
    return 0


def handle_git_push(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    require_stage(state, "change_approved")
    if args.repo:
        if _repo_is_skipped(state, args.repo):
            raise ValidationError(f"Repo {args.repo} is marked as skipped.")
        branches = [args.branch] if args.branch else _branches_from_docs(args.jira_key, args.repo, workspace, logger)
        for branch in branches:
            push_branch(
                args.repo,
                branch,
                state=state,
                workspace=workspace,
                auth=auth,
                dry_run=args.dry_run,
                logger=logger,
            )
    else:
        if getattr(args, "branch", None):
            raise ValidationError("--branch requires --repo")
        for repo in _repo_order(workspace):
            if _repo_is_skipped(state, repo):
                continue
            for branch in _branches_from_docs(args.jira_key, repo, workspace, logger):
                push_branch(
                    repo,
                    branch,
                    state=state,
                    workspace=workspace,
                    auth=auth,
                    dry_run=args.dry_run,
                    logger=logger,
                )
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("git-push completed.")
    return 0


def handle_pr_create(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    require_stage(state, "change_approved")
    platform = build_platform_provider(workspace)
    repos = [args.repo] if args.repo else _repo_order(workspace)
    results: list[dict] = []
    for repo in repos:
        if _repo_is_skipped(state, repo):
            continue
        doc_paths = _pr_doc_paths(args.jira_key, repo, workspace)
        if not doc_paths:
            raise ValidationError(f"Missing PR docs for {repo}.")
        for doc_path in doc_paths:
            title, description, branch = _parse_pr_doc(doc_path)
            record_artifact(state, doc_path.name, demand_dir=_demand_dir(workspace, args.jira_key))
            if not branch:
                branch = current_branch(repo, workspace, logger=logger)
                if not branch:
                    raise ValidationError(f"Missing source branch for {repo}.")
                logger.warn(f"Branch not found in {doc_path.name}; using {branch}.")
            results.extend(
                _create_prs_for_repo(
                    repo_name=repo,
                    source_branch=branch,
                    title=title,
                    description=description,
                    state=state,
                    workspace=workspace,
                    platform=platform,
                    dry_run=args.dry_run,
                    logger=logger,
                )
            )
    if not results:
        raise ValidationError("No PRs created.")
    state["prs"] = results
    _update_repo_statuses_from_results(state, results, workspace)
    advance_stage(state, "pr_docs_generated")
    advance_stage(state, "prs_created")
    has_conflict = any(pr.get("has_conflict") is True or pr.get("has_conflict") is None for pr in results)
    _switch_repos_to_base_branches(results, workspace=workspace, dry_run=args.dry_run, logger=logger)
    if has_conflict:
        advance_stage(state, "conflict-resolution")
    else:
        advance_stage(state, "done")
    jira_url = state.get("jiraUrl")
    message_path = write_teams_message(args.jira_key, jira_url, results, workspace=workspace, dry_run=args.dry_run)
    record_artifact(state, "03-pr-team-message.md", demand_dir=_demand_dir(workspace, args.jira_key))
    if has_conflict:
        logger.warn("PRs created with conflicts. Check the Teams message.")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info(f"PRs created. Teams message saved at {message_path}.")
    return 0


def handle_pr_publish(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    require_stage(state, "change_approved")
    platform = build_platform_provider(workspace)
    results: list[dict] = []
    repos_with_branches: list[str] = []
    for repo in _repo_order(workspace):
        if _repo_is_skipped(state, repo):
            continue
        branches = _branches_for_jira(repo, args.jira_key, workspace=workspace, dry_run=args.dry_run, logger=logger)
        if not branches:
            continue
        repos_with_branches.append(repo)
        doc_paths = _pr_doc_paths(args.jira_key, repo, workspace)
        if not doc_paths:
            raise ValidationError(f"Missing PR docs for {repo}.")
        doc_specs: list[tuple[Path, str, str, str]] = []
        doc_branches: list[str] = []
        for doc_path in doc_paths:
            title, description, branch = _parse_pr_doc(doc_path)
            if not branch:
                if len(branches) == 1:
                    branch = branches[0]
                    logger.warn(f"Branch not found in {doc_path.name}; using {branch}.")
                else:
                    raise ValidationError(f"Missing branch in PR doc {doc_path.name} for {repo}.")
            if not _is_jira_branch(branch, args.jira_key):
                raise ValidationError(
                    f"Branch {branch} in {doc_path.name} does not match {args.jira_key}."
                )
            doc_branches.append(branch)
            doc_specs.append((doc_path, title, description, branch))
        missing_docs = sorted(set(branches) - set(doc_branches))
        if missing_docs:
            raise ValidationError(
                f"Missing PR docs for branches in {repo}: {', '.join(missing_docs)}"
            )
        missing_branches = sorted(set(doc_branches) - set(branches))
        if missing_branches:
            raise ValidationError(
                f"Branch(es) not found in {repo}: {', '.join(missing_branches)}"
            )
        # Commit changes (semantic commit from PR doc title)
        committed: set[str] = set()
        for _, doc_title, _, branch in doc_specs:
            if branch in committed:
                continue
            if has_uncommitted_changes(repo, workspace=workspace, logger=logger):
                commit_msg = _commit_message_from_title(doc_title, repo, workspace)
                logger.info(f"Committing changes in {repo}: {commit_msg}")
                commit_changes(
                    repo,
                    commit_msg,
                    state=state,
                    workspace=workspace,
                    dry_run=args.dry_run,
                    logger=logger,
                )
            else:
                logger.info(f"No uncommitted changes in {repo}; skipping commit.")
            committed.add(branch)
        # Push branches
        pushed: set[str] = set()
        for _, _, _, branch in doc_specs:
            if branch in pushed:
                continue
            push_branch(
                repo,
                branch,
                state=state,
                workspace=workspace,
                auth=auth,
                dry_run=args.dry_run,
                logger=logger,
            )
            pushed.add(branch)
        # Create PRs
        for doc_path, title, description, branch in doc_specs:
            record_artifact(state, doc_path.name, demand_dir=_demand_dir(workspace, args.jira_key))
            results.extend(
                _create_prs_for_repo(
                    repo_name=repo,
                    source_branch=branch,
                    title=title,
                    description=description,
                    state=state,
                    workspace=workspace,
                    platform=platform,
                    dry_run=args.dry_run,
                    logger=logger,
                )
            )
    if not repos_with_branches:
        raise ValidationError(f"No branches found for {args.jira_key}.")
    if not results:
        raise ValidationError("No PRs created.")
    state["prs"] = results
    _update_repo_statuses_from_results(state, results, workspace)
    advance_stage(state, "pr_docs_generated")
    advance_stage(state, "prs_created")
    has_conflict = any(pr.get("has_conflict") is True or pr.get("has_conflict") is None for pr in results)
    _switch_repos_to_base_branches(results, workspace=workspace, dry_run=args.dry_run, logger=logger)
    if has_conflict:
        advance_stage(state, "conflict-resolution")
    else:
        advance_stage(state, "done")
    jira_url = state.get("jiraUrl")
    message_path = write_teams_message(args.jira_key, jira_url, results, workspace=workspace, dry_run=args.dry_run)
    record_artifact(state, "03-pr-team-message.md", demand_dir=_demand_dir(workspace, args.jira_key))
    if has_conflict:
        logger.warn("PRs created with conflicts. Check the Teams message.")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info(f"PRs created. Teams message saved at {message_path}.")
    return 0


# ---------------------------------------------------------------------------
# PR conflict resolution handlers
# ---------------------------------------------------------------------------

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


def _is_feature_pr(pr: dict, workspace) -> bool:
    """Return True if this PR targets the base branch (feature -> OG-GLOBAL)."""
    repo_name = _repo_name_from_pr(pr, workspace)
    if not repo_name:
        return False
    repo_cfg = workspace.repos.get(repo_name)
    if not repo_cfg:
        return False
    return pr.get("target_branch") == repo_cfg.base_branch


def handle_conflict_solved(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    require_stage(state, "conflict-resolution")
    pr_results = state.get("prs")
    if not pr_results:
        raise ValidationError("No PRs recorded. Run dop pr-create before resolving conflicts.")

    platform = build_platform_provider(workspace)
    _set_azure_defaults_from_prs(pr_results, workspace)
    repos_state = state.setdefault("repos", {})

    # Check for pending rebase in active repos
    for repo_name, repo_status in repos_state.items():
        if repo_status.get("status") not in ("pending", "conflict-resolution"):
            continue
        if has_pending_rebase(repo_name, workspace=workspace):
            raise ValidationError(
                f"Rebase em andamento em {repo_name}. Finalize antes de continuar."
            )

    # Identify feature PRs that need force push (post-rebase)
    force_push_repos: set[str] = set()
    for pr in pr_results:
        if _is_feature_pr(pr, workspace):
            rn = _repo_name_from_pr(pr, workspace)
            if rn:
                force_push_repos.add(rn)

    # Force push feature branches BEFORE refreshing PR status
    _, branches_by_repo_pre = _collect_repo_conflicts(pr_results, workspace)
    for repo_name in force_push_repos:
        repo_status = repos_state.get(repo_name, {})
        if repo_status.get("status") not in ("pending", "conflict-resolution"):
            continue
        branches = branches_by_repo_pre.get(repo_name, set())
        local_branches = set(list_local_branches(repo_name, workspace=workspace, dry_run=args.dry_run, logger=logger))
        for branch in branches:
            if branch not in local_branches:
                logger.warn(f"Branch {branch} not found in {repo_name}; skipping push.")
                continue
            force_push_branch(
                repo_name, branch,
                state=state, workspace=workspace, auth=auth,
                dry_run=args.dry_run, logger=logger,
            )

    # Now refresh PR statuses (after any force pushes)
    refreshed = _refresh_pr_results(pr_results, platform=platform, workspace=workspace, dry_run=args.dry_run, logger=logger)
    state["prs"] = refreshed

    conflict_by_repo, branches_by_repo = _collect_repo_conflicts(refreshed, workspace)
    has_conflict = any(conflict_by_repo.values())

    for repo_name, repo_status in repos_state.items():
        if repo_name not in conflict_by_repo:
            continue
        if conflict_by_repo[repo_name]:
            repo_status["status"] = "conflict-resolution"

    if has_conflict:
        error_msg = _build_conflict_error_message(refreshed, workspace)
        logger.error(error_msg)
        append_command_log(state, _command_string(args), user=getpass.getuser())
        save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
        return 1

    # Push remaining (non-force-push) repos
    for repo_name, repo_status in repos_state.items():
        if repo_status.get("status") not in ("pending", "conflict-resolution"):
            continue
        if repo_name in force_push_repos:
            repo_status["status"] = "done"
            continue
        branches = branches_by_repo.get(repo_name, set())
        if not branches:
            logger.warn(f"No branches found for repo {repo_name}; marking done.")
            repo_status["status"] = "done"
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
        repo_status["status"] = "done"

    _switch_repos_to_base_branches(refreshed, workspace=workspace, dry_run=args.dry_run, logger=logger)
    advance_stage(state, "done")
    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    logger.info("conflict-solved recorded.")
    return 0


# ---------------------------------------------------------------------------
# solve-conflict handler (feature branch rebase)
# ---------------------------------------------------------------------------

def handle_solve_conflict(args: argparse.Namespace, logger, workspace, auth) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    require_stage(state, "prs_created")

    pr_results = state.get("prs")
    if not pr_results:
        raise ValidationError("No PRs recorded.")

    # Find repos with conflicts in feature PRs
    conflict_repos: list[tuple[str, str]] = []  # (repo_name, branch)
    for pr in pr_results:
        if not pr.get("has_conflict"):
            continue
        if not _is_feature_pr(pr, workspace):
            continue
        repo_name = _repo_name_from_pr(pr, workspace)
        branch = pr.get("source_branch")
        if not repo_name or not branch:
            continue
        if args.repo and repo_name != args.repo:
            continue
        conflict_repos.append((repo_name, branch))

    if not conflict_repos:
        raise ValidationError("Nenhum PR com conflito encontrado.")

    for repo_name, branch in conflict_repos:
        repo_cfg = workspace.repos.get(repo_name)
        if not repo_cfg:
            raise ValidationError(f"Unknown repo: {repo_name}")
        base_branch = repo_cfg.base_branch
        repo_dir = repo_path(workspace, repo_name)

        fetch_origin(repo_name, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
        checkout_branch(repo_name, branch, workspace=workspace, dry_run=args.dry_run, logger=logger)

        success, conflicts = rebase_on_base(repo_name, base_branch, workspace=workspace, dry_run=args.dry_run, logger=logger)

        if success:
            logger.info(f"Rebase sem conflitos em {repo_name}.")
            logger.info(f"Execute: dop conflict-solved {args.jira_key} --repo {repo_name}")
        else:
            logger.warn(f"Conflitos detectados durante rebase em {repo_name}. Arquivos:")
            for f in conflicts:
                logger.warn(f"  {f}")
            logger.info("Resolva os conflitos manualmente, depois execute:")
            logger.info(f"  git add <arquivos-resolvidos>")
            logger.info(f"  git rebase --continue")
            logger.info("Repita ate o rebase estar completo.")
            logger.info(f"Depois execute: dop conflict-solved {args.jira_key} --repo {repo_name}")
            # Stop on first repo with conflicts
            break

    append_command_log(state, _command_string(args), user=getpass.getuser())
    save_state(_state_path(workspace, args.jira_key), state, original, dry_run=args.dry_run)
    return 0


# ---------------------------------------------------------------------------
# Integration handlers (no JIRA key)
# ---------------------------------------------------------------------------

def handle_update_repos(args: argparse.Namespace, logger, workspace, auth) -> int:
    """Pull base branches (OG-GLOBAL/desenv) for all repos. Called before starting a new demand."""
    repos = [args.repo] if getattr(args, "repo", None) else _repo_order(workspace)
    for repo in repos:
        repo_cfg = workspace.repos.get(repo)
        if not repo_cfg:
            raise ValidationError(f"Unknown repo: {repo}")
        base_branch = repo_cfg.base_branch
        branches_to_pull = [base_branch]
        if base_branch != DESENV_BRANCH and DESENV_BRANCH in (t for t in repo_cfg.pr_targets):
            branches_to_pull.append(DESENV_BRANCH)
        for branch in branches_to_pull:
            logger.info(f"Pulling {branch} in {repo}...")
            try:
                checkout_branch(repo, branch, workspace=workspace, dry_run=args.dry_run, logger=logger)
                pull_branch(repo, branch_name=branch, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)
            except Exception as exc:
                logger.warn(f"Could not pull {branch} in {repo}: {exc}")
    logger.info("update-repos completed.")
    return 0


def handle_integrate_desenv(args: argparse.Namespace, logger, workspace, auth) -> int:
    platform = build_platform_provider(workspace)
    repos = [args.repo] if args.repo else _repo_order(workspace)
    today = datetime.now().strftime("%Y-%m-%d")

    summary: list[str] = []
    conflict_repos: list[str] = []

    for repo in repos:
        repo_cfg = workspace.repos.get(repo)
        if not repo_cfg:
            raise ValidationError(f"Unknown repo: {repo}")
        base_branch = repo_cfg.base_branch

        fetch_origin(repo, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)

        # Check for new commits (always execute, even in dry_run)
        commits = log_diff(repo, f"origin/{DESENV_BRANCH}", f"origin/{base_branch}", workspace=workspace)
        if not commits:
            logger.info(f"Repo {repo}: {DESENV_BRANCH} ja esta atualizado")
            summary.append(f"{repo:<25} Nenhum commit novo -- skip")
            continue

        # Check for existing active PR
        existing = platform.list_prs(
            repo_name=repo,
            source_branch=base_branch,
            target_branch=DESENV_BRANCH,
            dry_run=args.dry_run,
            logger=logger,
        )
        if existing:
            pr = existing[0]
            pr_id = pr.pr_id or "?"
            conflict_note = " (CONFLITOS)" if pr.has_conflict else ""
            logger.info(f"Repo {repo}: PR #{pr_id} ja existe{conflict_note}")
            summary.append(f"{repo:<25} PR #{pr_id} ja existe{conflict_note}")
            if pr.has_conflict:
                conflict_repos.append(repo)
            continue

        # Create PR
        description = "Integracao automatica. Commits incluidos:\n" + "\n".join(commits)
        title = f"Integracao diaria {base_branch} -> {DESENV_BRANCH} ({today}) - {repo}"

        pr = platform.create_pr(
            repo_name=repo,
            source_branch=base_branch,
            target_branch=DESENV_BRANCH,
            title=title,
            description=description,
            dry_run=args.dry_run,
            logger=logger,
        )

        pr_id = pr.pr_id or "?"
        if pr.has_conflict:
            summary.append(f"{repo:<25} PR #{pr_id} criado (CONFLITOS DETECTADOS)")
            conflict_repos.append(repo)
            logger.warn(f"Repo {repo}: PR #{pr_id} criado com conflitos")
        else:
            summary.append(f"{repo:<25} PR #{pr_id} criado (sem conflitos)")
            logger.info(f"Repo {repo}: PR #{pr_id} criado sem conflitos")

    # Print summary
    print(f"\n=== Integracao Diaria OG-GLOBAL -> {DESENV_BRANCH} ({today}) ===")
    for line in summary:
        print(line)

    if conflict_repos:
        print(f"\nRepos com conflito: {', '.join(conflict_repos)}")
        for r in conflict_repos:
            print(f"-> Execute: dop prepare-merge-conflicts --repo {r}")

    print()
    logger.info("integrate-desenv completed.")
    return 0


def handle_prepare_merge_conflicts(args: argparse.Namespace, logger, workspace, auth) -> int:
    repos = [args.repo] if args.repo else _repo_order(workspace)

    summary: list[str] = []

    for repo in repos:
        repo_cfg = workspace.repos.get(repo)
        if not repo_cfg:
            raise ValidationError(f"Unknown repo: {repo}")
        base_branch = repo_cfg.base_branch
        r_dir = repo_path(workspace, repo)

        fetch_origin(repo, workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger)

        # Create or recreate branch from origin/desenv
        checkout_new_branch_from_remote(
            repo, MERGE_CONFLICTS_BRANCH, DESENV_BRANCH,
            workspace=workspace, dry_run=args.dry_run, logger=logger,
        )

        # Merge origin/OG-GLOBAL
        success, conflicts = merge_remote_branch(
            repo, base_branch,
            workspace=workspace, dry_run=args.dry_run, logger=logger,
        )

        if success:
            logger.info(f"Merge sem conflitos em {repo}. Execute finish-merge-conflicts.")
            summary.append(f"{repo}: merge sem conflitos")
        else:
            logger.warn(f"Conflitos detectados em {repo}. Arquivos em conflito:")
            for f in conflicts:
                logger.warn(f"  {f}")
            logger.info("Resolva os conflitos manualmente, depois execute:")
            logger.info(f"  cd {r_dir}")
            logger.info(f"  git add <arquivos-resolvidos>")
            logger.info(f"  git commit")
            logger.info(f"  dop finish-merge-conflicts --repo {repo}")
            summary.append(f"{repo}: CONFLITOS ({len(conflicts)} arquivos)")

    # Print summary
    print(f"\n=== Prepare Merge Conflicts ===")
    for line in summary:
        print(line)
    print()
    logger.info("prepare-merge-conflicts completed.")
    return 0


def handle_finish_merge_conflicts(args: argparse.Namespace, logger, workspace, auth) -> int:
    platform = build_platform_provider(workspace)
    repos = [args.repo] if args.repo else _repo_order(workspace)
    today = datetime.now().strftime("%Y-%m-%d")

    summary: list[str] = []

    for repo in repos:
        repo_cfg = workspace.repos.get(repo)
        if not repo_cfg:
            raise ValidationError(f"Unknown repo: {repo}")
        r_dir = repo_path(workspace, repo)

        # Verify branch exists and is current
        cur = current_branch(repo, workspace, logger=logger)
        if cur != MERGE_CONFLICTS_BRANCH:
            # Try to find the branch locally
            branches = list_local_branches(repo, workspace, logger=logger)
            if MERGE_CONFLICTS_BRANCH not in branches:
                raise ValidationError(
                    f"Branch {MERGE_CONFLICTS_BRANCH} nao existe em {repo}. "
                    f"Execute prepare-merge-conflicts primeiro."
                )
            raise ValidationError(
                f"Branch atual em {repo} e '{cur}', esperado '{MERGE_CONFLICTS_BRANCH}'. "
                f"Execute: git checkout {MERGE_CONFLICTS_BRANCH}"
            )

        # Verify no pending merge
        if has_pending_merge(repo, workspace=workspace):
            raise ValidationError(
                f"Merge incompleto em {repo}. Resolva os conflitos e faca commit antes."
            )

        # Verify there are commits to publish
        commits = log_diff(repo, f"origin/{DESENV_BRANCH}", "HEAD", workspace=workspace)
        if not commits:
            raise ValidationError(f"Nenhum commit para publicar em {repo}.")

        # Push
        push_branch_simple(
            repo, MERGE_CONFLICTS_BRANCH,
            workspace=workspace, auth=auth, dry_run=args.dry_run, logger=logger,
        )

        # Check for existing PR
        existing = platform.list_prs(
            repo_name=repo,
            source_branch=MERGE_CONFLICTS_BRANCH,
            target_branch=DESENV_BRANCH,
            dry_run=args.dry_run,
            logger=logger,
        )
        if existing:
            pr = existing[0]
            pr_id = pr.pr_id or "?"
            conflict_note = " (CONFLITOS)" if pr.has_conflict else ""
            logger.info(f"Repo {repo}: PR #{pr_id} ja existe{conflict_note}")
            summary.append(f"{repo}: PR #{pr_id} ja existe{conflict_note}")
            if pr.has_conflict:
                logger.warn(f"PR #{pr_id} criado mas AINDA tem conflitos (cenario raro).")
            continue

        # Create PR
        title = f"Integracao OG-GLOBAL -> {DESENV_BRANCH} com resolucao de conflitos ({today}) - {repo}"
        description = f"Resolucao de conflitos da integracao OG-GLOBAL -> {DESENV_BRANCH}."

        pr = platform.create_pr(
            repo_name=repo,
            source_branch=MERGE_CONFLICTS_BRANCH,
            target_branch=DESENV_BRANCH,
            title=title,
            description=description,
            dry_run=args.dry_run,
            logger=logger,
        )

        pr_id = pr.pr_id or "?"
        if pr.has_conflict:
            logger.warn(f"PR #{pr_id} criado mas AINDA tem conflitos (cenario raro).")
            summary.append(f"{repo}: PR #{pr_id} criado (CONFLITOS - cenario raro)")
        else:
            logger.info(f"Repo {repo}: PR #{pr_id} criado sem conflitos.")
            summary.append(f"{repo}: PR #{pr_id} criado (sem conflitos)")

    # Print summary
    print(f"\n=== Finish Merge Conflicts ===")
    for line in summary:
        print(line)
    print()
    logger.info("finish-merge-conflicts completed.")
    return 0


# ---------------------------------------------------------------------------
# Rerun / Reset / Invalidate handlers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class SecureArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        safe = redact(message)
        self.print_usage(sys.stderr)
        raise ValidationError(safe)


def build_parser() -> argparse.ArgumentParser:
    parser = SecureArgumentParser(prog="dop")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without executing git/az")
    parser.add_argument("--workspace", default=None, help="Workspace name (default: auto-detect by CWD)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_jira_arg(subparser):
        subparser.add_argument("jira_key")

    def add_repo_arg(subparser):
        subparser.add_argument("--repo", default=None)

    # --- Gate commands (require JIRA key) ---

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

    # --- Git/DevOps commands (unified from dop-devops) ---

    pull_parser = subparsers.add_parser("git-pull")
    add_jira_arg(pull_parser)
    add_repo_arg(pull_parser)
    pull_parser.add_argument("--branch", default=None)
    pull_parser.set_defaults(func=handle_git_pull, command="git-pull")

    push_parser = subparsers.add_parser("git-push")
    add_jira_arg(push_parser)
    add_repo_arg(push_parser)
    push_parser.add_argument("--branch", default=None)
    push_parser.set_defaults(func=handle_git_push, command="git-push")

    pr_create_parser = subparsers.add_parser("pr-create")
    add_jira_arg(pr_create_parser)
    add_repo_arg(pr_create_parser)
    pr_create_parser.set_defaults(func=handle_pr_create, command="pr-create")

    publish_parser = subparsers.add_parser("pr-publish")
    add_jira_arg(publish_parser)
    publish_parser.set_defaults(func=handle_pr_publish, command="pr-publish")

    # --- Feature conflict resolution (requires JIRA key) ---

    solve_parser = subparsers.add_parser("solve-conflict")
    add_jira_arg(solve_parser)
    add_repo_arg(solve_parser)
    solve_parser.set_defaults(func=handle_solve_conflict, command="solve-conflict")

    # --- Integration commands (no JIRA key) ---

    update_repos_parser = subparsers.add_parser("update-repos")
    add_repo_arg(update_repos_parser)
    update_repos_parser.set_defaults(func=handle_update_repos, command="update-repos")

    integrate_parser = subparsers.add_parser("integrate-desenv")
    add_repo_arg(integrate_parser)
    integrate_parser.set_defaults(func=handle_integrate_desenv, command="integrate-desenv")

    prepare_parser = subparsers.add_parser("prepare-merge-conflicts")
    add_repo_arg(prepare_parser)
    prepare_parser.set_defaults(func=handle_prepare_merge_conflicts, command="prepare-merge-conflicts")

    finish_parser = subparsers.add_parser("finish-merge-conflicts")
    add_repo_arg(finish_parser)
    finish_parser.set_defaults(func=handle_finish_merge_conflicts, command="finish-merge-conflicts")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = get_workspace(args.workspace)

    jira_key = getattr(args, "jira_key", None)
    if jira_key:
        validate_jira_key(jira_key, pattern=workspace.jira_key_pattern)
        logs_dir = _logs_dir(workspace, jira_key)
        logger = get_logger(jira_key, args.command, logs_dir=logs_dir)
    else:
        logs_dir = Path(workspace.root) / "logs"
        logger = get_logger("integration", args.command, logs_dir=logs_dir)

    auth = build_auth_provider(workspace)

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
