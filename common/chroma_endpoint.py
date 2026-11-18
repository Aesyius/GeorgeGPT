"""Loopback-only endpoint and readiness checks for the pinned Chroma server."""

import json
import os
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CHROMA_VERSION = "1.5.9"
# The self-hosted Rust CLI in the 1.5.9 distribution reports "1.0.0"
# on its version route. The package pin and the API probe are distinct.
SUPPORTED_SERVER_VERSIONS = ("1.0.0", CHROMA_VERSION)
CHROMA_HOST = "127.0.0.1"
DEFAULT_CHROMA_PORT = 8010


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def chroma_port() -> int:
    """Read and validate the local server port override."""
    try:
        port = int(os.getenv("RAG_CHROMA_PORT", str(DEFAULT_CHROMA_PORT)))
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    raise ValueError("RAG_CHROMA_PORT must be an integer from 1 to 65535.")


def chroma_request(port: int, path: str, data: dict | None = None):
    """Make a bounded Chroma v2 request directly to loopback, bypassing proxies."""
    payload = None if data is None else json.dumps(data).encode("utf-8")
    request = Request(f"http://{CHROMA_HOST}:{port}/api/v2{path}", data=payload,
                      headers={"Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), _NoRedirects()).open(request, timeout=3) as response:
            return json.load(response)
    except HTTPError as error:
        error.close()
        raise


def check_chroma_server(port: int) -> None:
    """Require a supported Chroma version response and a valid v2 heartbeat."""
    try:
        version = chroma_request(port, "/version")
        heartbeat = chroma_request(port, "/heartbeat")
    except HTTPError as error:
        raise RuntimeError(f"Port {port} did not answer as Chroma (HTTP {error.code}). "
                           "Stop the conflicting listener or set RAG_CHROMA_PORT.") from error
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot reach Chroma at {CHROMA_HOST}:{port}: {error}. "
                           "Run python -m services.chroma_server start; there is no embedded fallback.") from error
    if version not in SUPPORTED_SERVER_VERSIONS:
        raise RuntimeError(f"Chroma at {CHROMA_HOST}:{port} reports {version!r}; "
                           f"expected a release compatible with the pinned {CHROMA_VERSION} server.")
    if not isinstance(heartbeat, dict) or type(heartbeat.get("nanosecond heartbeat")) is not int:
        raise RuntimeError(f"Invalid Chroma heartbeat on port {port}. Check the local Chroma server.")
