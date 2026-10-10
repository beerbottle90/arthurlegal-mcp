"""The shared retrieval module: hybrid fusion, exact refs, the scan, the copies.

A synthetic index with 16-dimensional vectors; no embeddings backend, no network.
The fusion cases are the ones the 2026-10-09 known-item measurement turned up:
a question in plain words lost its document to keyword noise, and an ECLI lost
to the semantic channel.

    python -m unittest discover -s tests
"""

import importlib.util
import math
import operator
import os
import random
import struct
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The aggregator imports Türkiye first, so this copy serves every backend.
CANONICAL = os.path.join(ROOT, "arthur-tr-hukuk-mcp", "retrieval.py")
COPIES = ("at-ris-mcp", "es-boe-mcp", "eu-cellar-mcp", "fi-finlex-mcp", "gleif-mcp",
          "ie-statutebook-mcp", "jp-egov-mcp", "nl-rechtspraak-mcp", "pl-sejm-mcp",
          "uk-legislation-mcp")
DIM = 16


def _load():
    spec = importlib.util.spec_from_file_location("retrieval_under_test", CANONICAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load()


def _unit(vec):
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class Corpus(unittest.TestCase):
    """Twelve decisions. Only #0 is about rent; #1-#11 share the word "kadar"."""

    def setUp(self):
        self.rng = random.Random(20261009)
        self.queries = {}
        self.embed_calls = []
        R._probe_cache.update(at=0.0, ok=False, error="")
        patcher = mock.patch.object(R, "_embed", self._embed)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.idx = R.Index(os.path.join(tempfile.mkdtemp(), "index.db"))
        self.addCleanup(self.idx.db.close)
        self.vec = {}
        docs = [("tr:rent", "Kira artışı", "Konut kira bedelinin yıllık artış oranı ve tavanı")]
        docs += [("tr:noise-%d" % i, "Kurul kararı %d" % i,
                  "Başvurunun %d gün kadar ertelenmesi ve ücret tarifesi" % i) for i in range(1, 12)]
        docs.append(("ECLI:NL:HR:2026:842", "ECLI:NL:HR:2026:842", "Hoge Raad, cassatie"))
        for ref, title, body in docs:
            self.add(ref, title, body)
        self.idx.reindex_fts()
        rent = self.vec["tr:rent"]
        noise = _unit([self.rng.gauss(0, 1) for _ in range(DIM)])
        # Close to the rent decision, sharing none of its words.
        self.queries["ev sahibi kirayı ne kadar yükseltebilir"] = _unit(
            [r + 0.5 * n for r, n in zip(rent, noise)])

    def add(self, ref, title, body, vector=True, dim=DIM):
        doc_id = self.idx.upsert({"ref": ref, "title": title, "body": body, "lang": "tur",
                                  "date": "2026-01-01", "subject": "rekabet"})
        if vector:
            vec = _unit([self.rng.gauss(0, 1) for _ in range(dim)])
            self.vec[ref] = vec
            self.idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?,?,?,?)",
                                (doc_id, dim, struct.pack("<%df" % dim, *vec), R.embeddings_model()))
            self.idx.db.commit()
        return doc_id

    def _embed(self, texts, timeout=60, input_type=None):
        self.embed_calls.append((tuple(texts), input_type))
        if getattr(self, "down", False):
            raise OSError("connection refused")
        return [self.queries.get(t) or _unit([self.rng.gauss(0, 1) for _ in range(DIM)]) for t in texts]

    def refs(self, out):
        return [r["ref"] for r in out["results"]]


class FusionTests(Corpus):
    def test_a_question_in_plain_words_is_ranked_by_meaning(self):
        out = self.idx.search("ev sahibi kirayı ne kadar yükseltebilir", limit=5)
        self.assertEqual(self.refs(out)[0], "tr:rent")
        self.assertEqual(out["retrieval"]["keyword_match"], "some words")
        self.assertIn("ranking", out["retrieval"])

    def test_a_keyword_query_keeps_its_keyword_ranking(self):
        out = self.idx.search("kira artışı", limit=5)
        self.assertEqual(self.refs(out)[0], "tr:rent")
        self.assertEqual(out["retrieval"]["keyword_match"], "all words")
        self.assertNotIn("ranking", out["retrieval"])

    def test_an_identifier_is_listed_first_in_any_case(self):
        for query in ("ECLI:NL:HR:2026:842", "ecli:nl:hr:2026:842"):
            out = self.idx.search(query, limit=5)
            self.assertEqual(self.refs(out)[0], "ECLI:NL:HR:2026:842", query)
            self.assertIn("exact_ref", out["retrieval"])

    def test_an_identifier_outside_the_filter_is_not_pinned(self):
        out = self.idx.search("ECLI:NL:HR:2026:842", limit=5, filters={"subject": "epdk"})
        self.assertNotIn("ECLI:NL:HR:2026:842", self.refs(out))

    def test_any_word_matches_fill_a_list_the_semantic_channel_left_short(self):
        self.idx.db.execute("DELETE FROM vecs WHERE doc_id IN (SELECT id FROM docs WHERE ref LIKE 'tr:noise-%')")
        self.idx.db.commit()
        out = self.idx.search("ev sahibi kirayı ne kadar yükseltebilir", limit=8)
        refs = self.refs(out)
        self.assertEqual(refs[0], "tr:rent")
        self.assertEqual(len(refs), 8)
        self.assertTrue(all(r.startswith("tr:noise-") for r in refs[2:]))
        self.assertTrue(all(r["score"] == 0.0 for r in out["results"][2:]))

    def test_without_the_semantic_channel_any_word_matches_rank_as_before(self):
        self.down = True
        out = self.idx.search("ev sahibi kirayı ne kadar yükseltebilir", limit=5)
        self.assertEqual(out["retrieval"]["semantic"], "off")
        self.assertNotIn("ranking", out["retrieval"])
        self.assertTrue(out["results"])
        self.assertTrue(all(r["score"] > 0 for r in out["results"]))
        self.assertIn("warning", out["retrieval"])

    def test_the_any_word_rung_is_only_checked_when_meaning_fills_the_list(self):
        seen = []
        real = R.Index._run_fts

        def run_fts(index, expr, filters, k):
            seen.append(expr)
            return real(index, expr, filters, k)

        query = "ev sahibi kirayı ne kadar yükseltebilir"
        with mock.patch.object(R.Index, "_run_fts", run_fts):
            out = self.idx.search(query, limit=5)
        self.assertEqual(out["retrieval"]["keyword_match"], "some words")
        self.assertEqual(len(seen), 2)                    # all words, all words as prefixes
        self.assertNotIn(R._fts_query(query, prefix=True, join="OR"), seen)
        self.assertNotIn("lexical", out["retrieval"]["channels_used"])

    def test_lexical_mode_is_unchanged(self):
        out = self.idx.search("ev sahibi kirayı ne kadar yükseltebilir", mode="lexical", limit=5)
        self.assertEqual(out["retrieval"]["keyword_match"], "some words")
        self.assertNotIn("tr:rent", self.refs(out))


class ScanTests(Corpus):
    def setUp(self):
        super().setUp()
        for i in range(300):
            self.add("tr:extra-%d" % i, "Ek karar %d" % i, "metin %d" % i)
        self.add("tr:other-dim", "Başka boyut", "metin", dim=DIM + 1)

    def scan(self):
        return self.idx._semantic("ev sahibi kirayı ne kadar yükseltebilir", {}, 25, 50000)

    def test_every_engine_and_chunk_size_returns_the_same_ranking(self):
        reference = self.scan()
        self.assertEqual(len(reference), 25)
        self.assertEqual(reference[0], self.idx.db.execute(
            "SELECT id FROM docs WHERE ref='tr:rent'").fetchone()["id"])
        engines = [("python, map", None, lambda a, b: sum(map(operator.mul, a, b)))]
        if hasattr(math, "sumprod"):
            engines.append(("python, sumprod", None, math.sumprod))
        for name, numpy, dot in engines:
            with mock.patch.object(R, "_np", numpy), mock.patch.object(R, "_dot", dot):
                self.assertEqual(self.scan(), reference, name)
        for chunk in (1, 7, 64):
            with mock.patch.object(R, "_SCAN_CHUNK", chunk):
                self.assertEqual(self.scan(), reference, "chunk %d" % chunk)

    def test_numpy_scan_matches_the_python_scan(self):
        if R._np is None:
            self.skipTest("numpy is not installed here")
        with_numpy = self.scan()
        with mock.patch.object(R, "_np", None):
            self.assertEqual(self.scan(), with_numpy)


class ResidentTests(ScanTests):
    """SEMANTIC_RESIDENT_MB: the vectors stay in memory as float16."""

    def setUp(self):
        super().setUp()
        if R._np is None:
            self.skipTest("numpy is not installed here")
        self.addCleanup(self.idx._drop_resident)
        for i in range(5):
            self.add("tr:epdk-%d" % i, "EPDK kararı %d" % i, "metin")
        self.idx.db.execute("UPDATE docs SET subject = 'epdk' WHERE ref LIKE 'tr:epdk-%'")
        self.idx.db.commit()

    def resident(self, mb="50"):
        return mock.patch.dict(os.environ, {"SEMANTIC_RESIDENT_MB": mb})

    def test_resident_vectors_rank_like_the_scan(self):
        streamed = self.scan()
        with self.resident():
            self.assertEqual(self.scan(), streamed)
            self.assertIsNotNone(getattr(self.idx, "_resident", None))
            out = self.idx.search("ev sahibi kirayı ne kadar yükseltebilir", mode="semantic", limit=3)
            self.assertIn("resident", out["retrieval"]["scan"])

    def test_resident_vectors_respect_filters(self):
        q = "ev sahibi kirayı ne kadar yükseltebilir"
        streamed = self.idx._semantic(q, {"subject": "epdk"}, 10, 50000)
        with self.resident():
            self.assertEqual(self.idx._semantic(q, {"subject": "epdk"}, 10, 50000), streamed)
            self.assertEqual(len(streamed), 5)
            self.assertEqual(self.idx._semantic(q, {"subject": "yok"}, 10, 50000), [])

    def test_a_vector_written_by_another_connection_is_seen(self):
        q = "ev sahibi kirayı ne kadar yükseltebilir"
        with self.resident():
            before = self.idx._semantic(q, {}, 3, 50000)
            other = R.Index(self.idx.path)          # the boot-time embedder is another process
            doc_id = other.upsert({"ref": "tr:new", "title": "Yeni", "body": "metin", "subject": "rekabet"})
            vec = self.queries[q]                   # the query itself: the nearest possible vector
            other.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?,?,?,?)",
                             (doc_id, DIM, struct.pack("<%df" % DIM, *vec), R.embeddings_model()))
            other.db.commit()
            other.db.close()
            after = self.idx._semantic(q, {}, 3, 50000)
        self.assertNotEqual(before[0], doc_id)
        self.assertEqual(after[0], doc_id)

    def test_a_budget_that_is_too_small_falls_back_to_the_scan(self):
        streamed = self.scan()
        with self.resident("0.001"):
            self.assertEqual(self.scan(), streamed)
            self.assertIsNone(getattr(self.idx, "_resident", None))


class ProbeTests(Corpus):
    def test_a_search_after_a_quiet_minute_makes_one_embedding_call(self):
        self.idx.search("ev sahibi kirayı ne kadar yükseltebilir", mode="semantic", limit=3)
        self.assertEqual(self.embed_calls, [(("ev sahibi kirayı ne kadar yükseltebilir",), "query")])

    def test_a_lexical_search_does_not_ping(self):
        self.idx.search("kira artışı", mode="semantic", limit=3)
        R._probe_cache["at"] = time.time() - 3600       # stale, last seen up
        del self.embed_calls[:]
        out = self.idx.search("kira artışı", mode="lexical", limit=3)
        self.assertEqual(self.embed_calls, [])
        self.assertEqual(out["retrieval"]["semantic"], "on")

    def test_a_recent_failure_is_not_retried_for_every_query(self):
        self.down = True
        self.idx.search("kira artışı", limit=3)
        self.idx.search("kira artışı", limit=3)
        self.assertEqual(len(self.embed_calls), 1)


class CopyTests(unittest.TestCase):
    def test_every_backend_vendors_the_same_retrieval_module(self):
        def text(path):
            with open(path, "rb") as fh:
                return fh.read().replace(b"\r\n", b"\n")
        canonical = text(CANONICAL)
        for directory in COPIES:
            self.assertEqual(text(os.path.join(ROOT, directory, "retrieval.py")), canonical, directory)


if __name__ == "__main__":
    unittest.main()
