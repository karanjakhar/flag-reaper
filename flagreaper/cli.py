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


def client_for(args):
    if args.demo:
        return DemoClient(args.demo)
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

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except FlagsmithError as e:
        print(f"{C['r']}flagsmith:{C['x']} {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
