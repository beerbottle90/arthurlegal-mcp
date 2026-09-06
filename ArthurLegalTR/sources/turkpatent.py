"""TÜRKPATENT — Türk Patent ve Marka Kurumu.

Honest status (2026-09): there is **no public decision database**. YİDK
(Yeniden İnceleme ve Değerlendirme Kurulu) decisions are served to the parties
and are not searchable online; ``/yidk-kararlari`` is a 404 shell. The
trademark / patent / design *research* portal (``portal.turkpatent.gov.tr``,
``/api/research``) sits behind Google reCAPTCHA v3, so an unattended server can
only reach it with a paid captcha-solving service — the path the reference
``markapatent-mcp`` takes with CapSolver. This server does not ship a captcha
bypass.

What this adapter does instead:

* ``search`` returns the right *route* for the question: the research portal
  URL with the query pre-filled for a human, and — the part that is actually
  searchable — the courts that review YİDK decisions. Ankara Fikrî ve Sınaî
  Haklar Hukuk Mahkemeleri' judgments and their Yargıtay 11. HD appeals are in
  Bedesten, so ``ictihat_ara`` with ``chamber="H11"`` and the mark name is the
  working path to TÜRKPATENT practice.
* ``get`` is not available.

Citation contract: none produced here.
"""

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import quote

from sources import Source

PORTAL = "https://portal.turkpatent.gov.tr/anonim/arastirma"


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    kind = args.get("kind") or "marka"
    return {
        "query": q, "kind": kind, "results": [],
        "unavailable": True,
        "reason": ("TÜRKPATENT araştırma portalı reCAPTCHA arkasındadır ve YİDK kararları çevrimiçi "
                   "yayımlanmaz; bu sunucu captcha aşma servisi içermez."),
        "manual_url": "%s/%s" % (PORTAL, kind),
        "recommended": [
            {"tool": "ictihat_ara", "args": {"query": "\"%s\" YİDK marka" % q if q else "YİDK marka",
                                              "courts": ["YARGITAYKARARI"], "chamber": "H11"},
             "why": "YİDK kararlarına karşı açılan davalar Ankara FSHHM → Yargıtay 11. HD; Bedesten'de aranabilir."},
            {"tool": "mevzuat_ara", "args": {"query": "Sınai Mülkiyet", "types": ["KANUN"]},
             "why": "6769 sayılı SMK ve uygulama yönetmeliği için."},
        ],
    }


SEARCH_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string"}, "kind": {"type": "string", "enum": ["marka", "patent", "tasarim"], "default": "marka"}}}

SOURCE = Source(
    key="turkpatent", label="TÜRKPATENT — (erişilemez: reCAPTCHA; YİDK kararları yayımlanmaz)", kind="kurum",
    notes="Kaynak canlı değildir; araç yalnız doğru rotayı (portal URL + Yargıtay 11. HD içtihadı) verir.",
    search=search, get=None, live=False, search_schema=SEARCH_SCHEMA,
    homepage="https://www.turkpatent.gov.tr",
)
