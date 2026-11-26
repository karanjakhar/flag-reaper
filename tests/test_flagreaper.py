import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flagreaper.analyze import classify, find_refs  # noqa: E402
from flagreaper.cli import main  # noqa: E402
from flagreaper.rewrite import rewrite_text  # noqa: E402

DEMO = ROOT / "examples" / "demo-flags.json"
APP = ROOT / "examples" / "demo-app"
NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)


def compiles(src: str) -> bool:
    try:
        compile(src, "<t>", "exec")
        return True
    except SyntaxError:
        return False


class RewritePython(unittest.TestCase):
    def test_keep_on_branch_drops_else(self):
        src = 'def f(flags):\n    if flags.is_feature_enabled("k"):\n        a()\n        b()\n    else:\n        c()\n    d()\n'
        out = rewrite_text(src, "py", "k", True)
        self.assertEqual(out, "def f(flags):\n    a()\n    b()\n    d()\n")

    def test_keep_off_branch_uses_else(self):
        src = 'if flags.is_feature_enabled("k"):\n    a()\nelse:\n    c()\n'
        self.assertEqual(rewrite_text(src, "py", "k", False), "c()\n")

    def test_not_inverts(self):
        src = 'if not flags.is_feature_enabled("k"):\n    a()\n'
        self.assertEqual(rewrite_text(src, "py", "k", False), "a()\n")
        self.assertEqual(rewrite_text(src, "py", "k", True), "")

    def test_elif_promoted_when_off(self):
        src = 'if flags.is_feature_enabled("k"):\n    a()\nelif x:\n    b()\nelse:\n    c()\n'
        out = rewrite_text(src, "py", "k", False)
        self.assertEqual(out, "if x:\n    b()\nelse:\n    c()\n")

    def test_empty_block_gets_pass(self):
        src = 'def f(flags):\n    if flags.is_feature_enabled("k"):\n        a()\n\ndef g():\n    pass\n'
        out = rewrite_text(src, "py", "k", False)
        self.assertTrue(compiles(out), out)
        self.assertIn("    pass", out.split("def g")[0])

    def test_value_and_ternary(self):
        src = 'n = flags.get_feature_value("k")\ns = "x" if flags.is_feature_enabled("k") else "y"\n'
        self.assertEqual(rewrite_text(src, "py", "k", True, 5), 'n = 5\ns = "x"\n')

    def test_other_flags_untouched(self):
        src = 'if flags.is_feature_enabled("kk"):\n    a()\n'
        self.assertEqual(rewrite_text(src, "py", "k", True), src)


class RewriteJS(unittest.TestCase):
    def test_if_else_keep_on(self):
        src = 'function f() {\n  if (flagsmith.hasFeature("k")) {\n    a();\n  } else {\n    b();\n  }\n}\n'
        self.assertEqual(rewrite_text(src, "c", "k", True), "function f() {\n  a();\n}\n")

    def test_if_only_off_removed(self):
        src = 'x();\nif (flagsmith.hasFeature("k")) {\n  a("}");\n}\ny();\n'
        self.assertEqual(rewrite_text(src, "c", "k", False), "x();\ny();\n")

    def test_negation(self):
        src = 'if (!flags.isFeatureEnabled("k")) {\n  a();\n}\n'
        self.assertEqual(rewrite_text(src, "c", "k", False), "a();\n")

    def test_else_if_chain_off(self):
        src = 'if (flagsmith.hasFeature("k")) {\n  a();\n} else if (z) {\n  b();\n} else {\n  c();\n}\n'
        self.assertEqual(rewrite_text(src, "c", "k", False), "if (z) {\n  b();\n} else {\n  c();\n}\n")

    def test_else_if_chain_on(self):
        src = 'if (flagsmith.hasFeature("k")) {\n  a();\n} else if (z) {\n  b();\n}\n'
        self.assertEqual(rewrite_text(src, "c", "k", True), "a();\n")

    def test_ternary_and_value(self):
        src = 'const b = flagsmith.hasFeature("k") ? "new" : "";\nconst n = flagsmith.getValue("k");\n'
        self.assertEqual(rewrite_text(src, "c", "k", True, "v"), 'const b = "new";\nconst n = "v";\n')


class RewriteExperimentValues(unittest.TestCase):
    """After an A/B test, the flag's value is fixed to the winner; comparisons fold away."""

    def test_js_ternary_on_value(self):
        src = 'return flagsmith.getValue("p") === "annual_first" ? annualFirst() : monthlyFirst();\n'
        self.assertEqual(rewrite_text(src, "c", "p", True, "annual_first"), "return annualFirst();\n")
        self.assertEqual(rewrite_text(src, "c", "p", True, "monthly_first"), "return monthlyFirst();\n")

    def test_js_if_on_value_with_not_equal(self):
        src = 'if (flagsmith.getValue("p") !== "annual_first") {\n  a();\n} else {\n  b();\n}\n'
        self.assertEqual(rewrite_text(src, "c", "p", True, "annual_first"), "b();\n")

    def test_python_if_on_value(self):
        src = 'if flags.get_feature_value("p") == "annual_first":\n    a()\nelse:\n    b()\n'
        self.assertEqual(rewrite_text(src, "py", "p", True, "annual_first"), "a()\n")

    def test_experiment_flag_value(self):
        src = 'if (flagsmith.getExperimentFlag("p").value === "annual_first") {\n  a();\n} else {\n  b();\n}\n'
        self.assertEqual(rewrite_text(src, "c", "p", True, "annual_first"), "a();\n")
        py = 'flag = 1\nif fs.get_experiment_flag("p", identifier=user.id).value == "x":\n    a()\nelse:\n    b()\n'
        self.assertEqual(rewrite_text(py, "py", "p", True, "y"), "flag = 1\nb()\n")

    def test_unrelated_literal_if_untouched(self):
        src = 'if (true) {\n  keep();\n}\nif (flagsmith.hasFeature("k")) {\n  a();\n}\n'
        self.assertEqual(rewrite_text(src, "c", "k", False), 'if (true) {\n  keep();\n}\n')
        py = 'if True:\n    keep()\nif flags.is_feature_enabled("k"):\n    a()\n'
        self.assertEqual(rewrite_text(py, "py", "k", False), 'if True:\n    keep()\n')

    def test_nested_flag_in_kept_branch(self):
        src = 'if flags.is_feature_enabled("k"):\n    if not flags.is_feature_enabled("k"):\n        x()\n    y()\n'
        self.assertEqual(rewrite_text(src, "py", "k", True), "y()\n")


class Decide(unittest.TestCase):
    """Winner rule on top of Flagsmith's experiment results payload (shape taken from the live API)."""

    def payload(self, n_c, conv_c, n_v, conv_v, chance):
        return {"payload": {"metrics": [{
            "variants": {"control": {"n": n_c, "sum": conv_c}, "annual_first": {"n": n_v, "sum": conv_v}},
            "inference": {"annual_first": {"lift": 0.95, "chance_to_win": chance}}}]}}

    def test_clear_winner(self):
        from flagreaper.cli import decide
        _, winner, _ = decide(self.payload(385, 38, 415, 80, 0.996), 0.95, 50)
        self.assertEqual(winner, "annual_first")

    def test_control_wins(self):
        from flagreaper.cli import decide
        _, winner, _ = decide(self.payload(400, 80, 400, 40, 0.01), 0.95, 50)
        self.assertEqual(winner, "control")

    def test_no_winner_yet(self):
        from flagreaper.cli import decide
        _, winner, why = decide(self.payload(400, 40, 400, 44, 0.7), 0.95, 50)
        self.assertIsNone(winner)
        self.assertIn("Keep the experiment running", why)

    def test_too_few_users(self):
        from flagreaper.cli import decide
        _, winner, why = decide(self.payload(20, 2, 25, 9, 0.99), 0.95, 50)
        self.assertIsNone(winner)
        self.assertIn("not enough users", why)


class Classify(unittest.TestCase):
    def test_demo_verdicts(self):
        snap = json.loads(DEMO.read_text())
        refs = find_refs(APP, [f["name"] for f in snap["flags"]])
        got = {r["flag"]: r["verdict"] for r in classify(snap, refs, 30, NOW)}
        self.assertEqual(got, {
            "new_checkout": "stale_on", "max_upload_mb": "stale_on", "legacy_banner": "stale_off",
            "old_onboarding_v1": "orphan", "dark_mode": "active",
            "pricing_experiment": "active", "beta_search": "active",
        })

    def test_recent_change_is_active(self):
        snap = {"environments": ["P"], "flags": [{"name": "k", "states": {
            "P": {"enabled": True, "value": None, "overrides": 0, "updated": "2026-11-20T00:00:00Z"}}}]}
        self.assertEqual(classify(snap, {"k": ["a:1"]}, 30, NOW)[0]["verdict"], "active")


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(APP, self.tmp / "app")
        shutil.copy(DEMO, self.tmp / "flags.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*args, "--repo", str(self.tmp / "app"), "--demo", str(self.tmp / "flags.json")])
        return code, buf.getvalue()

    def test_reap_all_stale_flags_keeps_python_valid(self):
        for flag in ("new_checkout", "legacy_banner", "max_upload_mb"):
            code, _ = self.run_cli("reap", flag, "--yes", "--no-pr")
            self.assertEqual(code, 0)
        py = (self.tmp / "app/api/orders.py").read_text()
        self.assertTrue(compiles(py), py)
        js = (self.tmp / "app/src/checkout.js").read_text()
        for k in ("new_checkout", "legacy_banner", "max_upload_mb"):
            self.assertNotIn(k, py + js)
        self.assertIn("dark_mode", js)  # active flag untouched
        if shutil.which("node"):
            r = subprocess.run(["node", "--check", "-"], input=js.replace("import flagsmith from \"flagsmith\";", "")
                               .replace("export ", ""), text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_active_flag_is_refused(self):
        code, out = self.run_cli("reap", "beta_search", "--yes", "--no-pr")
        self.assertEqual(code, 1)
        self.assertIn("safety stop", out)

    def test_archive_refused_while_referenced(self):
        code, out = self.run_cli("archive", "new_checkout", "--yes")
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "flags.archived.log").exists())

    def test_archive_after_reap(self):
        self.run_cli("reap", "new_checkout", "--yes", "--no-pr")
        code, _ = self.run_cli("archive", "new_checkout", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual((self.tmp / "flags.archived.log").read_text(), "new_checkout\n")

    def test_no_tty_without_yes_refuses(self):
        code, out = self.run_cli("archive", "old_onboarding_v1")
        self.assertIn("needs a human", out)
        self.assertFalse((self.tmp / "flags.archived.log").exists())


if __name__ == "__main__":
    unittest.main()
