# 🪓 Flag Reaper

**Flag debt cleanup for [Flagsmith](https://flagsmith.com): an agent finds the flags that stopped doing work, removes their dead code, opens the PR and archives the flag. A human approves every write.**

Built for **Toggle Wars, the Flagsmith Experimentation Hackathon**. Track 02 (AI Agents and Feature Flags), also packaged as a Track 04 Agent Skill ([`skill/SKILL.md`](skill/SKILL.md)).

## The problem

Every experiment and rollout leaves a flag behind. Once the winner ships to 100%, the flag check and the losing branch stay in the code. Nobody knows which flags are safe to delete, so nobody deletes them. The result is dead code paths, more combinations to test, and dashboards full of flags nobody trusts.

## What it does

```
flagreaper scan        →  reads Flagsmith + greps your code, gives each flag a verdict
flagreaper reap <flag> →  rewrites code to keep only the live branch, shows diff,
                          asks you, branches, commits, pushes, opens a PR (gh)
flagreaper archive <f> →  after merge: checks no reference remains, asks you, archives in Flagsmith
```

| Verdict | Rule (all environments) | Action |
|---|---|---|
| `stale_on` | enabled everywhere, same value, no segment/identity overrides, unchanged ≥ 30 days | inline the ON branch |
| `stale_off` | disabled everywhere, no overrides, unchanged ≥ 30 days | delete the ON branch |
| `orphan` | exists in Flagsmith, no reference in code | archive only |
| `active` | differs per env, has overrides, multivariate/experiment, or recently changed | **refused, never touched** |

### Where it stops for a person

| Step | Automatic | Needs approval |
|---|---|---|
| Read Flagsmith, scan code, classify | ✅ | |
| Show the diff (`--dry-run`) | ✅ | |
| Write source files | | ✅ |
| Push branch + open PR | | ✅ |
| Archive the flag in Flagsmith | | ✅, and refused while code still references it |

With no TTY (CI, a coding agent), every write is refused unless `--yes` is passed, so an agent must ask in chat first. The skill tells it to.

## Quick start (offline demo, 30 seconds)

No account needed, stdlib Python ≥ 3.9, zero dependencies:

```bash
git clone https://github.com/karanjakhar/flag-reaper && cd flag-reaper
python3 -m flagreaper scan --repo examples/demo-app --demo examples/demo-flags.json
python3 -m flagreaper reap new_checkout --repo examples/demo-app --demo examples/demo-flags.json --dry-run
```

```
FLAG                VERDICT    REFS  REASON
max_upload_mb       stale_on      2  ON in all 3 envs, unchanged 209d
new_checkout        stale_on      3  ON in all 3 envs, unchanged 284d
legacy_banner       stale_off     3  OFF in all 3 envs, unchanged 269d
old_onboarding_v1   orphan        0  no references in code
beta_search         active        1  2 segment/identity override(s)
dark_mode           active        1  differs across environments
pricing_experiment  active        1  multivariate / experiment
```

```diff
-  if (flagsmith.hasFeature("new_checkout")) {
-    // one-page checkout, shipped to 100% in February
-    return onePageCheckout(cart, total);
-  } else {
-    return legacyThreeStepCheckout(cart, total);
-  }
+  // one-page checkout, shipped to 100% in February
+  return onePageCheckout(cart, total);
...
-export const showBadge = () => (flagsmith.hasFeature("new_checkout") ? "new" : "");
+export const showBadge = () => "new";
```

## Against your real Flagsmith project

```bash
pip install git+https://github.com/karanjakhar/flag-reaper
export FLAGSMITH_API_TOKEN=<organisation API key>   # Organisation settings → API Keys
export FLAGSMITH_PROJECT_ID=<id>                    # from the project URL
# self-hosted: export FLAGSMITH_API_URL=https://flags.example.com/api/v1

cd your-app
flagreaper scan
flagreaper reap legacy_banner     # diff → y → branch, commit → y → PR
# ...merge, deploy...
flagreaper archive legacy_banner  # verifies no refs → y → archived
```

Works with Flagsmith Cloud and self-hosted (same Admin API).

## Use it from a coding agent

Copy [`skill/SKILL.md`](skill/SKILL.md) into your agent's skills folder (e.g. `.claude/skills/flagsmith-flag-reaper/SKILL.md`), then ask *"clean up our stale flags"*. The agent:

1. runs `flagreaper scan --json --only stale_on,stale_off,orphan`: one compact line per debt flag, so it costs few tokens;
2. asks you which to reap, shows the `--dry-run` diff;
3. applies and opens the PR only after you say yes;
4. archives only after you confirm the merge.

```json
[{"flag":"new_checkout","v":"stale_on","why":"ON in all 3 envs, unchanged 284d","refs":3}, ...]
```

Weekly report in CI: [`examples/flag-debt-report.yml`](examples/flag-debt-report.yml).

## What the rewriter understands

| Pattern | Languages |
|---|---|
| `if [not] flags.is_feature_enabled("k"):` … `elif` … `else:` | Python |
| `if ([!]flagsmith.hasFeature("k")) {…} else if (…) {…} else {…}` | JS/TS, Java, Kotlin, Dart, Go (brace style) |
| `cond ? a : b` / `a if cond else b` with the flag as condition | JS/TS, Python |
| `getValue("k")`, `get_feature_value("k")` | replaced by the flag's value |
| any other call | replaced by `true`/`false` |

SDK calls recognised: `hasFeature`, `isFeatureEnabled`, `is_feature_enabled`, `IsFeatureEnabled`, `getValue`, `getFeatureValue`, `get_feature_value`. If a flag is referenced some other way (e.g. a constant), `reap` lists the references and stops so you can edit by hand.

## Safety notes

- Verdicts are conservative: any override, any env difference, any multivariate option, or a change in the last N days (`--stale-days`) → `active`.
- Code changes ship first, the flag is archived last. Archiving a flag that is still evaluated would flip users to the default.
- Archive is reversible in Flagsmith (unarchive); nothing is deleted.
- Known gap: Edge identity overrides are not visible through the endpoints used; check them for flags used with per-user targeting.

## Development

```bash
python3 -m unittest discover -s tests -v   # 20 tests, incl. end-to-end on the demo app
```

Layout: `flagreaper/api.py` (Flagsmith Admin API + demo client), `analyze.py` (refs + verdicts), `rewrite.py` (code surgery), `cli.py` (commands + approval gates).

MIT licensed.
