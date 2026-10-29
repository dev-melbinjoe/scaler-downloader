"""Scaler Downloader.

  python main.py              opens your class library in the browser, with buttons
  python main.py <command>    command line, e.g. `python main.py run 12` (see `python main.py --help`)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def in_project_venv() -> bool:
    return Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()


if __name__ == "__main__":
    if not in_project_venv():
        if not VENV_PYTHON.exists():
            sys.exit("Set up first:  python -m venv .venv   then   .venv\\Scripts\\pip install -r requirements.txt")
        # Started with the system Python: hand over to the project's environment (it has the packages).
        child = subprocess.Popen([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
        while True:
            try:
                sys.exit(child.wait())
            except KeyboardInterrupt:  # Ctrl+C reaches the child too; let it close cleanly
                continue

    from scaler_dl.cli import main

    sys.exit(main(sys.argv[1:] or ["app"]))
