"""Outbound hooks (#35): tell a chat, an automation or an agent when a month closes.

A small JSON event is POSTed to the webhook URL on the Webhooks page, signed so the receiver can check it came from this ledger.
Only derived figures go out, never transactions. Delivery runs off the request/build path (a background task or a daemon thread),
with a short timeout, no redirects and no retries; every attempt is logged and nothing here ever raises into its caller.

Signature: header `X-Ledger-Signature: sha256=<hex>` is HMAC-SHA256, keyed with the shared secret, of `<X-Ledger-Timestamp>.<raw body>`.
Receivers recompute it over the bytes they received and reject a timestamp more than a few minutes old (docs/WEBHOOKS.md).
"""
from __future__ import annotations
import hashlib, hmac, ipaddress, json, logging, threading, time, uuid
import datetime as dt
from urllib.parse import urlsplit

import httpx

from . import db
from .config import settings

log = logging.getLogger("ledger")
SETTING = "webhook"
TIMEOUT = 5.0           # seconds, connect and read each; a slow receiver costs one background thread, nothing else
KEEP_LOG = 500          # newest delivery rows kept
EVENTS = ("build.completed", "review.stored", "months.missing", "test")
TRANSPORT: httpx.BaseTransport | None = None   # tests install an httpx.MockTransport here; production uses the default


def check_url(url: str) -> str | None:
    """Why `url` is not an acceptable webhook target, or None. http(s) only, no credentials in the URL, no link-local literal
    (the cloud metadata address). LAN and localhost targets are allowed on purpose: this is a self-hosted app and the receiver is
    usually ntfy or Home Assistant next door. Redirects are never followed, so a receiver cannot bounce a delivery elsewhere."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018  raises on a bad port
    except ValueError:
        return "not a valid URL"
    if parts.scheme not in ("http", "https") or not host:
        return "the URL must start with http:// or https://"
    if parts.username or parts.password:
        return "put no user name or password in the URL; the shared secret signs each request"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            return "that address is not a valid destination (link-local, unspecified or multicast)"
    except ValueError:
        pass
    return None


def config(conn) -> dict:
    c = db.get_setting(conn, SETTING, {}) or {}
    return {"url": str(c.get("url") or ""), "secret": str(c.get("secret") or ""), "enabled": bool(c.get("enabled"))}


def active(conn) -> dict | None:
    c = config(conn)
    return c if c["enabled"] and c["url"] and c["secret"] else None


def _target(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def build_payload(conn, event: str, month: str | None = None, **extra) -> dict:
    """The JSON body: event type, month, headline figures from the built `monthly` dataset, reconciliation status, dashboard link."""
    from pipeline.run_pipeline import reconcile
    hh = db.get_setting(conn, "household", {}) or {}
    p = {"event": event, "id": str(uuid.uuid4()), "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         "household": hh.get("name") or hh.get("title") or "Household", "dashboard_url": settings.base_url + "/"}
    if event in ("test", "months.missing") and not month:
        p.update(extra)
        return p
    row = conn.execute("SELECT rows FROM datasets WHERE name='monthly'").fetchone()
    monthly = {r["m"]: r for r in (row["rows"] if row else [])}
    month = month or (max(monthly) if monthly else None)
    p["month"] = month
    p["currency"] = hh.get("currency") or "USD"
    m = monthly.get(month)
    p["figures"] = {k: m[k] for k in ("income", "spend", "debt", "net") if k in m} if m else None
    rec = reconcile(conn, month) if month else None
    p["reconciliation"] = None if rec is None else {"status": "clean" if rec["clean"] else "problems", "problems": len(rec["problems"])}
    p.update(extra)
    return p


def deliver(conn, cfg: dict, payload: dict) -> dict:
    """POST one payload and log the attempt. Returns {"ok", "status", "error"}; never raises."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "User-Agent": "household-ledger-webhook/1", "X-Ledger-Event": payload["event"],
               "X-Ledger-Delivery": payload["id"], "X-Ledger-Timestamp": ts, "X-Ledger-Signature": sign(cfg["secret"], ts, body)}
    t0, status, error = time.monotonic(), None, None
    try:
        if (why := check_url(cfg["url"])):
            raise ValueError(why)
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT, follow_redirects=False) as client:
            with client.stream("POST", cfg["url"], content=body, headers=headers) as resp:   # the response body is never read
                status = resp.status_code
        if not 200 <= status < 300:
            error = "redirect not followed" if 300 <= status < 400 else f"receiver answered HTTP {status}"
    except Exception as e:  # noqa: BLE001
        error = (type(e).__name__ + (": " + str(e) if str(e) else ""))[:200]
    ok = error is None
    ms = int((time.monotonic() - t0) * 1000)
    try:
        conn.execute("INSERT INTO webhook_deliveries (event, month, target, ok, http_status, error, duration_ms) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (payload["event"], payload.get("month") or (payload.get("months") or [None])[0], _target(cfg["url"]), ok, status, error, ms))
        conn.execute("DELETE FROM webhook_deliveries WHERE id <= (SELECT id FROM webhook_deliveries ORDER BY id DESC OFFSET %s LIMIT 1)", (KEEP_LOG,))
        conn.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not log webhook delivery")
        conn.rollback()
    if not ok:
        log.warning("webhook %s failed: %s", payload["event"], error)
    return {"ok": ok, "status": status, "error": error}


def notify(event: str, month: str | None = None, **extra) -> dict | None:
    """Build and deliver one event on a connection of its own, if hooks are configured. Safe to call from a background task."""
    try:
        with db.pool.connection() as conn:
            if (cfg := active(conn)) is None:
                return None
            return deliver(conn, cfg, build_payload(conn, event, month, **extra))
    except Exception:  # noqa: BLE001
        log.exception("webhook %s skipped", event)
        return None


def notify_async(conn, event: str, month: str | None = None, **extra) -> threading.Thread | None:
    """For callers on the request path: a cheap configured-check on their connection, then delivery on a daemon thread."""
    try:
        if active(conn) is None:
            return None
        t = threading.Thread(target=notify, args=(event, month), kwargs=extra, daemon=True, name="webhook")
        t.start()
        return t
    except Exception:  # noqa: BLE001
        log.exception("webhook %s skipped", event)
        return None


def notify_missing_months(missing: list[str]) -> dict | None:
    """The stale-data check's hook: `missing` is the list of budget months that should be loaded but are not. Months announced
    before are not announced again; a month that is no longer missing is forgotten, so losing it again later is news again."""
    try:
        with db.pool.connection() as conn:
            conn.execute("DELETE FROM webhook_months_notified WHERE NOT (month = ANY(%s))", (list(missing),))
            fresh = [r["month"] for r in conn.execute("SELECT m AS month FROM unnest(%s::text[]) m WHERE NOT EXISTS (SELECT 1 FROM webhook_months_notified n WHERE n.month = m) ORDER BY m", (sorted(set(missing)),)).fetchall()]
            conn.commit()
            if not fresh or (cfg := active(conn)) is None:
                return None
            conn.execute("INSERT INTO webhook_months_notified (month) SELECT unnest(%s::text[]) ON CONFLICT DO NOTHING", (fresh,))
            conn.commit()
            return deliver(conn, cfg, build_payload(conn, "months.missing", None, months=fresh))
    except Exception:  # noqa: BLE001
        log.exception("webhook months.missing skipped")
        return None
