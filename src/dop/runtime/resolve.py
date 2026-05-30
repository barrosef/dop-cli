from __future__ import annotations

import os

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError

# Maps canonical BE app name → (inject_env_var, azure_fallback_env_var, local_port)
_BE_URL_MAP: dict[str, tuple[str, str, int]] = {
    "lifesupport-api": ("LIFESUPPORT_URL", "AZURE_LIFESUPPORT_URL", 8082),
    "optum-support-be": ("OPTUM_SUPPORT_BE_URL", "AZURE_OPTUM_SUPPORT_BE_URL", 8080),
    "providers-back-end": ("PROVIDERS_BE_URL", "AZURE_PROVIDERS_BE_URL", 8083),
    "canal-empresa-be": ("CANAL_EMPRESA_BE_URL", "AZURE_CANAL_EMPRESA_BE_URL", 8084),
    "appoptum-be": ("APPOPTUM_BE_URL", "AZURE_APPOPTUM_BE_URL", 8081),
}


def expand_apps(
    ws: WorkspaceConfig,
    tokens: list[str],
    *,
    no_deps: bool = False,
) -> set[str]:
    """Resolve alias tokens to canonical app names and auto-add BE deps for FE apps.

    Args:
        ws: Workspace configuration with runtime apps, aliases and fe_deps.
        tokens: List of app names or aliases as provided by the user.
        no_deps: When True, skip automatic BE injection for FE apps.

    Returns:
        Set of canonical app names.

    Raises:
        ValidationError: If any token does not resolve to a known app.
    """
    aliases = ws.runtime.aliases
    apps = ws.runtime.apps

    resolved: set[str] = set()
    for token in tokens:
        name = aliases.get(token, token)
        if name not in apps:
            available_apps = ", ".join(sorted(apps))
            available_aliases = ", ".join(sorted(aliases))
            raise ValidationError(
                f"App '{token}' not found. "
                f"Available apps: {available_apps} "
                f"(aliases: {available_aliases})"
            )
        resolved.add(name)

    if not no_deps:
        for dep in ws.runtime.fe_deps:
            if dep.fe in resolved and dep.be not in resolved:
                resolved.add(dep.be)

    return resolved


def infer_urls(
    ws: WorkspaceConfig,
    requested: set[str],
) -> dict[str, str]:
    """Infer backend URL environment variables based on the requested app set.

    When a BE app is in the requested set, its URL is set to the local Docker
    service address (http://<name>:<port>). Otherwise, the Azure URL from the
    environment is used as fallback (if present).

    Args:
        ws: Workspace configuration (currently unused but kept for future use).
        requested: Set of canonical app names that are being started.

    Returns:
        Dict of env var name → URL string for each known BE app that has a URL.
    """
    env: dict[str, str] = {}
    for be_name, (inject_var, azure_var, port) in _BE_URL_MAP.items():
        if be_name in requested:
            env[inject_var] = f"http://{be_name}:{port}"
        else:
            fallback = os.environ.get(azure_var, "")
            if fallback:
                env[inject_var] = fallback
    return env
