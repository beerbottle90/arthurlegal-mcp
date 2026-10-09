"""search_acts says when a result matched by meaning alone (defect #7).

Live, fi_search_acts("vahingonkorvauslaki") — the Tort Liability Act, 412/1974 —
returned three unrelated acts with no warning. The index holds only the
2024-2025 consolidated texts (250 documents), so no word of the query occurs in
it: the keyword channels returned nothing and the semantic channel returned its
nearest neighbours, which are always *something*.

Reproduced on a copy of the deployed index re-embedded with bge-m3: the query's
best cosine was 0.493, 2.79 standard deviations above the corpus mean — what
the top of 250 unrelated documents looks like. Related documents found by
meaning alone (a Turkish or English query for a Finnish act) stood 3.45-4.27
sd above it.

The vectors here are synthetic and fixed (64 dimensions, seeded), so the test
needs no embeddings backend.

    python -m unittest discover -s tests          (from fi-finlex-mcp/)
"""

from __future__ import annotations

import math
import os
import random
import struct
import tempfile
import unittest
from unittest import mock

import fi_fakes
import retrieval

DIM = 64


def _unit(vec):
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class SemanticOnlyTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(20261009)
        self.vectors = {}
        tmp = tempfile.mkdtemp()
        p1 = mock.patch.object(retrieval, "_probe", lambda force=False: True)
        p2 = mock.patch.object(retrieval, "_embed", self._embed)
        for p in (p1, p2):
            p.start()
            self.addCleanup(p.stop)
        self.server = fi_fakes.load_server(os.path.join(tmp, "index.db"))
        idx = self.server._index
        docs = [("Hevosten hyvinvointi", "Valtioneuvoston asetus hevosten hyvinvoinnista")]
        docs += [("Asetus %d" % i, "Valtioneuvoston asetus numero %d maksuista ja menettelyistä" % i)
                 for i in range(59)]
        model = retrieval.embeddings_model()
        for i, (title, body) in enumerate(docs):
            ref = "/akn/fi/act/statute-consolidated/2025/%d/fin@" % (i + 1)
            doc_id = idx.upsert({"ref": ref, "title": title, "body": body, "lang": "fin",
                                 "date": "2025-01-01", "status": "consolidated"})
            vec = _unit([rng.gauss(0, 1) for _ in range(DIM)])
            self.vectors[ref] = vec
            idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?,?,?,?)",
                           (doc_id, DIM, struct.pack("<%df" % DIM, *vec), model))
        idx.db.commit()
        idx.reindex_fts()
        idx.set_state("coverage", "statute-consolidated 2024-2025 (fin+swe)")
        horse = self.vectors["/akn/fi/act/statute-consolidated/2025/1/fin@"]
        noise = _unit([rng.gauss(0, 1) for _ in range(DIM)])
        self.queries = {
            # Unrelated to every document: a random direction.
            "vahingonkorvauslaki": _unit([rng.gauss(0, 1) for _ in range(DIM)]),
            # Related to the horse-welfare decree, in another language: cosine
            # ~0.55 to it, about 4.4 sd above random documents.
            "horse welfare": _unit([h + 1.5 * n for h, n in zip(horse, noise)]),
        }

    def _embed(self, texts, timeout=60, input_type=None):
        return [self.queries.get(t, [1.0] + [0.0] * (DIM - 1)) for t in texts]

    def search(self, query, **args):
        args.update({"query": query})
        return self.server._t_search(args)

    def test_unrelated_query_is_flagged_and_its_noise_marked(self):
        out = self.search("vahingonkorvauslaki", limit=3)
        self.assertEqual(out["retrieval"]["channels_used"].get("lexical"), 0)
        self.assertIn("warning", out)
        self.assertIn("2024-2025", out["warning"])
        self.assertIn("meaning", out["warning"])
        self.assertIn("probably outside the index", out["warning"])
        self.assertTrue(out["results"])
        self.assertTrue(all(r.get("likely_unrelated") for r in out["results"]))
        self.assertEqual(out["semantic_filter"]["likely_unrelated"], len(out["results"]))

    def test_semantic_mode_is_never_flagged(self):
        # 1.1.0 treated every mode="semantic" call as "no word occurs" (the keyword
        # channels are not asked in that mode) and dropped right answers below the
        # floor: fi-02 and fi-04 came back empty in the 2026-10-09 measurement.
        out = self.search("horse welfare", limit=3, mode="semantic")
        self.assertEqual(out["results"][0]["title"], "Hevosten hyvinvointi")
        self.assertNotIn("warning", out)
        self.assertNotIn("semantic_filter", out)
        self.assertEqual(len(out["results"]), 3)
        self.assertTrue(all("match" not in r for r in out["results"]))

    def test_related_query_found_by_meaning_is_kept_and_labelled(self):
        out = self.search("horse welfare", limit=3)
        self.assertTrue(out["results"])
        top = out["results"][0]
        self.assertEqual(top["title"], "Hevosten hyvinvointi")
        self.assertEqual(top["match"], "semantic only")
        self.assertGreater(top["similarity_z"], 3.4)
        self.assertNotIn("likely_unrelated", top)
        self.assertIn("warning", out)
        self.assertNotIn("probably outside the index", out["warning"])
        self.assertTrue(all(r["match"] == "semantic only" for r in out["results"]))

    def test_keyword_matches_are_left_alone(self):
        out = self.search("hevosten hyvinvoinnista", limit=3)
        self.assertGreater(out["retrieval"]["channels_used"]["lexical"], 0)
        self.assertNotIn("semantic_filter", out)
        self.assertEqual(out["results"][0]["title"], "Hevosten hyvinvointi")
        self.assertNotIn("match", out["results"][0])


if __name__ == "__main__":
    unittest.main()
