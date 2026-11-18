"""HTTP-only Chroma access, with an isolated server database per local index path."""

import hashlib
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import getproxies, proxy_bypass_environment

import chromadb

from common.chroma_endpoint import CHROMA_HOST, check_chroma_server, chroma_port, chroma_request
from common.paths import resolve_path

_DATABASES = "/tenants/default_tenant/databases"


def server_database_name(db_path: str | Path) -> str:
    """Derive a stable Chroma namespace from the canonical local index path."""
    key = os.path.normcase(str(resolve_path(db_path))).encode("utf-8")
    return "georgegpt-" + hashlib.sha256(key).hexdigest()[:32]


def open_chroma(db_path: str | Path, *, create: bool = False):
    """Open a loopback HTTP client; only index builders may create namespaces."""
    port = chroma_port()
    proxies = getproxies()
    if ({"http", "all"} & proxies.keys()) and not (
        proxy_bypass_environment(CHROMA_HOST, proxies) or proxy_bypass_environment(f"{CHROMA_HOST}:{port}", proxies)
    ):
        raise RuntimeError("Chroma is local-only. Add 127.0.0.1 to NO_PROXY before connecting to Chroma.")
    check_chroma_server(port)
    name = server_database_name(db_path)
    try:
        chroma_request(port, f"{_DATABASES}/{name}")
    except HTTPError as error:
        if error.code != 404:
            raise RuntimeError(f"Cannot inspect Chroma namespace {name}: HTTP {error.code}") from error
        if not create:
            raise ValueError(f"No Chroma server index exists for {resolve_path(db_path)}. Run vectorizer.vectorize first.") from error
        try:
            chroma_request(port, _DATABASES, {"name": name})
        except HTTPError as create_error:
            # A concurrent process may have created the same namespace.
            if create_error.code != 409:
                raise RuntimeError(f"Cannot create Chroma namespace {name}: HTTP {create_error.code}") from create_error
    try:
        # The pinned HTTP adapter does not close its HTTPX pool on Client.close().
        # Don't retain sockets across the short-lived catalog/client operations.
        return chromadb.HttpClient(host=CHROMA_HOST, port=port, database=name,
                                   headers={"Connection": "close"})
    except Exception as error:
        raise RuntimeError(f"Cannot open Chroma HTTP database {name} on port {port}: {error}") from error
