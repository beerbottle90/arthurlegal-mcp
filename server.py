#!/usr/bin/env python3
"""arthurlegal-mcp — every ArthurLegal jurisdiction server behind one endpoint.

    python server.py                                 # stdio
    python server.py --transport http --port 8900    # http://127.0.0.1:8900/mcp

One connector instead of ten. The point is not tidiness: quick-tunnel URLs change
on every restart, so ten connectors meant ten addresses to re-enter by hand each
time. One endpoint means one.

How it is put together, and why
-------------------------------
The nine standard-library servers are **loaded in-process**, not proxied. Their
tool handlers are called directly, so aggregation costs nothing measurable — and
the whole thing ships as a single container with no network between the parts.
de-eli runs on FastMCP and is async, so it is proxied over HTTP when reachable
(measured at ~15 ms against a ~1,400 ms search: about 1%).

**Every tool is prefixed with its jurisdiction.** This is not cosmetic. Across
the ten servers `get_act` means five different things, `search_legislation`
three, `search_acts` three. Merging them unprefixed would route a Spanish query
to a Finnish server and answer confidently with the wrong country's law — the
one failure mode this whole package exists to prevent.

**The nine per-server `server_status` tools collapse into one.** Nine tools that
each answer "am I healthy" is nine ways to ask the same question; `status`
answers it once for everything, and reports which backends failed to load rather
than quietly serving fewer tools.

Each backend keeps its own SQLite index — `INDEX_PATH` is set per backend during
load — so consolidating changes nothing about what each jurisdiction knows.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcpcore import McpError, Tool, run  # noqa: E402

__version__ = "1.0.0"

HERE = os.path.dirname(os.path.abspath(__file__))
# Deployed bundle: every backend sits next to this file.
ROOT = HERE
AL = HERE

# prefix, directory, module name, human label. The prefix is the jurisdiction the
# tool answers for, so a reader of a tool name always knows which law they are in.
STDLIB_BACKENDS = [
    # Türkiye loads FIRST on purpose: every backend does `import retrieval` by bare
    # name and Python caches the first copy. ArthurLegalTR's retrieval.py is the
    # same module plus dotless-ı query expansion, so letting it win costs the
    # others nothing and keeps Turkish queries working.
    ("tr", os.path.join(AL, "ArthurLegalTR"), "srv_tr",
     "🇹🇷 Türkiye — içtihat + mevzuat + 8 düzenleyici kurum + Resmî Gazete"),
    ("nl", os.path.join(ROOT, "nl-rechtspraak-mcp"), "srv_nl", "🇳🇱 Hollanda — içtihat + mevzuat"),
    ("pl", os.path.join(ROOT, "pl-sejm-mcp"), "srv_pl", "🇵🇱 Polonya — mevzuat"),
    ("at", os.path.join(ROOT, "at-ris-mcp"), "srv_at", "🇦🇹 Avusturya — mevzuat + içtihat"),
    ("ie", os.path.join(ROOT, "ie-statutebook-mcp"), "srv_ie", "🇮🇪 İrlanda — Act'ler"),
    ("fi", os.path.join(ROOT, "fi-finlex-mcp"), "srv_fi", "🇫🇮 Finlandiya — mevzuat"),
    ("es", os.path.join(ROOT, "es-boe-mcp"), "srv_es", "🇪🇸 İspanya — mevzuat"),
    ("uk", os.path.join(ROOT, "uk-legislation-mcp"), "srv_uk",
     "🇬🇧 Birleşik Krallık — mevzuat + işlenmemiş tadiller"),
    ("eu", os.path.join(ROOT, "eu-cellar-mcp"), "srv_eu",
     "🇪🇺 AB — mevzuat + CJEU içtihadı (CELLAR)"),
    ("jp", os.path.join(ROOT, "jp-egov-mcp"), "srv_jp", "🇯🇵 Japonya — mevzuat"),
    ("gleif", os.path.join(ROOT, "gleif-mcp"), "srv_gleif",
     "🌍 Tüzel kişi kimliği + grup yapısı (LEI)"),
    # Not a jurisdiction and not a corpus: it never calls TKGM (Parsel Sorgu terms
    # art. 3). Under the HTTP transport it also refuses file paths, so the public
    # endpoint only ever works on parcel text the caller pastes in.
    ("tkgm", os.path.join(ROOT, "tkgm-mcp"), "srv_tkgm",
     "🇹🇷 Tapu-kadastro — parsel ölçü, ölçekli kroki, hukuk köprüsü (TKGM'ye bağlanmaz)"),
]

# The three older servers expose TOOLS as dicts with a "handler" key rather than
# mcpcore.Tool objects, and live one package deep.
LEGACY_BACKENDS = [
    ("az", os.path.join(AL, "eqanun-api"), "eqanun.mcp_server", "🇦🇿 Azerbaycan — mevzuat"),
    ("scholar", os.path.join(AL, "lex-scholar-api"), "lexscholar.mcp_server", "🌍 Hukuk doktrini"),
    ("contracts", os.path.join(AL, "resourcecontracts-api"), "resourcecontracts.mcp_server",
     "🌍 İmzalı sözleşme emsali"),
]

# de-eli is async/FastMCP; proxied rather than imported. Its tools already carry a
# de_ prefix upstream, so they are passed through unrenamed.
DE_ELI_URL = os.environ.get("DE_ELI_URL", "http://127.0.0.1:8790/mcp")

_loaded: List[Dict[str, Any]] = []
_failed: List[Dict[str, str]] = []
_tools: List[Tool] = []
_instructions: List[str] = []


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _prefixed(prefix: str, name: str) -> str:
    return "%s_%s" % (prefix, name)


def _load_stdlib(prefix: str, directory: str, modname: str, label: str) -> None:
    """Import a v1.6.0 server and adopt its tools under a prefix.

    Each of these imports ``mcpcore``, ``retrieval`` and its own client by bare
    name, so the directory has to be on ``sys.path`` during exec. INDEX_PATH is
    set per backend, which is what keeps each jurisdiction pointed at its own
    SQLite index instead of sharing one.
    """
    sys.path.insert(0, directory)
    previous_index = os.environ.get("INDEX_PATH")
    os.environ["INDEX_PATH"] = os.path.join(directory, "data", "index.db")
    try:
        spec = importlib.util.spec_from_file_location(
            modname, os.path.join(directory, "server.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)

        count = 0
        for tool in module.TOOLS:
            if tool.name in ("server_status", "status"):
                continue          # replaced by the aggregate `status` tool
            _tools.append(Tool(
                _prefixed(prefix, tool.name),
                "[%s] %s" % (label, tool.description),
                tool.input_schema,
                tool.handler,
            ))
            count += 1
        text = getattr(module, "INSTRUCTIONS", "")
        if text:
            _instructions.append("### %s  (araç öneki: `%s_`)\n%s" % (label, prefix, text))
        _loaded.append({"prefix": prefix, "label": label, "tools": count,
                        "mode": "in-process", "module": modname})
    except Exception as exc:  # noqa: BLE001 - one bad backend must not sink the rest
        _failed.append({"prefix": prefix, "label": label,
                        "error": "%s: %s" % (type(exc).__name__, exc)})
    finally:
        if directory in sys.path:
            sys.path.remove(directory)
        if previous_index is None:
            os.environ.pop("INDEX_PATH", None)
        else:
            os.environ["INDEX_PATH"] = previous_index


def _load_legacy(prefix: str, directory: str, dotted: str, label: str) -> None:
    """Import one of the older dict-shaped servers and adopt its tools."""
    sys.path.insert(0, directory)
    try:
        module = importlib.import_module(dotted)
        count = 0
        for entry in module.TOOLS:
            if entry["name"] in ("server_status", "status"):
                continue
            _tools.append(Tool(
                _prefixed(prefix, entry["name"]),
                "[%s] %s" % (label, entry["description"]),
                entry["inputSchema"],
                entry["handler"],
            ))
            count += 1
        _loaded.append({"prefix": prefix, "label": label, "tools": count,
                        "mode": "in-process", "module": dotted})
    except Exception as exc:  # noqa: BLE001
        _failed.append({"prefix": prefix, "label": label,
                        "error": "%s: %s" % (type(exc).__name__, exc)})
    finally:
        if directory in sys.path:
            sys.path.remove(directory)


# --------------------------------------------------------------------------- #
# de-eli proxy
# --------------------------------------------------------------------------- #
class _DeEliProxy:
    """Minimal MCP client for the one backend that cannot be imported.

    FastMCP hands out a session id on initialize that later calls must echo, and
    answers in SSE frames. Both are handled here so the rest of the aggregator
    does not have to know de-eli is different.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.session: Optional[str] = None
        self.tools: List[Dict[str, Any]] = []

    def _post(self, payload: dict, timeout: float = 90.0):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self.session:
            req.add_header("Mcp-Session-Id", self.session)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace"), dict(resp.headers)

    @staticmethod
    def _unwrap(raw: str) -> dict:
        import re
        m = re.search(r"^data:\s*(\{.*)$", raw, re.M)
        return json.loads(m.group(1) if m else raw)

    def connect(self, timeout: float = 20.0) -> None:
        raw, headers = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "arthurlegal-mcp", "version": __version__}},
        }, timeout)
        self.session = next((v for k, v in headers.items()
                             if k.lower() == "mcp-session-id"), None)
        self._unwrap(raw)
        if self.session:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout)
        raw, _ = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, timeout)
        self.tools = self._unwrap(raw).get("result", {}).get("tools", []) or []

    def call(self, name: str, arguments: dict) -> Any:
        raw, _ = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}})
        result = self._unwrap(raw).get("result", {})
        data = result.get("structuredContent")
        if data is not None:
            return data
        content = result.get("content") or [{}]
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except ValueError:
            return text


def _load_de_eli(url: str) -> None:
    label = "🇩🇪 Almanya — mevzuat + içtihat"
    proxy = _DeEliProxy(url)
    try:
        proxy.connect()
    except Exception as exc:  # noqa: BLE001
        _failed.append({"prefix": "de", "label": label,
                        "error": "proxy unreachable at %s (%s)" % (url, exc),
                        "hint": "de-eli is a separate process; start it or set DE_ELI_URL."})
        return

    count = 0
    for spec in proxy.tools:
        name = spec.get("name", "")
        if name in ("de_ranking_status",):
            continue
        # de-eli already namespaces its tools with de_; renaming would break the
        # identifiers its own instructions and this package's guides refer to.
        def make(tool_name: str) -> Callable[[Dict[str, Any]], Any]:
            def handler(args: Dict[str, Any]) -> Any:
                try:
                    return proxy.call(tool_name, args)
                except urllib.error.URLError as exc:
                    raise McpError(
                        "de-eli backend unreachable (%s). The other jurisdictions "
                        "are unaffected." % exc.reason) from exc
            return handler
        _tools.append(Tool(name, "[%s] %s" % (label, spec.get("description", "")),
                           spec.get("inputSchema", {"type": "object", "properties": {}}),
                           make(name)))
        count += 1
    _loaded.append({"prefix": "de", "label": label, "tools": count,
                    "mode": "proxy → %s" % url})


# --------------------------------------------------------------------------- #
def _t_status(args: Dict[str, Any]) -> Any:
    """One health answer for the whole package."""
    out: Dict[str, Any] = {
        "server": "arthurlegal-mcp",
        "version": __version__,
        "backends_loaded": _loaded,
        "tools_exposed": len(_tools),
        "note": "Every tool is prefixed with its jurisdiction (nl_, pl_, at_, ie_, "
                "fi_, es_, uk_, eu_, jp_, gleif_, tkgm_, az_, scholar_, contracts_, de_). Across the underlying "
                "servers `get_act` means five different things, so the prefix is "
                "what keeps a Spanish question from being answered with Finnish law.",
    }
    if _failed:
        out["backends_failed"] = _failed
        out["warning"] = (
            "%d backend did not load. Its jurisdiction is UNAVAILABLE — not empty. "
            "Do not read a missing result as 'no such law'." % len(_failed)
        )
    # Ask each in-process backend what it knows about itself.
    detail = {}
    for entry in _loaded:
        mod = sys.modules.get(entry.get("module", ""))
        if mod is None:
            continue
        fn = getattr(mod, "_t_status", None) or getattr(mod, "_t_server_status", None)
        if callable(fn):
            try:
                detail[entry["prefix"]] = fn({})
            except Exception as exc:  # noqa: BLE001
                detail[entry["prefix"]] = {"error": str(exc)}
    if detail:
        out["backend_status"] = detail
    return out


def build() -> None:
    for prefix, directory, modname, label in STDLIB_BACKENDS:
        _load_stdlib(prefix, directory, modname, label)
    for prefix, directory, dotted, label in LEGACY_BACKENDS:
        _load_legacy(prefix, directory, dotted, label)
    if DE_ELI_URL and DE_ELI_URL.lower() != "off":
        _load_de_eli(DE_ELI_URL)

    _tools.append(Tool(
        "status",
        "Health of every jurisdiction behind this endpoint: which backends "
        "loaded, how many documents each has indexed, what date range was "
        "crawled, and whether semantic search is live. Call it when results look "
        "thin — it distinguishes an unavailable jurisdiction from a genuinely "
        "empty result set.",
        {"type": "object", "properties": {}},
        _t_status,
    ))


INSTRUCTIONS_HEADER = """ArthurLegal — 15 yargı çevresi tek uçta.

ARAÇ ÖNEKLERİ. Her araç ait olduğu yargı çevresinin önekini taşır:
`tr_` Türkiye (içtihat, mevzuat, EPDK/Rekabet/SPK/BDDK/KVKK/BTK/GİB/Sigorta Tahkim,
Resmî Gazete, semantik arşiv; KİK/Sayıştay/TÜRKPATENT/İSTAÇ YOK — resmi uçları cevap vermiyor) · `nl_` Hollanda · `pl_` Polonya ·
`at_` Avusturya · `ie_` İrlanda · `fi_` Finlandiya · `es_` İspanya · `uk_` Birleşik Krallık ·
`eu_` AB (CELLAR) · `jp_` Japonya · `az_` Azerbaycan · `de_` Almanya ·
`gleif_` tüzel kişi kimliği (LEI) · `scholar_` doktrin · `contracts_` sözleşme emsali ·
`tkgm_` tapu-kadastro parsel araçları (kullanıcının getirdiği GeoJSON/KML'den ölçü, kroki,
hukuk köprüsü; TKGM servislerine BAĞLANMAZ — önce `tkgm_rehber`).

TÜRKİYE İÇİN GİRİŞ NOKTASI: karmaşık Türk hukuku sorusunda önce `tr_hukuk_arastirma_rehberi`
(hangi soru için hangi araç), sonra `tr_kurum_listesi` (8 kurumun filtreleri).

Bu kozmetik değil: alttaki sunucularda `get_act` beş ayrı şey, `search_legislation`
üç ayrı şey demek. Önek, İspanyol hukuku sorusunun Fin mevzuatıyla
cevaplanmasını engelleyen şeydir.

DURUM. `status` aracı hepsinin sağlığını tek seferde verir — hangi yargı çevresi
yüklendi, kaç belge indeksli, hangi aralık tarandı, semantik arama açık mı.
Sonuçlar ince göründüğünde **önce onu çağır**: yüklenememiş bir yargı çevresi ile
gerçekten boş bir sonuç kümesi farklı şeylerdir.

Aşağıda her yargı çevresinin kendi kuralları var. Bunlar tavsiye değil, o
hukukun doğru alıntılanması için gereken disiplinlerdir.
"""


if __name__ == "__main__":
    build()
    text = INSTRUCTIONS_HEADER + "\n\n" + "\n\n".join(_instructions)
    sys.stderr.write("arthurlegal-mcp: %d backend, %d araç"
                     % (len(_loaded), len(_tools)))
    if _failed:
        sys.stderr.write(" (%d backend yüklenemedi: %s)"
                         % (len(_failed), ", ".join(f["prefix"] for f in _failed)))
    sys.stderr.write("\n")
    run(_tools, name="arthurlegal-mcp", version=__version__, instructions=text)
