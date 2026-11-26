"""Record a real flagreaper session into demo/transcript.json.

Commands run for real through a pseudo-terminal, so approval prompts appear
exactly as a user sees them; the recorder types the answers.

usage: python3 demo/record.py <path-to-demo-app-git-repo>
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


def run(cmd: str, cwd: Path, answers=()):
    """Run cmd in a pty; answer each `[y/N]` prompt from `answers` in order."""
    env = dict(os.environ, NO_COLOR="1", PYTHONPATH=str(ROOT), COLUMNS="104", GH_PROMPT_DISABLED="1")
    master, slave = pty.openpty()
    p = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave)
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
    text = out.decode(errors="replace").replace("\r\n", "\n").replace("\r", "")
    return text.rstrip("\n")


def main():
    app = Path(sys.argv[1]).resolve()
    snap = "flagsmith-snapshot.json"
    fr = f"python3 -m flagreaper"
    steps = []

    def step(caption, cmd, answers=(), show=None):
        print(f"$ {show or cmd}", file=sys.stderr)
        out = run(cmd, app, answers)
        print(out, file=sys.stderr)
        steps.append({"caption": caption, "cmd": show or cmd, "out": out})

    step("1 · Scan: read every flag from Flagsmith, grep the code, give each flag a verdict",
         f"{fr} scan --demo {snap}", show="flagreaper scan")
    step("Agents get the same answer as compact JSON: one line per debt flag, few tokens",
         f"{fr} scan --demo {snap} --json --only stale_on,stale_off,orphan",
         show="flagreaper scan --json --only stale_on,stale_off,orphan")
    step("Guardrail: a flag that still does work (different per environment) is refused",
         f"{fr} reap dark_mode --demo {snap}", show="flagreaper reap dark_mode")
    step("2 · Reap: keep the live ON branch, delete the dead one. It asks before writing, again before the PR",
         f"{fr} reap new_checkout --demo {snap}", answers=("y", "y"), show="flagreaper reap new_checkout")
    pr = subprocess.run(["gh", "pr", "list", "--head", "flagreaper/remove-new_checkout", "--json", "number", "-q",
                         ".[0].number"], cwd=app, capture_output=True, text=True).stdout.strip()
    step("The pull request is real, on GitHub, with the evidence from Flagsmith in the body",
         f"gh pr view {pr} --json title,author,headRefName,url,body -t "
         "'title:   {{.title}}\nauthor:  {{.author.login}}\nbranch:  {{.headRefName}}\nurl:     {{.url}}\n--\n{{.body}}'",
         show=f"gh pr view {pr}")
    step("Guardrail: archiving is refused while main still calls the flag",
         f"git checkout -q main && {fr} archive new_checkout --demo {snap}", show="flagreaper archive new_checkout")
    subprocess.run(["gh", "pr", "merge", pr, "--squash", "--delete-branch"], cwd=app, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=app)
    subprocess.run(["git", "pull", "-q"], cwd=app)
    step("3 · After the PR is merged: no references left, so it asks, then archives the flag",
         f"{fr} archive new_checkout --demo {snap}", answers=("y",),
         show="git pull && flagreaper archive new_checkout")
    step("Orphan flags (in Flagsmith, not in code) skip the code step and go straight to archive",
         f"{fr} reap old_onboarding_v1 --demo {snap}", answers=("y",), show="flagreaper reap old_onboarding_v1")

    (HERE / "transcript.json").write_text(json.dumps(steps, indent=1))
    print(f"wrote {HERE / 'transcript.json'}", file=sys.stderr)


if __name__ == "__main__":
    main()
