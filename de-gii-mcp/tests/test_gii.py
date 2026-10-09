"""gesetze-im-internet.de client -- offline, against fixtures shaped like gii-norm.dtd.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import io
import os
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gii  # noqa: E402
import server  # noqa: E402

DOCTYPE = '<!DOCTYPE dokumente SYSTEM "http://www.gesetze-im-internet.de/dtd/1.01/gii-norm.dtd">'


def act_xml(jurabk, title, norms, amtabk="", stand="Zuletzt geändert durch Art. 1 G v. 10.9.2026 I Nr. 250"):
    body = "".join(
        '<norm doknr="N%d"><metadaten><jurabk>%s</jurabk><enbez>%s</enbez><titel format="parat">%s</titel>'
        '</metadaten><textdaten><text format="XML"><Content>%s</Content></text></textdaten></norm>'
        % (i, jurabk, enbez, titel, content) for i, (enbez, titel, content) in enumerate(norms))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>%s<dokumente doknr="D">'
        '<norm doknr="H"><metadaten><jurabk>%s</jurabk>%s'
        '<ausfertigung-datum manuell="ja">1896-08-18</ausfertigung-datum>'
        '<fundstelle typ="amtlich"><periodikum>RGBl</periodikum><zitstelle>1896, 195</zitstelle></fundstelle>'
        '<langue>%s</langue>'
        '<standangabe checked="ja"><standtyp>Neuf</standtyp><standkommentar>Neugefasst durch Bek. v. 2.1.2002 I 42</standkommentar></standangabe>'
        '<standangabe checked="ja"><standtyp>Stand</standtyp><standkommentar>%s</standkommentar></standangabe>'
        '</metadaten><textdaten><text format="XML"><Content><P>Inhaltsübersicht</P></Content></text></textdaten></norm>'
        '%s</dokumente>' % (DOCTYPE, jurabk, ("<amtabk>%s</amtabk>" % amtabk) if amtabk else "", title, stand, body)
    ).encode("utf-8")


def zipped(xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("act.xml", xml)
    return buf.getvalue()


BGB_823 = ("<P>(1) Wer vorsätzlich oder fahrlässig das Leben, den Körper, die Gesundheit, die Freiheit, "
           "das Eigentum oder ein sonstiges Recht eines anderen widerrechtlich verletzt, ist dem anderen "
           "zum Ersatz des daraus entstehenden Schadens verpflichtet.</P>"
           "<P>(2) Die gleiche Verpflichtung trifft denjenigen, welcher gegen ein den Schutz eines anderen "
           "bezweckendes Gesetz verstößt.</P>")
LIST_NORM = ('<P>(1) Fixture&nbsp;norm with a list:</P><DL Font="normal" Type="arabic">'
             '<DT>1.</DT><DD Font="normal"><LA Size="normal">first item,</LA></DD>'
             '<DT>2.</DT><DD Font="normal"><LA Size="normal">second item.</LA></DD></DL>')

FILES = {
    gii.TOC_URL: (
        '<?xml version="1.0" encoding="UTF-8"?><items>'
        '<item><title>Bürgerliches Gesetzbuch</title><link>http://www.gesetze-im-internet.de/bgb/xml.zip</link></item>'
        '<item><title>Einführungsgesetz zum Bürgerlichen Gesetzbuche</title><link>http://www.gesetze-im-internet.de/bgbeg/xml.zip</link></item>'
        '<item><title>Grundgesetz für die Bundesrepublik Deutschland</title><link>http://www.gesetze-im-internet.de/gg/xml.zip</link></item>'
        '<item><title>Abgabenordnung</title><link>http://www.gesetze-im-internet.de/ao_1977/xml.zip</link></item>'
        '</items>').encode("utf-8"),
    gii.BASE + "/bgb/xml.zip": zipped(act_xml("BGB", "Bürgerliches Gesetzbuch",
                                               [("§ 823", "Schadensersatzpflicht", BGB_823),
                                                ("§ 2", "Fixture", LIST_NORM),
                                                ("§ 3", "Long", "<P>%s</P>" % ("x" * 45000))])),
    gii.BASE + "/gg/xml.zip": zipped(act_xml("GG", "Grundgesetz für die Bundesrepublik Deutschland",
                                              [("Art 1", "", "<P>(1) Die Würde des Menschen ist unantastbar.</P>")])),
    gii.BASE + "/ao_1977/xml.zip": zipped(act_xml("AO", "Abgabenordnung",
                                                   [("§ 1", "Anwendungsbereich", "<P>(1) Dieses Gesetz gilt …</P>")])),
    gii.BASE + "/woeigg/xml.zip": zipped(act_xml("WoEigG", "Wohnungseigentumsgesetz",
                                                  [("§ 1", "Begriffsbestimmungen", "<P>(1) Nach Maßgabe …</P>")],
                                                  amtabk="WEG")),
    # A page whose act is not the one its name suggests: must never be served as HGB.
    gii.BASE + "/hgb/xml.zip": zipped(act_xml("BGB", "Bürgerliches Gesetzbuch", [("§ 1", "", "<P>x</P>")])),
}


def fake_fetch(url, timeout=60.0):
    if url in FILES:
        return FILES[url]
    raise gii.GiiError("HTTP 404 from %s" % url, status=404)


class GiiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = gii._fetch
        gii._fetch = fake_fetch
        gii._laws.clear()
        gii._toc.update(at=0.0, items=[])

    def tearDown(self) -> None:
        gii._fetch = self._saved

    def test_paragraph_text_citation_stand_and_page(self) -> None:
        out = gii.norm("BGB", "823")
        self.assertTrue(out["text"].startswith("(1) Wer vorsätzlich oder fahrlässig"))
        self.assertIn("\n(2) Die gleiche Verpflichtung", out["text"])
        self.assertEqual(out["citation"], "§ 823 BGB")
        self.assertEqual(out["heading"], "Schadensersatzpflicht")
        self.assertEqual(out["source_url"], "https://www.gesetze-im-internet.de/bgb/__823.html")
        self.assertIn("Stand: Zuletzt geändert durch Art. 1 G v. 10.9.2026 I Nr. 250", out["stand"])
        self.assertIn("Nicht amtliche", out["authenticity"])

    def test_norm_number_forms(self) -> None:
        for nr in ("§ 823", "§823", " 823 "):
            self.assertEqual(gii.norm("bgb", nr)["norm"], "§ 823")

    def test_articles_link_their_own_page(self) -> None:
        out = gii.norm("GG", "1")
        self.assertEqual(out["citation"], "Art 1 GG")
        self.assertEqual(out["source_url"], "https://www.gesetze-im-internet.de/gg/art_1.html")
        self.assertEqual(gii.norm("GG", "Art. 1")["norm"], "Art 1")

    def test_lists_and_entities_render_as_lines(self) -> None:
        text = gii.norm("BGB", "2")["text"]
        self.assertIn("Fixture norm with a list:", text)   # &nbsp; parsed, not fatal
        self.assertIn("\n1. first item,", text)
        self.assertIn("\n2. second item.", text)

    def test_long_norms_are_paged(self) -> None:
        first = gii.norm("BGB", "3")
        self.assertEqual(first["pages"], 3)
        self.assertEqual(len(first["text"]), gii._PAGE_CHARS)
        self.assertEqual(gii.norm("BGB", "3", page=3)["page"], 3)

    def test_aliased_page_names(self) -> None:
        self.assertEqual(gii.norm("AO", "1")["source_url"], "https://www.gesetze-im-internet.de/ao_1977/__1.html")
        weg = gii.norm("WEG", "1")
        self.assertEqual(weg["law"], "WoEigG")   # the act's own jurabk is reported

    def test_full_title_resolves_through_the_table_of_contents(self) -> None:
        self.assertEqual(gii.norm("Bürgerliches Gesetzbuch", "823")["law"], "BGB")

    def test_a_page_holding_another_act_is_refused(self) -> None:
        with self.assertRaises(gii.GiiError) as ctx:
            gii.norm("HGB", "1")
        self.assertIn("not the act asked for", str(ctx.exception))

    def test_unknown_norm_lists_candidates(self) -> None:
        with self.assertRaises(gii.GiiError) as ctx:
            gii.norm("BGB", "8")
        self.assertIn("§ 823", ctx.exception.candidates)

    def test_unknown_act(self) -> None:
        with self.assertRaises(gii.GiiError) as ctx:
            gii.norm("XYZG", "1")
        self.assertEqual(ctx.exception.status, 404)

    def test_title_search_prefers_the_shorter_title(self) -> None:
        hits = gii.search_laws("Gesetzbuch")
        self.assertEqual(hits[0]["slug"], "bgb")
        self.assertEqual(hits[0]["url"], "https://www.gesetze-im-internet.de/bgb/index.html")

    def test_tool_answers_an_unreachable_site_with_the_warning_line(self) -> None:
        def down(url, timeout=60.0):
            raise gii.GiiError("gesetze-im-internet.de unreachable (timed out)")
        gii._fetch = down
        out = server._t_norm({"kanun": "BGB", "norm": "823"})
        self.assertIn("UYARI: veri çekilemedi", out["warning"])
        self.assertIn("UYARI: veri çekilemedi", server._t_search({"sorgu": "Gesetzbuch"})["warning"])

    def test_tool_names(self) -> None:
        self.assertEqual([t.name for t in server.TOOLS], ["norm_getir", "gesetz_ara", "server_status"])


if __name__ == "__main__":
    unittest.main()
