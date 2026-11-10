import json
from pathlib import Path
import tempfile
import unittest
from ui_subpaths import configure


class UiSubpathsTests(unittest.TestCase):
    def test_snapshots_precede_scoped_rollouts_and_preserve_api_settings(self):
        calls = []

        def run(*args):
            calls.append(args)
            if "get" in args:
                if "deployment" in args:
                    return json.dumps({"spec": {"template": {"spec": {"volumes": [
                        {"name": "ui-config", "configMap": {"name": "nginx"}}]}}}}).encode()
                return json.dumps({"data": {"KAGENT_UI_API_BASE_URL": "/api"}}).encode()
            if "patch" in args:
                self.assertEqual(len(list(state.glob("*.json"))), 1)
                patch = json.loads(args[-1])
                self.assertEqual(list(patch["data"]), ["KAGENT_UI_BASE_PATH"])
            return b""

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            state = Path(directory)
            configure(True, state, run)
            snapshots = json.loads(next(state.glob("*.json")).read_text())
            self.assertEqual([entry[2] for entry in snapshots], ["/kagent"])
            self.assertEqual(len([call for call in calls if "restart" in call]), 1)
            self.assertTrue(all(call[1] == "factory-executions" for call in calls))

    def test_dry_run_only_reads(self):
        calls = []

        def run(*args):
            calls.append(args)
            return json.dumps({"spec": {"template": {"spec": {"volumes": [
                {"name": "ui-config", "secret": {"secretName": "nginx"}}]}}}}).encode()

        configure(run=run)
        self.assertTrue(all("get" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
