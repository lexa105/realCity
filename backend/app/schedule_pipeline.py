"""Generate or install the macOS daily realCity pipeline LaunchAgent."""

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from decimal import Decimal

from .database import BACKEND_DIR
from .pipeline import PipelineOptions, STATE_DIR


LABEL = "cz.realcity.daily-pipeline"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hour", type=int, default=8)
    parser.add_argument("--minute", type=int, default=0)
    parser.add_argument("--max-cost-usd", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--install", action="store_true", help="Install and enable the schedule for the current macOS user")
    args = parser.parse_args()
    if not 0 <= args.hour <= 23 or not 0 <= args.minute <= 59:
        parser.error("Use hour 0–23 and minute 0–59.")
    options = PipelineOptions(max_cost_usd=args.max_cost_usd)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(Path(sys.executable).absolute()), "-m", "app.pipeline", "--max-cost-usd", str(options.max_cost_usd)],
        "WorkingDirectory": str(BACKEND_DIR),
        "StartCalendarInterval": {"Hour": args.hour, "Minute": args.minute},
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(STATE_DIR / "scheduler.log"),
        "StandardErrorPath": str(STATE_DIR / "scheduler-error.log"),
        "ProcessType": "Background",
        "RunAtLoad": False,
    }
    prepared = STATE_DIR / f"{LABEL}.plist"
    prepared.write_bytes(plistlib.dumps(config))
    print(f"Prepared {prepared}: daily {args.hour:02}:{args.minute:02} local time, ${options.max_cost_usd} cap per scrape.")
    if not args.install:
        return
    if sys.platform != "darwin":
        parser.error("Installation uses macOS launchd; on Linux schedule app.pipeline with cron/systemd.")
    destination = Path.home() / "Library" / "LaunchAgents" / prepared.name
    if destination.exists():
        parser.error(f"A schedule already exists at {destination}; unload and remove it before replacing it.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(prepared.read_bytes())
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(destination)], check=True)
    print(f"Enabled {LABEL}. It will run at the next scheduled time; no scrape is started by installation.")


if __name__ == "__main__":
    main()
