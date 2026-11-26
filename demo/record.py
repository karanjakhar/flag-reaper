"""Record a real, live flagreaper session into demo/transcript.json.

Commands run for real against Flagsmith and GitHub, through a pseudo-terminal, so the
approval prompts appear exactly as a user sees them; the recorder types the answers.

Prerequisites (see README "Re-making the demo video"):
  * .env in the repo root with FLAGSMITH_API_TOKEN and FLAGSMITH_PROJECT_ID
  * project seeded with demo/seed_flagsmith.py, experiment running with traffic
  * <demo-app> is a git repo with a GitHub remote, on main

usage: python3 demo/record.py <demo-app-git-repo>
"""
import json
import os
import pty
import select
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def env_with_dotenv():
    env = dict(os.environ, NO_COLOR="1", PYTHONPATH=str(ROOT), COLUMNS="104", GH_PROMPT_DISABLED="1", GH_PAGER="cat", PAGER="cat")
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


ENV = env_with_dotenv()


def run(cmd: str, cwd: Path, answers=()):
    """Run cmd in a pty; answer each `[y/N]` prompt from `answers` in order."""
    master, slave = pty.openpty()
    p = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, env=ENV, stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    out, answers = b"", list(answers)
    while True:
        r, _, _ = select.select([master], [], [], 0.2)
        if r:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if out.rstrip().endswith(b"[y/N]") and answers:
                os.write(master, (answers.pop(0) + "\n").encode())
        elif p.poll() is not None:
            break
    p.wait()
    os.close(master)
    return out.decode(errors="replace").replace("\r\n", "\n").replace("\r", "").rstrip("\n")


def main():
    app = Path(sys.argv[1]).resolve()
    fr = "python3 -m flagreaper"
    steps = []

    def step(caption, cmd, answers=(), show=None):
        print(f"$ {show or cmd}", file=sys.stderr)
        out = run(cmd, app, answers)
        print(out, file=sys.stderr)
        steps.append({"caption": caption, "cmd": show or cmd, "out": out})

    step("Scan: every flag in the live Flagsmith project, checked against the code",
         f"{fr} scan --stale-days 0", show="flagreaper scan --stale-days 0")
    step("Close the A/B test: read Flagsmith's result, end it, roll out the winner (asks twice)",
         f"{fr} close pricing_experiment", answers=("y", "y"), show="flagreaper close pricing_experiment")
    step("Same flag, one step later: no traffic split, same value everywhere, so it is now debt",
         f"{fr} scan --stale-days 0 --only stale_on,stale_off,orphan",
         show="flagreaper scan --stale-days 0 --only stale_on,stale_off,orphan")
    step("Reap: delete the losing variant's code, keep the winner, open a PR (asks twice)",
         f"{fr} reap pricing_experiment --stale-days 0", answers=("y", "y"),
         show="flagreaper reap pricing_experiment --stale-days 0")
    pr = subprocess.run(["gh", "pr", "list", "--head", "flagreaper/remove-pricing_experiment", "--json", "number",
                         "-q", ".[0].number"], cwd=app, capture_output=True, text=True).stdout.strip()
    step("The pull request is real, with the experiment evidence in its description",
         f"gh pr view {pr} --json title,author,headRefName,url,body -t "
         "'title:   {{.title}}\nauthor:  {{.author.login}}\nbranch:  {{.headRefName}}\nurl:     {{.url}}\n--\n{{.body}}'",
         show=f"gh pr view {pr}")
    step("Guardrail: a flag with a segment override still does work, so it is refused",
         f"{fr} reap beta_search --stale-days 0", show="flagreaper reap beta_search")
    step("Guardrail: archiving is refused while main still calls the flag",
         f"git checkout -q main && {fr} archive pricing_experiment --stale-days 0",
         show="flagreaper archive pricing_experiment")
    subprocess.run(["gh", "pr", "merge", pr, "--squash", "--delete-branch"], cwd=app, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=app)
    subprocess.run(["git", "pull", "-q"], cwd=app)
    step("After the PR is merged: no references left, so it asks, then archives the flag in Flagsmith",
         f"{fr} archive pricing_experiment --stale-days 0", answers=("y",),
         show="git pull && flagreaper archive pricing_experiment")
    step("For coding agents: the same scan as compact JSON, one line per flag, few tokens",
         f"{fr} scan --stale-days 0 --json --only stale_on,stale_off,orphan",
         show="flagreaper scan --json --only stale_on,stale_off,orphan")

    (HERE / "transcript.json").write_text(json.dumps(steps, indent=1))
    print(f"wrote {HERE / 'transcript.json'}", file=sys.stderr)


if __name__ == "__main__":
    main()
