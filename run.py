"""Convenience entry point: python run.py [--host HOST] [--port PORT] [--debug]"""
import argparse
from app import create_app

parser = argparse.ArgumentParser(description="Run the Mu2e Shift Scheduler web server.")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=5000)
parser.add_argument("--debug", action="store_true")
parser.add_argument(
    "--preferences-shifts",
    metavar="CSV",
    help="Shifts CSV file to use for the preference-collection pages.",
)
parser.add_argument(
    "--preferences-json",
    default="preferences.json",
    metavar="FILE",
    help="Path to JSON file where preferences are stored (default: preferences.json).",
)
args = parser.parse_args()

app = create_app(
    preferences_shifts_csv=args.preferences_shifts,
    preferences_json=args.preferences_json,
)
print(f"Starting Mu2e Shift Scheduler at http://{args.host}:{args.port}/")
app.run(host=args.host, port=args.port, debug=args.debug)
