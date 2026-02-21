# iadev

IA-First development workflow CLI - multi-workspace, multi-platform.

## Install

```bash
pip install git+https://github.com/Digital-Business-One/iadev.git
```

## Configuration

Create `~/.config/iadev/config.toml` (or set `IADEV_CONFIG` to a custom path):

```toml
[workspaces.optum]
root = "/opt/wks/csptech/optum"
demands_dir = "docs/RFC"
platform = "azure_devops"
auth_method = "token"

[workspaces.optum.credentials]
login_env = "GIT_OPTUM_LOGIN"
token_env = "GIT_OPTUM_TOKEN"

[workspaces.optum.platform_config]
org_env = "AZURE_DEVOPS_ORG"
project_env = "AZURE_DEVOPS_PROJECT"
reviewers_env = "AZURE_DEVOPS_REVIEWERS"

[workspaces.optum.pr_doc_suffix_map]
"lifesupport-api"    = "lifesupport-api"
"optumsupport-be"    = "optum-support-be"
"optumsupport-fe"    = "optum-support-fe"
"providers-back-end" = "providers-back-end"
"providers-front-end"= "providers-front-end"

[workspaces.optum.repos.lifesupport-api]
dir         = "repos/lifesupport-api"
base_branch = "OG-GLOBAL"
pr_targets  = ["OG-GLOBAL", "desenv"]
primary     = true

# ... other repos
```

## CLI Commands

### Gate commands

```bash
iadev context-approved OG-123
iadev plan-approved OG-123
iadev build-passed OG-123
iadev change-approved OG-123
iadev rerun plan OG-123
iadev reset OG-123 --to plan_generated
iadev --dry-run change-approved OG-123
```

### DevOps commands

```bash
iadev-devops git-pull OG-123
iadev-devops git-push OG-123
iadev-devops pr-create OG-123 --summary "Short summary"
iadev-devops pr-publish OG-123
```

## Plan branch table requirement

The `plan-approved` command reads `02-plan-00.md` and expects this section:

```markdown
## Branch de Trabalho

| Repo | Branch |
|---|---|
| lifesupport-api | OG-123-my-change |
| optumsupport-be | OG-123-my-change |
```

Repos omitted from the table are marked as `skipped`.

## Multi-platform support

Supported platforms:

- Azure DevOps
- GitHub
- GitLab

## Auth methods

Supported auth methods:

- `token` (HTTPS + GIT_ASKPASS)
- `ssh_rsa`
- `ssh_ed25519`

For GitHub and GitLab APIs, use tokens via `credentials.token_env`.
