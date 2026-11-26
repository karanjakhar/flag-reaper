"""Seed a Flagsmith project with the demo-shop flags, so `flagreaper scan` has real debt to find.

usage:
    FLAGSMITH_API_TOKEN=... FLAGSMITH_PROJECT_ID=... python3 demo/seed_flagsmith.py

Idempotent: existing environments / flags / segments are reused.
Creates: Development, Staging, Production environments and these flags:

    new_checkout        ON everywhere                      -> stale_on
    max_upload_mb       ON everywhere, value 50            -> stale_on
    legacy_banner       OFF everywhere                     -> stale_off
    old_onboarding_v1   OFF everywhere, not in code        -> orphan
    dark_mode           ON in dev/staging, OFF in prod     -> active
    beta_search         segment override in Production     -> active
    pricing_experiment  multivariate (A/B)                 -> active
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flagreaper.api import FlagsmithClient  # noqa: E402
from flagreaper.cli import load_dotenv  # noqa: E402

ENVS = ["Development", "Staging", "Production"]
FLAGS = {
    # name: (description, {env: (enabled, value)})
    "new_checkout": ("One-page checkout. Shipped to 100% in February.", {e: (True, None) for e in ENVS}),
    "max_upload_mb": ("Upload size limit in MB.", {e: (True, 50) for e in ENVS}),
    "legacy_banner": ("'We moved' banner from the 2025 domain change.", {e: (False, None) for e in ENVS}),
    "old_onboarding_v1": ("First onboarding flow. Code deleted long ago.", {e: (False, None) for e in ENVS}),
    "dark_mode": ("Dark theme, being rolled out.", {"Development": (True, None), "Staging": (True, None),
                                                    "Production": (False, None)}),
    "beta_search": ("New search, beta testers only.", {e: (False, None) for e in ENVS}),
    "pricing_experiment": ("Annual vs monthly plan first.", {e: (True, "monthly_first") for e in ENVS}),
}


def typed(v):
    if isinstance(v, bool):
        return {"type": "bool", "boolean_value": v}
    if isinstance(v, int):
        return {"type": "int", "integer_value": v}
    return {"type": "unicode", "string_value": "" if v is None else str(v)}


def items(d):
    return d["results"] if isinstance(d, dict) else d


def main():
    load_dotenv(Path.cwd(), Path(__file__).resolve().parents[1])
    fs = FlagsmithClient.from_env()
    pid = fs.project_id
    if not pid:
        projects = items(fs._req("GET", "/projects/"))
        if len(projects) != 1:
            sys.exit("Set FLAGSMITH_PROJECT_ID (found %d projects)" % len(projects))
        pid = fs.project_id = str(projects[0]["id"])

    envs = {e["name"]: e for e in items(fs._req("GET", f"/environments/?project={pid}"))}
    for name in ENVS:
        if name not in envs:
            envs[name] = fs._req("POST", "/environments/", {"name": name, "project": int(pid)})
            print("created environment", name)

    feats = {f["name"]: f for f in fs._list(f"/projects/{pid}/features/?page_size=999")}
    for name, (desc, _) in FLAGS.items():
        if name not in feats:
            feats[name] = fs._req("POST", f"/projects/{pid}/features/",
                                  {"name": name, "description": desc, "default_enabled": False})
            print("created flag", name)

    mv = feats["pricing_experiment"]
    if not items(fs._req("GET", f"/projects/{pid}/features/{mv['id']}/mv-options/")):
        fs._req("POST", f"/projects/{pid}/features/{mv['id']}/mv-options/",
                {"type": "unicode", "string_value": "annual_first", "default_percentage_allocation": 50,
                 "feature": mv["id"]})
        print("added variant annual_first to pricing_experiment")

    # Environments created today use v2 feature versioning: publish a new version per flag.
    segs = {s["name"]: s for s in fs._list(f"/projects/{pid}/segments/?page_size=999")}
    seg = segs.get("beta_testers") or fs._req("POST", f"/projects/{pid}/segments/", {
        "name": "beta_testers", "project": int(pid),
        "rules": [{"type": "ALL", "rules": [{"type": "ANY", "rules": [], "conditions": [
            {"operator": "EQUAL", "property": "plan", "value": "beta"}]}], "conditions": []}],
    })
    for ename, env in envs.items():
        states = fs._list(f"/environments/{env['api_key']}/featurestates/?page_size=999")
        current = {s["feature"]: s for s in states if not s.get("feature_segment") and not s.get("identity")}
        for name, (_, per_env) in FLAGS.items():
            enabled, value = per_env[ename]
            fid = feats[name]["id"]
            cur = current.get(fid, {})
            body = {"publish_immediately": True}
            if cur.get("enabled") != enabled or cur.get("feature_state_value") != value:
                body["feature_states_to_update"] = [
                    {"feature_segment": None, "enabled": enabled, "feature_state_value": typed(value)}]
            if name == "beta_search" and ename == "Production":
                have = fs._list(f"/features/feature-segments/?environment={env['id']}&feature={fid}")
                if not have:
                    body["feature_states_to_create"] = [{
                        "feature_segment": {"segment": seg["id"]}, "enabled": True,
                        "feature_state_value": typed(None)}]
            if len(body) > 1:
                fs._req("POST", f"/environments/{env['id']}/features/{fid}/versions/", body)
        print("published flag states in", ename)
    print(f"done. project={pid}, API calls={fs.calls}")


if __name__ == "__main__":
    main()
