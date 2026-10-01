import tempfile
import unittest
from pathlib import Path
from scripts import online_library


class OnlineLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cache = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_search_strips_markup_and_uses_cache(self):
        calls = []
        def requester(params):
            calls.append(params)
            return {"query": {"search": [{"title": "红楼梦", "snippet": "<span class='searchmatch'>红楼</span> 梦", "size": 100}]}}
        first = online_library.search(" 红楼梦 ", self.cache, requester)
        second = online_library.search("红楼梦", self.cache, lambda _: self.fail("cache was not used"))
        self.assertEqual(first["items"][0]["snippet"], "红楼 梦")
        self.assertTrue(second["cached"])
        self.assertEqual(len(calls), 1)

    def test_read_extracts_text_and_keeps_stale_cache_on_outage(self):
        def requester(_):
            return {"parse": {"title": "红楼梦", "text": "<h2>第一回</h2><p>满纸荒唐言，一把辛酸泪。都云作者痴，谁解其中味。</p><script>bad()</script><p>这是用于验证公开正文提取、缓存和稳定阅读的一段完整测试内容。</p>"}}
        first = online_library.read("红楼梦", self.cache, requester)
        text = first["path"].read_text(encoding="utf-8")
        self.assertIn("第一回", text)
        self.assertNotIn("bad()", text)
        first["path"].touch()
        cached = online_library.read("红楼梦", self.cache, lambda _: self.fail("fresh cache was not used"))
        self.assertTrue(cached["cached"])

    def test_invalid_or_empty_requests_are_rejected(self):
        with self.assertRaises(online_library.OnlineLibraryError) as empty:
            online_library.search("", self.cache)
        self.assertEqual(empty.exception.status, 400)
        with self.assertRaises(online_library.OnlineLibraryError) as missing:
            online_library.read("书名", self.cache, lambda _: {"parse": {"text": "太短"}})
        self.assertEqual(missing.exception.status, 422)

    def test_directory_page_combines_subpages_in_reading_order(self):
        calls = []
        def requester(params):
            calls.append(params["action"])
            if params["action"] == "parse":
                return {"parse": {"title": "长篇", "text": "<p>作品目录</p>", "links": [{"title": "长篇/第一回"}, {"title": "长篇/第二回"}]}}
            return {"query": {"pages": [
                {"title": "长篇/第二回", "revisions": [{"slots": {"main": {"content": "{{center|'''第二回'''}}\n第二回正文内容足够长，能够正常提取和阅读。"}}}]},
                {"title": "长篇/第一回", "revisions": [{"slots": {"main": {"content": "{{center|'''第一回'''}}\n第一回正文内容足够长，能够正常提取和阅读。"}}}]},
            ]}}
        page = online_library.read("长篇", self.cache, requester)
        text = page["path"].read_text(encoding="utf-8")
        self.assertLess(text.index("第一回"), text.index("第二回"))
        self.assertEqual(calls, ["parse", "query"])


if __name__ == "__main__":
    unittest.main()
