"""Convenience entry point: python run.py [--host HOST] [--port PORT] [--debug]"""
import argparse
from app import create_app

parser = argparse.ArgumentParser(description="Run the Mu2e Shift Scheduler web server.")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=5000)
parser.add_argument("--debug", action="store_true")
args = parser.parse_args()

app = create_app()
print(f"Starting Mu2e Shift Scheduler at http://{args.host}:{args.port}/")
app.run(host=args.host, port=args.port, debug=args.debug)
