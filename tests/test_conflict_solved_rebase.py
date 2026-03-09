import argparse
import copy
import unittest
from unittest.mock import MagicMock, patch

from iadev import cli
from iadev.core.errors import ValidationError
from iadev.platform.base import PRResult


def _make_args(jira_key="OG-123", dry_run=False, workspace=None):
    return argparse.Namespace(
        command="conflict-solved",
        jira_key=jira_key,
        dry_run=dry_run,
        workspace=workspace,
    )


def _make_workspace(repos=None):
    ws = MagicMock()
    ws.root = "/tmp/test"
    ws.demands_dir = "docs/RFC"
    ws.jira_base_url = "https://atlassian.net/browse"
    ws.jira_key_pattern = r"^[A-Z][A-Z0-9]+-\d+$"
    ws.platform_config = MagicMock()
    ws.platform_config.org_env = "AZURE_DEVOPS_ORG"
    ws.platform_config.project_env = "AZURE_DEVOPS_PROJECT"
    ws.repos = {}
    for name in (repos or ["repo-a"]):
        cfg = MagicMock()
        cfg.base_branch = "OG-GLOBAL"
        cfg.pr_targets = ["OG-GLOBAL"]
        cfg.dir = f"repos/{name}"
        ws.repos[name] = cfg
    ws.pr_doc_suffix_map = {}
    return ws


def _make_state(stage="conflict-resolution", prs=None, repos=None):
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


class TestConflictSolvedRebase(unittest.TestCase):
    @patch("iadev.cli.save_state")
    @patch("iadev.cli.load_state")
    @patch("iadev.cli.delete_local_branch")
    @patch("iadev.cli.list_local_branches")
    @patch("iadev.cli.force_push_branch")
    @patch("iadev.cli.has_pending_rebase")
    @patch("iadev.cli.build_platform_provider")
    def test_force_push_after_rebase(
        self, mock_platform_builder, mock_pending, mock_force_push, mock_list_branches,
        mock_delete, mock_load, mock_save
    ):
        state = _make_state()
        mock_load.return_value = (state, copy.deepcopy(state))
        mock_pending.return_value = False
        mock_list_branches.return_value = ["OG-123-feature", "OG-GLOBAL"]

        platform = MagicMock()
        # After force push, PR shows no conflict
        platform.get_pr_status.return_value = PRResult(
            repo_name="repo-a",
            source_branch="OG-123-feature",
            target_branch="OG-GLOBAL",
            pr_id="100",
            web_url="https://example.test/pr/100",
            has_conflict=False,
            merge_status="succeeded",
        )
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_conflict_solved(_make_args(), logger, ws, auth)

        self.assertEqual(result, 0)
        mock_force_push.assert_called_once_with(
            "repo-a", "OG-123-feature",
            state=state, workspace=ws, auth=auth,
            dry_run=False, logger=logger,
        )
        self.assertEqual(state["stage"], "done")

    @patch("iadev.cli.save_state")
    @patch("iadev.cli.load_state")
    @patch("iadev.cli.has_pending_rebase")
    @patch("iadev.cli.build_platform_provider")
    def test_error_on_pending_rebase(self, mock_platform_builder, mock_pending, mock_load, mock_save):
        state = _make_state()
        mock_load.return_value = (state, copy.deepcopy(state))
        mock_pending.return_value = True
        mock_platform_builder.return_value = MagicMock()

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        with self.assertRaises(ValidationError) as ctx:
            cli.handle_conflict_solved(_make_args(), logger, ws, auth)
        self.assertIn("Rebase em andamento", str(ctx.exception))

    @patch("iadev.cli.save_state")
    @patch("iadev.cli.load_state")
    @patch("iadev.cli.list_local_branches")
    @patch("iadev.cli.force_push_branch")
    @patch("iadev.cli.has_pending_rebase")
    @patch("iadev.cli.build_platform_provider")
    def test_verify_pr_after_push(
        self, mock_platform_builder, mock_pending, mock_force_push, mock_list_branches,
        mock_load, mock_save
    ):
        state = _make_state()
        mock_load.return_value = (state, copy.deepcopy(state))
        mock_pending.return_value = False
        mock_list_branches.return_value = ["OG-123-feature"]

        platform = MagicMock()
        # After force push, PR still has conflict (rare: OG-GLOBAL got new commits)
        platform.get_pr_status.return_value = PRResult(
            repo_name="repo-a",
            source_branch="OG-123-feature",
            target_branch="OG-GLOBAL",
            pr_id="100",
            web_url="https://example.test/pr/100",
            has_conflict=True,
            merge_status="conflicts",
        )
        mock_platform_builder.return_value = platform

        ws = _make_workspace(["repo-a"])
        logger = MagicMock()
        auth = MagicMock()

        result = cli.handle_conflict_solved(_make_args(), logger, ws, auth)

        # Should return 1 because conflicts persist
        self.assertEqual(result, 1)
        mock_force_push.assert_called_once()
        logger.error.assert_called()


if __name__ == "__main__":
    unittest.main()
