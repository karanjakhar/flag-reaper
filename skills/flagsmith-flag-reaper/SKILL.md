---
name: flagsmith-flag-reaper
description: Finish Flagsmith A/B tests and clean up flag debt end to end. Reads an experiment's result, rolls out the winner, removes the losing variant's code in a PR, and archives the flag; also finds flags that are fully on, fully off, or unused in code. Use when the user asks to close/conclude/ship an experiment, roll out a winning variant, remove stale or dead feature flags, or reduce flag debt in Flagsmith.
---

# Flag Reaper

Goal: take a flag from "experiment running" (or "forgotten") to "gone", in this order: decide, roll out, code PR, archive. A human approves every write.

## Setup (once)

```bash
pip install git+https://github.com/karanjakhar/flag-reaper   # stdlib only
# .env in the app folder, or real env vars:
FLAGSMITH_API_TOKEN=...     # Flagsmith account API key
FLAGSMITH_PROJECT_ID=...    # number in the project URL
# FLAGSMITH_API_URL=https://flags.example.com/api/v1   (self-hosted)
```

## Workflow A: close an experiment

1. **Read the result** (no writes until a prompt is answered):
   ```bash
   flagreaper close <flag> < /dev/null
   ```
   With no TTY it prints the results table and decision, then refuses to change anything. Show the user the table and the `Decision:` line.
   - "no clear winner" or "not enough users": stop and report. Do not lower `--threshold` unless the user asks.
2. **Roll out**, only after the user says yes to the decision:
   ```bash
   flagreaper close <flag> --yes
   ```
   Ends the experiment, sends 100% to the winner, and sets the winning value in every environment.
3. Continue with Workflow B for that flag. It is now `stale_on`. If it was changed less than 30 days ago, ask the user before adding `--stale-days 0`.

## Workflow B: remove a finished flag

1. **Scan** (cheap, one line per flag):
   ```bash
   flagreaper scan --json --only stale_on,stale_off,orphan
   ```
   `stale_on` keeps the ON code. `stale_off` keeps the OFF code. `orphan` means archive only. `active` means **never touch**.
2. **Ask the user which flags to reap.** Don't choose for them when more than one is listed.
3. **Preview** and show the diff:
   ```bash
   flagreaper reap <flag> --dry-run
   ```
   If it says no call sites could be rewritten, list the refs it printed, edit them by hand, and stop for that flag.
4. **Apply + PR** after the user approves the diff. Run the project's tests first if they exist; stop if they fail.
   ```bash
   flagreaper reap <flag> --yes
   ```
5. **Archive** only after the user says the PR is merged and deployed:
   ```bash
   git checkout main && git pull && flagreaper archive <flag> --yes
   ```
   It refuses while any reference remains.

## Rules

- Never pass `--yes` before the user has approved that exact step in chat.
- Never work around a `safety stop`. Report it.
- Keep output small: `--json --only ...`. Don't `cat` source files; `--dry-run` already shows the change.
- Finish with: experiments closed (winner, chance), PR URLs, flags archived, flags waiting for a merge.
