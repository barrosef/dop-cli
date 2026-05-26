from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepoConfig:
    name: str
    dir: str
    base_branch: str
    pr_targets: list[str]
    primary: bool = True
    azure_org: str | None = None
    azure_project: str | None = None
    long_branches: list[str] | None = None  # override; se None, usa workspace.long_branches


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
class AppConfig:
    name: str
    repo: str
    kind: str               # "java" | "vite" | "vue-cli"
    port: int
    debug_port: int | None = None
    aliases: list[str] = field(default_factory=list)
    dev_cmd: str | None = None
    lifesupport_url_env: str | None = None


@dataclass
class FeDependency:
    fe: str
    be: str


@dataclass
class RuntimeConfig:
    compose_file: str = "docker-compose.yml"
    env_file: str = "docker/.env"
    default_max_strikes: int = 3
    compose_timeout: int = 300
    apps: dict[str, AppConfig] = field(default_factory=dict)
    fe_deps: list[FeDependency] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)


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
    long_branches: list[str] = field(default_factory=lambda: ["master", "main", "desenv", "hml", "OG-GLOBAL"])
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
