import unittest

from iadev import devops


class TestPrOps(unittest.TestCase):
    def test_format_teams_message_includes_conflict(self):
        message = devops.format_teams_message(
            "OG-101",
            "https://csptech.atlassian.net/browse/OG-101",
            [
                {
                    "repo": "optumsupport-be",
                    "source_branch": "OG-101-branch",
                    "target_branch": "master",
                    "web_url": "https://example.test/pr/1",
                    "has_conflict": True,
                }
            ],
        )
        self.assertIn("PRs - OG-101 - https://csptech.atlassian.net/browse/OG-101", message)
        self.assertIn("Conflito: Sim", message)

    def test_format_teams_message_no_conflict(self):
        message = devops.format_teams_message(
            "OG-102",
            "https://csptech.atlassian.net/browse/OG-102",
            [
                {
                    "repo": "optumsupport-fe",
                    "source_branch": "OG-102-branch",
                    "target_branch": "desenv",
                    "web_url": "https://example.test/pr/2",
                    "has_conflict": False,
                }
            ],
        )
        self.assertIn("PRs - OG-102 - https://csptech.atlassian.net/browse/OG-102", message)
        self.assertIn("Conflito: Não", message)


if __name__ == "__main__":
    unittest.main()
