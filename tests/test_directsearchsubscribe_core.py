"""直搜订阅纯逻辑测试。"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "plugins.v2" / "directsearchsubscribe" / "core.py"
SPEC = importlib.util.spec_from_file_location("directsearchsubscribe_core", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(CORE)


class EpisodeParsingTest(unittest.TestCase):
    def test_parse_and_format_ranges(self):
        self.assertEqual(CORE.parse_episodes("1-3, 5，7-6"), {1, 2, 3, 5, 6, 7})
        self.assertEqual(CORE.episodes_text({7, 3, 2, 1, 5, 6}), "1-3,5-7")

    def test_invalid_expression_is_rejected(self):
        self.assertFalse(CORE.episode_expression_is_valid("1-3,abc"))
        self.assertFalse(CORE.episode_expression_is_valid("0"))
        task = CORE.normalize_task({"name": "测试剧", "type": "电视剧", "episodes": "1-3,abc"})
        self.assertIn("格式无效", CORE.validate_task(task, {"episodes": "1-3,abc"}))

    def test_explicit_targets_override_total_and_owned_are_complete(self):
        task = CORE.normalize_task({
            "name": "测试剧", "episodes": "2-4", "total_episode": 20, "owned_episodes": "2",
        })
        self.assertEqual(CORE.target_episodes(task), {2, 3, 4})
        self.assertEqual(CORE.missing_episodes(task), {3, 4})

    def test_start_must_not_exceed_total(self):
        payload = {"name": "测试剧", "start_episode": 13, "total_episode": 12}
        task = CORE.normalize_task(payload)
        self.assertIn("起始集", CORE.validate_task(task, payload))


class TaskSafetyTest(unittest.TestCase):
    def test_new_task_uses_safe_defaults(self):
        task = CORE.normalize_task({"name": "测试剧"})
        self.assertEqual(task["type"], "电视剧")
        self.assertFalse(task["auto_download"])
        self.assertFalse(task["accept_unknown_episode"])
        self.assertTrue(task["strict_title_match"])

    def test_alias_title_and_word_filters(self):
        task = CORE.normalize_task({
            "name": "Re:Zero", "aliases": "从零开始的异世界生活",
            "include": "1080p,HEVC", "exclude": "预告,试看",
        })
        self.assertTrue(CORE.title_matches(task, "[Group] Re Zero S04E01 1080p HEVC"))
        self.assertTrue(CORE.title_matches(task, "从零开始的异世界生活 第四季"))
        self.assertTrue(CORE.words_match(task, "Re Zero S04E01 1080P HEVC"))
        self.assertFalse(CORE.words_match(task, "Re Zero S04E01 1080P HEVC 预告"))

    def test_fingerprint_ignores_rotating_download_url_when_page_is_stable(self):
        first = CORE.resource_fingerprint(1, "https://tracker/download?t=old", "https://tracker/details/8", "Title", 10)
        second = CORE.resource_fingerprint(1, "https://tracker/download?t=new", "https://tracker/details/8", "Title", 10)
        changed = CORE.resource_fingerprint(1, "https://tracker/download?t=new", "https://tracker/details/9", "Title", 10)
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_episode_coverage_has_priority_over_seeders(self):
        broad = CORE.candidate_score({1, 2, 3}, {1, 2, 3}, seeders=1, free_factor=1, size=1)
        popular = CORE.candidate_score({1}, {1, 2, 3}, seeders=100000, free_factor=0, size=10)
        self.assertGreater(broad, popular)


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_plugin_does_not_use_native_subscription_classes(self):
        source = (ROOT / "plugins.v2" / "directsearchsubscribe" / "__init__.py").read_text(encoding="utf-8")
        for forbidden in (
            "from app.chain.subscribe", "app.db.subscribe_oper", "app.db.models.subscribe",
            "SubscribeChain", "SubscribeOper", "Subscribe(",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("search_by_title", source)
        self.assertIn("media.genre_ids = [-1]", source)

    def test_compatibility_directory_is_an_exact_mirror(self):
        for filename in ("__init__.py", "core.py", "README.md"):
            current = ROOT / "plugins.v2" / "directsearchsubscribe" / filename
            compatible = ROOT / "plugins" / "directsearchsubscribe" / filename
            self.assertEqual(current.read_bytes(), compatible.read_bytes())

    def test_manifest_versions_match_plugin(self):
        for filename in ("package.v2.json", "package.json"):
            package = json.loads((ROOT / filename).read_text(encoding="utf-8"))
            self.assertEqual(package["directsearchsubscribe"]["version"], "2.0.1")

    def test_downloads_keep_moviepilot_system_tag(self):
        source = (ROOT / "plugins.v2" / "directsearchsubscribe" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from app.core.config import global_vars, settings", source)
        self.assertIn("label=_download_labels()", source)
        self.assertIn("settings.TORRENT_TAG", source)


if __name__ == "__main__":
    unittest.main()
