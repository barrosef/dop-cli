import unittest

from dop import cli


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

    def test_solve_conflict_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(["solve-conflict", "OG-101", "--repo", "lifesupport-api"])
        self.assertEqual(args.command, "solve-conflict")
        self.assertEqual(args.jira_key, "OG-101")
        self.assertEqual(args.repo, "lifesupport-api")
        self.assertEqual(args.func, cli.handle_solve_conflict)

    def test_integrate_desenv_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(["integrate-desenv"])
        self.assertEqual(args.command, "integrate-desenv")
        self.assertIsNone(args.repo)
        self.assertEqual(args.func, cli.handle_integrate_desenv)

    def test_integrate_desenv_with_repo(self):
        parser = cli.build_parser()
        args = parser.parse_args(["integrate-desenv", "--repo", "optumsupport-fe"])
        self.assertEqual(args.repo, "optumsupport-fe")

    def test_prepare_merge_conflicts_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(["prepare-merge-conflicts", "--repo", "optumsupport-fe"])
        self.assertEqual(args.command, "prepare-merge-conflicts")
        self.assertEqual(args.repo, "optumsupport-fe")
        self.assertEqual(args.func, cli.handle_prepare_merge_conflicts)

    def test_finish_merge_conflicts_parsing(self):
        parser = cli.build_parser()
        args = parser.parse_args(["finish-merge-conflicts", "--repo", "optumsupport-fe"])
        self.assertEqual(args.command, "finish-merge-conflicts")
        self.assertEqual(args.repo, "optumsupport-fe")
        self.assertEqual(args.func, cli.handle_finish_merge_conflicts)


if __name__ == "__main__":
    unittest.main()
