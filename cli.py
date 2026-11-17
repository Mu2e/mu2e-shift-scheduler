#!/usr/bin/env python3
"""
Mu2e Shift Scheduler — command-line interface.

Subcommands:
  solve   Run the scheduler and write assignments to a file.
  serve   Start the web interface.

Examples:
  python cli.py solve \\
      --shifts sample_data/shifts.csv \\
      --people sample_data/people.csv \\
      --output results.csv

  python cli.py solve \\
      --shifts sample_data/shifts.csv \\
      --people sample_data/people.csv \\
      --output results.json --format json \\
      --target 3 --min 1 --max 5

  python cli.py serve --port 5000 --debug
"""
import argparse
import sys

from scheduler.exporter import compute_stats, to_csv, to_json
from scheduler.loader import build_constraints, load_config, load_people, load_shifts, validate
from scheduler.solver import InfeasibleError, solve_two_pass as solve


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def _print_summary(stats: dict, config_summary: dict) -> None:
    print()
    print("=" * 60)
    print(" Mu2e Shift Scheduler — Results Summary")
    print("=" * 60)
    print(f"  Shifts filled   : {stats['filled_shifts']} / {stats['total_shifts']}")
    if stats["unfilled_shifts"]:
        print(f"  Unfilled shifts : {stats['unfilled_shifts']}  <-- WARNING")
    print(f"  Preferred pct   : {stats['preference_pct']}%")
    print(
        f"  Constraints     : target={config_summary['target']}, "
        f"min={config_summary['min']}, max={config_summary['max']}, "
        f"alpha={config_summary['alpha']}"
    )
    print()

    col_w = max(len(ps["name"]) for ps in stats["person_stats"]) + 2
    header = (
        f"  {'Name':<{col_w}} {'Assigned':>8} {'Target':>6} {'Dev':>5} {'Preferred':>9}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for ps in stats["person_stats"]:
        dev = ps["deviation"]
        dev_str = f"+{dev}" if dev > 0 else str(dev)
        bar = "*" * ps["assigned"]
        print(
            f"  {ps['name']:<{col_w}} {ps['assigned']:>8} {ps['target']:>6} "
            f"{dev_str:>5} {ps['preferred']:>9}  {bar}"
        )
    print()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_solve(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    if args.alpha is not None:
        config["alpha"] = args.alpha

    cli_overrides = {
        "target": args.target,
        "min": args.min_shifts,
        "max": args.max_shifts,
    }

    shifts = load_shifts(args.shifts)
    people = load_people(args.people)
    validate(shifts, people)

    constraints = build_constraints(people, config, cli_overrides)
    effective_alpha = args.alpha if args.alpha is not None else config.get("alpha", 1.0)
    g = config.get("global", {})
    effective_pass2_min = args.pass2_min if args.pass2_min is not None else g.get("pass2_min_shifts_per_person", 0)
    effective_pass2_max = args.pass2_max if args.pass2_max is not None else g.get("pass2_max_shifts_per_person", 1000)

    print(f"Loaded {len(shifts)} shifts and {len(people)} people. Solving...")
    results, _ = solve(
        shifts, people, constraints,
        alpha=effective_alpha,
        pass2_min=effective_pass2_min,
        pass2_max=effective_pass2_max,
    )

    config_summary = {
        "target": cli_overrides.get("target") or g.get("target_shifts_per_person", 3),
        "min": cli_overrides.get("min") or g.get("min_shifts_per_person", 1),
        "max": cli_overrides.get("max") or g.get("max_shifts_per_person", 5),
        "alpha": effective_alpha,
    }
    stats = compute_stats(results, constraints)
    _print_summary(stats, config_summary)

    if args.output:
        fmt = args.format
        if not fmt:
            fmt = "json" if args.output.lower().endswith(".json") else "csv"
        if fmt == "json":
            to_json(results, args.output)
        else:
            to_csv(results, args.output)
        print(f"Results written to: {args.output}")
    else:
        print("(No --output specified; results not saved to file.)")
        print("Run with --output results.csv or --output results.json to save.")


def cmd_serve(args: argparse.Namespace) -> None:
    from app import create_app

    app = create_app(
        config_path=args.config,
        preferences_shifts_csv=args.preferences_shifts,
        preferences_json=args.preferences_json,
        signup_overrides={
            "enabled": args.signup_enabled,
            "allow_block_signup": args.signup_allow_block,
            "allow_release_assigned": args.signup_allow_release_assigned,
            "signup_cutoff_hours": args.signup_cutoff_hours,
            "release_cutoff_hours": args.signup_release_cutoff_hours,
        },
    )
    if args.show_admin_login is not None:
        app.config["SHOW_ADMIN_LOGIN"] = args.show_admin_login
    print(f"Starting web server at http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=args.debug)


def cmd_collab_sync(args: argparse.Namespace) -> int:
    """Sync (or inspect) the Mu2e Collaboration directory copy in app.sqlite."""
    import json
    import os

    # The CLI runs one sync; never start the periodic thread here.
    os.environ["MU2E_COLLAB_SYNC_HOURS"] = "0"
    from app import collab, create_app, store

    app = create_app(config_path=args.config)
    overrides = {
        "base_url": args.base_url,
        "token_file": args.token_file,
        "fetch_phones": args.fetch_phones,
        "members_only": args.members_only,
        "timeout": args.timeout,
    }
    if args.token_file:
        overrides["token"] = ""  # --token-file beats a token from the environment
        os.environ.pop("MU2E_COLLAB_TOKEN", None)
    app.config["COLLAB"] = collab.resolve_config(
        collab.load_yaml_section(args.config), overrides=overrides
    )
    cfg = app.config["COLLAB"]

    with app.app_context():
        if args.status:
            out = {"config": collab.public_config(cfg), "status": store.get_collab_status(),
                   "counts": store.collab_member_counts(),
                   "updated_since": store.get_setting(store.SETTING_COLLAB_SINCE)}
            print(json.dumps(out, indent=2, default=str))
            return 0
    if not cfg["enabled"]:
        print("Error: no collaboration API token (use --token-file, MU2E_COLLAB_TOKEN or "
              "MU2E_COLLAB_TOKEN_FILE).", file=sys.stderr)
        return 2
    if args.dry_run:
        with app.app_context():
            status = collab.sync(cfg, full=args.full, dry_run=True)
    else:
        status = collab.sync_with_lease(app, full=args.full)
        if status is None:
            print("Error: another collaboration sync holds the lease; try again later.", file=sys.stderr)
            return 3
    if args.json:
        print(json.dumps(status, indent=2, default=str))
    elif status["ok"]:
        print(f"OK: {status['fetched']} record(s){' (dry run)' if args.dry_run else ''}, "
              f"{status.get('phone_lookups', 0)} phone lookup(s), {status['duration_s']} s; "
              f"token {status.get('token_owner')}/{status.get('token_mode')}")
        if status.get("warning"):
            print(f"Warning: {status['warning']}")
    else:
        print(f"Error: {status.get('error')}", file=sys.stderr)
    return 0 if status["ok"] else 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mu2e-scheduler",
        description="Mu2e Shift Scheduler — assign people to shifts using ILP optimization.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        metavar="PATH",
        help="Path to YAML config file (default: config/config.yaml)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- solve ----
    sp = subparsers.add_parser("solve", help="Run the scheduler and output assignments.")
    sp.add_argument("--shifts", required=True, metavar="CSV", help="Path to shifts CSV file.")
    sp.add_argument("--people", required=True, metavar="CSV", help="Path to people CSV file.")
    sp.add_argument("--output", metavar="FILE", help="Output file path (.csv or .json).")
    sp.add_argument(
        "--format",
        choices=["csv", "json"],
        help="Output format. Inferred from --output extension if omitted.",
    )
    sp.add_argument(
        "--target",
        type=int,
        metavar="N",
        help="Target shifts per person (overrides config).",
    )
    sp.add_argument(
        "--min",
        dest="min_shifts",
        type=int,
        metavar="N",
        help="Minimum shifts per person (overrides config).",
    )
    sp.add_argument(
        "--max",
        dest="max_shifts",
        type=int,
        metavar="N",
        help="Maximum shifts per person (overrides config).",
    )
    sp.add_argument(
        "--alpha",
        type=float,
        metavar="F",
        help=(
            "Load-balancing weight (overrides config). "
            "Higher values penalize deviation from target more strongly."
        ),
    )
    sp.add_argument(
        "--pass2-min",
        dest="pass2_min",
        type=int,
        metavar="N",
        help="Pass-2 minimum shifts per person (overrides config; default: 0).",
    )
    sp.add_argument(
        "--pass2-max",
        dest="pass2_max",
        type=int,
        metavar="N",
        help="Pass-2 maximum shifts per person (overrides config; default: 1000).",
    )

    # ---- serve ----
    sv = subparsers.add_parser("serve", help="Start the web interface.")
    sv.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    sv.add_argument("--port", type=int, default=5000, help="Bind port (default: 5000).")
    sv.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    sv.add_argument(
        "--preferences-shifts",
        metavar="CSV",
        help="Shifts CSV to use for the preference-collection pages.",
    )
    sv.add_argument(
        "--preferences-json",
        default="preferences.json",
        metavar="FILE",
        help="JSON file where preferences are stored (default: preferences.json).",
    )
    sg = sv.add_argument_group(
        "shift self-signup",
        "Override the config.yaml 'signup:' section and MU2E_SIGNUP_* environment variables.",
    )
    sg_on = sg.add_mutually_exclusive_group()
    sg_on.add_argument("--signup", dest="signup_enabled", action="store_true", default=None,
                       help="Enable shift self-signup (MU2E_SIGNUP_ENABLED).")
    sg_on.add_argument("--no-signup", dest="signup_enabled", action="store_false",
                       help="Disable shift self-signup.")
    sg_blk = sg.add_mutually_exclusive_group()
    sg_blk.add_argument("--signup-block", dest="signup_allow_block", action="store_true", default=None,
                        help="Offer 'sign up for whole block' (MU2E_SIGNUP_ALLOW_BLOCK).")
    sg_blk.add_argument("--no-signup-block", dest="signup_allow_block", action="store_false",
                        help="Only allow individual-shift sign-up.")
    sg_rel = sg.add_mutually_exclusive_group()
    sg_rel.add_argument("--signup-release-assigned", dest="signup_allow_release_assigned",
                        action="store_true", default=None,
                        help="Let shifters drop shifts assigned by the solver or an admin "
                             "(MU2E_SIGNUP_ALLOW_RELEASE_ASSIGNED).")
    sg_rel.add_argument("--no-signup-release-assigned", dest="signup_allow_release_assigned",
                        action="store_false",
                        help="Shifters may only drop shifts they signed up for themselves (default).")
    sg.add_argument("--signup-cutoff-hours", type=float, default=None, metavar="H",
                    help="Refuse sign-up within H hours of shift start (MU2E_SIGNUP_CUTOFF_HOURS, default 0).")
    sg.add_argument("--signup-release-cutoff-hours", type=float, default=None, metavar="H",
                    help="Refuse release within H hours of shift start "
                         "(MU2E_SIGNUP_RELEASE_CUTOFF_HOURS, default 0).")
    admin_login = sv.add_mutually_exclusive_group()
    admin_login.add_argument(
        "--show-admin-login",
        dest="show_admin_login",
        action="store_true",
        default=None,
        help="Show the administrator password form on the login page "
             "(overrides SHOW_ADMIN_LOGIN).",
    )
    admin_login.add_argument(
        "--hide-admin-login",
        dest="show_admin_login",
        action="store_false",
        help="Hide the administrator password form, leaving only Fermilab SSO "
             "(overrides SHOW_ADMIN_LOGIN; this is the default).",
    )

    # ---- collab-sync ----
    cs = subparsers.add_parser(
        "collab-sync",
        help="Sync the Mu2e Collaboration member directory into app.sqlite.",
        description="Fetch the member directory from mu2e-collaboration.fnal.gov "
                    "(incremental via updated_since unless --full) and store it in "
                    "APP_DB_PATH. Options override MU2E_COLLAB_* environment variables, "
                    "which override the config.yaml 'collaboration:' section.",
    )
    cs.add_argument("--full", action="store_true", help="Ignore updated_since and fetch every record.")
    cs.add_argument("--dry-run", action="store_true", help="Fetch and validate but write nothing.")
    cs.add_argument("--status", action="store_true", help="Print config (token redacted), last status and counts; no fetch.")
    cs.add_argument("--json", action="store_true", help="Print the sync status as JSON.")
    cs.add_argument("--base-url", metavar="URL", help="Collaboration site (default https://mu2e-collaboration.fnal.gov).")
    cs.add_argument("--token-file", metavar="PATH", help="File holding the bearer token (overrides MU2E_COLLAB_TOKEN).")
    cs.add_argument("--timeout", type=float, metavar="SEC", help="HTTP timeout per request (default 30).")
    phones = cs.add_mutually_exclusive_group()
    phones.add_argument("--phones", dest="fetch_phones", action="store_true", default=None,
                        help="Fetch phone numbers via ?detail=full (admin-owned token).")
    phones.add_argument("--no-phones", dest="fetch_phones", action="store_false",
                        help="Skip per-record phone lookups.")
    members = cs.add_mutually_exclusive_group()
    members.add_argument("--members-only", dest="members_only", action="store_true", default=None,
                         help="Only collaboration members are assignable (default).")
    members.add_argument("--all-accounts", dest="members_only", action="store_false",
                         help="Every active account is assignable.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "solve":
            cmd_solve(args)
        elif args.command == "serve":
            cmd_serve(args)
        elif args.command == "collab-sync":
            sys.exit(cmd_collab_sync(args))
    except (ValueError, InfeasibleError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
