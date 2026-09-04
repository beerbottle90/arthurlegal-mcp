"""Offline unit tests — no network, runnable in CI.

    python3 -m unittest discover -s tests -v

The live end-to-end check lives in ``examples/smoke_test.py`` and is deliberately
kept separate: it talks to the official e-qanun.az host, so it is opt-in rather
than something CI hammers on every push.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eqanun import EqanunClient, EqanunError  # noqa: E402
from eqanun._html import decode_html, html_to_text  # noqa: E402
from eqanun.mcp_server import TOOLS, _handle_payload  # noqa: E402


class DecodeHtmlTests(unittest.TestCase):
    """The declared codepage varies per document — 1251, 1252 and 1254 all occur."""

    def test_reads_the_declared_charset_not_a_fixed_one(self):
        # «» sits at 0xAB/0xBB in all three codepages but decodes to mojibake if
        # the bytes are read as UTF-8, so it proves the declared charset is used.
        for cp in ("windows-1251", "windows-1252", "windows-1254"):
            raw = (
                '<html><head><meta charset="%s"></head>'
                "<body>«test»</body></html>" % cp
            ).encode(cp)
            self.assertIn("«test»", decode_html(raw), f"failed for {cp}")

    def test_a_1251_only_byte_is_not_read_as_1254(self):
        # 0xC0 is А (Cyrillic) in cp1251 and À in cp1254 — a fixed-codepage
        # decoder would silently return the wrong letter.
        raw = b'<html><head><meta charset="windows-1251"></head><body>\xc0</body></html>'
        self.assertIn("А", decode_html(raw))

    def test_numeric_char_refs_become_azerbaijani_letters(self):
        raw = b'<html><head><meta charset="windows-1251"></head><body>&#601;m&#601;k</body></html>'
        self.assertEqual(html_to_text(decode_html(raw)), "əmək")

    def test_survives_missing_meta(self):
        self.assertIn("plain", decode_html(b"<html><body>plain</body></html>"))


class SearchQueryTests(unittest.TestCase):
    """search() must build the upstream query correctly — no network."""

    def setUp(self):
        self.client = EqanunClient()
        self.captured = {}

        def fake_get_json(path, params=None):
            self.captured["path"] = path
            self.captured["params"] = params
            return {"data": [], "totalCount": 0}

        self.client._get_json = fake_get_json  # type: ignore[assignment]

    def test_types_become_a_comma_separated_array(self):
        self.client.search("x", types=[73, 87])
        self.assertEqual(self.captured["params"]["array"], "73,87")

    def test_no_types_sends_empty_array_which_is_mandatory_upstream(self):
        self.client.search("x")
        self.assertEqual(self.captured["params"]["array"], "")
        # Omitting any of these three makes the upstream return HTTP 500.
        for key in ("length", "orderColumn", "array"):
            self.assertIn(key, self.captured["params"])

    def test_scope_maps_to_title_and_text_flags(self):
        self.client.search("x", scope="text")
        self.assertEqual(self.captured["params"]["title"], "false")
        self.assertEqual(self.captured["params"]["text"], "true")

    def test_rejects_bad_scope_and_status(self):
        for kwargs in ({"scope": "nope"}, {"status": "repealed"}):
            with self.assertRaises(ValueError):
                self.client.search("x", **kwargs)
            with self.assertRaises(ValueError):
                self.client.count("x", **kwargs)

    def test_rejects_non_integer_types(self):
        with self.assertRaises(ValueError):
            self.client.search("x", types=["abc"])

    def test_count_raises_rather_than_returning_none(self):
        self.client._get_json = lambda p, q=None: {"unexpected": True}  # type: ignore[assignment]
        with self.assertRaises(EqanunError):
            self.client.count("x")


class McpProtocolTests(unittest.TestCase):
    """JSON-RPC dispatch, without touching the network."""

    def test_initialize_returns_a_protocol_version(self):
        resp, _ = _handle_payload({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        })
        self.assertIn("protocolVersion", resp["result"])

    def test_tools_list_exposes_the_documented_six(self):
        resp, _ = _handle_payload({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {
            "search_acts", "count_acts", "get_act",
            "get_act_fulltext", "list_types", "list_sections",
        })

    def test_search_acts_advertises_the_types_filter(self):
        schema = [t for t in TOOLS if t["name"] == "search_acts"][0]["inputSchema"]
        self.assertEqual(schema["properties"]["types"]["type"], "array")

    def test_unknown_method_is_a_jsonrpc_error(self):
        resp, _ = _handle_payload({"jsonrpc": "2.0", "id": 1, "method": "nope"})
        self.assertEqual(resp["error"]["code"], -32601)

    def test_notification_gets_no_response(self):
        resp, _ = _handle_payload({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(resp)

    def test_tool_payloads_are_json_serialisable(self):
        resp, _ = _handle_payload({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        json.dumps(resp, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
