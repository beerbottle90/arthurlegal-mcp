"""get_act resolves the current consolidated version itself (defect #8).

fi_get_act(1974, 412, "fin@") answered "ERROR: HTTP 404 from Finlex:
.../1974/412/fin@/main.akn". A consolidated text lives under a version stamp
(fin@20101051 for the Tort Liability Act); "fin@" alone exists only for some
acts, and where it does it is the first consolidated version, not the latest.
Finlex resolves ``{lang}@latest`` to the current version, and the package it
returns states which version that is.

The tests drive the MCP tool handler, the surface a caller sees.

    python -m unittest discover -s tests          (from fi-finlex-mcp/)
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import fi_fakes
import finlex
from mcpcore import McpError


class VersionResolutionTests(unittest.TestCase):
    def setUp(self):
        self.fake = fi_fakes.FakeUrlopen()
        p = mock.patch.object(finlex.urllib.request, "urlopen", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.server = fi_fakes.load_server(os.path.join(tempfile.mkdtemp(), "index.db"))

    def get(self, **args):
        args.setdefault("year", 1974)
        args.setdefault("number", "412")
        return self.server._t_get_act(args)

    def test_bare_language_resolves_to_the_latest_version(self):
        act = self.get(lang_version="fin@")
        self.assertEqual(act["lang_version"], "fin@20101051")
        self.assertEqual(act["title"], "Vahingonkorvauslaki")
        self.assertEqual(act["consolidated"], "2010-12-03")
        self.assertIn("vahingon", act["text"].lower())
        self.assertIn("fin@20101051", act["version_note"])

    def test_lang_version_may_be_omitted(self):
        act = self.get()
        self.assertEqual(act["lang_version"], "fin@20101051")

    def test_swedish(self):
        act = self.get(lang_version="swe@")
        self.assertEqual(act["lang_version"], "swe@20101051")
        self.assertEqual(act["title"], "Skadeståndslag")

    def test_an_explicit_version_is_used_as_given(self):
        act = self.get(lang_version="fin@20101051")
        self.assertEqual(act["lang_version"], "fin@20101051")
        self.assertFalse(any("@latest" in u for u in self.fake.calls), self.fake.calls)

    def test_original_publication_needs_no_version(self):
        act = self.get(lang_version="fin@", act_type="statute")
        self.assertEqual(act["act_type"], "statute")
        self.assertFalse(any("@latest" in u for u in self.fake.calls), self.fake.calls)

    def test_no_consolidated_text_says_what_to_do(self):
        with self.assertRaises(McpError) as ctx:
            self.get(year=2024, number="1060", lang_version="fin@")
        self.assertIn("act_type='statute'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
