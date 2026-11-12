# Outbound webhooks

Configure on **Settings, Webhooks** (`/webhooks`): a URL, a shared secret (write-only; never shown again) and an on/off switch.
**Send test event** posts a `test` event immediately. The delivery log on that page shows the last 50 attempts (host only, no body).

## Events

| `event` | When | Extra fields |
|---|---|---|
| `build.completed` | a background rebuild finished successfully | |
| `review.stored` | a month-close review was generated or an agent saved one | `sections` |
| `months.missing` | the previous budget month is not loaded; once per missing month | `months` (list) |
| `checkin.completed` | a month's check-in was marked Done ([CHECK-IN.md](CHECK-IN.md)) | `mode` (`month`, `quarter`, `year`), `steps` (each step's status) |
| `test` | the button on the page | `note` |

Common fields: `id`, `sent_at`, `household`, `dashboard_url`. Build, review and check-in events add `month` (budget month), `currency`,
`figures` (`income`, `spend`, `debt`, `net` for that month, as numbers) and `reconciliation` (`status` is `clean` or `problems`, and `problems` counts them).
Only derived figures are sent, never transactions. Delivery is a single attempt with a 5 second timeout; redirects are not followed.

## Verifying a delivery

Headers: `X-Ledger-Event`, `X-Ledger-Delivery`, `X-Ledger-Timestamp` (Unix seconds) and
`X-Ledger-Signature: sha256=<hex>`, the HMAC-SHA256 keyed with the shared secret of `"<timestamp>.<raw body>"`.
Verify over the bytes you received (not a re-serialised copy), compare in constant time, and reject a timestamp more than five minutes
from your clock, which stops a captured request being replayed later.

```python
import hashlib, hmac, time

def verify(secret: str, headers: dict, raw_body: bytes, tolerance: int = 300) -> bool:
    ts = headers["X-Ledger-Timestamp"]
    if abs(time.time() - int(ts)) > tolerance:
        return False
    want = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, headers["X-Ledger-Signature"])
```

## Notes for self-hosters

The URL may point at your LAN (ntfy, Home Assistant). Only `http` and `https` are accepted, no credentials in the URL, and the cloud
metadata address (link-local) is refused. Put a token a receiver needs in the URL path if it must be there; the log keeps only the host.
