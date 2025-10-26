from pathlib import Path

# Store the SQLite database alongside the repository so the path works for any user.
DB_PATH = Path(__file__).resolve().parent.parent / "airquality.db"
