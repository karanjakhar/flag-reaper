# Flag Reaper

**Finish what your A/B test started.**

Flag Reaper reads your Flagsmith Experimentation result, rolls out the winner, deletes the losing variant's code in a pull request, and archives the flag. It also finds every other flag that has stopped doing work. It asks a human before every change.

Built for **Toggle Wars, the Flagsmith Experimentation Hackathon**. Track 02 (AI Agents and Feature Flags), also shipped as an Agent Skill for Track 04.

| | |
|---|---|
| 🎬 **Demo video** (3 min, narrated, everything live) | [`demo/flag-reaper-demo.mp4`](demo/flag-reaper-demo.mp4) |
| 🔀 **Pull request opened by the tool** | [flag-reaper-demo-shop#2](https://github.com/karanjakhar/flag-reaper-demo-shop/pull/2) |
| 🤖 **Agent Skill** | [`skills/flagsmith-flag-reaper/SKILL.md`](skills/flagsmith-flag-reaper/SKILL.md) |

---

## The problem

An A/B test ends and someone ships the winner to 100%. Then everyone moves on.

The flag check stays in the code, and so does the losing variant. Multiply that by every experiment and every rollout, and after a year nobody knows which flags are safe to delete. So nobody deletes them. You end up with dead code, more paths to test, and a flag dashboard nobody trusts.

The hard part isn't deleting code. It's knowing, with evidence, that a flag is finished, then doing the four chores in the right order without breaking anything.

## What Flag Reaper does

```
1  scan      read every flag from Flagsmith, find where the code uses it, give each one a verdict
2  close     read the experiment result, end it, roll the winner out everywhere
3  reap      delete the losing code, keep the winner, open a pull request
4  archive   once the PR is merged, archive the flag in Flagsmith
```

### 1 · scan

```
FLAG                VERDICT    REFS  REASON
max_upload_mb       stale_on      2  ON in all 3 envs, unchanged 209d
legacy_banner       stale_off     3  OFF in all 3 envs, unchanged 269d
old_onboarding_v1   orphan        0  no references in code
beta_search         active        1  1 segment/identity override(s)
dark_mode           active        1  differs across environments
pricing_experiment  active        1  experiment running in Production
```

| Verdict | Means | What Flag Reaper offers |
|---|---|---|
| `stale_on` | on in every environment, same value, no overrides, unchanged for 30+ days | keep the ON code, delete the check |
| `stale_off` | off everywhere, same conditions | delete the ON code |
| `orphan` | exists in Flagsmith, never used in code | archive only |
| `active` | anything else: an experiment is running, variants split traffic, segment or identity overrides, environments differ, or a recent change | **nothing. Refused.** |

### 2 · close (Flagsmith Experimentation)

```
pricing_experiment · "Annual vs monthly plan first" · Production · running
Metric: Checkout started (higher is better)

VARIANT                   USERS CONVERTED    RATE   LIFT  CHANCE TO BE BEST
monthly_first (control)     385        38    9.9%      —      0.4%
annual_first                415        80   19.3%   +95%     99.6%  ← winner

Decision: annual_first wins: 99.6% chance to be best (threshold 95%)
? End the experiment and send 100% of Production to 'annual_first'? [y/N] y
? Make 'annual_first' the flag's value in every environment? [y/N] y
```

The numbers come from Flagsmith's own Bayesian engine. Flag Reaper only applies your rule: a variant must reach 95% chance to be best, with at least 50 users per variant (both configurable). If there's no clear winner, it says so and changes nothing.

### 3 · reap

```diff
   // A/B test "Annual vs monthly plan first" (Flagsmith Experimentation)
-  if (flagsmith.getExperimentFlag("pricing_experiment").value === "annual_first") {
-    return renderPlans(["annual", "monthly"]);
-  } else {
-    return renderPlans(["monthly", "annual"]);
-  }
+  return renderPlans(["annual", "monthly"]);
```

It shows the diff, asks, commits on a new branch, asks again, then pushes and opens a PR. The PR description carries the evidence from Flagsmith.

### 4 · archive

`archive` refuses while any code still references the flag, so a flag is never archived while it's still being evaluated. Once the PR is merged, it asks and then archives. Archiving can be undone in Flagsmith; nothing is deleted.

## Where it stops for a person

| Step | Automatic | Asks first |
|---|---|---|
| Read Flagsmith, read results, scan code, show the diff | ✅ | |
| End the experiment and roll out the winner | | ✅ |
| Set the winning value in every environment | | ✅ |
| Write code changes | | ✅ |
| Push and open the PR | | ✅ |
| Archive the flag | | ✅ (and refused while code still uses it) |

Without a terminal (CI, a coding agent), every change is refused unless `--yes` is passed. The Agent Skill tells the agent to ask in chat before it ever passes `--yes`.

---

## Try it in 30 seconds (no account needed)

Python 3.10+, no dependencies.

```bash
git clone https://github.com/karanjakhar/flag-reaper && cd flag-reaper
python3 -m flagreaper scan --repo examples/demo-app --demo examples/demo-flags.json
python3 -m flagreaper reap new_checkout --repo examples/demo-app --demo examples/demo-flags.json --dry-run
```

`--demo` reads an offline snapshot instead of the API. `close` needs a live project.

## Use it on your project

```bash
pip install git+https://github.com/karanjakhar/flag-reaper

# .env in your app's folder (or real environment variables)
FLAGSMITH_API_TOKEN=...      # Flagsmith → Account settings → API key
FLAGSMITH_PROJECT_ID=...     # the number in your project's URL
# FLAGSMITH_API_URL=https://flags.example.com/api/v1   # self-hosted

cd your-app
flagreaper scan
flagreaper close pricing_experiment
flagreaper reap pricing_experiment
flagreaper archive pricing_experiment   # after the PR is merged
```

Works with Flagsmith Cloud and self-hosted, and with v1 and v2 feature versioning.

## Use it from a coding agent

Copy [`skills/flagsmith-flag-reaper/SKILL.md`](skills/flagsmith-flag-reaper/SKILL.md) to your agent's skills folder (for Claude Code: `.claude/skills/flagsmith-flag-reaper/SKILL.md`), then ask: *"close the pricing experiment and clean up the flag"*, or *"clean up our stale flags"*.

The agent runs `flagreaper scan --json`, which returns one short line per flag so it costs few tokens. It shows you each diff and result, and only passes `--yes` for a step after you approve it in chat.

```json
[{"flag":"legacy_banner","v":"stale_off","why":"OFF in all 3 envs, unchanged 269d","refs":3}]
```

A weekly flag-debt report for CI: [`examples/flag-debt-report.yml`](examples/flag-debt-report.yml).

## What the code rewriter understands

| Pattern | Languages |
|---|---|
| `if [not] flags.is_feature_enabled("k"):` … `elif` … `else:` | Python |
| `if ([!]flagsmith.hasFeature("k")) {…} else if (…) {…} else {…}` | JS/TS, Java, Kotlin, Go, Dart |
| `getExperimentFlag("k").value === "annual_first"` and `get_experiment_flag("k", identifier=…).value == …` | experiment flags |
| `getValue("k")`, `get_feature_value("k")` | replaced by the final value, comparisons folded |
| `cond ? a : b`, `a if cond else b` | JS/TS, Python |

Only expressions that come from the flag being removed are ever folded. An unrelated `if (true)` elsewhere is left alone. When a flag is used in a way the rewriter doesn't recognise (for example stored in a constant), `reap` lists the places and stops so you can edit them by hand.

## Honest notes

- Verdicts are cautious on purpose. If an override count can't be read, the flag counts as `active`.
- Code changes ship first and the flag is archived last. Archiving a flag that's still evaluated would flip users to the default.
- Edge identity overrides aren't visible through the endpoints used. Check those flags yourself if you target individual users.
- Ending an experiment and setting its result use endpoints from Flagsmith's coding-agent guide, plus `experiments/<id>/complete/` (found by testing on throwaway flags, not yet in the public docs).
- The demo experiment uses simulated traffic (800 users through the official Python SDK), which the hackathon allows.

## Reproduce the demo

```bash
python3 demo/seed_flagsmith.py                 # 3 environments, 7 flags, a segment override, an A/B flag
.venv/bin/python demo/simulate_traffic.py --flag pricing_experiment --event checkout_started \
    --users 800 --rate monthly_first=0.10 --rate annual_first=0.16
python3 demo/record.py ../flag-reaper-demo-shop   # runs the real commands, answers the prompts
python3 demo/render.py                          # transcript + narration -> demo/flag-reaper-demo.mp4
```

The voiceover is generated offline with [Piper](https://github.com/OHF-Voice/piper1-gpl) (voice `en_US-ryan-high`) from [`demo/narration.json`](demo/narration.json). Each scene stretches to fit its line. Setup: `.venv/bin/pip install piper-tts` and `.venv/bin/python -m piper.download_voices en_US-ryan-high` in `~/.cache/piper-voices`. Set `NO_VOICE=1` for a silent render.

## Development

```bash
python3 -m unittest discover -s tests -v   # 30 tests, including end-to-end runs on the demo app
```

`flagreaper/api.py` talks to Flagsmith, `analyze.py` decides verdicts, `rewrite.py` edits code, `cli.py` holds the commands and approval prompts.

MIT licensed.
