"""Send simulated users through a running Flagsmith experiment, using the official Python SDK.

Each user is enrolled with get_experiment_flag() (this records the exposure) and converts
with a per-variant probability via track_event(). The hackathon allows simulated traffic.

usage:
    FLAGSMITH_SERVER_KEY=ser.xxx .venv/bin/python demo/simulate_traffic.py \
        --flag pricing_experiment --event checkout_started --users 800 \
        --rate monthly_first=0.10 --rate annual_first=0.16
"""
import argparse
import os
import random
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flagreaper.cli import load_dotenv  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--flag", required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--users", type=int, default=800)
    p.add_argument("--rate", action="append", default=[], help="variant=probability")
    p.add_argument("--prefix", default="sim_user_")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    load_dotenv(Path.cwd(), Path(__file__).resolve().parents[1])
    rates = {k: float(v) for k, v in (r.split("=") for r in a.rate)}

    from flagsmith import Flagsmith  # official SDK, >= 6.3 for Experimentation

    fs = Flagsmith(environment_key=os.environ["FLAGSMITH_SERVER_KEY"], enable_events=True)
    rng = random.Random(a.seed)
    rolls = {f"{a.prefix}{i:05d}": rng.random() for i in range(a.users)}
    seen, conv = Counter(), Counter()

    def visit(uid):
        flag = fs.get_experiment_flag(a.flag, identifier=uid)
        variant = flag.value
        if rolls[uid] < rates.get(str(variant), 0.0):
            fs.track_event(a.event, identifier=uid)
            return variant, True
        return variant, False

    with ThreadPoolExecutor(8) as pool:
        for variant, converted in pool.map(visit, rolls):
            seen[variant] += 1
            conv[variant] += converted
    fs.flush_events()
    for v in seen:
        print(f"{v:15} users={seen[v]:4}  conversions={conv[v]:3}  rate={conv[v] / seen[v]:.1%}")


if __name__ == "__main__":
    main()
