"""Find flag references in code and classify each flag."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".kt", ".rb", ".dart"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "dist", "build", "__pycache__", ".next"}

BOOL_FNS = r"(?:hasFeature|isFeatureEnabled|is_feature_enabled|IsFeatureEnabled|isEnabled)"
VALUE_FNS = r"(?:getValue|getFeatureValue|get_feature_value|GetFeatureValue)"


EXP_FNS = r"(?:getExperimentFlag|get_experiment_flag|GetExperimentFlag)"
_RECV = r"(?:[A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*)*\??\.)?"
_EXTRA_ARGS = r"(?:\s*,[^()]*(?:\([^()]*\)[^()]*)*)?"


def call_pattern(key: str, kind: str = "any") -> re.Pattern:
    """Regex for SDK calls that read flag `key`.

    kind: "bool"      hasFeature("k"), is_feature_enabled("k"), getExperimentFlag("k").enabled
          "value"     getValue("k"), get_feature_value("k"), getExperimentFlag("k", ...).value
          "any"       all of the above
    """
    k = r"['\"`]" + re.escape(key) + r"['\"`]"
    plain = lambda fns: _RECV + fns + r"\(\s*" + k + r"\s*\)"
    exp = lambda attr: _RECV + EXP_FNS + r"\(\s*" + k + _EXTRA_ARGS + r"\s*\)\??\." + attr + r"\b"
    parts = {
        "bool": [plain(BOOL_FNS), exp("(?:enabled|Enabled)")],
        "value": [plain(VALUE_FNS), exp("(?:value|Value)")],
    }
    chosen = parts.get(kind, parts["bool"] + parts["value"])
    return re.compile("|".join(f"(?:{c})" for c in chosen))


def iter_code_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath, fn)
            if p.suffix in CODE_EXT:
                yield p


def find_refs(root: Path, keys: list[str]) -> dict[str, list[str]]:
    """Return {flag_key: ["path:line", ...]} — exact SDK-call matches plus bare string mentions."""
    refs: dict[str, list[str]] = {k: [] for k in keys}
    if not keys:
        return refs
    loose = re.compile(r"['\"`](" + "|".join(re.escape(k) for k in keys) + r")['\"`]")
    for path in iter_code_files(root):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            for m in loose.finditer(line):
                refs[m.group(1)].append(f"{rel}:{i}")
    return refs


def _age_days(ts: str | None, now: datetime) -> int | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days


def classify(snapshot: dict, refs: dict[str, list[str]], stale_days: int = 30,
             now: datetime | None = None) -> list[dict]:
    """Verdicts:
    orphan     – no code references: archive only.
    stale_on   – on everywhere, same value, no overrides, untouched >= stale_days: inline ON path.
    stale_off  – off everywhere, no overrides, untouched >= stale_days: delete ON path.
    active     – anything else (still doing work). Never touched.
    """
    now = now or datetime.now(timezone.utc)
    envs = snapshot.get("environments", [])
    out = []
    for f in snapshot["flags"]:
        states = f.get("states", {})
        r = refs.get(f["name"], [])
        reason = ""
        ages = [_age_days(s.get("updated"), now) for s in states.values()]
        ages = [a for a in ages if a is not None] or [_age_days(f.get("created"), now)]
        age = min(a for a in ages if a is not None) if any(a is not None for a in ages) else None
        overrides = sum(s.get("overrides", 0) for s in states.values())
        enabled = {s.get("enabled") for s in states.values()}
        values = {repr(s.get("value")) for s in states.values()}
        missing = [e for e in envs if e not in states]

        if not r:
            verdict, reason = "orphan", "no references in code"
        elif f.get("experiment"):
            verdict, reason = "active", f["experiment"]
        elif f.get("multivariate"):
            verdict, reason = "active", "variants still split traffic"
        elif overrides:
            verdict, reason = "active", f"{overrides} segment/identity override(s)"
        elif missing:
            verdict, reason = "active", f"no state in {','.join(missing)}"
        elif len(enabled) != 1 or len(values) != 1:
            verdict, reason = "active", "differs across environments"
        elif age is None or age < stale_days:
            verdict, reason = "active", f"changed {age}d ago (<{stale_days}d)"
        else:
            on = enabled == {True}
            verdict = "stale_on" if on else "stale_off"
            reason = f"{'ON' if on else 'OFF'} in all {len(states)} envs, unchanged {age}d"
        out.append({
            "flag": f["name"], "id": f.get("id"), "verdict": verdict, "reason": reason,
            "value": next(iter(states.values())).get("value") if states else None,
            "age_days": age, "refs": r,
        })
    order = {"stale_on": 0, "stale_off": 1, "orphan": 2, "active": 3}
    return sorted(out, key=lambda x: (order[x["verdict"]], x["flag"]))
