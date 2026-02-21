"""Git operations for demands."""

from __future__ import annotations

from pathlib import Path

from ..config.schema import WorkspaceConfig
from ..core.errors import ProcessError, ValidationError
from ..core.process import run_command
from ..core.state import require_stage
from .auth.base import GitAuthProvider


def repo_path(workspace: WorkspaceConfig, repo_name: str) -> Path:
    repo_cfg = workspace.repos.get(repo_name)
    if not repo_cfg:
        raise ValidationError(f"Unknown repo: {repo_name}")
    return Path(workspace.root) / repo_cfg.dir


def create_branch(
    repo_name: str,
    branch_name: str,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    *,
    pull_first: bool = False,
    dry_run: bool = False,
    logger=None,
) -> None:
    repo_cfg = workspace.repos.get(repo_name)
    if not repo_cfg:
        raise ValidationError(f"Missing repo config for {repo_name}")
    repo_dir = repo_path(workspace, repo_name)
    base_branch = repo_cfg.base_branch
    if pull_first:
        pull_branch(
            repo_name,
            base_branch,
            workspace=workspace,
            auth=auth,
            dry_run=dry_run,
            logger=logger,
        )
    run_command(["git", "checkout", base_branch], cwd=repo_dir, dry_run=dry_run, logger=logger)
    run_command(
        ["git", "checkout", "-b", branch_name],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )


def current_branch(
    repo_name: str,
    workspace: WorkspaceConfig,
    *,
    dry_run: bool = False,
    logger=None,
) -> str:
    repo_dir = repo_path(workspace, repo_name)
    result = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )
    return result.stdout.strip() if result.stdout else ""


def list_local_branches(
    repo_name: str,
    workspace: WorkspaceConfig,
    *,
    dry_run: bool = False,
    logger=None,
) -> list[str]:
    repo_dir = repo_path(workspace, repo_name)
    result = run_command(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )
    if not result.stdout:
        return []
    branches = []
    for line in result.stdout.splitlines():
        branch = line.strip()
        if branch:
            branches.append(branch)
    return branches


def get_remote_url(
    repo_name: str,
    workspace: WorkspaceConfig,
    remote_name: str = "origin",
    *,
    dry_run: bool = False,
    logger=None,
) -> str:
    repo_dir = repo_path(workspace, repo_name)
    result = run_command(
        ["git", "remote", "get-url", remote_name],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )
    return result.stdout.strip() if result.stdout else ""


def _run_git_remote(
    args: list[str],
    *,
    repo_dir: Path,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    prefix = auth.git_command_prefix()
    if not prefix or prefix[0] != "git":
        raise ValidationError("git_command_prefix must start with 'git'.")
    cmd = [*prefix, *args]
    env = auth.git_env() if not dry_run else None
    run_command(cmd, cwd=repo_dir, env=env, dry_run=dry_run, logger=logger)


def commit_changes(
    repo_name: str,
    commit_message: str,
    *,
    state: dict,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> None:
    require_stage(state, "change_approved")
    repo_dir = repo_path(workspace, repo_name)
    run_command(["git", "add", "-A"], cwd=repo_dir, dry_run=dry_run, logger=logger)
    run_command(
        ["git", "commit", "-m", commit_message],
        cwd=repo_dir,
        dry_run=dry_run,
        logger=logger,
    )


def push_branch(
    repo_name: str,
    branch_name: str,
    *,
    state: dict,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    require_stage(state, "change_approved")
    repo_dir = repo_path(workspace, repo_name)
    _run_git_remote(
        ["push", "-u", "origin", branch_name],
        repo_dir=repo_dir,
        auth=auth,
        dry_run=dry_run,
        logger=logger,
    )


def pull_branch(
    repo_name: str,
    branch_name: str | None,
    *,
    workspace: WorkspaceConfig,
    auth: GitAuthProvider,
    dry_run: bool = False,
    logger=None,
) -> None:
    repo_dir = repo_path(workspace, repo_name)
    args = ["pull", "--ff-only"]
    if branch_name:
        args.extend(["origin", branch_name])
    _run_git_remote(args, repo_dir=repo_dir, auth=auth, dry_run=dry_run, logger=logger)


def delete_local_branch(
    repo_name: str,
    branch_name: str,
    *,
    workspace: WorkspaceConfig,
    dry_run: bool = False,
    logger=None,
) -> bool:
    repo_cfg = workspace.repos.get(repo_name)
    if not repo_cfg:
        raise ValidationError(f"Missing repo config for {repo_name}")
    repo_dir = repo_path(workspace, repo_name)
    base_branch = repo_cfg.base_branch
    current = current_branch(repo_name, workspace, dry_run=dry_run, logger=logger)
    if current == branch_name:
        run_command(["git", "checkout", base_branch], cwd=repo_dir, dry_run=dry_run, logger=logger)
    try:
        run_command(
            ["git", "show-ref", "--verify", f"refs/heads/{branch_name}"],
            cwd=repo_dir,
            dry_run=dry_run,
            logger=logger,
        )
    except ProcessError:
        if logger:
            logger.warn(f"Branch {branch_name} not found in {repo_name}; skipping delete.")
        return False
    run_command(["git", "branch", "-D", branch_name], cwd=repo_dir, dry_run=dry_run, logger=logger)
    return True
