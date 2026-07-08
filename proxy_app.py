from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default)).strip()


TOKEN_URL = _env("PROXY_TOKEN_URL")
CLIENT_ID = _env("PROXY_CLIENT_ID")
CLIENT_SECRET = _env("PROXY_CLIENT_SECRET")
SCOPE = _env("PROXY_SCOPE")
OCI_BASE_URL = _env("PROXY_OCI_BASE_URL").rstrip("/")
PROXY_HOST = _env("PROXY_HOST", "0.0.0.0") or "0.0.0.0"
PROXY_PORT = int(_env("PROXY_PORT", "8090") or "8090")
PROXY_TIMEOUT_S = float(_env("PROXY_TIMEOUT_S", "120") or "120")


@dataclass
class CachedToken:
    access_token: str
    expires_at_epoch_s: float


_TOKEN_CACHE: CachedToken | None = None
_TOKEN_LOCK = threading.Lock()


def _require(name: str, value: str) -> str:
    clean = value.strip()
    if not clean:
        raise RuntimeError(f"Missing required proxy configuration: {name}")
    return clean


def _get_access_token() -> str:
    global _TOKEN_CACHE

    now = time.time()
    with _TOKEN_LOCK:
        if _TOKEN_CACHE and now < _TOKEN_CACHE.expires_at_epoch_s:
            return _TOKEN_CACHE.access_token

        token_url = _require("PROXY_TOKEN_URL", TOKEN_URL)
        client_id = _require("PROXY_CLIENT_ID", CLIENT_ID)
        client_secret = _require("PROXY_CLIENT_SECRET", CLIENT_SECRET)
        scope = _require("PROXY_SCOPE", SCOPE)

        payload = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": scope,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30.0) as response:
            body = response.read()

        data = json.loads(body.decode("utf-8"))
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("Identity domain did not return an access token.")

        expires_in = int(data.get("expires_in") or 3600)
        _TOKEN_CACHE = CachedToken(
            access_token=access_token,
            expires_at_epoch_s=now + max(60, expires_in - 60),
        )
        return access_token


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def log_message(self, format: str, *args) -> None:
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args))

    def _handle(self) -> None:
        if self.path.split("?", 1)[0] == "/__proxy_health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "configured": {
                        "token_url": bool(TOKEN_URL),
                        "client_id": bool(CLIENT_ID),
                        "client_secret": bool(CLIENT_SECRET),
                        "scope": bool(SCOPE),
                        "oci_base_url": bool(OCI_BASE_URL),
                    },
                    "token_cached": _TOKEN_CACHE is not None and time.time() < _TOKEN_CACHE.expires_at_epoch_s,
                },
            )
            return

        try:
            self._proxy_request()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self._send_bytes(exc.code, body, content_type=exc.headers.get("Content-Type", "application/octet-stream"))
        except Exception as exc:
            self._send_json(500, {"detail": str(exc)})

    def _proxy_request(self) -> None:
        base_url = _require("PROXY_OCI_BASE_URL", OCI_BASE_URL)
        token = _get_access_token()

        target_url = f"{base_url}{self.path}" if self.path.startswith("/") else f"{base_url}/{self.path}"
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_length) if content_length > 0 else None

        upstream_headers = {}
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in {"host", "authorization", "content-length", "connection", "accept-encoding"}:
                continue
            upstream_headers[key] = value
        upstream_headers["Authorization"] = f"Bearer {token}"

        request = urllib.request.Request(
            target_url,
            data=body,
            headers=upstream_headers,
            method=self.command,
        )

        with urllib.request.urlopen(request, timeout=PROXY_TIMEOUT_S) as response:
            data = response.read()
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() in {"transfer-encoding", "connection", "content-encoding"}:
                    continue
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

    def _send_json(self, status_code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self._send_bytes(status_code, data, content_type="application/json")

    def _send_bytes(self, status_code: int, data: bytes, *, content_type: str) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def main() -> None:
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
    print(f"Proxy listening on http://{PROXY_HOST}:{PROXY_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
