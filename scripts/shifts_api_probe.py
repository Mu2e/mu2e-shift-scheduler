#!/usr/bin/env python3
"""Probe the Mu2e Shift Scheduler token API from the command line.

Checks /api/v1/health, then calls /api/v1/on-duty with a bearer token and
prints who is on duty. Useful for verifying a freshly minted token, a new
deployment, or the bulletin board's view of the schedule.

Precedence: command line > environment > .env > defaults.

Usage:
    scripts/shifts_api_probe.py --base-url https://mu2e-shifts.fnal.gov --token mu2e_...
    SHIFTS_API_TOKEN=mu2e_... scripts/shifts_api_probe.py --at 2026-11-06T22:00
    scripts/shifts_api_probe.py --endpoint classifications --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_dotenv(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def main(argv=None) -> int:
    dotenv = _load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    def setting(name, default=""):
        return os.environ.get(name) or dotenv.get(name) or default

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default=setting("SHIFTS_API_URL", "http://127.0.0.1:8001"),
                        help="Scheduler base URL (env SHIFTS_API_URL)")
    parser.add_argument("--token", default=setting("SHIFTS_API_TOKEN"),
                        help="Bearer token (env SHIFTS_API_TOKEN)")
    parser.add_argument("--endpoint", default="on-duty",
                        choices=["on-duty", "classifications", "schedules", "health"])
    parser.add_argument("--at", default="", help="ISO-8601 datetime for on-duty (default: now)")
    parser.add_argument("--classification", default="",
                        help="Comma-separated classification slugs to keep")
    parser.add_argument("--timeout", type=float, default=float(setting("SHIFTS_API_TIMEOUT", "10")))
    parser.add_argument("--json", action="store_true", help="Print the raw JSON body")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS verification")
    args = parser.parse_args(argv)

    try:
        import requests
    except ImportError:
        print("requests is not installed; run bootstrap.sh or pip install requests", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    verify = not args.insecure

    try:
        health = requests.get(f"{base}/api/v1/health", timeout=args.timeout, verify=verify)
    except requests.RequestException as exc:
        print(f"health check failed: {exc}", file=sys.stderr)
        return 1
    if health.status_code != 200:
        print(f"health check returned HTTP {health.status_code}: {health.text[:200]}", file=sys.stderr)
        return 1
    if args.endpoint == "health":
        print(json.dumps(health.json(), indent=2) if args.json else "health: ok")
        return 0

    if not args.token:
        print("no token: pass --token or set SHIFTS_API_TOKEN", file=sys.stderr)
        return 2

    params = {}
    if args.endpoint == "on-duty":
        if args.at:
            params["at"] = args.at
        if args.classification:
            params["classification"] = args.classification
    try:
        resp = requests.get(
            f"{base}/api/v1/{args.endpoint}",
            headers={"Authorization": f"Bearer {args.token}"},
            params=params, timeout=args.timeout, verify=verify,
        )
    except requests.RequestException as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1
    if resp.status_code == 401:
        print(f"unauthorized: {resp.json().get('error', resp.text[:200])}", file=sys.stderr)
        return 3
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return 1
    body = resp.json()
    if args.json:
        print(json.dumps(body, indent=2))
        return 0

    if args.endpoint == "on-duty":
        print(f"On duty at {body['at']} ({body['timezone']}):")
        if not body["on_duty"]:
            print("  (nobody)")
        for hit in body["on_duty"]:
            p = hit["person"]
            contact = ", ".join(x for x in (p.get("phone"), p.get("email")) if x)
            print(f"  {hit['classification'] or '-':<22} {hit['shift_name']:<12} "
                  f"{hit['starts_at']} -> {hit['ends_at']}  {p['name']}"
                  + (f"  [{contact}]" if contact else ""))
    elif args.endpoint == "classifications":
        for c in body["classifications"]:
            print(f"  {c['sort_order']:>2}  {c['slug']:<24} {c['name']}")
    else:
        for s in body["schedules"]:
            print(f"  {s['id']:>3}  {s['classification_slug'] or '-':<24} {s['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
