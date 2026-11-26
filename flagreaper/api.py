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
                "multivariate": bool(f.get("multivariate_options")),
                "states": {},
            }
            for f in features
        }
        for env in envs:
            ename = env["name"]
            states = self._list(f"/environments/{env['api_key']}/featurestates/?page_size=999")
            for st in states:
                fid = st.get("feature")
                fid = fid.get("id") if isinstance(fid, dict) else fid
                if fid not in flags:
                    continue
                slot = flags[fid]["states"].setdefault(
                    ename, {"enabled": False, "value": None, "overrides": 0, "updated": None}
                )
                if st.get("feature_segment") or st.get("identity"):
                    slot["overrides"] += 1
                    continue
                slot["enabled"] = bool(st.get("enabled"))
                slot["value"] = st.get("feature_state_value")
                slot["updated"] = st.get("updated_at") or st.get("live_from") or st.get("created_at")
            # Segment overrides live on a separate endpoint; count them conservatively.
            try:
                segs = self._list(f"/features/feature-segments/?environment={env['id']}")
            except FlagsmithError:
                segs = []
            for s in segs:
                fid = s.get("feature")
                if fid in flags:
                    slot = flags[fid]["states"].setdefault(
                        ename, {"enabled": False, "value": None, "overrides": 0, "updated": None}
                    )
                    slot["overrides"] += 1
        return {
            "project": pid,
            "environments": [e["name"] for e in envs],
            "flags": list(flags.values()),
        }

    def archive(self, flag: dict) -> str:
        self._req("PATCH", f"/projects/{self.project_id}/features/{flag['id']}/", {"is_archived": True})
        return f"archived '{flag['name']}' (id {flag['id']}) in Flagsmith"


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
