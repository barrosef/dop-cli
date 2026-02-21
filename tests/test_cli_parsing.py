import unittest

from iadev import cli


class TestCLIParsing(unittest.TestCase):
    def test_context_approved_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(["context-approved", "OG-101"])
        self.assertEqual(args.command, "context-approved")
        self.assertEqual(args.jira_key, "OG-101")
        self.assertEqual(args.func, cli.handle_context_approved)

    def test_rerun_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(["rerun", "plan", "OG-101"])
        self.assertEqual(args.command, "rerun")
        self.assertEqual(args.type, "plan")
        self.assertEqual(args.jira_key, "OG-101")

    def test_reset_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(["reset", "OG-101", "--to", "plan_generated"])
        self.assertEqual(args.command, "reset")
        self.assertEqual(args.jira_key, "OG-101")
        self.assertEqual(args.to_stage, "plan_generated")

    def test_conflict_solved_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(["conflict-solved", "OG-101"])
        self.assertEqual(args.command, "conflict-solved")
        self.assertEqual(args.jira_key, "OG-101")
        self.assertEqual(args.func, cli.handle_conflict_solved)


if __name__ == "__main__":
    unittest.main()
