#!/usr/bin/env python3
"""Gate for the secret-exemplar detector (Phase 1.7 "mirror your last-added secret").

Pure AST + injectable blame dates — no git history needed. Negative cases lead: a
FALSE exemplar makes the engineer correct a confident-looking wrong proposal, so
precision (not over-matching `cache_key` / `public_key`) is the property under test."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_exemplar as se  # noqa: E402

_SETTINGS = """\
from pydantic_settings import BaseSettings
from pydantic import SecretStr

class Settings(BaseSettings):
    debug: bool = False
    cache_key: str = "default"          # NOT a secret (bare _key)
    public_key: str = ""                # NOT a secret
    database_url: str = ""
    stripe_api_key: SecretStr           # secret (annotation + name)
    legacy_token: str = ""              # secret (name suffix)
    openai_secret: SecretStr | None = None   # secret (annotation, Optional)
"""


class FindSecretFields(unittest.TestCase):
    def test_finds_secretstr_and_name_suffix_not_decoys(self):
        names = [f.name for f in se.find_secret_fields(_SETTINGS)]
        self.assertIn("stripe_api_key", names)     # SecretStr + name
        self.assertIn("legacy_token", names)       # name suffix only
        self.assertIn("openai_secret", names)      # SecretStr | None
        self.assertNotIn("cache_key", names)       # bare _key decoy
        self.assertNotIn("public_key", names)      # decoy
        self.assertNotIn("database_url", names)    # not secret-typed/named
        self.assertNotIn("debug", names)

    def test_reason_prefers_annotation(self):
        by = {f.name: f for f in se.find_secret_fields(_SETTINGS)}
        self.assertIn("SecretStr", by["stripe_api_key"].reason)
        self.assertIn("name suffix", by["legacy_token"].reason)

    def test_no_secrets_returns_empty(self):
        src = "class S:\n    a: int = 1\n    name: str = ''\n"
        self.assertEqual(se.find_secret_fields(src), [])

    def test_syntax_error_returns_empty(self):
        self.assertEqual(se.find_secret_fields("class (:\n"), [])


class ProposeExemplar(unittest.TestCase):
    def test_newest_by_blame_wins_even_if_not_last_defined(self):
        fields = se.find_secret_fields(_SETTINGS)
        by_name = {f.name: f for f in fields}
        # stripe_api_key (NOT last-defined) is the most-recently-ADDED by blame date
        dates = {by_name["stripe_api_key"].lineno: 2000.0,
                 by_name["legacy_token"].lineno: 1000.0,
                 by_name["openai_secret"].lineno: 1500.0}
        ex = se.propose_exemplar(_SETTINGS, line_dates=dates)
        self.assertEqual(ex.field.name, "stripe_api_key")
        self.assertEqual(ex.confidence, "blame")

    def test_falls_back_to_last_defined_without_dates(self):
        ex = se.propose_exemplar(_SETTINGS, line_dates=None)
        self.assertEqual(ex.field.name, "openai_secret")   # last-defined secret
        self.assertEqual(ex.confidence, "position")        # weaker signal -> flagged

    def test_none_when_no_secret(self):
        self.assertIsNone(se.propose_exemplar("class S:\n    a: int = 1\n"))


class BlamePorcelain(unittest.TestCase):
    def test_parses_author_time_per_sha_incl_repeated_commit(self):
        a = "a" * 40
        b = "b" * 40
        porcelain = (
            f"{a} 1 1 1\nauthor X\nauthor-time 1000\nauthor-tz +0000\n\tline one\n"
            f"{b} 2 2 1\nauthor Y\nauthor-time 2000\nauthor-tz +0000\n\tline two\n"
            # line 3 reuses commit `a` — header repeats, author-time does NOT
            f"{a} 3 3 1\n\tline three\n"
        )
        got = se._parse_porcelain_author_times(porcelain, {1, 2, 3})
        self.assertEqual(got, {1: 1000.0, 2: 2000.0, 3: 1000.0})

    def test_blame_empty_linenos_returns_empty(self):
        self.assertEqual(se.blame_line_dates("whatever.py", []), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
