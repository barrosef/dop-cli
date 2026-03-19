from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .schema import WorkspaceConfig, RepoConfig, CredentialsConfig, PlatformConfig

CONFIG_DEFAULT_PATH = Path.home() / ".config" / "dop" / "config.toml"


def config_path() -> Path:
    override = os.environ.get("DOP_CONFIG")
    return Path(override) if override else CONFIG_DEFAULT_PATH


def load_config() -> dict[str, WorkspaceConfig]:
    """Retorna dict {workspace_name: WorkspaceConfig}."""
    path = config_path()
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return {
        name: _parse_workspace(name, data)
        for name, data in raw.get("workspaces", {}).items()
    }


def _parse_workspace(name: str, data: dict) -> WorkspaceConfig:
    repos: dict[str, RepoConfig] = {}
    for repo_name, repo_data in data.get("repos", {}).items():
        repos[repo_name] = RepoConfig(
            name=repo_name,
            dir=repo_data["dir"],
            base_branch=repo_data["base_branch"],
            pr_targets=repo_data.get("pr_targets", []),
            primary=repo_data.get("primary", True),
            azure_org=repo_data.get("azure_org"),
            azure_project=repo_data.get("azure_project"),
        )

    creds_data = data.get("credentials", {})
    credentials = CredentialsConfig(
        login_env=creds_data.get("login_env"),
        token_env=creds_data.get("token_env"),
        ssh_key_env=creds_data.get("ssh_key_env"),
    )

    plat_data = data.get("platform_config", {})
    platform_config = PlatformConfig(
        org_env=plat_data.get("org_env"),
        project_env=plat_data.get("project_env"),
        reviewers_env=plat_data.get("reviewers_env"),
        org=plat_data.get("org"),
        gitlab_url=plat_data.get("gitlab_url", "https://gitlab.com"),
        namespace=plat_data.get("namespace"),
    )

    pr_doc_suffix_map = data.get("pr_doc_suffix_map", {})
    return WorkspaceConfig(
        name=name,
        root=data["root"],
        demands_dir=data.get("demands_dir", "docs/RFC"),
        platform=data.get("platform", "azure_devops"),
        auth_method=data.get("auth_method", "token"),
        jira_base_url=data.get("jira_base_url", "https://atlassian.net/browse"),
        jira_key_pattern=data.get("jira_key_pattern", r"^[A-Z][A-Z0-9]+-\d+$"),
        credentials=credentials,
        platform_config=platform_config,
        repos=repos,
        pr_doc_prefix=data.get("pr_doc_prefix", "99-pr-00"),
        pr_doc_suffix_map=pr_doc_suffix_map,
    )
