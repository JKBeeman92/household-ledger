"""Every setting as the files a seed directory holds (issue #160): `/export/settings.zip` and `python -m app.admin export-settings DIR`.

Unzip into an empty SEED_DIR and a new install starts with the same mapping, CPI, goals, events, accounts, rules and household.
Month-end balances are typed facts, so they are in sources.zip (as balances.csv, which the seed loader reads from SEED_DIR too). Each file is
written by the Settings editor's own exporter (`_editor_body`), which already produces the shape `seed_reference_tables` reads.

Deliberately left out: secrets and per-install state (API tokens, the webhook destination and secret, sessions, review provider
settings, stored reviews, the build log). A later reference table adds its file to FILES and to the seed loader in the same change."""
from __future__ import annotations
import json, time
from pathlib import Path

# seed-directory file -> Settings editor key; the order is the order in the zip
FILES = {"item_mapping.csv": "mapping", "cpi_u.csv": "cpi", "goals.json": "goals", "events.json": "events",
         "accounts.csv": "accounts", "checkins.json": "checkins", "rules.json": "rules", "household.json": "household"}
VERSION_FILE = Path(__file__).resolve().parents[1] / "version.txt"   # bumped by release-please; copied into the image


def app_version() -> str:
    try:
        return VERSION_FILE.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def settings_files(conn) -> dict[str, bytes]:
    """{filename: content} for every seed file, plus manifest.json."""
    from app import db
    from app.routes.settings import _editor_body
    out = {}
    for name, key in FILES.items():
        body = _editor_body(conn, key)
        out[name] = (body if body.endswith("\n") else body + "\n").encode()
    hh = db.get_setting(conn, "household", {}) or {}
    manifest = {"app_version": app_version(), "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "household": hh.get("name", ""), "files": list(FILES),
                "migrations": [r["filename"] for r in conn.execute("SELECT filename FROM schema_migrations ORDER BY filename").fetchall()],
                "how_to_use": "Unzip into an empty SEED_DIR and start a new install: it seeds these settings on first boot (README, \"Run it\")."}
    out["manifest.json"] = (json.dumps(manifest, indent=1) + "\n").encode()
    return out


def write_dir(conn, directory: Path) -> list[str]:
    """Write the files into `directory` (created if needed); returns the names written."""
    directory.mkdir(parents=True, exist_ok=True)
    files = settings_files(conn)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return list(files)
