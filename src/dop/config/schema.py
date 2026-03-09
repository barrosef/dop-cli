from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepoConfig:
    name: str
    dir: str
    base_branch: str
    pr_targets: list[str]
    primary: bool = True


@dataclass
class CredentialsConfig:
    login_env: str | None = None
    token_env: str | None = None
    ssh_key_env: str | None = None


@dataclass
class PlatformConfig:
    org_env: str | None = None
    project_env: str | None = None
    reviewers_env: str | None = None
    org: str | None = None
    gitlab_url: str = "https://gitlab.com"
    namespace: str | None = None


@dataclass
class WorkspaceConfig:
    name: str
    root: str
    demands_dir: str = "docs/RFC"
    platform: str = "azure_devops"
    auth_method: str = "token"
    jira_base_url: str = "https://atlassian.net/browse"
    jira_key_pattern: str = r"^[A-Z][A-Z0-9]+-\d+$"
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    platform_config: PlatformConfig = field(default_factory=PlatformConfig)
    repos: dict[str, RepoConfig] = field(default_factory=dict)
    pr_doc_prefix: str = "99-pr-00"
    pr_doc_suffix_map: dict[str, str] = field(default_factory=dict)
