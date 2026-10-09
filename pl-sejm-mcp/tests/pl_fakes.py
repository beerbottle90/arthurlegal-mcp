"""Offline stand-ins for the Sejm ELI API, shared by the pl-sejm-mcp tests.

Every fixture is a real response captured from api.sejm.gov.pl on 2026-10-09:

- ``act_DU_1964_93.json``            GET /eli/acts/DU/1964/93 (Kodeks cywilny)
- ``act_DU_2025_1508.json``          GET /eli/acts/DU/2025/1508 (an amending act)
- ``references_DU_1964_93.json.gz``  GET /eli/acts/DU/1964/93/references
- ``search_kodeks_cywilny.json.gz``  GET /eli/acts/search?title=Kodeks cywilny&limit=500

The module name carries the backend prefix on purpose: every backend's tests
put their own directory on ``sys.path``, and a shared name would collide when
several suites run in one process.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import sejm  # noqa: E402


def fixture(name: str) -> Any:
    path = os.path.join(FIXTURES, name)
    opener = gzip.open if name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


class FakeSejm:
    """Replaces ``sejm._get``: answers from the fixtures and records every call.

    The search fixture is the full upstream result set for "Kodeks cywilny"
    (61 acts, newest first), so ``limit``/``offset``/``inForce`` are applied
    here exactly as the API applies them.
    """

    def __init__(self, fail_references: bool = False) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.search_items = fixture("search_kodeks_cywilny.json.gz")["items"]
        self.fail_references = fail_references

    def __call__(self, path: str, params: Optional[Dict[str, Any]] = None,
                 timeout: int = 45) -> Any:
        params = dict(params or {})
        self.calls.append((path, params))
        if path == "acts/search":
            items = self.search_items
            if params.get("year"):
                # A year listing is served from the same captured acts.
                items = [i for i in items if i.get("year") == int(params["year"])]
            if str(params.get("inForce", "")) == "1":
                items = [i for i in items if i.get("inForce") == "IN_FORCE"]
            offset = int(params.get("offset") or 0)
            limit = int(params.get("limit") or 500)
            page = items[offset: offset + limit]
            return {"count": len(page), "totalCount": len(items), "offset": offset,
                    "items": page}
        if path == "acts/DU/1964/93":
            return fixture("act_DU_1964_93.json")
        if path == "acts/DU/1964/93/references":
            if self.fail_references:
                raise sejm.SejmError("HTTP 503 from the Sejm API: %s" % path)
            return fixture("references_DU_1964_93.json.gz")
        if path == "acts/DU/2025/1508":
            return fixture("act_DU_2025_1508.json")
        raise sejm.SejmError("Not found (404): %s" % path)


def load_module(alias: str, filename: str, index_path: Optional[str] = None) -> Any:
    """Import one of the backend's scripts under a collision-free name.

    ``server.py`` opens its index at import time, so ``INDEX_PATH`` is pointed
    at a throwaway file first; without that it would create ``index.db`` in the
    working directory.
    """
    if index_path:
        os.environ["INDEX_PATH"] = index_path
    spec = importlib.util.spec_from_file_location(alias, os.path.join(BACKEND, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
