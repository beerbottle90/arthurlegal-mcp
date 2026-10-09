"""get_act is metadata only; get_act_text is the law in force, with its header (defect #6).

Live, es_get_act returned the Código Civil's metadata AND 1,996,594 characters
of text, because it fetched /legislacion-consolidada/id/{id} whole.
es_get_act_text fetched /texto, which carries no metadata, so title, rango and
date came back empty and the citation was " (BOE-A-1889-4763)".

The text itself was not right either. Each <bloque> of the consolidated text
holds every <version> it has had, and the old code joined them all: article 1
appeared in its 1889 wording ("Las Leyes obligarán en la Península...") and in
its 1974 wording, one after the other. Only one of those is law.

The tests drive the MCP tool handlers, the surface a caller sees.

    python -m unittest discover -s tests          (from es-boe-mcp/)
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import boe
import es_fakes

TITLE = "Real Decreto de 24 de julio de 1889 por el que se publica el Código Civil."
ART1_1889 = "Las Leyes obligarán en la Península"
ART1_1974 = "Las fuentes del ordenamiento jurídico español son la ley, la costumbre"
ART278_2021 = "Serán removidos de la curatela"
ART56_2017 = "Quienes deseen contraer matrimonio acreditarán previamente en acta o expediente"


class ActTextTests(unittest.TestCase):
    def setUp(self):
        self.fake = es_fakes.FakeBoe()
        p = mock.patch.object(boe, "_get", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.server = es_fakes.load_server(os.path.join(tempfile.mkdtemp(), "index.db"))

    def text(self, **args):
        args.setdefault("boe_id", es_fakes.CODE)
        return self.server._t_get_act_text(args)

    def test_get_act_is_metadata_only(self):
        act = self.server._t_get_act({"boe_id": es_fakes.CODE})
        self.assertNotIn("text", act)
        self.assertEqual(act["title"], TITLE)
        self.assertEqual(act["rango"], "Real Decreto")
        self.assertEqual(act["date"], "1889-07-24")
        self.assertEqual(act["estatus_derogacion"], "N")
        self.assertIn("Código Civil", act["materias"])
        self.assertFalse(any(u.endswith(es_fakes.CODE) or u.endswith("/texto")
                             for u in self.fake.calls), self.fake.calls)

    def test_text_carries_the_header(self):
        doc = self.text()
        self.assertEqual(doc["title"], TITLE)
        self.assertEqual(doc["rango"], "Real Decreto")
        self.assertEqual(doc["date"], "1889-07-24")
        self.assertEqual(doc["citation"], "%s (%s)" % (TITLE, es_fakes.CODE))

    def test_text_is_the_version_in_force(self):
        text = self.text(as_of="2026-10-09")["text"]
        self.assertIn(ART1_1974, text)
        self.assertNotIn(ART1_1889, text)
        self.assertEqual(text.count("Artículo 1."), 1)

    def test_irregular_versions_resolve_to_the_current_one(self):
        text = self.text(as_of="2026-10-09")["text"]
        self.assertIn(ART278_2021, text)            # versions listed out of order
        self.assertIn(ART56_2017, text)             # one version with no fecha_vigencia
        self.assertEqual(text.count("Artículo 56."), 1)
        self.assertEqual(text.count("Artículo 278."), 1)

    def test_as_of_gives_the_text_on_that_date(self):
        doc = self.text(as_of="1950-01-01")
        self.assertIn(ART1_1889, doc["text"])
        self.assertNotIn(ART1_1974, doc["text"])
        self.assertEqual(doc["as_of"], "1950-01-01")

    def test_text_pages_with_offset(self):
        whole = self.text(as_of="2026-10-09", max_chars=100000)
        first = self.text(as_of="2026-10-09", max_chars=2000)
        self.assertEqual(len(first["text"]), 2000)
        self.assertEqual(first["next_offset"], 2000)
        second = self.text(as_of="2026-10-09", max_chars=2000, offset=first["next_offset"])
        self.assertEqual(first["text"] + second["text"], whole["text"][:4000])
        self.assertEqual(whole["length_chars"], len(whole["text"]))
        self.assertNotIn("next_offset", whole)


if __name__ == "__main__":
    unittest.main()
