#!/usr/bin/env python3
"""Gate for the secret-exemplar detector (Phase 1.7 "mirror your last-added secret").

Pure AST + injectable blame dates — no git history needed. Negative cases lead: a
FALSE exemplar makes the engineer correct a confident-looking wrong proposal, so
precision (not over-matching `cache_key` / `public_key`) is the property under test."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
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
    def test_newest_by_blame_wins_and_opinion_from_last_three(self):
        fields = se.find_secret_fields(_SETTINGS)
        by_name = {f.name: f for f in fields}
        # stripe_api_key (NOT last-defined) is the most-recently-ADDED by blame date
        dates = {by_name["stripe_api_key"].lineno: 2000.0,
                 by_name["legacy_token"].lineno: 1000.0,
                 by_name["openai_secret"].lineno: 1500.0}
        ex = se.propose_exemplar(_SETTINGS, line_dates=dates)
        self.assertEqual(ex.field.name, "stripe_api_key")    # primary = newest
        self.assertEqual(ex.confidence, "blame")
        # OPINION from the last 3 (not just one): stripe(SecretStr), openai(SecretStr),
        # legacy(plain) -> 2/3 SecretStr -> majority, not a single instance.
        self.assertEqual({f.name for f in ex.considered},
                         {"stripe_api_key", "legacy_token", "openai_secret"})
        self.assertEqual(ex.secret_type, "SecretStr")
        self.assertEqual(ex.agreement, "majority")

    def test_falls_back_to_last_defined_without_dates(self):
        ex = se.propose_exemplar(_SETTINGS, line_dates=None)
        self.assertEqual(ex.field.name, "openai_secret")   # last-defined secret
        self.assertEqual(ex.confidence, "position")        # weaker signal -> flagged

    def test_unanimous_when_recent_three_agree(self):
        src = ("from pydantic import SecretStr\n"
               "class S:\n"
               "    a_token: SecretStr\n    b_secret: SecretStr\n    c_api_key: SecretStr\n")
        ex = se.propose_exemplar(src)
        self.assertEqual(ex.secret_type, "SecretStr")
        self.assertEqual(ex.agreement, "unanimous")
        self.assertEqual(len(ex.considered), 3)

    def test_single_secret_agreement(self):
        src = "class S:\n    only_api_key: str = ''\n"
        ex = se.propose_exemplar(src)
        self.assertEqual(ex.agreement, "single")
        self.assertEqual(len(ex.considered), 1)

    def test_split_when_two_disagree(self):
        # exactly two recent secrets, one each type -> no majority -> split/mixed.
        src = ("from pydantic import SecretStr\n"
               "class S:\n    a_token: SecretStr\n    b_password: str = ''\n")
        ex = se.propose_exemplar(src, n=2)
        self.assertEqual(ex.agreement, "split")
        self.assertEqual(ex.secret_type, "mixed")

    def test_none_when_no_secret(self):
        self.assertIsNone(se.propose_exemplar("class S:\n    a: int = 1\n"))


class AccessIdiomSearch(unittest.TestCase):
    """SEARCH for HOW the config is read (the dimension blame can't see). The
    singleton case is moo-arc's — and the one the get_settings()-hardcoded helper
    broke on (ImportError: no get_settings in app/config.py)."""

    def test_singleton_module_var(self):
        src = ("from pydantic_settings import BaseSettings\n"
               "class Settings(BaseSettings):\n"
               "    arc_global_api_key: str = ''\n"
               "settings = Settings()\n")          # moo-arc shape
        idiom = se.detect_access_idiom(src)
        self.assertEqual(idiom.kind, "singleton")
        self.assertEqual(idiom.import_name, "settings")
        self.assertEqual(idiom.read("moolabs_api_key"), "settings.moolabs_api_key")

    def test_factory_function(self):
        src = ("class Settings:\n    api_key: str = ''\n"
               "def get_settings():\n    return Settings()\n")
        idiom = se.detect_access_idiom(src)
        self.assertEqual(idiom.kind, "factory")
        self.assertEqual(idiom.import_name, "get_settings")
        self.assertEqual(idiom.read("moolabs_api_key"), "get_settings().moolabs_api_key")

    def test_factory_wins_when_both_present(self):
        src = ("class Settings:\n    api_key: str = ''\n"
               "settings = Settings()\n"
               "def get_settings():\n    return settings\n")
        self.assertEqual(se.detect_access_idiom(src).kind, "factory")

    def test_unknown_when_neither(self):
        # DI / custom: a Settings class but no module singleton, no factory.
        src = "class Settings:\n    api_key: str = ''\n"
        idiom = se.detect_access_idiom(src)
        self.assertEqual(idiom.kind, "unknown")
        self.assertIsNone(idiom.import_name)
        self.assertIsNone(idiom.read("moolabs_api_key"))   # caller -> flag / stub

    def test_singleton_only_when_rhs_is_a_settings_class(self):
        # a module-level `x = SomethingElse()` must NOT be read as the settings singleton
        src = ("class Settings:\n    api_key: str = ''\n"
               "logger = Logger()\n")
        self.assertEqual(se.detect_access_idiom(src).kind, "unknown")


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


class TokenDerivation(unittest.TestCase):
    """The env var is the identifier that bridges config <-> deployment in every
    pattern. Deriving it from the field is the BOUNDED, testable part of the trace."""

    def test_snake_and_camel_to_env_var(self):
        self.assertEqual(se.env_var_for_field("moolabs_api_key"), "MOOLABS_API_KEY")
        self.assertEqual(se.env_var_for_field("arc_global_api_key"), "ARC_GLOBAL_API_KEY")
        self.assertEqual(se.env_var_for_field("arcGlobalApiKey"), "ARC_GLOBAL_API_KEY")
        self.assertEqual(se.env_var_for_field("MoolabsApiKey"), "MOOLABS_API_KEY")


@unittest.skipUnless(shutil.which("grep"), "grep not available")
class GrepTokens(unittest.TestCase):
    """Format-AGNOSTIC wide search — the same grep finds the exemplar in terraform, k8s
    yaml, AND python, and skips vendor. No format zoo: the agent classifies the hits."""

    def test_wide_search_finds_all_formats_skips_vendor(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "infra"))
            os.makedirs(os.path.join(d, "node_modules", "x"))
            with open(os.path.join(d, "infra", "main.tf"), "w") as f:
                f.write('secrets = [{ name = "ARC_GLOBAL_API_KEY", valueFrom = x["shared/api-key"] }]\n')
            with open(os.path.join(d, "deploy.yaml"), "w") as f:
                f.write("        - name: ARC_GLOBAL_API_KEY\n          valueFrom:\n")
            with open(os.path.join(d, "config.py"), "w") as f:
                f.write("arc_global_api_key: SecretStr\n")
            with open(os.path.join(d, "node_modules", "x", "main.tf"), "w") as f:
                f.write("ARC_GLOBAL_API_KEY\n")   # vendor copy -> must be skipped
            hits = se.grep_tokens(d, ["ARC_GLOBAL_API_KEY", "arc_global_api_key"])
            files = {h[0] for h in hits}
            self.assertIn("infra/main.tf", files)
            self.assertIn("deploy.yaml", files)
            self.assertIn("config.py", files)
            self.assertNotIn("node_modules/x/main.tf", files)

    def test_empty_tokens_returns_empty(self):
        self.assertEqual(se.grep_tokens("/tmp", []), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
