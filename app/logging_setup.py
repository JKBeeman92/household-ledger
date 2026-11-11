"""Logging: readable by default, one JSON object per line with a request id when LOG_FORMAT=json."""
from __future__ import annotations
import contextvars, json, logging, re, time, uuid

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET = {"/healthz"}  # polled by the Docker HEALTHCHECK every 30 s
access_log = logging.getLogger("ledger.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
             "level": record.levelname, "logger": record.name, "msg": record.getMessage(), "request_id": request_id.get()}
        d.update(getattr(record, "fields", {}))
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, default=str)


TEXT_FORMAT = "%(levelname)-8s %(name)s: %(message)s"


def configure(fmt: str) -> None:
    """Called once at import. 'json': every logger as one JSON object per line. Anything else: readable lines, `LEVEL logger: message`,
    from a root handler, and only when nothing has configured the root logger yet (a test runner, an embedding app). Without it the
    access line (uvicorn's own runs with --no-access-log) would have no INFO handler, and the mcp SDK's later logging.basicConfig would
    print every line bare; with it that basicConfig call does nothing."""
    if fmt.strip().lower() != "json":
        root = logging.getLogger()
        if not root.handlers:
            h = logging.StreamHandler(); h.setFormatter(logging.Formatter(TEXT_FORMAT))
            root.addHandler(h); root.setLevel(logging.INFO)
        return
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = name != "uvicorn.access"  # replaced by the access line below
    logging.getLogger("uvicorn.access").disabled = True


def _host(addr) -> str:
    return addr[0] if addr else "-"


class RequestIdMiddleware:
    """Pure ASGI: takes a sane incoming X-Request-ID or makes one, echoes it, and logs one access line per request.
    It must wrap the proxy-headers middleware (app/main.py), never sit inside it: `peer` is scope["client"] as the connection arrived
    (the reverse proxy, the value FORWARDED_ALLOW_IPS wants), `client` the same key after X-Forwarded-For was applied in place."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        peer = _host(scope.get("client"))
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)
        status = 500
        t0 = time.perf_counter()

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["path"] not in _QUIET:
                ms = round((time.perf_counter() - t0) * 1000, 1)
                client = _host(scope.get("client"))
                via = "" if client == peer else f" via {peer}"
                access_log.info('%s%s "%s %s" %s %sms', client, via, scope["method"], scope["path"], status, ms,
                                extra={"fields": {"method": scope["method"], "path": scope["path"], "status": status, "duration_ms": ms,
                                                  "client": client, "peer": peer}})
            request_id.reset(token)
