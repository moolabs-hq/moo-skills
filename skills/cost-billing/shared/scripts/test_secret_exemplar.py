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
        src = "class Settings:\n    a: int = 1\n    name: str = ''\n"
        self.assertEqual(se.find_secret_fields(src), [])

    def test_scoped_to_settings_classes_not_dtos(self):
        # a DTO with a secret-named field must NOT be proposed as the config exemplar.
        src = ("from pydantic_settings import BaseSettings\n"
               "from pydantic import SecretStr\n"
               "class LoginForm:\n    password: str = ''\n"
               "class Settings(BaseSettings):\n    stripe_api_key: SecretStr\n")
        names = [f.name for f in se.find_secret_fields(src)]
        self.assertIn("stripe_api_key", names)   # Settings field
        self.assertNotIn("password", names)      # DTO field excluded (not a *Settings class)

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
               "class Settings:\n"
               "    a_token: SecretStr\n    b_secret: SecretStr\n    c_api_key: SecretStr\n")
        ex = se.propose_exemplar(src)
        self.assertEqual(ex.secret_type, "SecretStr")
        self.assertEqual(ex.agreement, "unanimous")
        self.assertEqual(len(ex.considered), 3)

    def test_single_secret_agreement(self):
        src = "class Settings:\n    only_api_key: str = ''\n"
        ex = se.propose_exemplar(src)
        self.assertEqual(ex.agreement, "single")
        self.assertEqual(len(ex.considered), 1)

    def test_split_when_two_disagree(self):
        # exactly two recent secrets, one each type -> no majority -> split/mixed.
        src = ("from pydantic import SecretStr\n"
               "class Settings:\n    a_token: SecretStr\n    b_password: str = ''\n")
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

    def test_mutator_not_misclassified_as_factory(self):
        # `reset_settings` / `update_settings` end in 'settings' but RETURN None — they
        # are NOT the accessor. A real singleton must win, not the mutator.
        for mutator in ("reset_settings", "update_settings", "save_settings"):
            src = ("class Settings:\n    api_key: str = ''\n"
                   "settings = Settings()\n"
                   f"def {mutator}():\n    global settings\n    settings = Settings()\n")
            idiom = se.detect_access_idiom(src)
            self.assertEqual(idiom.kind, "singleton", mutator)
            self.assertEqual(idiom.import_name, "settings", mutator)
        # but a genuine provider (load_settings) IS a factory
        src2 = ("class Settings:\n    api_key: str = ''\n"
                "def load_settings():\n    return Settings()\n")
        self.assertEqual(se.detect_access_idiom(src2).kind, "factory")


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

    def test_acronyms_not_shattered(self):
        self.assertEqual(se.env_var_for_field("apiKeyURL"), "API_KEY_URL")
        self.assertEqual(se.env_var_for_field("someHTTPKey"), "SOME_HTTP_KEY")


@unittest.skipUnless(shutil.which("grep"), "grep not available")
class GrepTokens(unittest.TestCase):
    """Format-AGNOSTIC wide search — the same search finds the exemplar in terraform,
    k8s yaml, AND python, and skips vendor. No format zoo: the agent classifies hits."""

    @staticmethod
    def _make_repo(d, git):
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
        if git:
            import subprocess
            with open(os.path.join(d, ".gitignore"), "w") as f:
                f.write("node_modules/\n")
            subprocess.run(["git", "init", "-q", d], check=True)
            subprocess.run(["git", "-C", d, "add", "-A"], check=True)

    def test_fallback_grep_rn_non_git_excludes_vendor(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_repo(d, git=False)   # not a git checkout -> grep -rn fallback
            files = {h[0] for h in se.grep_tokens(d, ["ARC_GLOBAL_API_KEY", "arc_global_api_key"])}
            self.assertIn("infra/main.tf", files)
            self.assertIn("config.py", files)
            self.assertNotIn("node_modules/x/main.tf", files)

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_git_grep_primary_skips_gitignored_vendor(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_repo(d, git=True)   # git checkout -> git grep (tracked-only)
            files = {h[0] for h in se.grep_tokens(d, ["ARC_GLOBAL_API_KEY", "arc_global_api_key"])}
            self.assertIn("infra/main.tf", files)
            self.assertIn("deploy.yaml", files)
            self.assertIn("config.py", files)
            self.assertNotIn("node_modules/x/main.tf", files)   # gitignored -> untracked -> unseen

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_resolves_toplevel_finds_infra_ABOVE_the_service(self):
        # The complete path often lives ABOVE the service dir (moo-arc: service at
        # services/moo-arc, infra at repo-root infrastructure/). `git -C <subdir> grep`
        # would scope to the subdir and miss it -> #550 false-negative. The toplevel
        # resolution must find infra above the service even when repo_root IS the service.
        with tempfile.TemporaryDirectory() as d:
            import subprocess
            os.makedirs(os.path.join(d, "infrastructure"))
            os.makedirs(os.path.join(d, "services", "foo"))
            with open(os.path.join(d, "infrastructure", "main.tf"), "w") as f:
                f.write('name = "ARC_GLOBAL_API_KEY"\n')          # centralized infra ABOVE the svc
            with open(os.path.join(d, "services", "foo", "config.py"), "w") as f:
                f.write("arc_global_api_key: SecretStr\n")
            subprocess.run(["git", "init", "-q", d], check=True)
            subprocess.run(["git", "-C", d, "add", "-A"], check=True)
            # repo_root is the SERVICE SUBDIR — must still find infra above it. The
            # agent passes BOTH the env var (deployment) and the field name (config),
            # since grep is case-sensitive and the two forms differ.
            files = {h[0] for h in se.grep_tokens(
                os.path.join(d, "services", "foo"),
                ["ARC_GLOBAL_API_KEY", "arc_global_api_key"])}
            self.assertIn("infrastructure/main.tf", files)        # found despite being above
            self.assertIn("services/foo/config.py", files)

    def test_timeout_RAISES_never_masks_as_empty(self):
        # THE #550 fix: a timeout must never look like 'no hits' (which ships an empty
        # secret path). It propagates, it does not return [] — on BOTH the git-grep path
        # and the grep -rn fallback.
        import subprocess as sp
        orig = sp.run
        # raise TimeoutExpired on every run -> git-grep path raises
        sp.run = lambda *a, **k: (_ for _ in ()).throw(sp.TimeoutExpired(cmd="grep", timeout=1))
        try:
            with self.assertRaises(sp.TimeoutExpired):
                se.grep_tokens("/whatever", ["TOKEN"])
            # fallback path: toplevel-resolve + git-grep OSError (git "absent"), grep times out
            calls = {"n": 0}

            def _git_absent_then_timeout(*a, **k):
                calls["n"] += 1
                if a and a[0] and a[0][0] == "git":
                    raise OSError("git absent")
                raise sp.TimeoutExpired(cmd="grep", timeout=1)
            sp.run = _git_absent_then_timeout
            with self.assertRaises(sp.TimeoutExpired):
                se.grep_tokens("/whatever", ["TOKEN"])
        finally:
            sp.run = orig

    def test_empty_tokens_returns_empty(self):
        self.assertEqual(se.grep_tokens("/tmp", []), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
