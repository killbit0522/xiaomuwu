import http.cookiejar
import hashlib
import gzip
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer

from scripts.server import LibraryHandler


class BookAccessIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(cls.temp.name)
        cls.site = root / "site"
        cls.books = root / "textbook"
        cls.data = root / "data"
        cls.site.mkdir()
        cls.books.mkdir()
        cls.data.mkdir()
        (cls.site / "pages").mkdir()
        (cls.site / "assets").mkdir()
        (cls.site / "pages" / "home.html").write_text("home", encoding="utf-8")
        (cls.site / "pages" / "catalog.html").write_text("catalog", encoding="utf-8")
        (cls.site / "pages" / "reader.html").write_text("reader", encoding="utf-8")
        catalog = [
            {"title": "白鸽", "searchablePath": "分类/白鸽.txt", "modifiedAt": "2026-10-01 09:00:00"},
            {"title": "新书甲", "searchablePath": "新上传1/新书甲.txt", "modifiedAt": "2026-10-06 10:00:00"},
            {"title": "新书乙", "searchablePath": "新上传1/新书乙.txt", "modifiedAt": "2026-10-06 11:00:00"},
            {"title": "新书丙", "searchablePath": "新上传1/新书丙.txt", "modifiedAt": "2026-10-06 09:00:00"},
        ]
        catalog.extend({"title": f"目录测试书{index}", "searchablePath": f"分类/{index}/测试.txt"} for index in range(96))
        cls.catalog_bytes = json.dumps(catalog, ensure_ascii=False).encode("utf-8")
        (cls.site / "assets" / "catalog.json").write_bytes(cls.catalog_bytes)

        cls.samples = {
            "utf8.txt": "白鸽飞过屋檐。",
            "gb18030.txt": "这是一本简体中文测试书。",
            "utf16.txt": "这是 UTF-16 中文测试。",
        }
        (cls.books / "utf8.txt").write_bytes(cls.samples["utf8.txt"].encode("utf-8"))
        (cls.books / "gb18030.txt").write_bytes(cls.samples["gb18030.txt"].encode("gb18030"))
        (cls.books / "utf16.txt").write_bytes(cls.samples["utf16.txt"].encode("utf-16"))
        cls.binary = b"PK\x03\x04book-binary-test"
        (cls.books / "sample.pdf").write_bytes(cls.binary)

        cls.db = cls.data / "library.db"
        with sqlite3.connect(cls.db) as db:
            db.execute("CREATE TABLE access_cards(id INTEGER PRIMARY KEY, code TEXT UNIQUE, card_type TEXT, download_limit INTEGER, remaining INTEGER, used INTEGER, expires_at TEXT, active INTEGER, created_at TEXT)")
            db.execute("CREATE TABLE download_sessions(token TEXT PRIMARY KEY, card_id INTEGER, created_at TEXT, last_used_at TEXT)")
            db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, type TEXT, visitor TEXT, detail TEXT, created_at TEXT)")
            db.execute("CREATE TABLE reviews(id INTEGER PRIMARY KEY, book TEXT, title TEXT, body TEXT, visitor TEXT, created_at TEXT, admin_reply TEXT NOT NULL DEFAULT '', replied_at TEXT NOT NULL DEFAULT '')")
            db.execute("CREATE TABLE book_requests(id INTEGER PRIMARY KEY, title TEXT, author TEXT, edition TEXT, section TEXT, purpose TEXT, contact TEXT, note TEXT, visitor TEXT, created_at TEXT)")
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO settings(key,value) VALUES('admin_password_hash',?)", (hashlib.sha256(b"test-admin-password").hexdigest(),))
            now = datetime.now(timezone.utc)
            db.execute("INSERT INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)", ("TEST-READER", "reader", 0, 0, 0, (now + timedelta(days=2)).isoformat(), 1, now.isoformat()))
            db.execute("INSERT INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)", ("TEST-EXPIRED", "reader", 0, 0, 0, (now - timedelta(seconds=1)).isoformat(), 1, now.isoformat()))
            db.execute("INSERT INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)", ("TEST-DOWNLOAD", "download", 2, 2, 0, "", 1, now.isoformat()))
            db.execute("INSERT INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)", ("TEST-DISABLE", "reader", 0, 0, 0, (now + timedelta(days=2)).isoformat(), 1, now.isoformat()))

        LibraryHandler.site_root = cls.site.resolve()
        LibraryHandler.textbook_root = cls.books.resolve()
        LibraryHandler.db_path = cls.db
        LibraryHandler.admin_token = "test-admin-token"
        LibraryHandler.read_cache_root = cls.data / "read-cache"
        LibraryHandler.read_cache_root.mkdir()
        LibraryHandler.online_cache_root = cls.data / "online-cache"
        LibraryHandler.online_cache_root.mkdir()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), LibraryHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def client(self):
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def setUp(self):
        (self.site / "assets" / "catalog.json").write_bytes(self.catalog_bytes)
        shutil.rmtree(self.books / "管理员上传", ignore_errors=True)
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM reviews")
            db.execute("DELETE FROM settings WHERE key='member_announcement'")

    def unlock(self, client, code):
        payload = json.dumps({"code": code}).encode()
        request = urllib.request.Request(self.base + "/api/unlock", data=payload, headers={"Content-Type": "application/json"})
        with client.open(request) as response:
            return json.loads(response.read())

    def test_reader_card_opens_all_common_chinese_encodings(self):
        client = self.client()
        result = self.unlock(client, "TEST-READER")
        self.assertEqual(result["cardType"], "reader")
        for filename, expected in self.samples.items():
            with self.subTest(filename=filename):
                url = self.base + "/read/" + urllib.parse.quote(filename)
                with client.open(url) as response:
                    self.assertEqual(response.headers.get_content_charset(), "utf-8")
                    self.assertEqual(response.read().decode("utf-8"), expected)

    def test_reader_text_uses_etag_cache_and_pdf_supports_byte_ranges(self):
        client = self.client()
        self.unlock(client, "TEST-READER")
        text_url = self.base + "/read/utf8.txt"
        with client.open(text_url) as response:
            etag = response.headers.get("ETag")
            self.assertIn("max-age=3600", response.headers.get("Cache-Control", ""))
            self.assertTrue(etag)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            client.open(urllib.request.Request(text_url, headers={"If-None-Match": etag}))
        self.assertEqual(caught.exception.code, 304)
        caught.exception.close()

        pdf_request = urllib.request.Request(self.base + "/read/sample.pdf", headers={"Range": "bytes=2-7"})
        with client.open(pdf_request) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers.get("Content-Range"), f"bytes 2-7/{len(self.binary)}")
            self.assertEqual(response.read(), self.binary[2:8])

    def test_catalog_is_public_and_compressed_but_reader_stays_protected(self):
        request = urllib.request.Request(self.base + "/assets/catalog.json", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
            self.assertEqual(gzip.decompress(response.read()), self.catalog_bytes)
        with urllib.request.urlopen(self.base + "/pages/catalog.html") as response:
            self.assertEqual(response.read(), b"catalog")
        with urllib.request.urlopen(self.base + "/pages/reader.html") as response:
            self.assertIn("/pages/home.html?card=required", response.geturl())
        with urllib.request.urlopen(self.base + "/pages/recent.html") as response:
            self.assertIn("/pages/home.html?card=required", response.geturl())

    def test_public_update_notice_only_returns_the_two_latest_books(self):
        with urllib.request.urlopen(self.base + "/api/updates") as response:
            payload = json.loads(response.read())
        self.assertEqual([item["title"] for item in payload["items"]], ["新书乙", "新书甲"])
        self.assertEqual(len(payload["version"]), 16)

    def test_recent_books_endpoint_returns_only_fifty_newest_items_for_reader(self):
        reader = self.client(); self.unlock(reader, "TEST-READER")
        with reader.open(self.base + "/api/recent") as response:
            items = json.loads(response.read())["items"]
        self.assertLessEqual(len(items), 50)
        self.assertEqual(items[0]["title"], "新书乙")

    def test_valid_reader_card_can_be_entered_again_but_expired_card_cannot(self):
        self.assertTrue(self.unlock(self.client(), "TEST-READER")["ok"])
        self.assertTrue(self.unlock(self.client(), "TEST-READER")["ok"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.unlock(self.client(), "TEST-EXPIRED")
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

    def test_download_is_utf8_and_each_success_consumes_exactly_one_use(self):
        client = self.client()
        self.unlock(client, "TEST-DOWNLOAD")
        with client.open(self.base + "/books/gb18030.txt") as first:
            body = first.read()
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(body[3:].decode("utf-8"), self.samples["gb18030.txt"])
        with client.open(self.base + "/books/sample.pdf") as second:
            self.assertEqual(second.read(), self.binary)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            client.open(self.base + "/books/utf8.txt")
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        with sqlite3.connect(self.db) as db:
            remaining, used = db.execute("SELECT remaining,used FROM access_cards WHERE code='TEST-DOWNLOAD'").fetchone()
        self.assertEqual((remaining, used), (0, 2))

    def test_admin_can_enter_and_read_without_reader_card(self):
        headers = {"Cookie": "cabin_admin=test-admin-token"}
        with urllib.request.urlopen(urllib.request.Request(self.base + "/api/access", headers=headers)) as response:
            access = json.loads(response.read())
        self.assertTrue(access["ok"])
        self.assertEqual(access["cardType"], "reader")
        with urllib.request.urlopen(urllib.request.Request(self.base + "/read/utf8.txt", headers=headers)) as response:
            self.assertEqual(response.read().decode("utf-8"), self.samples["utf8.txt"])

    def test_online_search_is_public_but_online_reading_requires_reader_card(self):
        result = {"query": "红楼梦", "items": [{"id": "红楼梦", "title": "红楼梦"}], "source": "中文维基文库"}
        with patch("scripts.server.online_library.search", return_value=result):
            with urllib.request.urlopen(self.base + "/api/online/search?q=" + urllib.parse.quote("红楼梦")) as response:
                self.assertEqual(json.loads(response.read())["items"][0]["title"], "红楼梦")
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(self.base + "/api/online/read?id=" + urllib.parse.quote("红楼梦"))
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        cached = self.data / "online-cache" / "test.txt"
        cached.write_text("稳定的联网阅读正文。", encoding="utf-8")
        client = self.client(); self.unlock(client, "TEST-READER")
        page = {"title": "红楼梦", "path": cached, "source": "中文维基文库", "sourceUrl": "https://zh.wikisource.org/wiki/红楼梦"}
        with patch("scripts.server.online_library.read", return_value=page):
            with client.open(self.base + "/api/online/read?id=" + urllib.parse.quote("红楼梦")) as response:
                self.assertEqual(response.read().decode("utf-8"), "稳定的联网阅读正文。")

    def test_admin_can_disable_card_and_invalidate_its_session(self):
        client = self.client()
        self.unlock(client, "TEST-DISABLE")
        payload = json.dumps({"codes": ["TEST-DISABLE"]}).encode()
        request = urllib.request.Request(
            self.base + "/api/admin/cards/disable",
            data=payload,
            headers={"Content-Type": "application/json", "Cookie": "cabin_admin=test-admin-token"},
        )
        with urllib.request.urlopen(request) as response:
            self.assertEqual(json.loads(response.read())["disabled"], 1)
        with client.open(self.base + "/api/access") as response:
            self.assertFalse(json.loads(response.read())["ok"])
        with sqlite3.connect(self.db) as db:
            active = db.execute("SELECT active FROM access_cards WHERE code='TEST-DISABLE'").fetchone()[0]
            sessions = db.execute("SELECT COUNT(*) FROM download_sessions JOIN access_cards ON access_cards.id=download_sessions.card_id WHERE access_cards.code='TEST-DISABLE'").fetchone()[0]
        self.assertEqual(active, 0)
        self.assertEqual(sessions, 0)

    def test_zip_reader_skips_broken_member_and_opens_next_book(self):
        archive = self.books / "fallback.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("a.txt", b"\x81\x30")
            bundle.writestr("second-book.txt", "压缩包里的正文可以阅读。".encode("utf-8"))
        client = self.client()
        self.unlock(client, "TEST-READER")
        with client.open(self.base + "/read/fallback.zip") as response:
            self.assertEqual(response.read().decode("utf-8"), "压缩包里的正文可以阅读。")

    def test_admin_login_cookie_is_remembered_for_24_hours(self):
        payload = json.dumps({"code": "test-admin-password"}).encode()
        request = urllib.request.Request(self.base + "/api/admin/login", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("Max-Age=86400", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_admin_data_reports_storage_and_upload_skips_existing_titles(self):
        headers = {"Cookie": "cabin_admin=test-admin-token"}
        with urllib.request.urlopen(urllib.request.Request(self.base + "/api/admin/data", headers=headers)) as response:
            storage = json.loads(response.read())["storage"]
        self.assertGreater(storage["total"], 0)
        self.assertGreaterEqual(storage["free"], 0)
        for filename in ("重复书.txt", "重复书（1）.txt"):
            request = urllib.request.Request(self.base + "/api/admin/upload", data=b"book", headers={**headers, "X-Filename": urllib.parse.quote(filename), "X-Relative-Path": urllib.parse.quote(filename)})
            with urllib.request.urlopen(request) as response:
                result = json.loads(response.read())
            if filename.endswith("（1）.txt"):
                self.assertTrue(result["skipped"])
            else:
                self.assertFalse(result.get("skipped", False))

    def test_admin_uploads_are_grouped_twenty_books_per_new_folder(self):
        for index in range(21):
            body = f"第{index + 1}本".encode("utf-8")
            request = urllib.request.Request(
                self.base + "/api/admin/upload",
                data=body,
                headers={
                    "Cookie": "cabin_admin=test-admin-token",
                    "X-Filename": f"book-{index:02d}.txt",
                    "X-Relative-Path": f"book-{index:02d}.txt",
                },
            )
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.loads(response.read())["ok"])
        first = self.books / "管理员上传" / "新上传1"
        second = self.books / "管理员上传" / "新上传2"
        self.assertEqual(sum(1 for path in first.rglob("*") if path.is_file()), 20)
        self.assertEqual(sum(1 for path in second.rglob("*") if path.is_file()), 1)
        with urllib.request.urlopen(self.base + "/assets/catalog.json") as response:
            catalog = json.loads(response.read())
            titles = {item["title"] for item in catalog}
        self.assertIn("book-20", titles)
        self.assertIn("新上传2", catalog[0]["category"])

    def test_admin_can_reply_to_type_requests_and_readers_can_see_reply(self):
        request = urllib.request.Request(
            self.base + "/api/reviews",
            data=json.dumps({"book": "想看类型", "title": "想看职场文", "body": "希望有轻松一点的职场类型小说。", "visitor": "reader-a"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request):
            pass
        with sqlite3.connect(self.db) as db:
            review_id = db.execute("SELECT id FROM reviews").fetchone()[0]
        reply = urllib.request.Request(
            self.base + "/api/admin/reviews/reply",
            data=json.dumps({"id": review_id, "reply": "收到，我会安排寻找。"}).encode(),
            headers={"Content-Type": "application/json", "Cookie": "cabin_admin=test-admin-token"},
        )
        with urllib.request.urlopen(reply) as response:
            self.assertTrue(json.loads(response.read())["ok"])
        reader = self.client(); self.unlock(reader, "TEST-READER")
        with reader.open(self.base + "/api/type-requests") as response:
            items = json.loads(response.read())["items"]
        self.assertEqual(items[0]["admin_reply"], "收到，我会安排寻找。")

    def test_member_announcement_is_visible_only_to_active_reader_cards(self):
        publish = urllib.request.Request(
            self.base + "/api/admin/announcement",
            data=json.dumps({"title": "本周推荐", "body": "欢迎阅读新上传。", "imageUrl": ""}).encode(),
            headers={"Content-Type": "application/json", "Cookie": "cabin_admin=test-admin-token"},
        )
        with urllib.request.urlopen(publish) as response:
            self.assertTrue(json.loads(response.read())["ok"])
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(self.base + "/api/announcement")
        self.assertEqual(denied.exception.code, 403); denied.exception.close()
        reader = self.client(); self.unlock(reader, "TEST-READER")
        with reader.open(self.base + "/api/announcement") as response:
            announcement = json.loads(response.read())["announcement"]
        self.assertEqual(announcement["title"], "本周推荐")


if __name__ == "__main__":
    unittest.main()
