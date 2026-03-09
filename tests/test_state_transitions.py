import tempfile
import unittest
from pathlib import Path

from dop.core.errors import StateError
from dop.core.state import (
    advance_stage,
    append_command_log,
    load_state,
    reset_stage,
    save_state,
)


class TestStateTransitions(unittest.TestCase):
    def _state_path(self, tmp_dir: str) -> Path:
        return Path(tmp_dir) / ".state.json"

    def test_monotonic_stage(self):
        jira = "OG-101"
        with tempfile.TemporaryDirectory() as tmp:
            state_path = self._state_path(tmp)
            state, original = load_state(
                jira,
                state_path,
                jira_base_url="https://example.test/browse",
                repo_names=["repo1"],
                create=True,
            )
            self.assertTrue(advance_stage(state, "context_approved"))
            changed = advance_stage(state, "rfc_generated")
            self.assertFalse(changed)
            save_state(state_path, state, original)

    def test_reset_allows_regression(self):
        jira = "OG-102"
        with tempfile.TemporaryDirectory() as tmp:
            state_path = self._state_path(tmp)
            state, original = load_state(
                jira,
                state_path,
                jira_base_url="https://example.test/browse",
                repo_names=["repo1"],
                create=True,
            )
            advance_stage(state, "plan_approved")
            reset_stage(state, "rfc_generated")
            self.assertEqual(state["stage"], "rfc_generated")
            save_state(state_path, state, original)

    def test_commands_log_append_only(self):
        jira = "OG-103"
        with tempfile.TemporaryDirectory() as tmp:
            state_path = self._state_path(tmp)
            state, original = load_state(
                jira,
                state_path,
                jira_base_url="https://example.test/browse",
                repo_names=["repo1"],
                create=True,
            )
            append_command_log(state, "context-approved OG-103")
            save_state(state_path, state, original)

            state2, original2 = load_state(
                jira,
                state_path,
                jira_base_url="https://example.test/browse",
                repo_names=["repo1"],
                create=True,
            )
            state2["commands_log"][0]["command"] = "tampered"
            with self.assertRaises(StateError):
                save_state(state_path, state2, original2)


if __name__ == "__main__":
    unittest.main()
