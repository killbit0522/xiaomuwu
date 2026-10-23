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

    def test_search_combines_wikisource_and_public_domain_gutenberg(self):
        def wiki(_):
            return {"query": {"search": [{"title": "红楼梦", "snippet": "中文古典小说"}]}}
        def gutenberg(params):
            self.assertEqual(params["copyright"], "false")
            return {"results": [{"id": 123, "title": "三字经", "authors": [{"name": "王应麟"}], "summaries": ["公版作品"]}]}
        result = online_library.search("古典", self.cache, wiki, gutenberg)
        self.assertEqual([item["source"] for item in result["items"]], [online_library.SOURCE_NAME, online_library.GUTENBERG_SOURCE])
        self.assertEqual(result["items"][1]["id"], "gutenberg:123")

    def test_gutenberg_read_uses_safe_plain_text_and_cache(self):
        def metadata(_):
            return {"results": [{"id": 123, "title": "三字经", "formats": {"text/plain; charset=utf-8": "https://www.gutenberg.org/files/123/123-0.txt"}}]}
        page = online_library.read("gutenberg:123", self.cache, gutenberg_requester=metadata, content_requester=lambda _: "人之初，性本善。\n" * 20)
        self.assertEqual(page["source"], online_library.GUTENBERG_SOURCE)
        self.assertIn("人之初", page["path"].read_text(encoding="utf-8"))
        cached = online_library.read("gutenberg:123", self.cache, gutenberg_requester=lambda _: self.fail("fresh cache was not used"))
        self.assertTrue(cached["cached"])

    def test_search_keeps_results_when_one_source_is_down(self):
        def wiki_down(_):
            raise online_library.OnlineLibraryError(503, "wiki down")
        def gutenberg(_):
            return {"results": [{"id": 321, "title": "论语", "authors": [], "summaries": []}]}
        result = online_library.search("论语", self.cache, wiki_down, gutenberg)
        self.assertTrue(result["partial"])
        self.assertEqual(result["items"][0]["id"], "gutenberg:321")

    def test_open_library_only_exposes_public_scans(self):
        def wiki(_):
            return {"query": {"search": []}}
        def openlibrary(params):
            self.assertIn("public_scan_b", params["fields"])
            return {"docs": [
                {"key": "/works/OL1W", "title": "公开古籍", "author_name": ["某作者"], "first_publish_year": 1901, "public_scan_b": True, "ia": ["public-book-1"]},
                {"key": "/works/OL2W", "title": "受限作品", "public_scan_b": False, "ia": ["restricted-book"]},
            ]}
        result = online_library.search("古籍", self.cache, wiki, openlibrary_requester=openlibrary)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], "archive:public-book-1")
        self.assertIn("某作者", result["items"][0]["snippet"])

    def test_archive_read_prefers_ocr_text_and_uses_cache(self):
        def metadata(identifier):
            self.assertEqual(identifier, "public-book-1")
            return {"metadata": {"title": "公开古籍"}, "files": [
                {"name": "public-book-1_meta.txt", "size": "20"},
                {"name": "public-book-1_djvu.txt", "size": "1200"},
            ]}
        requested = []
        page = online_library.read(
            "archive:public-book-1",
            self.cache,
            archive_metadata_requester=metadata,
            archive_content_requester=lambda url: requested.append(url) or ("正文内容。" * 30),
        )
        self.assertEqual(page["source"], online_library.ARCHIVE_SOURCE)
        self.assertTrue(requested[0].endswith("public-book-1_djvu.txt"))
        cached = online_library.read("archive:public-book-1", self.cache, archive_metadata_requester=lambda _: self.fail("fresh cache was not used"))
        self.assertTrue(cached["cached"])

    def test_archive_ids_and_download_hosts_are_validated(self):
        with self.assertRaises(online_library.OnlineLibraryError) as invalid:
            online_library.read("archive:../../secret", self.cache)
        self.assertEqual(invalid.exception.status, 400)
        self.assertFalse(online_library._allowed_archive_url("https://example.com/book.txt"))

    def test_source_registry_rejects_duplicate_keys_and_dispatches_reader(self):
        from scripts.book_sources import BookSourceAdapter, BookSourceRegistry
        adapter = BookSourceAdapter("sample", "样例", lambda _: [], lambda native_id: native_id)
        registry = BookSourceRegistry([adapter])
        self.assertIs(registry.reader_for("sample:book-1"), adapter)
        self.assertEqual(adapter.native_id("sample:book-1"), "book-1")
        with self.assertRaises(ValueError):
            registry.register(BookSourceAdapter("sample", "重复", lambda _: []))


if __name__ == "__main__":
    unittest.main()
