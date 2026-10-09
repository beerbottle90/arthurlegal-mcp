"""Offline stand-ins for opendata.finlex.fi, shared by the fi-finlex-mcp tests.

Fixtures are real ``main.akn`` packages (ZIP, ``main.xml`` inside) fetched on
2026-10-09 for the Tort Liability Act, Vahingonkorvauslaki 412/1974:

- ``consolidated_1974_412_fin-latest.akn``    statute-consolidated/1974/412/fin@latest
- ``consolidated_1974_412_swe-latest.akn``    statute-consolidated/1974/412/swe@latest
- ``consolidated_1974_412_fin-20101051.akn``  statute-consolidated/1974/412/fin@20101051
- ``statute_1974_412_fin.akn``                statute/1974/412/fin@ (as published)

``.../statute-consolidated/1974/412/fin@/main.akn`` answers 404 "No entry found
in given path" upstream, as does every path for 2024/1060, which has no
consolidated text; the fake reproduces both.

The module name carries the backend prefix on purpose: every backend's tests
put their own directory on ``sys.path``, and a shared name would collide when
several suites run in one process.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import urllib.error
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import finlex  # noqa: E402

ROUTES = {
    "statute-consolidated/1974/412/fin@latest/main.akn": "consolidated_1974_412_fin-latest.akn",
    "statute-consolidated/1974/412/swe@latest/main.akn": "consolidated_1974_412_swe-latest.akn",
    "statute-consolidated/1974/412/fin@20101051/main.akn": "consolidated_1974_412_fin-20101051.akn",
    "statute/1974/412/fin@/main.akn": "statute_1974_412_fin.akn",
}


class _Response(io.BytesIO):
    def __init__(self, data: bytes, headers: Optional[Dict[str, str]] = None) -> None:
        super().__init__(data)
        self.headers = headers or {}
        self.status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class FakeUrlopen:
    """Replaces ``urllib.request.urlopen`` inside finlex: fixtures or a 404."""

    def __init__(self, extra: Optional[Dict[str, bytes]] = None) -> None:
        self.calls: List[str] = []
        self.extra = extra or {}

    def __call__(self, req: Any, timeout: int = 90) -> _Response:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append(url)
        path = url.split("/akn/fi/act/", 1)[-1]
        if path in self.extra:
            return _Response(self.extra[path])
        if path in ROUTES:
            with open(os.path.join(FIXTURES, ROUTES[path]), "rb") as fh:
                return _Response(fh.read())
        raise urllib.error.HTTPError(url, 404, "Not Found", {},
                                     io.BytesIO(b"No entry found in given path"))


def load_server(index_path: str, alias: str = "fi_finlex_server") -> Any:
    """Import the backend's server.py under a collision-free name.

    It opens its index at import time, so ``INDEX_PATH`` points at a throwaway
    file first.
    """
    os.environ["INDEX_PATH"] = index_path
    spec = importlib.util.spec_from_file_location(alias, os.path.join(BACKEND, "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
