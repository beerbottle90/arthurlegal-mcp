"""Offline stand-ins for the BOE open-data API, shared by the es-boe-mcp tests.

Fixtures are real responses for the Código Civil (BOE-A-1889-4763), fetched on
2026-10-09 with ``Accept: application/xml`` and gzipped:

- ``metadatos_*``  /legislacion-consolidada/id/{id}/metadatos   (whole)
- ``analisis_*``   /legislacion-consolidada/id/{id}/analisis    (whole)
- ``texto_*``      /legislacion-consolidada/id/{id}/texto — cut to 9 of its
  2,444 blocks: the preamble, the opening headings, arts. 1 and 2, and two
  blocks whose versions are irregular upstream (art. 56: a version with an
  empty fecha_vigencia; art. 278: versions out of chronological order)
- ``full_*``       /legislacion-consolidada/id/{id} — the same document with
  its texto cut the same way (the real one is 3.4 MB)

The module name carries the backend prefix on purpose: every backend's tests
put their own directory on ``sys.path``, and a shared name would collide when
several suites run in one process.
"""

from __future__ import annotations

import gzip
import importlib.util
import os
import sys
from typing import Any, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import boe  # noqa: E402

CODE = "BOE-A-1889-4763"


def fixture(name: str) -> bytes:
    with gzip.open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


class FakeBoe:
    """Replaces ``boe._get``: serves the fixtures by URL and records each call."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def __call__(self, url: str, accept: str = "application/xml", timeout: int = 45) -> bytes:
        self.calls.append(url)
        base = "%s/legislacion-consolidada/id/%s" % (boe.API, CODE)
        routes = {
            base: "full_%s.xml.gz" % CODE,
            base + "/metadatos": "metadatos_%s.xml.gz" % CODE,
            base + "/analisis": "analisis_%s.xml.gz" % CODE,
            base + "/texto": "texto_%s.xml.gz" % CODE,
        }
        if url in routes:
            return fixture(routes[url])
        raise boe.BoeError("Not found (404): %s" % url)


def load_server(index_path: str, alias: str = "es_boe_server") -> Any:
    """Import the backend's server.py under a collision-free name.

    It opens its index at import time, so ``INDEX_PATH`` points at a throwaway
    file first.
    """
    os.environ["INDEX_PATH"] = index_path
    spec = importlib.util.spec_from_file_location(alias, os.path.join(BACKEND, "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
