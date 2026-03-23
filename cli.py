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
from scheduler.solver import InfeasibleError, solve


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

    print(f"Loaded {len(shifts)} shifts and {len(people)} people. Solving...")
    results = solve(shifts, people, constraints, alpha=effective_alpha)

    g = config.get("global", {})
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
    )
    print(f"Starting web server at http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=args.debug)


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
        default="config.yaml",
        metavar="PATH",
        help="Path to YAML config file (default: config.yaml)",
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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "solve":
            cmd_solve(args)
        elif args.command == "serve":
            cmd_serve(args)
    except (ValueError, InfeasibleError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
