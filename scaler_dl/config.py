"""Project paths and well-known Scaler URLs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Dedicated Chrome profile: you log in once, later runs reuse the session.
PROFILE_DIR = ROOT / ".scaler_profile"
DEBUG_DIR = ROOT / ".debug"

BASE_URL = "https://www.scaler.com"
DASHBOARD_URL = f"{BASE_URL}/academy/mentee-dashboard/classes"
