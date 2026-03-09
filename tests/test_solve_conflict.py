import argparse
import copy
import unittest
from unittest.mock import MagicMock, patch

from dop import cli
from dop.core.errors import ValidationError


def _make_args(jira_key="OG-123", repo=None, dry_run=False, workspace=None):
    return argparse.Namespace(
        command="solve-conflict",
        jira_key=jira_key,
        repo=repo,
        dry_run=dry_run,
        workspace=workspace,
    )


def _make_workspace(repos=None):
    ws = MagicMock()
    ws.root = "/tmp/test"
    ws.demands_dir = "docs/RFC"
    ws.jira_base_url = "https://atlassian.net/browse"
    ws.jira_key_pattern = r"^[A-Z][A-Z0-9]+-\d+$"
    ws.repos = {}
    for name in (repos or ["repo-a"]):
        cfg = MagicMock()
        cfg.base_branch = "OG-GLOBAL"
        cfg.pr_targets = ["OG-GLOBAL"]
        cfg.dir = f"repos/{name}"
        ws.repos[name] = cfg
    ws.pr_doc_suffix_map = {}
    return ws


def _make_state(stage="prs_created", prs=None, repos=None):
    return {
        "jiraKey": "OG-123",
        "jiraUrl": "https://atlassian.net/browse/OG-123",
        "stage": stage,
        "artifacts": {},
        "commands_log": [],
        "repos": repos or {"repo-a": {"status": "conflict-resolution", "branch": "OG-123-feature"}},
        "prs": prs or [
            {
                "repo_name": "repo-a",
                "source_branch": "OG-123-feature",
                "target_branch": "OG-GLOBAL",
                "web_url": "https://example.test/pr/100",
                "pull_request_id": "100",
                "merge_status": "conflicts",
                "has_conflict": True,
            }
        ],
    }


class TestSolveConflict(unittest.TestCase):
    @patch("dop.cli.save_state")
    @patch("dop.cli.load_state")
    @patch("dop.cli.rebase_on_base")
    @patch("dop.cli.checkout_branch")
    @patch("dop.cli.fetch_origin")
    @patch("dop.cli.repo_path")
    def test_rebase_no_conflicts(
        self, mock_repo_path, mock_fetch, mock_checkout, mock_rebase, mock_load, mock_save
    ):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        state = _make_state()
        mock_load.return_value = (state, copy.deepcopy(state))
        mock_rebase.return_value = (True, [])

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_solve_conflict(_make_args(), logger, ws, auth)

        self.assertEqual(result, 0)
        mock_rebase.assert_called_once()
        logger.info.assert_any_call("Rebase sem conflitos em repo-a.")

    @patch("dop.cli.save_state")
    @patch("dop.cli.load_state")
    @patch("dop.cli.rebase_on_base")
    @patch("dop.cli.checkout_branch")
    @patch("dop.cli.fetch_origin")
    @patch("dop.cli.repo_path")
    def test_rebase_with_conflicts(
        self, mock_repo_path, mock_fetch, mock_checkout, mock_rebase, mock_load, mock_save
    ):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        state = _make_state()
        mock_load.return_value = (state, copy.deepcopy(state))
        mock_rebase.return_value = (False, ["src/Service.java", "src/Config.java"])

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_solve_conflict(_make_args(), logger, ws, auth)

        self.assertEqual(result, 0)
        logger.warn.assert_any_call("Conflitos detectados durante rebase em repo-a. Arquivos:")
        logger.warn.assert_any_call("  src/Service.java")
        logger.warn.assert_any_call("  src/Config.java")

    @patch("dop.cli.save_state")
    @patch("dop.cli.load_state")
    def test_no_conflicting_prs(self, mock_load, mock_save):
        state = _make_state(prs=[
            {
                "repo_name": "repo-a",
                "source_branch": "OG-123-feature",
                "target_branch": "OG-GLOBAL",
                "has_conflict": False,
            }
        ])
        mock_load.return_value = (state, copy.deepcopy(state))

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        with self.assertRaises(ValidationError) as ctx:
            cli.handle_solve_conflict(_make_args(), logger, ws, auth)
        self.assertIn("Nenhum PR com conflito", str(ctx.exception))

    @patch("dop.cli.save_state")
    @patch("dop.cli.load_state")
    @patch("dop.cli.rebase_on_base")
    @patch("dop.cli.checkout_branch")
    @patch("dop.cli.fetch_origin")
    @patch("dop.cli.repo_path")
    def test_dry_run(
        self, mock_repo_path, mock_fetch, mock_checkout, mock_rebase, mock_load, mock_save
    ):
        mock_repo_path.return_value = "/tmp/test/repos/repo-a"
        state = _make_state()
        mock_load.return_value = (state, copy.deepcopy(state))
        mock_rebase.return_value = (True, [])

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_solve_conflict(_make_args(dry_run=True), logger, ws, auth)

        self.assertEqual(result, 0)
        self.assertTrue(mock_fetch.call_args.kwargs.get("dry_run"))
        self.assertTrue(mock_rebase.call_args.kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
