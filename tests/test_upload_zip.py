"""Zip upload, the upload size cap and the per-file result panel (#29)."""
from __future__ import annotations
import asyncio, dataclasses, io, struct, zipfile

from conftest import GOLDEN
from app import uploads

EXPORTS = sorted((GOLDEN / "exports").glob("*.csv"))


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return buf.getvalue()


def post_zip(client, data, name="catalogue.zip"):
    return client.post("/upload", files=[("files", (name, data, "application/zip"))])


def test_zip_of_exports_is_unpacked_stored_and_reported_per_file(client, reset_db):
    z = make_zip({f"backup/2026/{p.name}": p.read_bytes() for p in EXPORTS} | {"backup/": b"", "backup/readme.txt": b"hi", "__MACOSX/._x.csv": b"x"})
    assert post_zip(client, z).status_code == 303
    page = client.get("/upload").text
    assert f"{len(EXPORTS)} of {len(EXPORTS) + 1} files stored" in page
    assert "from catalogue.zip" in page and "readme.txt" in page and "not an EveryDollar export filename" in page
    assert "__MACOSX" not in page
    assert client.get("/api/status").json()["months"] == 4


def test_results_do_not_ride_in_the_session_cookie(client, reset_db):
    z = make_zip({f"junk-{i}-with-a-rather-long-name-to-fill-the-cookie.csv": b"a\n" for i in range(150)})
    assert post_zip(client, z).status_code == 303
    assert all(len(c.value) < 3500 for c in client.cookies.jar)
    assert client.get("/upload").text.count("Rejected") >= 150


def test_unreadable_and_nested_archives(client, reset_db):
    post_zip(client, b"PK\x03\x04 garbage")
    assert "not a readable zip file" in client.get("/upload").text
    inner = make_zip({EXPORTS[0].name: EXPORTS[0].read_bytes()})
    post_zip(client, make_zip({"inner.zip": inner, "disguised.csv": inner, EXPORTS[0].name: inner}))
    page = client.get("/upload").text
    assert page.count("nested archives are not accepted") == 2 and "0 of 3 files stored" in page
    assert client.get("/api/status").json()["months"] == 0


def test_zip_limits_are_counted_while_reading_not_from_the_header(monkeypatch):
    name = EXPORTS[0].name
    big = make_zip({name: b"x" * 5000})
    monkeypatch.setattr(uploads, "MAX_FILE_BYTES", 1000)
    (it,) = uploads.expand_zip("a.zip", big)
    assert it.data is None and "larger than" in it.error
    forged = bytearray(big)   # declare a tiny size in both headers: the reader must still stop at the real limit
    for sig, off in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        i = forged.index(sig); forged[i + off:i + off + 4] = struct.pack("<I", 10)
    (it,) = uploads.expand_zip("a.zip", bytes(forged))
    assert it.data is None
    monkeypatch.setattr(uploads, "MAX_FILE_BYTES", 10**6); monkeypatch.setattr(uploads, "MAX_TOTAL_BYTES", 8000)
    many = make_zip({name.replace("-01-", f"-{m:02d}-"): b"y" * 3000 for m in range(1, 6)})
    items = uploads.expand_zip("a.zip", many)
    assert [i.data is not None for i in items] == [True, True, False] and "total size" in items[-1].error
    monkeypatch.setattr(uploads, "MAX_ENTRIES", 3)
    (it,) = uploads.expand_zip("a.zip", make_zip({f"d{i}/": b"" for i in range(4)}))
    assert "limit is 3" in it.error


def test_entries_use_basenames_only_and_duplicates_are_refused():
    n = EXPORTS[0].name
    items = uploads.expand_zip("a.zip", make_zip({f"../../etc/{n}": b"1", f"x\\y\\{n}": b"2"}))
    assert [i.name for i in items] == [n, n] and items[0].data == b"1" and "duplicate" in items[1].error


def test_plain_upload_over_the_cap_is_413_and_stores_nothing(client, reset_db, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, max_upload_mb=1))
    r = client.post("/upload", files=[("files", (EXPORTS[0].name, b"x" * (2 * 2**20), "text/csv"))])
    assert r.status_code == 413 and "MAX_UPLOAD_MB" in r.text
    assert client.get("/api/status").json()["months"] == 0


def test_chunked_body_over_the_cap_is_cut_off():
    sent = []

    async def app(scope, receive, send):
        while (await receive())["more_body"]:
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []}); await send({"type": "http.response.body", "body": b""})

    chunks = iter([{"type": "http.request", "body": b"x" * 600, "more_body": True}] * 3)

    async def receive():
        return next(chunks)

    async def send(m):
        sent.append(m)

    asyncio.run(uploads.UploadLimit(app, 1000)({"type": "http", "method": "POST", "headers": []}, receive, send))
    assert sent[0]["status"] == 413


def test_the_form_degrades_to_a_plain_file_input_without_js(client, reset_db):
    page = client.get("/upload").text
    assert 'accept=".csv,.zip"' in page and 'name="files"' in page and 'class="muted dz-hint" hidden' in page
    assert " ondrop=" not in page and " onchange=" not in page
