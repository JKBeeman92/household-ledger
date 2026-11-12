"""Step 1, Files: both of the month's exports are loaded and a build has run since (#164)."""
from . import Step, register, _x


def _status(conn, month, state):
    files = _x(conn, "SELECT kind, filename, uploaded_at FROM source_files WHERE budget_month=%s", (month,)).fetchall()
    kinds = {f["kind"] for f in files}
    if not kinds:
        return "todo", "no files for this month yet"
    if kinds != {"transactions", "budget_items"}:
        return "todo", "only the " + ("transactions" if "transactions" in kinds else "budget") + " file; upload the other"
    last = max(f["uploaded_at"] for f in files)
    b = _x(conn, "SELECT ok, started_at, finished_at FROM build_log WHERE started_at >= %s ORDER BY id DESC LIMIT 1", (last,)).fetchone()
    if not b:
        return "todo", "both files loaded; waiting for a build"
    if b["finished_at"] is None:
        return "todo", "both files loaded; rebuilding"
    if not b["ok"]:
        return "attention", "the build after the upload failed: see Close a month"
    return "done", "both files loaded and built"


def _context(conn, month, state):
    return {"files": _x(conn, "SELECT kind, filename, row_count, uploaded_at FROM source_files WHERE budget_month=%s ORDER BY kind", (month,)).fetchall()}


register(Step("files", 10, "Files", _status, context=_context, in_short=True))
