from __future__ import annotations
import re
from pathlib import Path
from ..core.errors import ValidationError

JIRA_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
E2E_SUITE_ORDER = ["optum-support-fe", "providers-front-end", "Canal-empresa-fe"]


def normalize_jira_filter(key: str) -> str:
    """Normalize a JIRA key to the file-name fragment used in e2e test files.

    Examples:
        OG-150    -> og_150
        SUOPT-3144 -> suopt_3144
    """
    return key.lower().replace("-", "_")


def resolve_e2e_target(token: str, *, known_suites: list[str]) -> dict:
    """Resolve a ``dop e2e <target>`` token into a structured descriptor.

    Returns a dict with at least ``"kind"`` set to one of:
        - ``"suite"``  – token is a known suite name
        - ``"jira"``   – token looks like a JIRA key (ABC-123)
        - ``"file"``   – token is a file path (contains "/" or ends with ".py")

    Raises :class:`ValidationError` when the token cannot be resolved.
    """
    if token in known_suites:
        return {"kind": "suite", "suites": [token], "filter": None}

    if JIRA_RE.match(token):
        return {"kind": "jira", "suites": None, "filter": normalize_jira_filter(token)}

    if "/" in token or token.endswith(".py"):
        return {"kind": "file", "suites": None, "filter": None, "path": token}

    raise ValidationError(
        f"Cannot resolve e2e target '{token}'. "
        f"Expected: suite name ({', '.join(known_suites)}), JIRA key (OG-123), or file path."
    )


def find_suites_for_jira(
    jira_filter: str,
    *,
    e2e_root: Path,
    suites: list[str],
) -> list[str]:
    """Return suites (in E2E_SUITE_ORDER) that contain test files matching *jira_filter*.

    A test file matches when ``jira_filter`` appears anywhere in its filename.

    Args:
        jira_filter: Normalized JIRA fragment, e.g. ``"og_150"``.
        e2e_root:    Root directory that contains one sub-directory per suite.
        suites:      Allowed suites to consider (intersection with E2E_SUITE_ORDER).
    """
    matched: list[str] = []
    for suite in E2E_SUITE_ORDER:
        if suite not in suites:
            continue
        tests_dir = e2e_root / suite / "tests"
        if not tests_dir.is_dir():
            continue
        for f in tests_dir.iterdir():
            if f.is_file() and jira_filter in f.name:
                matched.append(suite)
                break
    return matched


def next_run_number(report_dir: Path) -> int:
    """Return the next sequential run number for Allure reports.

    Scans *report_dir* for sub-directories named ``run-N`` (where N is a
    positive integer) and returns ``max(N) + 1``, or ``1`` when none exist.
    """
    if not report_dir.is_dir():
        return 1
    existing = [
        int(d.name.split("-")[1])
        for d in report_dir.iterdir()
        if d.is_dir() and d.name.startswith("run-") and d.name.split("-")[1].isdigit()
    ]
    return max(existing, default=0) + 1
