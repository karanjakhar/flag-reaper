---
name: flagsmith-flag-reaper
description: Clean up Flagsmith flag debt end to end. Finds flags that are fully on, fully off, or unused in code, removes the dead branch from the source, opens a PR, and archives the flag after merge. Use when the user asks to remove stale/old/dead feature flags, reduce flag debt, or clean up Flagsmith.
---

# Flag Reaper

Goal: remove one or more stale Flagsmith flags **safely**: code first, Flagsmith last, with a human approving each write.

## Setup (once)

```bash
pip install git+https://github.com/karanjakhar/flag-reaper   # stdlib only, no deps
export FLAGSMITH_API_TOKEN=...      # Organisation API key (Organisation settings → API Keys)
export FLAGSMITH_PROJECT_ID=...     # number shown in the project URL
# self-hosted: export FLAGSMITH_API_URL=https://flagsmith.example.com/api/v1
```

No credentials? Add `--demo <snapshot.json>` to every command for an offline run.

## Workflow

1. **Scan** (cheap, ~1 line per flag). Read only the debt:
   ```bash
   flagreaper scan --json --only stale_on,stale_off,orphan
   ```
   Verdicts: `stale_on` (on everywhere, same value, no overrides, unchanged ≥30d) → keep ON path.
   `stale_off` → keep OFF path. `orphan` → no code refs, archive only. `active` → **never touch**.

2. **Show the user the list and ask which flags to reap.** Do not pick for them when more than one is listed.

3. **Preview** each chosen flag. Show the diff to the user:
   ```bash
   flagreaper reap <flag> --dry-run
   ```
   If it prints "no SDK call sites could be rewritten", list the refs it printed and edit those by hand; stop there for that flag.

4. **Apply + PR**, only after the user says yes to the diff:
   ```bash
   flagreaper reap <flag> --yes
   ```
   This branches `flagreaper/remove-<flag>`, commits, pushes and runs `gh pr create`. Run the project's tests before pushing if they exist; if they fail, stop and report.

5. **Archive later.** Only after the PR is merged and deployed, and the user confirms:
   ```bash
   git checkout main && git pull
   flagreaper archive <flag> --yes
   ```
   `archive` refuses while any code reference remains.

## Rules

- Never pass `--yes` before the user has approved that exact step in chat.
- Never reap a flag whose verdict is `active`; the tool refuses, do not work around it.
- Never archive a flag in the same session the code PR was opened unless the user says the PR is merged and deployed.
- Keep output small: prefer `--json --only ...`; do not `cat` whole source files to check the diff, `--dry-run` already shows it.
- Report at the end: flags reaped, PR URLs, flags waiting for archive.
