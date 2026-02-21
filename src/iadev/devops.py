"""CLI for Git/DevOps operations via iadev."""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

from .config import get_workspace
from .core.errors import MCPError, SecurityViolationError, ValidationError
from .core.fs import read_text, write_text
from .core.logging_utils import get_logger
from .core.security import guard_text, redact
from .core.state import (
    advance_stage,
    append_command_log,
    load_state,
    record_artifact,
    require_stage,
    save_state,
    validate_jira_key,
)
from .git import current_branch, delete_local_branch, list_local_branches, pull_branch, push_branch
from .git.auth import build_auth_provider
from .platform import PRResult, build_platform_provider

STATE_FILE_NAME = ".state.json"
LOGS_DIR_NAME = "logs"

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


def _pr_doc_name(repo_name: str, workspace) -> str:
    suffix = _pr_doc_suffix(repo_name, workspace)
    return f"{workspace.pr_doc_prefix}-{suffix}.md"


def _command_string(args: argparse.Namespace) -> str:
    parts = [args.command]
    if getattr(args, "jira_key", None):
        parts.append(args.jira_key)
    if getattr(args, "repo", None):
        parts.extend(["--repo", args.repo])
    if getattr(args, "branch", None):
        parts.extend(["--branch", args.branch])
    if getattr(args, "summary", None):
        parts.extend(["--summary", args.summary])
    if getattr(args, "workspace", None):
        parts.extend(["--workspace", args.workspace])
    return " ".join(parts)


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


def _load_state(workspace, jira_key: str, dry_run: bool):
    return load_state(
        jira_key,
        _state_path(workspace, jira_key),
        jira_base_url=workspace.jira_base_url,
        repo_names=_repo_order(workspace),
        create=not dry_run,
    )


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


def handle_git_pull(args: argparse.Namespace, logger, workspace, auth, platform) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    repos = [args.repo] if args.repo else _repo_order(workspace)
    for repo in repos:
        if _repo_is_skipped(state, repo):
            continue
        branch = args.branch
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


def handle_git_push(args: argparse.Namespace, logger, workspace, auth, platform) -> int:
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
        if args.branch:
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


def handle_pr_create(args: argparse.Namespace, logger, workspace, auth, platform) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    require_stage(state, "change_approved")
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
    _delete_clean_local_branches(results, workspace=workspace, dry_run=args.dry_run, logger=logger)
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


def handle_pr_publish(args: argparse.Namespace, logger, workspace, auth, platform) -> int:
    state, original = _load_state(workspace, args.jira_key, args.dry_run)
    require_stage(state, "change_approved")
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
    _delete_clean_local_branches(results, workspace=workspace, dry_run=args.dry_run, logger=logger)
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


class SecureArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        safe = redact(message)
        self.print_usage(sys.stderr)
        raise ValidationError(safe)


def build_parser() -> argparse.ArgumentParser:
    parser = SecureArgumentParser(prog="iadev-devops")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without executing git/az")
    parser.add_argument("--workspace", default=None, help="Workspace name (default: auto-detect by CWD)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_jira_arg(subparser):
        subparser.add_argument("jira_key")

    def add_repo_arg(subparser):
        subparser.add_argument("--repo")

    def add_branch_arg(subparser):
        subparser.add_argument("--branch")

    pull_parser = subparsers.add_parser("git-pull")
    add_jira_arg(pull_parser)
    add_repo_arg(pull_parser)
    add_branch_arg(pull_parser)
    pull_parser.set_defaults(func=handle_git_pull, command="git-pull")

    push_parser = subparsers.add_parser("git-push")
    add_jira_arg(push_parser)
    add_repo_arg(push_parser)
    add_branch_arg(push_parser)
    push_parser.set_defaults(func=handle_git_push, command="git-push")

    pr_parser = subparsers.add_parser("pr-create")
    add_jira_arg(pr_parser)
    add_repo_arg(pr_parser)
    pr_parser.add_argument("--summary", required=True)
    pr_parser.set_defaults(func=handle_pr_create, command="pr-create")

    publish_parser = subparsers.add_parser("pr-publish")
    add_jira_arg(publish_parser)
    publish_parser.set_defaults(func=handle_pr_publish, command="pr-publish")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = get_workspace(args.workspace)

    if getattr(args, "jira_key", None):
        validate_jira_key(args.jira_key, pattern=workspace.jira_key_pattern)

    auth = build_auth_provider(workspace)
    platform = build_platform_provider(workspace)
    logs_dir = _logs_dir(workspace, args.jira_key)
    logger = get_logger(args.jira_key, args.command, logs_dir=logs_dir)

    try:
        return args.func(args, logger, workspace, auth, platform)
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
