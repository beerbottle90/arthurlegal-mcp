"""Source registry for arthur-tr-hukuk-mcp.

Every upstream is a module in this package exposing a ``SOURCE`` object. The
server iterates the registry to build tools and the ``status`` report; the
crawler iterates it to build the local index. Adding a regulator means adding
one module here and nothing else.

A source declares:

``key``        short id used in tool parameters (``rekabet``, ``epdk`` …)
``label``      human label with the issuing body's full Turkish name
``kind``       ``ictihat`` | ``mevzuat`` | ``kurum`` | ``gazete`` | ``kurallar``
``live``       True when search/get go to the upstream at query time
``crawlable``  True when ``crawl(index, **opts)`` can populate the local index
``notes``      what the model must know before citing this source
``search(args) -> dict``  and  ``get(args) -> dict`` are the tool handlers.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List, Optional


class Source:
    def __init__(self, key: str, label: str, kind: str, notes: str,
                 search: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 get: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 crawl: Optional[Callable[..., Dict[str, Any]]] = None,
                 live: bool = True, search_schema: Optional[Dict[str, Any]] = None,
                 get_schema: Optional[Dict[str, Any]] = None,
                 extra_tools: Optional[List[Any]] = None,
                 homepage: str = "") -> None:
        self.key = key
        self.label = label
        self.kind = kind
        self.notes = notes
        self.search = search
        self.get = get
        self.crawl = crawl
        self.live = live
        self.crawlable = crawl is not None
        self.search_schema = search_schema or {}
        self.get_schema = get_schema or {}
        self.extra_tools = extra_tools or []
        self.homepage = homepage

    def describe(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "kind": self.kind, "live": self.live,
                "crawlable": self.crawlable, "homepage": self.homepage}


# Order matters: it is the order the model sees in `kurum` enums and in status.
MODULES = [
    "bedesten_ictihat",
    "anayasa",
    "uyusmazlik",
    "bedesten_mevzuat",
    "resmi_gazete",
    "rekabet",
    "epdk",
    "spk",
    "bddk",
    "kvkk",
    "btk",
    "gib",
    "sigorta_tahkim",
]

_loaded: Dict[str, Source] = {}
_failed: Dict[str, str] = {}


def load_all() -> Dict[str, Source]:
    if _loaded or _failed:
        return _loaded
    for name in MODULES:
        try:
            mod = importlib.import_module("sources." + name)
            src: Source = getattr(mod, "SOURCE")
            _loaded[src.key] = src
        except Exception as exc:  # noqa: BLE001 - one bad adapter must not sink the rest
            _failed[name] = "%s: %s" % (type(exc).__name__, exc)
    return _loaded


def failed() -> Dict[str, str]:
    return dict(_failed)


def get(key: str) -> Optional[Source]:
    return load_all().get(key)


def kurum_sources() -> List[Source]:
    return [s for s in load_all().values() if s.kind in ("kurum", "kurallar")]
