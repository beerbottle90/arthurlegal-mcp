"""crawl.py --backfill-text: the head of the consolidated text joins the metadata body.

With titles alone a question asked in plain words found its act in the top 10
three times in four (independent 120-question set, 2026-10-10); the preamble and
the opening articles say what an act regulates in plain words.

    python -m unittest discover -s tests          (from es-boe-mcp/)
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

import boe
import es_fakes
import retrieval

spec = importlib.util.spec_from_file_location("es_crawl_backfill", os.path.join(es_fakes.BACKEND, "crawl.py"))
crawl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crawl)


class BackfillTextTests(unittest.TestCase):
    def setUp(self):
        self.fake = es_fakes.FakeBoe()
        for p in (mock.patch.object(boe, "_get", self.fake), mock.patch.object(crawl.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.idx = retrieval.Index(os.path.join(tempfile.mkdtemp(), "index.db"))
        self.addCleanup(self.idx.db.close)
        for ref, title in ((es_fakes.CODE, "Real Decreto de 24 de julio de 1889 por el que se publica el Código Civil."),
                           ("BOE-A-1999-1", "Ley inexistente")):
            doc_id = self.idx.upsert({"ref": ref, "title": title, "body": "Real Decreto\nMinisterio de Gracia y Justicia",
                                      "lang": "es", "meta": {"rango": "Real Decreto"}})
            self.idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?,?,?,?)",
                                (doc_id, 2, b"\x00" * 8, retrieval.embeddings_model()))
        self.idx.db.commit()

    def row(self, ref):
        r = self.idx.db.execute("SELECT id, body, meta FROM docs WHERE ref = ?", (ref,)).fetchone()
        return r["id"], r["body"], json.loads(r["meta"])

    def test_the_head_of_the_text_follows_the_metadata(self):
        out = crawl.backfill_text(self.idx, log=lambda m: None)
        self.assertEqual(out, {"candidates": 2, "text": 1, "no_text": 0, "failed": 1})
        doc_id, body, meta = self.row(es_fakes.CODE)
        metadata, head = body.split(crawl.TEXT_MARK, 1)
        self.assertEqual(metadata, "Real Decreto\nMinisterio de Gracia y Justicia")
        self.assertTrue(head and len(head) <= crawl.HEAD_CHARS)
        self.assertEqual(meta["text_chars"], len(head))
        self.assertIsNone(self.idx.db.execute("SELECT 1 FROM vecs WHERE doc_id = ?", (doc_id,)).fetchone(),
                          "the old vector described the body without its text")

    def test_one_request_per_act_and_a_rerun_asks_again_only_for_failures(self):
        crawl.backfill_text(self.idx, log=lambda m: None)
        texto_calls = [u for u in self.fake.calls if u.endswith("/texto")]
        self.assertEqual(len(texto_calls), 2)
        self.assertFalse([u for u in self.fake.calls if u.endswith("/metadatos")])
        del self.fake.calls[:]
        out = crawl.backfill_text(self.idx, log=lambda m: None)
        self.assertEqual(out["candidates"], 1)          # only the act BOE did not answer for
        _, _, meta = self.row("BOE-A-1999-1")
        self.assertNotIn("text_chars", meta)

    def test_a_metadata_recrawl_keeps_the_head(self):
        crawl.backfill_text(self.idx, log=lambda m: None)
        _, before, meta_before = self.row(es_fakes.CODE)
        item = {"id": es_fakes.CODE, "title": "Real Decreto de 24 de julio de 1889 por el que se publica el Código Civil.",
                "numero_oficial": "", "rango": "Real Decreto", "departamento": "Ministerio de Gracia y Justicia",
                "ambito": "Estatal", "url": "https://www.boe.es/buscar/act.php?id=" + es_fakes.CODE}
        with mock.patch.object(crawl.BoeClient, "list_consolidated", lambda self, limit=100, offset=0: [item] if not offset else []):
            crawl.crawl(self.idx)
        _, after, meta_after = self.row(es_fakes.CODE)
        self.assertEqual(after.split(crawl.TEXT_MARK, 1)[1], before.split(crawl.TEXT_MARK, 1)[1])
        self.assertEqual(meta_after["text_chars"], meta_before["text_chars"])


if __name__ == "__main__":
    unittest.main()
