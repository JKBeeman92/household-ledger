"""Upload intake: a hard cap on request bodies, and safe in-memory unpacking of a zip of exports.

Zip limits are enforced while reading (a zip's declared sizes are attacker-controlled), nothing touches the disk, nested archives are
refused, directories are ignored and only the basename of an entry is used, so a path inside the archive can never matter."""
from __future__ import annotations
import io
import zipfile
from dataclasses import dataclass

from pipeline.run_pipeline import FN_RX

MAX_ENTRIES = 200               # entries in one archive, directories included
MAX_FILE_BYTES = 25 * 2**20     # one export, uncompressed
MAX_TOTAL_BYTES = 100 * 2**20   # all entries of one archive, uncompressed
CHUNK = 64 * 1024
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")


@dataclass
class Item:
    name: str                   # basename
    data: bytes | None = None   # None when rejected
    error: str = ""
    source: str = ""            # the zip it came from, "" for a plain file


def is_zip(filename: str, data: bytes) -> bool:
    return (filename or "").lower().endswith(".zip") or data[:4] in ZIP_MAGIC


def basename(name: str) -> str:
    return (name or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _read_limited(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes | None:
    """The entry's bytes, or None when it decompresses past `limit` (counted as it is read, never from the header)."""
    out, n = bytearray(), 0
    with zf.open(info) as fh:
        while chunk := fh.read(CHUNK):
            n += len(chunk)
            if n > limit:
                return None
            out += chunk
    return bytes(out)


def expand_zip(zname: str, data: bytes) -> list[Item]:
    """One Item per file entry of the archive; rejected entries carry an error. Whole-archive problems give a single rejected Item."""
    zname = basename(zname) or "archive.zip"
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [Item(zname, error="not a readable zip file")]
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            return [Item(zname, error=f"the archive has {len(infos)} entries; the limit is {MAX_ENTRIES}")]
        items, seen, total = [], set(), 0
        for info in infos:
            raw = info.filename.replace("\\", "/")
            if info.is_dir() or raw.endswith("/") or raw.startswith("__MACOSX/") or "/__MACOSX/" in raw:
                continue
            name = basename(raw)
            if name.lower().endswith(".zip"):
                items.append(Item(name, error="nested archives are not accepted", source=zname)); continue
            if not FN_RX.match(name):
                items.append(Item(name, error="not an EveryDollar export filename (<hex>-MM-YYYY-EveryDollar-Transactions|BudgetItems.csv)", source=zname)); continue
            if name in seen:
                items.append(Item(name, error="duplicate name in the archive; only the first is used", source=zname)); continue
            seen.add(name)
            try:
                body = _read_limited(zf, info, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total))
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError) as e:
                items.append(Item(name, error=f"unreadable in the archive ({e.__class__.__name__})", source=zname)); continue
            if body is None:
                over_total = MAX_TOTAL_BYTES - total <= MAX_FILE_BYTES
                items.append(Item(name, error="the archive expands past the total size limit; the rest was not read" if over_total else f"larger than {MAX_FILE_BYTES // 2**20} MB uncompressed", source=zname))
                if over_total:
                    break
                continue
            if body[:4] in ZIP_MAGIC:
                items.append(Item(name, error="nested archives are not accepted", source=zname)); continue
            total += len(body)
            items.append(Item(name, data=body, source=zname))
    return items or [Item(zname, error="the archive holds no files")]


class BodyTooLarge(Exception):
    pass


class UploadLimit:
    """Pure ASGI middleware: refuse any request body over `limit_bytes` with 413, by Content-Length up front and by counting bytes for
    chunked bodies, before the form parser spools an oversized body to disk."""
    def __init__(self, app, limit_bytes):
        self.app, self.limit = app, limit_bytes   # a number, or a callable so the setting is read per request

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit = self.limit() if callable(self.limit) else self.limit
        started, seen = False, 0
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            return await self._reject(send, limit)

        async def counting_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise BodyTooLarge
            return msg

        async def tracking_send(msg):
            nonlocal started
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if started:
                raise
            await self._reject(send, limit)

    @staticmethod
    async def _reject(send, limit):
        body = f"Upload too large: the limit is {limit // 2**20} MB (MAX_UPLOAD_MB).\n".encode()
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})
