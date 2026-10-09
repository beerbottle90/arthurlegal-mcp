"""Offline stand-ins for irishstatutebook.ie, shared by the ie-statutebook-mcp tests.

Every fixture is a real page fetched with urllib on 2026-10-09 (gzipped):

- ``act_2024_44_contents.html.gz``  /eli/2024/act/44/enacted/en/html — the
  Act's ELI page, which is its table of contents, not its text
- ``act_2024_44_print.html.gz``     /eli/2024/act/44/enacted/en/print.html —
  the whole Act ("View Full Act" on the site)
- ``act_2024_44_section_1.html.gz`` /eli/2024/act/44/section/1/enacted/en/html

The Act is the Companies (Corporate Governance, Enforcement and Regulatory
Provisions) Act 2024, No. 44 of 2024, enacted 12 November 2024.

The module name carries the backend prefix on purpose: every backend's tests
put their own directory on ``sys.path``, and a shared name would collide when
several suites run in one process.
"""

from __future__ import annotations

import gzip
import importlib.util
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import statutebook  # noqa: E402

BASE = "https://www.irishstatutebook.ie"


def page(name: str) -> str:
    with gzip.open(os.path.join(FIXTURES, name), "rt", encoding="utf-8") as fh:
        return fh.read()


class FakeStatuteBook:
    """Replaces ``statutebook._fetch``: serves the fixtures by URL, 404 otherwise.

    ``also_as`` lists other 2024 Act numbers that answer with No. 44's pages,
    so a year probe, which starts at No. 1, finds an Act without 43 misses.
    """

    def __init__(self, also_as: Optional[List[int]] = None,
                 pages: Optional[Dict[str, str]] = None) -> None:
        self.calls: List[str] = []
        self.numbers = [44] + list(also_as or [])
        self.pages = pages or {
            "contents": page("act_2024_44_contents.html.gz"),
            "print": page("act_2024_44_print.html.gz"),
            "section_1": page("act_2024_44_section_1.html.gz"),
        }

    def __call__(self, url: str, timeout: int = 60) -> str:
        self.calls.append(url)
        for number in self.numbers:
            prefix = "%s/eli/2024/act/%d/" % (BASE, number)
            if url == prefix + "enacted/en/html":
                return self.pages["contents"]
            if url == prefix + "enacted/en/print.html":
                return self.pages["print"]
            if url == prefix + "section/1/enacted/en/html":
                return self.pages["section_1"]
        raise statutebook.IeError("Not found (404): %s" % url)


def load_module(alias: str, filename: str, index_path: Optional[str] = None) -> Any:
    """Import one of the backend's scripts under a collision-free name.

    ``server.py`` opens its index at import time, so ``INDEX_PATH`` is pointed
    at a throwaway file first.
    """
    if index_path:
        os.environ["INDEX_PATH"] = index_path
    spec = importlib.util.spec_from_file_location(alias, os.path.join(BACKEND, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
