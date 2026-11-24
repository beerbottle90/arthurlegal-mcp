"""The two endpoints: /mcp (everything) and /directory/mcp (the directory listing).

    python -m unittest tests.test_profiles      # from the repository root

Run it in a scratch copy of the repository, not in the deploy clone: loading the
backends may create ``<backend>/data/`` folders, and start.sh treats an existing
``data/index.db`` as already seeded and would not copy the baked index over it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DE_ELI_URL"] = "off"   # de-eli is a separate process; not needed here

import mcpcore  # noqa: E402
import server  # noqa: E402
import tool_titles  # noqa: E402

server.build()
DIRECTORY_TOOLS, DIRECTORY_TEXT, DIRECTORY_VIEWS = server.build_directory()
MAIN_TEXT = server.INSTRUCTIONS_HEADER + "\n\n" + "\n\n".join(server._instructions)
OPENAI_TOOLS, OPENAI_TEXT, OPENAI_VIEWS = server.build_openai()


class Annotations(unittest.TestCase):
    def test_every_tool_has_title_and_hint(self):
        for tool in server._tools + DIRECTORY_TOOLS:
            public = tool.public()
            self.assertTrue(public.get("title"), tool.name)
            hints = public.get("annotations", {})
            self.assertEqual(hints.get("title"), public["title"], tool.name)
            self.assertTrue(hints.get("readOnlyHint") is True
                            or "destructiveHint" in hints, tool.name)
            self.assertLessEqual(len(tool.name), 64, tool.name)

    def test_titles_come_from_the_table(self):
        missing = [t.name for t in server._tools if t.name not in tool_titles.TITLES]
        self.assertEqual(missing, [], "add these to tool_titles.TITLES")

    def test_only_local_land_registry_writers_are_not_read_only(self):
        writers = [t.name for t in server._tools if not t.annotations.get("readOnlyHint")]
        self.assertTrue(set(writers) <= tool_titles.NOT_READ_ONLY, writers)
        self.assertTrue(all(t.annotations.get("readOnlyHint") is True for t in DIRECTORY_TOOLS))


class DirectoryProfile(unittest.TestCase):
    def test_land_registry_tools_stay_on_main_only(self):
        names = {t.name for t in DIRECTORY_TOOLS}
        self.assertFalse([n for n in names if n.startswith(("tkgm_", "hesap_"))])
        self.assertIn("status", names)
        self.assertIn("tr_mevzuat_madde_getir", names)
        main = {t.name for t in server._tools}
        # al_masaya_yaz and al_masada_basla tell Claude where to write; the directory takes no instructions.
        # al_dosya builds a file handed over with al_masaya_yaz: nothing to build here (3.5.0).
        # tr_resmi_gazete_ek_sayfa answers with a page image and stays on /mcp.
        self.assertIn("tr_resmi_gazete_ek_sayfa", main)
        self.assertEqual(names, {n for n in main if not n.startswith(("tkgm_", "hesap_"))
                                 and n not in ("al_masaya_yaz", "al_masada_basla", "al_dosya",
                                               "tr_resmi_gazete_ek_sayfa")})

    def test_fee_cards_on_main_and_on_its_desk(self):
        main = {t.name: t for t in server._tools}
        for name in ("hesap_kalemleri", "hesap_vekalet_ucreti", "hesap_yargi_harci", "hesap_faiz",
                     "hesap_iscilik_alacagi"):
            self.assertIn(name, main)
            self.assertTrue(main[name].annotations.get("readOnlyHint"), name)
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        desk = next(t for t in server._tools if t.name == "al_masasi")
        self.assertIn("hesap_vekalet_ucreti", desk.handler({})["yetenekler"])
        directory_desk = next(t for t in DIRECTORY_TOOLS if t.name == "al_masasi")
        self.assertFalse([n for n in directory_desk.handler({})["yetenekler"] if n.startswith("hesap_")])

    def test_directory_tools_keep_their_cards(self):
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        main = {t.name: t for t in server._tools}
        directory = {t.name: t for t in DIRECTORY_TOOLS}
        # Main endpoint: every tool opens the desk. Directory: the article card, the decision list, no view.
        self.assertEqual(main["tr_mevzuat_ara"].meta["ui"]["resourceUri"], "ui://arthurlegal/masa.html")
        self.assertEqual(directory["tr_mevzuat_madde_getir"].meta["ui"]["resourceUri"],
                         "ui://arthurlegal/madde-karti.html")
        self.assertEqual(directory["tr_ictihat_ara"].meta["ui"]["resourceUri"], "ui://arthurlegal/ictihat-karti.html")
        self.assertIsNone(directory["tr_mevzuat_ara"].meta)
        self.assertEqual(directory["tr_mevzuat_ara"].annotations, main["tr_mevzuat_ara"].annotations)
        self.assertEqual(directory["tr_mevzuat_ara"].title, main["tr_mevzuat_ara"].title)

    def test_hand_over_rule_near_the_top_of_main_only(self):
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        kural = server.al_arayuz.TESLIM_KURALI
        # A host may show only the beginning of long instructions.
        self.assertLess(MAIN_TEXT.index(kural) + len(kural), 2048)
        self.assertNotIn("al_masada_basla", DIRECTORY_TEXT)

    def test_model_rule_near_the_top_of_main_only(self):
        if server.al_arayuz is None or not hasattr(server.al_arayuz, "model_kurali"):
            self.skipTest("arayuz/ missing or older")
        kural = server.al_arayuz.model_kurali()
        # The server cannot switch the model: a chat on a model that fell short in the measurements says so once.
        self.assertLess(MAIN_TEXT.index(kural) + len(kural), 2048)
        self.assertNotIn("MODEL.", DIRECTORY_TEXT)
        # Claude reports its model name to the desk on the main endpoint; the directory desk keeps its schema.
        main_desk = next(t for t in server._tools if t.name == "al_masasi")
        directory_desk = next(t for t in DIRECTORY_TOOLS if t.name == "al_masasi")
        self.assertIn("model", main_desk.input_schema["properties"])
        self.assertNotIn("model", directory_desk.input_schema["properties"])
        # The desk opens here only with uzakta_kal (otherwise the local install is tried first and its desk warns).
        low = main_desk.handler({"baglayicilar": [], "model": "Claude Haiku 5.5", "uzakta_kal": True})
        self.assertEqual(low["model"]["duzey"], "dusuk")
        self.assertIn("MODEL:", low["not"])
        self.assertNotIn("MODEL:", main_desk.handler({"baglayicilar": [], "model": "Claude Opus 5.5",
                                                      "uzakta_kal": True})["not"])

    def test_directory_status_leaves_land_registry_out(self):
        status = next(t for t in DIRECTORY_TOOLS if t.name == "status")
        text = json.dumps(status.handler({}), ensure_ascii=False)
        self.assertNotIn("tkgm", text)
        self.assertEqual(status.handler({})["tools_exposed"], len(DIRECTORY_TOOLS))

    def test_hosted_status_leaves_machine_details_out(self):
        from unittest import mock
        hosted = {"FLY_APP_NAME": "arthurlegal-mcp", "FLY_MACHINE_ID": "makine-kimligi-123",
                  "FLY_IMAGE_REF": "registry.fly.io/arthurlegal-mcp:deneme-20261103", "FLY_REGION": "ams"}
        with mock.patch.dict(os.environ, hosted):
            os.environ.pop("STATUS_DETAIL", None)
            out = server._t_status({})
            text = json.dumps(out, ensure_ascii=False)
            self.assertEqual(out["build"], {"image": "deneme-20261103", "region": "ams"})
            for hidden in ("makine-kimligi-123", "registry.fly.io", '"memory"', '"module"', '"path"',
                           '"used_today_here"', '"entries"'):
                self.assertNotIn(hidden, text)
            self.assertTrue(out["backends_loaded"])
            with mock.patch.dict(os.environ, {"STATUS_DETAIL": "1"}):
                self.assertIn("makine-kimligi-123", json.dumps(server._t_status({})))
        self.assertNotIn("build", server._t_status({}))   # local: nothing hosted to describe

    def test_directory_instructions_describe_rather_than_direct(self):
        self.assertTrue(DIRECTORY_TEXT.startswith("ArthurLegal MCP: read-only"))
        self.assertNotIn("MADDE ATFI KURALI", DIRECTORY_TEXT)
        self.assertNotIn("tkgm", DIRECTORY_TEXT.lower())
        self.assertNotIn("hesap_", DIRECTORY_TEXT)

    def test_main_endpoint_keeps_its_rules(self):
        self.assertTrue(MAIN_TEXT.startswith("MADDE ATFI KURALI"))
        self.assertIn("MADDE ATFI KURALI", "\n".join(server._instructions))

    def test_directory_desk_shows_land_registry_as_desktop_only(self):
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        main = next(t for t in server._tools if t.name == "al_masasi")
        directory = next(t for t in DIRECTORY_TOOLS if t.name == "al_masasi")
        self.assertEqual(main.handler({})["moduller"]["tapu"]["durum"], "claude")
        self.assertEqual(directory.handler({})["moduller"]["tapu"]["durum"], "masaustu")
        self.assertEqual(directory.meta, main.meta)
        self.assertFalse([n for n in directory.handler({})["yetenekler"] if n.startswith("tkgm_")])
        # The directory desk keeps the answer in the chat: no hand-over rule, no hand-over tool.
        self.assertIn("al_masaya_yaz", main.handler({})["not"])
        self.assertNotIn("al_masaya_yaz", directory.handler({})["not"])
        self.assertNotIn("al_masaya_yaz", directory.handler({})["yetenekler"])
        self.assertEqual(directory.handler({"baglayicilar": ["Gmail"]})["baglayicilar"], [{"ad": "Gmail", "durum": "bagli"}])
        # The main desk hands over to the lawyer's install when it is connected; the directory desk never sends
        # Claude to another server and keeps its plain schema.
        yerel = {"baglayicilar": [{"ad": "arthurlegal-yerel"}]}
        self.assertIn("yonlendir", main.handler(yerel))
        self.assertNotIn("yonlendir", directory.handler(yerel))
        self.assertNotIn("arthurlegal-yerel", directory.handler(yerel)["not"])
        self.assertNotIn("required", directory.input_schema)
        self.assertNotIn("uzakta_kal", directory.input_schema["properties"])
        self.assertEqual(main.input_schema["required"], ["baglayicilar"])
        # Only the main endpoint points Claude at the lawyer's local install; the directory names no other server
        # and no hand-over tool it does not have.
        self.assertIn("arthurlegal-yerel", main.description)
        self.assertNotIn("arthurlegal-yerel", directory.description)
        self.assertNotIn("al_masada_basla", directory.description)

    def test_directory_serves_the_same_views(self):
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        self.assertEqual([r.uri for r in DIRECTORY_VIEWS], [r.uri for r in server._resources])
        uris = {r.uri for r in DIRECTORY_VIEWS}
        for tool in DIRECTORY_TOOLS:
            if tool.meta:
                self.assertIn(tool.meta["ui"]["resourceUri"], uris, tool.name)


class HttpRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main = mcpcore.Dispatcher(server._tools, "arthurlegal-mcp", server.__version__, MAIN_TEXT,
                                  resources=server._resources)
        directory = mcpcore.Dispatcher(DIRECTORY_TOOLS, "arthurlegal-mcp", server.__version__,
                                       DIRECTORY_TEXT, resources=DIRECTORY_VIEWS)
        handler = mcpcore._make_handler(main, {server.DIRECTORY_PATH: directory})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def rpc(self, path, method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(self, path, method, params=None):
        return self.rpc(path, method, params)["result"]

    def test_each_path_serves_its_own_tools_and_instructions(self):
        main = self.post("/mcp", "tools/list")["tools"]
        directory = self.post(server.DIRECTORY_PATH, "tools/list")["tools"]
        self.assertEqual(len(main), len(server._tools))
        self.assertEqual(len(directory), len(DIRECTORY_TOOLS))
        self.assertTrue(all("annotations" in t for t in directory))
        init = self.post(server.DIRECTORY_PATH + "/", "initialize",
                         {"protocolVersion": "2025-06-18"})
        self.assertTrue(init["instructions"].startswith("ArthurLegal MCP: read-only"))

    def test_directory_reads_the_desk_view(self):
        if not DIRECTORY_VIEWS:
            self.skipTest("arayuz/ missing")
        uri = "ui://arthurlegal/masa.html"
        out = self.post(server.DIRECTORY_PATH, "resources/read", {"uri": uri})
        self.assertEqual(out["contents"][0]["uri"], uri)
        self.assertIn("<html", out["contents"][0]["text"].lower())

    def test_directory_refuses_land_registry_calls(self):
        out = self.rpc(server.DIRECTORY_PATH, "tools/call",
                       {"name": "tkgm_baslangic", "arguments": {}})
        self.assertEqual(out.get("error", {}).get("code"), -32602, out)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/elsewhere", "tools/list")
        self.assertEqual(caught.exception.code, 404)



HINTS = ("readOnlyHint", "destructiveHint", "openWorldHint")


class ExplicitHints(unittest.TestCase):
    """OpenAI's directory review rejects a tool whose three hints are not explicit booleans."""

    def test_every_tool_on_every_path_has_three_booleans(self):
        for tool in server._tools + DIRECTORY_TOOLS + OPENAI_TOOLS:
            hints = tool.public().get("annotations", {})
            for key in HINTS:
                self.assertIsInstance(hints.get(key), bool, (tool.name, key))

    def test_research_reaches_the_open_world_and_the_desk_does_not(self):
        hints = {t.name: t.annotations for t in server._tools}
        self.assertTrue(hints["tr_mevzuat_madde_getir"]["openWorldHint"])
        self.assertFalse(hints["status"]["openWorldHint"])
        if "al_masasi" in hints:
            self.assertFalse(hints["al_masasi"]["openWorldHint"])
            self.assertTrue(hints["al_masaya_yaz"]["readOnlyHint"])


class OpenAIProfile(unittest.TestCase):
    def test_no_tool_asks_for_a_precise_location(self):
        names = {t.name for t in OPENAI_TOOLS}
        self.assertFalse(names & server.OPENAI_EXCLUDED_TOOLS)
        for tool in OPENAI_TOOLS:
            props = (tool.input_schema or {}).get("properties") or {}
            for key in server.OPENAI_STRIPPED_INPUTS.get(tool.name, ()):
                self.assertNotIn(key, props, (tool.name, key))
                self.assertNotIn(key, (tool.input_schema or {}).get("required") or [], (tool.name, key))
            for key in ("enlem", "boylam", "noktalar", "hat"):
                self.assertNotIn(key, props, (tool.name, key))
        main = {t.name for t in server._tools}
        for name in server.OPENAI_EXCLUDED_TOOLS | set(server.OPENAI_STRIPPED_INPUTS):
            self.assertIn(name, main, "the profile names a tool /mcp does not have: " + name)

    def test_parcels_and_calculators_stay(self):
        names = {t.name for t in OPENAI_TOOLS}
        for name in ("tkgm_parsel_sorgula", "tkgm_parsel_raporu", "hesap_yargi_harci", "tr_mevzuat_madde_getir", "status"):
            self.assertIn(name, names)

    def test_stripped_input_is_ignored_when_sent(self):
        seen = []
        tool = next(t for t in OPENAI_TOOLS if t.name == "tkgm_parsel_sorgula")
        original = next(t for t in server._tools if t.name == "tkgm_parsel_sorgula").handler
        wrapped = server._openai_handler("tkgm_parsel_sorgula", lambda args: seen.append(args) or {})
        wrapped({"metin": "41.0, 29.0", "il": "İstanbul"})
        self.assertEqual(seen, [{"il": "İstanbul"}])
        self.assertIsNot(tool.handler, original)

    def test_first_512_characters_carry_the_trigger_and_the_rule(self):
        head = OPENAI_TEXT[:512]
        self.assertIn("al_masasi", head)
        self.assertIn("ArthurLegal Masası'nı aç", head)
        self.assertIn("tr_mevzuat_madde_getir", head)
        self.assertIn("UYARI: veri çekilemedi", head)

    def test_host_neutral_text(self):
        self.assertNotIn("Claude", OPENAI_TEXT)
        self.assertNotIn("MODEL.", OPENAI_TEXT)
        for tool in OPENAI_TOOLS:
            self.assertNotIn("Claude", json.dumps(tool.public(), ensure_ascii=False), tool.name)

    def test_desk_tools_prefer_the_local_install(self):
        # 24.11.2026 (kullanıcı: "Şimdi ChatGPT'ye de ekle"): Claude'daki uzak uç gibi ChatGPT'de de.
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        for name in ("al_masada_basla", "al_masaya_yaz"):
            tool = next(t for t in OPENAI_TOOLS if t.name == name)
            self.assertIn("arthurlegal-yerel", tool.description, name)

    def test_only_the_desk_and_the_hand_over_carry_the_view(self):
        if server.al_arayuz is None:
            self.skipTest("arayuz/ missing")
        with_view = sorted(t.name for t in OPENAI_TOOLS if (t.meta or {}).get("ui", {}).get("resourceUri"))
        self.assertEqual(with_view, ["al_masasi", "al_masaya_yaz"])
        names = {t.name for t in OPENAI_TOOLS}
        self.assertIn("al_masada_basla", names)
        desk = next(t for t in OPENAI_TOOLS if t.name == "al_masasi")
        self.assertNotIn("model", desk.input_schema["properties"])
        self.assertNotIn("uzakta_kal", desk.input_schema["properties"])
        state = desk.handler({})
        self.assertEqual(state["ev"], "chatgpt")
        # Files on the desk (3.5.0): the helper is for the desk only, as on /mcp, and builds without storing.
        if "al_dosya" in {t.name for t in server._tools}:
            dosya = next(t for t in OPENAI_TOOLS if t.name == "al_dosya")
            self.assertEqual(dosya.meta, {"ui": {"visibility": ["app"]}})
            self.assertEqual(dosya.input_schema["properties"]["islem"]["enum"], ["uret"])
            self.assertIn("al_dosya", state["yetenekler"])
        self.assertNotIn("yonlendir", state)
        for view in OPENAI_VIEWS:
            self.assertEqual(view.meta["ui"]["domain"], server.OPENAI_DOMAIN)
        self.assertNotIn("domain", server._resources[0].meta["ui"], "/mcp views keep no domain")


class OpenAIRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main = mcpcore.Dispatcher(server._tools, "arthurlegal-mcp", server.__version__, MAIN_TEXT,
                                  resources=server._resources)
        openai = mcpcore.Dispatcher(OPENAI_TOOLS, "arthurlegal-mcp", server.__version__, OPENAI_TEXT,
                                    resources=OPENAI_VIEWS)
        handler = mcpcore._make_handler(main, {server.OPENAI_PATH: openai})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, path, method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_openai_path_serves_its_profile(self):
        tools = self.post(server.OPENAI_PATH, "tools/list")["result"]["tools"]
        self.assertEqual(len(tools), len(OPENAI_TOOLS))
        init = self.post(server.OPENAI_PATH, "initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertTrue(init["instructions"].startswith("ArthurLegal: legal research"))
        out = self.post(server.OPENAI_PATH, "tools/call", {"name": "tkgm_konumdan_parsel", "arguments": {}})
        self.assertEqual(out.get("error", {}).get("code"), -32602, out)


if __name__ == "__main__":
    unittest.main()
