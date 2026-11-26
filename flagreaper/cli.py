"""flagreaper – find stale Flagsmith flags, remove their dead code, open a PR, archive the flag.

Every write (source files, git, GitHub, Flagsmith) stops for human approval.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .analyze import classify, find_refs
from .api import DemoClient, FlagsmithClient, FlagsmithError
from .rewrite import plan_rewrite, unified

C = {"r": "\033[31m", "g": "\033[32m", "y": "\033[33m", "b": "\033[1m", "d": "\033[2m", "x": "\033[0m"}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}

BADGE = {"stale_on": "g", "stale_off": "y", "orphan": "r", "active": "d"}


def load_dotenv(*dirs: Path) -> None:
    """Load KEY=VALUE lines from .env files (cwd, repo, flagreaper home); never overrides real env vars."""
    seen = set()
    for d in dirs:
        f = (d / ".env").resolve()
        if f in seen or not f.is_file():
            continue
        seen.add(f)
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def client_for(args):
    if args.demo:
        return DemoClient(args.demo)
    load_dotenv(Path.cwd(), Path(args.repo), Path.home() / ".config" / "flagreaper")
    return FlagsmithClient.from_env()


def analyse(args):
    cli = client_for(args)
    snap = cli.snapshot()
    refs = find_refs(Path(args.repo), [f["name"] for f in snap["flags"]])
    return cli, snap, classify(snap, refs, args.stale_days)


def approve(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"{C['y']}auto-approved (--yes):{C['x']} {question}")
        return True
    if not sys.stdin.isatty():
        print(f"{C['r']}refusing: '{question}' needs a human (no TTY). Re-run interactively or pass --yes.{C['x']}")
        return False
    ans = input(f"{C['b']}? {question} [y/N] {C['x']}").strip().lower()
    return ans in ("y", "yes")


def sh(cmd: list[str], cwd: Path, check=True) -> str:
    r = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and r.returncode:
        raise SystemExit(f"$ {' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout.strip()


# ----------------------------------------------------------------- commands


def cmd_scan(args):
    _, snap, rows = analyse(args)
    if args.only:
        rows = [r for r in rows if r["verdict"] in args.only.split(",")]
    if args.json:
        # compact, agent-friendly: only what's needed to decide the next step
        print(json.dumps([
            {"flag": r["flag"], "v": r["verdict"], "why": r["reason"], "refs": len(r["refs"])}
            for r in rows
        ], separators=(",", ":")))
        return 0
    print(f"{C['b']}flagreaper{C['x']} project={snap['project']} envs={','.join(snap['environments'])} "
          f"stale>={args.stale_days}d\n")
    w = max([len(r["flag"]) for r in rows] + [4])
    print(f"{'FLAG'.ljust(w)}  {'VERDICT':10} {'REFS':>4}  REASON")
    for r in rows:
        v = r["verdict"]
        print(f"{r['flag'].ljust(w)}  {C[BADGE[v]]}{v:10}{C['x']} {len(r['refs']):>4}  {r['reason']}")
    n = sum(r["verdict"] != "active" for r in rows)
    print(f"\n{n} of {len(rows)} flags are debt. Next: flagreaper reap <flag>")
    return 0


def cmd_reap(args):
    cli, snap, rows = analyse(args)
    row = next((r for r in rows if r["flag"] == args.flag), None)
    if not row:
        print(f"unknown flag '{args.flag}'")
        return 2
    repo = Path(args.repo).resolve()
    v = row["verdict"]
    print(f"{C['b']}{row['flag']}{C['x']}: {C[BADGE[v]]}{v}{C['x']} — {row['reason']}")

    if v == "active":
        print(f"{C['r']}safety stop:{C['x']} flag still does work. Not touching code or Flagsmith.")
        return 1

    if v == "orphan":
        print("No code references. Only the Flagsmith flag needs archiving.")
        if args.dry_run or not approve(f"Archive '{row['flag']}' in Flagsmith?", args.yes):
            print("nothing changed.")
            return 0
        print(cli.archive({"id": row["id"], "name": row["flag"]}))
        return 0

    enabled = v == "stale_on"
    changes = plan_rewrite(repo, row["flag"], enabled, row["value"])
    if not changes:
        print("Flag is referenced but no SDK call sites could be rewritten automatically:")
        for ref in row["refs"]:
            print("  ", ref)
        return 1

    diff = unified(repo, changes)
    print(f"\nKeeping the {'ON' if enabled else 'OFF'} path. {len(changes)} file(s):\n")
    print(diff)
    if args.dry_run:
        print(f"{C['d']}--dry-run: nothing written.{C['x']}")
        return 0
    if not approve(f"Apply this change to {len(changes)} file(s)?", args.yes):
        print("aborted, nothing written.")
        return 0

    is_git = (repo / ".git").exists() or sh(["git", "rev-parse", "--is-inside-work-tree"], repo, False) == "true"
    branch = f"flagreaper/remove-{row['flag']}"
    if is_git and not args.no_pr:
        sh(["git", "checkout", "-b", branch], repo)
    for path, (_, new) in changes.items():
        path.write_text(new)
    print(f"{C['g']}wrote{C['x']} {len(changes)} file(s)")

    if not is_git or args.no_pr:
        print(f"Next: review, then `flagreaper archive {row['flag']}` once deployed.")
        return 0

    files = [str(p.relative_to(repo)) for p in changes]
    sh(["git", "add", *files], repo)
    msg = (f"chore(flags): remove stale flag '{row['flag']}'\n\n"
           f"{row['reason']}. Kept the {'ON' if enabled else 'OFF'} path.\n"
           f"Generated by flagreaper; flag archived in Flagsmith after merge.")
    sh(["git", "commit", "-m", msg], repo)
    print(f"{C['g']}committed{C['x']} on {branch}")

    has_remote = bool(sh(["git", "remote"], repo, False))
    if not has_remote or not shutil.which("gh"):
        print("No remote or gh CLI: push and open the PR yourself.")
        return 0
    if not approve(f"Push '{branch}' and open a pull request?", args.yes):
        print("branch kept locally.")
        return 0
    sh(["git", "push", "-u", "origin", branch], repo)
    body = (
        f"## Remove stale flag `{row['flag']}`\n\n"
        f"**Evidence from Flagsmith:** {row['reason']}.\n\n"
        f"**Change:** kept the {'ON' if enabled else 'OFF'} branch, deleted the dead one in "
        f"{len(files)} file(s):\n" + "".join(f"- `{f}`\n" for f in files) +
        f"\n**After merge + deploy:** `flagreaper archive {row['flag']}` "
        "(verifies no references remain, asks before archiving).\n\n"
        "_Opened by flagreaper — a human approved this diff._"
    )
    url = sh(["gh", "pr", "create", "--title", f"Remove stale flag {row['flag']}", "--body", body,
              "--head", branch], repo)
    print(f"{C['g']}PR:{C['x']} {url}")
    return 0


def cmd_archive(args):
    cli, _, rows = analyse(args)
    row = next((r for r in rows if r["flag"] == args.flag), None)
    if not row:
        print(f"unknown flag '{args.flag}'")
        return 2
    if row["refs"]:
        print(f"{C['r']}safety stop:{C['x']} '{args.flag}' still referenced at:")
        for ref in row["refs"][:10]:
            print("  ", ref)
        print("Merge the cleanup PR (and check out that code) first.")
        return 1
    if not approve(f"Archive '{args.flag}' in Flagsmith? (no code references remain)", args.yes):
        print("nothing changed.")
        return 0
    print(cli.archive({"id": row["id"], "name": row["flag"]}))
    return 0


def _pct(x, signed=False):
    if x is None:
        return "—"
    return f"{x * 100:+.0f}%" if signed else f"{x * 100:.1f}%"


def decide(results: dict, threshold: float, min_users: int):
    """Pick a winner from Flagsmith's Bayesian results.

    Returns (rows, winner_key | None, explanation). `winner_key` is "control" or a variant key.
    """
    metric = (results.get("payload") or {}).get("metrics", [{}])[0]
    variants = metric.get("variants") or {}
    inference = metric.get("inference") or {}
    rows = []
    for key, v in variants.items():
        n, conv = int(v.get("n") or 0), float(v.get("sum") or 0)
        inf = inference.get(key, {})
        rows.append({"key": key, "n": n, "conv": conv, "rate": conv / n if n else None,
                     "lift": inf.get("lift"), "chance": inf.get("chance_to_win")})
    if not rows:
        return rows, None, "no results yet (no exposures recorded)"
    challengers = [r for r in rows if r["key"] != "control"]
    if len(challengers) == 1 and challengers[0]["chance"] is not None:
        for r in rows:
            if r["key"] == "control":
                r["chance"] = 1 - challengers[0]["chance"]
    small = [r["key"] for r in rows if r["n"] < min_users]
    if small:
        return rows, None, f"not enough users yet in {', '.join(small)} (need {min_users} each)"
    best = max(rows, key=lambda r: r["chance"] or 0)
    if (best["chance"] or 0) >= threshold:
        return rows, best["key"], f"{best['key']} wins: {_pct(best['chance'])} chance to be best (threshold {threshold:.0%})"
    return rows, None, (f"no clear winner yet: best is {best['key']} at {_pct(best['chance'])}, "
                        f"needs {threshold:.0%}. Keep the experiment running.")


def cmd_close(args):
    """Read an experiment's result, roll the winner out, and set the flag up for code cleanup."""
    if args.demo:
        print("close needs a live Flagsmith project (FLAGSMITH_API_TOKEN, FLAGSMITH_PROJECT_ID).")
        return 2
    fs = client_for(args)
    env, exp = fs.find_experiment(args.flag)
    if not exp:
        print(f"no experiment found for '{args.flag}'.")
        return 2
    exp = fs.experiment(env, exp["id"])
    feat = exp["feature"]
    control_value = (exp.get("experiment_rollout") or {}).get("feature_state_value", {}).get("value")
    options = {o["key"]: o for o in feat.get("multivariate_options") or [] if o.get("key")}
    metric = (exp.get("metrics") or [{}])[0]
    better = {"up": "higher is better", "down": "lower is better"}.get(metric.get("direction"), "")

    print(f"{C['b']}{feat['name']}{C['x']} · \"{exp['name']}\" · {env['name']} · {exp['status']}")
    print(f"{C['d']}Hypothesis:{C['x']} {exp.get('hypothesis', '')}")
    res = fs.experiment_results(env, exp["id"], refresh=exp["status"] != "completed")
    as_of = (res.get("as_of") or "")[:16].replace("T", " ")
    print(f"{C['d']}Metric:{C['x']} {metric.get('metric_name', '?')} ({better}) · results as of {as_of} UTC\n")

    rows, winner, why = decide(res, args.threshold, args.min_users)
    label = lambda k: f"{control_value} (control)" if k == "control" else k
    w = max([len(label(r["key"])) for r in rows] + [7])
    print(f"{'VARIANT'.ljust(w)}  {'USERS':>6} {'CONVERTED':>9} {'RATE':>7} {'LIFT':>6}  CHANCE TO BE BEST")
    for r in rows:
        mark = f"  {C['g']}← winner{C['x']}" if r["key"] == winner else ""
        print(f"{label(r['key']).ljust(w)}  {r['n']:>6} {r['conv']:>9.0f} {_pct(r['rate']):>7} "
              f"{_pct(r['lift'], True):>6}  {_pct(r['chance']):>8}{mark}")
    print(f"\n{C['b']}Decision:{C['x']} {why}")
    if winner is None:
        return 0

    win_value = control_value if winner == "control" else options[winner]["string_value"] \
        if options[winner].get("type") == "unicode" else options[winner].get("integer_value")
    if exp["status"] != "completed":
        q = f"End the experiment and send 100% of {env['name']} to '{win_value}'?"
        if not approve(q, args.yes):
            print("nothing changed; the experiment keeps running.")
            return 0
        fs.complete_experiment(env, exp["id"], None if winner == "control" else options[winner]["id"])
        print(f"{C['g']}ended{C['x']} experiment {exp['id']}, rolled out '{win_value}' in {env['name']}")
    else:
        print(f"experiment already ended (rollout: {exp.get('rollout_decision')}).")

    names = ", ".join(e["name"] for e in fs.environments())
    if not approve(f"Make '{win_value}' the flag's value in every environment ({names})?", args.yes):
        print("left the other environments as they are.")
        return 0
    done = fs.set_value_everywhere(feat["id"], win_value)
    print(f"{C['g']}{feat['name']} = {json.dumps(win_value)}{C['x']} in {', '.join(done)}")
    print(f"Next: flagreaper reap {feat['name']}   (removes the losing variant's code and opens a PR)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="flagreaper", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="code to scan (default: .)")
    common.add_argument("--demo", metavar="SNAPSHOT.json",
                        help="use an offline Flagsmith snapshot instead of the API")
    common.add_argument("--stale-days", type=int, default=int(os.environ.get("FLAGREAPER_STALE_DAYS", 30)))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", parents=[common], help="classify every flag")
    s.add_argument("--json", action="store_true", help="compact JSON for agents")
    s.add_argument("--only", help="comma list: stale_on,stale_off,orphan,active")
    s.set_defaults(fn=cmd_scan)

    for name, fn, h in (("reap", cmd_reap, "remove one stale flag's code and open a PR"),
                        ("archive", cmd_archive, "archive a flag once code no longer uses it")):
        s = sub.add_parser(name, parents=[common], help=h)
        s.add_argument("flag")
        s.add_argument("--yes", action="store_true", help="skip approval prompts (CI only)")
        if name == "reap":
            s.add_argument("--dry-run", action="store_true", help="show diff, write nothing")
            s.add_argument("--no-pr", action="store_true", help="edit files only, no git/gh")
        s.set_defaults(fn=fn)

    s = sub.add_parser("close", parents=[common],
                       help="read an experiment's result, roll out the winner, prepare the flag for cleanup")
    s.add_argument("flag")
    s.add_argument("--threshold", type=float, default=0.95, help="chance to be best needed (default 0.95)")
    s.add_argument("--min-users", type=int, default=50, help="users needed per variant (default 50)")
    s.add_argument("--yes", action="store_true", help="skip approval prompts (CI only)")
    s.set_defaults(fn=cmd_close)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except FlagsmithError as e:
        print(f"{C['r']}flagsmith:{C['x']} {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
