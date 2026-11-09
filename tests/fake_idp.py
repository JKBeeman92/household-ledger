"""An in-process OpenID provider shaped like Authentik's: discovery, JWKS, authorization code + rotating refresh tokens,
userinfo with a live `groups` claim, an end-session URL. No network: the app's authlib client reaches it through
httpx.ASGITransport, and a test plays the browser by handing the authorize URL to `authorize()`."""
from __future__ import annotations
import base64, secrets, time
from urllib.parse import parse_qs, urlsplit

from authlib.jose import JsonWebKey, jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

HOST = "https://idp.shire.test"
ISSUER = HOST + "/application/o/ledger/"
END_SESSION = ISSUER + "end-session/"


class FakeIdp:
    def __init__(self, client_id: str = "ledger", client_secret: str = "second-breakfast"):
        self.client_id, self.client_secret = client_id, client_secret
        self.key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "shire-1"})
        self.users: dict[str, dict] = {}      # sub -> email, name, groups, active
        self.codes: dict[str, tuple] = {}     # code -> (sub, nonce)
        self.access: dict[str, str] = {}      # access token -> sub
        self.refresh: dict[str, str] = {}     # refresh token -> sub (rotated on use)
        self.id_tokens: list[str] = []
        self.grants: list[str] = []           # grant_type of every token request
        self.down = False                     # True: every endpoint answers 503
        self.app = Starlette(routes=[
            Route("/application/o/ledger/.well-known/openid-configuration", self.discovery),
            Route("/application/o/ledger/jwks/", self.jwks),
            Route("/application/o/token/", self.token, methods=["POST"]),
            Route("/application/o/userinfo/", self.userinfo),
        ])

    def add_user(self, sub: str, email: str, groups: list[str], name: str = "") -> None:
        self.users[sub] = {"email": email, "name": name or email.split("@")[0].title(), "groups": list(groups), "active": True}

    def authorize(self, url: str, sub: str) -> tuple[str, str]:
        """The user signs in at the authorize URL: check what the app asked for and return (code, state) for the callback."""
        u = urlsplit(url)
        assert f"{u.scheme}://{u.netloc}{u.path}" == HOST + "/application/o/authorize/"
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        assert q["client_id"] == self.client_id and q["response_type"] == "code" and "openid" in q["scope"].split()
        code = secrets.token_urlsafe(16)
        self.codes[code] = (sub, q.get("nonce"))
        return code, q["state"]

    # ---- endpoints ----
    def _down(self):
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503) if self.down else None

    async def discovery(self, request: Request):
        return self._down() or JSONResponse({
            "issuer": ISSUER, "authorization_endpoint": HOST + "/application/o/authorize/", "token_endpoint": HOST + "/application/o/token/",
            "userinfo_endpoint": HOST + "/application/o/userinfo/", "jwks_uri": ISSUER + "jwks/", "end_session_endpoint": END_SESSION,
            "id_token_signing_alg_values_supported": ["RS256"], "response_types_supported": ["code"], "subject_types_supported": ["public"],
        })

    async def jwks(self, request: Request):
        return self._down() or JSONResponse({"keys": [self.key.as_dict(is_private=False)]})

    def _claims(self, sub: str) -> dict:
        u = self.users[sub]
        return {"sub": sub, "email": u["email"], "name": u["name"], "groups": list(u["groups"])}

    def _issue(self, sub: str, nonce: str | None) -> dict:
        now = int(time.time())
        idt = jwt.encode({"alg": "RS256", "kid": "shire-1"}, {"iss": ISSUER, "aud": self.client_id, "iat": now, "exp": now + 600,
                                                              **({"nonce": nonce} if nonce else {}), **self._claims(sub)}, self.key).decode()
        at, rt = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        self.access[at], self.refresh[rt] = sub, sub
        self.id_tokens.append(idt)
        return {"access_token": at, "token_type": "Bearer", "expires_in": 3600, "refresh_token": rt, "id_token": idt}

    async def token(self, request: Request):
        if r := self._down():
            return r
        want = "Basic " + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        if request.headers.get("authorization") != want:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        form = await request.form()
        self.grants.append(form.get("grant_type"))
        if form.get("grant_type") == "authorization_code" and form.get("code") in self.codes:
            sub, nonce = self.codes.pop(form["code"])
            return JSONResponse(self._issue(sub, nonce))
        if form.get("grant_type") == "refresh_token" and (sub := self.refresh.pop(form.get("refresh_token"), None)) and self.users[sub]["active"]:
            return JSONResponse(self._issue(sub, None))
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    async def userinfo(self, request: Request):
        if r := self._down():
            return r
        sub = self.access.get(request.headers.get("authorization", "")[7:])
        if not sub or not self.users[sub]["active"]:
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        return JSONResponse(self._claims(sub))
