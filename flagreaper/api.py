"""Flagsmith Admin API client (stdlib only) + offline demo client.

Both clients return the same normalized snapshot:

    {
      "project": "<name or id>",
      "environments": ["Development", "Production"],
      "flags": [
        {
          "id": 12, "name": "new_checkout", "created": "2026-01-10T09:00:00Z",
          "multivariate": false,
          "states": {
            "Production": {"enabled": true, "value": null,
                           "overrides": 0, "updated": "2026-02-01T10:00:00Z"}
          }
        }
      ]
    }
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_API = "https://api.flagsmith.com/api/v1"


class FlagsmithError(RuntimeError):
    pass


class FlagsmithClient:
    def __init__(self, token: str, project_id: str, api_url: str = DEFAULT_API):
        if not token:
            raise FlagsmithError(
                "FLAGSMITH_API_TOKEN not set (Organisation API key or user token)."
            )
        self.token = token
        self.project_id = str(project_id)
        self.api_url = api_url.rstrip("/")
        self.calls = 0

    @classmethod
    def from_env(cls) -> "FlagsmithClient":
        return cls(
            token=os.environ.get("FLAGSMITH_API_TOKEN", ""),
            project_id=os.environ.get("FLAGSMITH_PROJECT_ID", ""),
            api_url=os.environ.get("FLAGSMITH_API_URL", DEFAULT_API),
        )

    # -- http -------------------------------------------------------------
    def _req(self, method: str, path: str, body: dict | None = None):
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        # Organisation API keys use "Api-Key", personal tokens use "Token".
        scheme = "Token" if len(self.token) == 40 and "." not in self.token else "Api-Key"
        scheme = os.environ.get("FLAGSMITH_AUTH_SCHEME", scheme)
        req = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"{scheme} {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise FlagsmithError(f"{method} {url} -> {e.code}: {detail}") from None

    def _list(self, path: str) -> list:
        out, url = [], path
        while url:
            data = self._req("GET", url)
            if isinstance(data, list):
                return out + data
            out.extend(data.get("results", []))
            url = data.get("next")
        return out

    # -- snapshot ---------------------------------------------------------
    def snapshot(self) -> dict:
        pid = self.project_id
        features = [
            f for f in self._list(f"/projects/{pid}/features/?page_size=999")
            if not f.get("is_archived")
        ]
        envs = self._list(f"/environments/?project={pid}")
        flags = {
            f["id"]: {
                "id": f["id"],
                "name": f["name"],
                "created": f.get("created_date"),
                "multivariate": False,   # True only while variants actually split traffic
                "experiment": None,      # e.g. "running in Production"
                "states": {},
            }
            for f in features
        }
        option_value = {
            o["id"]: _option_value(o) for f in features for o in f.get("multivariate_options") or []
        }
        for env in envs:
            ename = env["name"]
            # 1) per-environment view of every feature: default state + override counts
            for f in self._list(f"/projects/{pid}/features/?environment={env['id']}&page_size=999"):
                if f["id"] not in flags:
                    continue
                efs = f.get("environment_feature_state") or {}
                seg_n = f.get("num_segment_overrides")
                id_n = f.get("num_identity_overrides")
                if id_n is None and f.get("is_num_identity_overrides_complete", True):
                    id_n = 0
                # Fail closed: an unknown override count counts as "has overrides" -> active.
                overrides = (seg_n or 0) + (id_n or 0) if seg_n is not None and id_n is not None else 1
                flags[f["id"]]["states"][ename] = {
                    "enabled": bool(efs.get("enabled")),
                    "value": efs.get("feature_state_value") if efs.get("feature_state_value") != "" else None,
                    "overrides": overrides,
                    "updated": None,
                }
            # 2) last-change timestamps for the environment defaults
            for st in self._list(f"/environments/{env['api_key']}/featurestates/?page_size=999"):
                fid = st.get("feature")
                fid = fid.get("id") if isinstance(fid, dict) else fid
                if st.get("feature_segment") or st.get("identity") or fid not in flags:
                    continue
                slot = flags[fid]["states"].get(ename)
                if slot is None:
                    continue
                slot["updated"] = st.get("updated_at") or st.get("live_from") or st.get("created_at")
                # variant weights: 0% everywhere = plain flag, 100% on one = that value, else a live split
                weights = {m["multivariate_feature_option"]: float(m.get("percentage_allocation") or 0)
                           for m in st.get("multivariate_feature_state_values") or []}
                full = [o for o, w in weights.items() if w >= 100]
                if full:
                    slot["value"] = option_value.get(full[0])
                elif any(w > 0 for w in weights.values()):
                    flags[fid]["multivariate"] = True
            # 3) experiments that have not finished yet
            try:
                exps = self._list(f"/environments/{env['api_key']}/experiments/?page_size=999")
            except FlagsmithError:
                exps = []
            for x in exps:
                fid = x["feature"]["id"] if isinstance(x.get("feature"), dict) else x.get("feature")
                if fid in flags and x.get("status") in ("created", "running", "paused"):
                    flags[fid]["experiment"] = f"experiment {x['status']} in {ename}"
        return {
            "project": pid,
            "environments": [e["name"] for e in envs],
            "flags": list(flags.values()),
        }

    # -- experimentation --------------------------------------------------
    def environments(self) -> list[dict]:
        return self._list(f"/environments/?project={self.project_id}")

    def find_experiment(self, flag_name: str):
        """Latest experiment for a flag across environments -> (env, experiment) or (None, None)."""
        best = (None, None)
        for env in self.environments():
            for x in self._list(f"/environments/{env['api_key']}/experiments/?page_size=999"):
                feat = x.get("feature")
                name = feat.get("name") if isinstance(feat, dict) else None
                if name is None:
                    full = self._req("GET", f"/environments/{env['api_key']}/experiments/{x['id']}/")
                    feat = full.get("feature")
                    name = feat.get("name") if isinstance(feat, dict) else None
                if name == flag_name and (best[1] is None or x["id"] > best[1]["id"]):
                    best = (env, x)
        return best

    def experiment(self, env: dict, xid: int) -> dict:
        return self._req("GET", f"/environments/{env['api_key']}/experiments/{xid}/")

    def experiment_results(self, env: dict, xid: int, refresh: bool = True, wait_s: int = 60) -> dict:
        """Ask Flagsmith to recompute (allowed every 5 min), then wait for a fresh result."""
        base = f"/environments/{env['api_key']}/experiments/{xid}/results/"
        if refresh:
            try:
                self._req("POST", base + "refresh/")
            except FlagsmithError:
                pass  # refreshed less than 5 minutes ago: use the latest result
        import time
        deadline = time.time() + wait_s
        while True:
            r = (self._req("GET", base) or {}).get("results") or {}
            fresh = r.get("as_of") and (not r.get("refresh_requested_at") or r["as_of"] >= r["refresh_requested_at"])
            if fresh or time.time() > deadline:
                return r
            time.sleep(3)

    def complete_experiment(self, env: dict, xid: int, winner_option_id) -> dict:
        body = {"rollout_decision": "variant", "rollout_variant": winner_option_id} if winner_option_id \
            else {"rollout_decision": "control"}
        return self._req("POST", f"/environments/{env['api_key']}/experiments/{xid}/complete/", body)

    def set_value_everywhere(self, feature_id: int, value) -> list[str]:
        """Make `value` the flag's plain value in every environment (variant weights -> 0)."""
        done = []
        for env in self.environments():
            states = self._list(f"/environments/{env['api_key']}/featurestates/?feature={feature_id}")
            st = next((s for s in states if not s.get("feature_segment") and not s.get("identity")), None)
            if st is None:
                continue
            mv = [{"multivariate_feature_option": m["multivariate_feature_option"], "percentage_allocation": 0}
                  for m in st.get("multivariate_feature_state_values") or []]
            if env.get("use_v2_feature_versioning"):
                self._req("POST", f"/environments/{env['id']}/features/{feature_id}/versions/", {
                    "publish_immediately": True,
                    "feature_states_to_update": [{"feature_segment": None, "enabled": True,
                                                  "feature_state_value": _typed(value),
                                                  "multivariate_feature_state_values": mv}]})
            else:
                self._req("PATCH", f"/environments/{env['api_key']}/featurestates/{st['id']}/",
                          {"enabled": True, "feature_state_value": value,
                           "multivariate_feature_state_values": mv})
            done.append(env["name"])
        return done

    def archive(self, flag: dict) -> str:
        self._req("PATCH", f"/projects/{self.project_id}/features/{flag['id']}/", {"is_archived": True})
        return f"archived '{flag['name']}' (id {flag['id']}) in Flagsmith"


def _option_value(o: dict):
    t = o.get("type")
    if t == "int":
        return o.get("integer_value")
    if t == "bool":
        return o.get("boolean_value")
    return o.get("string_value")


def _typed(v) -> dict:
    if isinstance(v, bool):
        return {"type": "bool", "boolean_value": v}
    if isinstance(v, int):
        return {"type": "int", "integer_value": v}
    return {"type": "unicode", "string_value": "" if v is None else str(v)}


class DemoClient:
    """Reads a snapshot from a JSON file. Archive is simulated and logged."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.calls = 0

    def snapshot(self) -> dict:
        return json.loads(self.path.read_text())

    def archive(self, flag: dict) -> str:
        log = self.path.with_suffix(".archived.log")
        with log.open("a") as fh:
            fh.write(flag["name"] + "\n")
        return f"[demo] archived '{flag['name']}' (logged to {log.name})"
