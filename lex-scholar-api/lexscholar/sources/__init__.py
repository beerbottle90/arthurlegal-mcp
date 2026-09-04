"""Source registry — every adapter exposes the same small surface.

An adapter is just a module with:

    NAME     : str
    META     : dict   capability card the router reads
    search() : -> {"total": int|None, "results": [record, ...], ...}
    get()    : -> record                        (optional)
    fulltext(): -> str                          (optional)

Adding a source is therefore one module plus one line in ``REGISTRY`` — nothing
in the MCP layer changes, which is the whole point of the single endpoint.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any, Dict, List

from . import (
    crossref,
    dergipark,
    dialnet,
    doaj,
    hal,
    lawreviewcommons,
    openaire,
    openalex,
    scielo,
    unpaywall,
)

REGISTRY: Dict[str, ModuleType] = {
    doaj.NAME: doaj,
    dergipark.NAME: dergipark,
    lawreviewcommons.NAME: lawreviewcommons,
    scielo.NAME: scielo,
    hal.NAME: hal,
    dialnet.NAME: dialnet,
    openaire.NAME: openaire,
    crossref.NAME: crossref,
    unpaywall.NAME: unpaywall,
    openalex.NAME: openalex,
}

# Sources the router may fan out to for ordinary discovery. Crossref and
# Unpaywall are resolvers (DOI-driven), so they are not in the discovery pool.
DISCOVERY: List[str] = [
    doaj.NAME, lawreviewcommons.NAME, scielo.NAME, hal.NAME,
    dialnet.NAME, dergipark.NAME, openaire.NAME, openalex.NAME,
]

RESOLVERS: List[str] = [crossref.NAME, unpaywall.NAME]


def get_source(name: str) -> ModuleType:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError("unknown source %r; known: %s"
                       % (name, ", ".join(sorted(REGISTRY)))) from None


def capabilities() -> List[Dict[str, Any]]:
    """Capability cards for every source, for ``list_sources`` and the router."""
    out: List[Dict[str, Any]] = []
    for name, mod in REGISTRY.items():
        card = dict(getattr(mod, "META", {}))
        card["name"] = name
        card["role"] = "discovery" if name in DISCOVERY else "resolver"
        card["can_fulltext"] = hasattr(mod, "fulltext")
        if name == openalex.NAME:
            card["budget"] = openalex.budget()
        out.append(card)
    return out
