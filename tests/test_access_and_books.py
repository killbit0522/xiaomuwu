import http.cookiejar
import hashlib
import gzip
import json
import os
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
            db.execute("CREATE TABLE community_posts(id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
            db.execute("CREATE TABLE community_comments(id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, body TEXT NOT NULL, visitor TEXT NOT NULL DEFAULT '', display_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE reader_shelves(device_key TEXT PRIMARY KEY, books_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO settings(key,value) VALUES('admin_password_hash',?)", (hashlib.sha256(b"test-admin-password").hexdigest(),))
            now = datetime.now(timezone.utc)
            db.execute("INSERT INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)", ("TEST-READER", "reader", 0, 0, 0, (now + timedelta(days=2)).isoformat(), 1, now.isoformat()))
            db.execute("INSERT INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)", ("TEST-EXPIRING", "reader", 0, 0, 0, (now + timedelta(hours=12)).isoformat(), 1, now.isoformat()))
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
        LibraryHandler.replaced_root = cls.data / "replaced-books"
        LibraryHandler.replaced_root.mkdir()
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

    def no_redirect_client(self, client):
        class StopRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        cookiejar = next(handler.cookiejar for handler in client.handlers if isinstance(handler, urllib.request.HTTPCookieProcessor))
        return urllib.request.build_opener(StopRedirect(), urllib.request.HTTPCookieProcessor(cookiejar))

    def setUp(self):
        LibraryHandler.auth_attempts.clear()
        LibraryHandler.auth_last_cleanup = 0.0
        (self.site / "assets" / "catalog.json").write_bytes(self.catalog_bytes)
        shutil.rmtree(self.books / "管理员上传", ignore_errors=True)
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM reviews")
            db.execute("DELETE FROM book_requests")
            db.execute("DELETE FROM community_comments")
            db.execute("DELETE FROM community_posts")
            db.execute("DELETE FROM reader_shelves")
            db.execute("DELETE FROM settings WHERE key='member_announcement'")
            db.execute("DELETE FROM settings WHERE key='member_messages'")

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

    def test_community_comments_store_and_display_reader_chosen_id(self):
        client = self.client()
        self.unlock(client, "TEST-READER")
        with sqlite3.connect(self.db) as db:
            cursor = db.execute("INSERT INTO community_posts(title,body,created_at) VALUES(?,?,?)", ("测试动态", "分享", datetime.now(timezone.utc).isoformat()))
            post_id = cursor.lastrowid
        payload = json.dumps({"postId": post_id, "body": "很喜欢这段", "displayId": "木木读者7"}).encode()
        request = urllib.request.Request(self.base + "/api/community/comments", data=payload, headers={"Content-Type": "application/json"})
        with client.open(request) as response:
            self.assertEqual(json.loads(response.read())["ok"], True)
        with client.open(self.base + "/api/community") as response:
            data = json.loads(response.read())
        comment = data["items"][0]["comments"][0]
        self.assertEqual(comment["display_id"], "木木读者7")
        self.assertNotIn("visitor", comment)

    def test_community_rejects_invalid_reader_display_id(self):
        client = self.client()
        self.unlock(client, "TEST-READER")
        with sqlite3.connect(self.db) as db:
            cursor = db.execute("INSERT INTO community_posts(title,body,created_at) VALUES(?,?,?)", ("测试动态", "分享", datetime.now(timezone.utc).isoformat()))
            post_id = cursor.lastrowid
        payload = json.dumps({"postId": post_id, "body": "测试", "displayId": "<b>恶意</b>"}).encode()
        request = urllib.request.Request(self.base + "/api/community/comments", data=payload, headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            client.open(request)
        self.assertEqual(error.exception.code, 400)

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
            etag = response.headers.get("ETag")
            self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
            self.assertIn("no-cache", response.headers.get("Cache-Control", ""))
            self.assertIn("Content-Security-Policy", response.headers)
            self.assertNotIn("Python", response.headers.get("Server", ""))
            self.assertEqual(gzip.decompress(response.read()), self.catalog_bytes)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.request.Request(self.base + "/assets/catalog.json", headers={"If-None-Match": etag}))
        self.assertEqual(caught.exception.code, 304)
        caught.exception.close()
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

    def test_shelf_sync_follows_device_across_different_reader_cards(self):
        device = "a1" * 16
        first = self.client(); self.unlock(first, "TEST-READER")
        headers = {"Content-Type": "application/json", "X-Shelf-Device": device}
        book = {"title": "白鸽", "searchablePath": "分类/白鸽.txt", "format": "txt", "category": "分类"}
        request = urllib.request.Request(self.base + "/api/shelf", data=json.dumps({"items": [book]}).encode(), headers=headers)
        with first.open(request) as response:
            self.assertEqual(json.loads(response.read())["count"], 1)

        second = self.client(); self.unlock(second, "TEST-EXPIRING")
        read_request = urllib.request.Request(self.base + "/api/shelf", headers={"X-Shelf-Device": device})
        with second.open(read_request) as response:
            self.assertEqual(json.loads(response.read())["items"][0]["searchablePath"], "分类/白鸽.txt")
        remove_request = urllib.request.Request(self.base + "/api/shelf", data=json.dumps({"items": [], "remove": [book["searchablePath"]]}).encode(), headers=headers)
        with second.open(remove_request) as response:
            self.assertEqual(json.loads(response.read())["count"], 0)

        download = self.client()
        with self.assertRaises(urllib.error.HTTPError) as denied:
            download.open(urllib.request.Request(self.base + "/api/shelf", headers={"X-Shelf-Device": device}))
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()

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
            self.assertEqual(first.headers.get_content_type(), "application/octet-stream")
            self.assertIn("attachment", first.headers.get("Content-Disposition", ""))
            self.assertIn(".txt", first.headers.get("Content-Disposition", ""))
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

    def test_reader_and_download_cards_cannot_be_mistaken_for_each_other(self):
        reader = self.client()
        self.unlock(reader, "TEST-READER")
        with self.assertRaises(urllib.error.HTTPError) as denied_download:
            reader.open(self.base + "/books/utf8.txt")
        self.assertEqual(denied_download.exception.code, 403)
        denied_download.exception.close()

        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT OR REPLACE INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("TEST-CROSS-DOWNLOAD", "download", 1, 1, 0, "", 1, datetime.now(timezone.utc).isoformat()),
            )
        downloader = self.client()
        self.unlock(downloader, "TEST-CROSS-DOWNLOAD")
        with self.assertRaises(urllib.error.HTTPError) as denied_reader:
            downloader.open(self.base + "/read/utf8.txt")
        self.assertEqual(denied_reader.exception.code, 403)
        denied_reader.exception.close()

    def test_admin_can_enter_and_read_without_reader_card(self):
        headers = {"Cookie": "cabin_admin=test-admin-token"}
        with urllib.request.urlopen(urllib.request.Request(self.base + "/api/access", headers=headers)) as response:
            access = json.loads(response.read())
        self.assertTrue(access["ok"])
        self.assertEqual(access["cardType"], "reader")
        with urllib.request.urlopen(urllib.request.Request(self.base + "/read/utf8.txt", headers=headers)) as response:
            self.assertEqual(response.read().decode("utf-8"), self.samples["utf8.txt"])

    def test_access_reports_server_calculated_expiry_for_current_card(self):
        regular = self.client()
        self.unlock(regular, "TEST-READER")
        with regular.open(self.base + "/api/access") as response:
            regular_access = json.loads(response.read())
        self.assertGreater(regular_access["expiresInSeconds"], 86400)

        expiring = self.client()
        self.unlock(expiring, "TEST-EXPIRING")
        with expiring.open(self.base + "/api/access") as response:
            expiring_access = json.loads(response.read())
        self.assertGreater(expiring_access["expiresInSeconds"], 0)
        self.assertLessEqual(expiring_access["expiresInSeconds"], 86400)

    def test_external_ao3_and_comics_redirects_require_an_active_card(self):
        destinations = {
            "/go/ao3": "https://aoya.moe/works/search?page=1&view_adult=true&work_search%5Bquery%5D=%E7%A6%85%E9%99%A2%E7%9B%B4%E5%93%89",
            "/go/comics": "https://appgu.cc/rank/",
        }
        guest = self.no_redirect_client(self.client())
        for route in destinations:
            with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError) as denied:
                guest.open(self.base + route)
            self.assertEqual(denied.exception.code, 302)
            self.assertEqual(denied.exception.headers.get("Location"), "/pages/home.html?card=required")
            denied.exception.close()

        member = self.client()
        self.unlock(member, "TEST-READER")
        gated = self.no_redirect_client(member)
        for route, destination in destinations.items():
            with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError) as allowed:
                gated.open(self.base + route)
            self.assertEqual(allowed.exception.code, 302)
            self.assertEqual(allowed.exception.headers.get("Location"), destination)
            self.assertEqual(allowed.exception.headers.get("Cache-Control"), "no-store")
            allowed.exception.close()

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

    def test_admin_login_cookie_is_remembered_for_one_week(self):
        payload = json.dumps({"code": "test-admin-password"}).encode()
        request = urllib.request.Request(self.base + "/api/admin/login", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("Max-Age=604800", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_admin_data_reports_storage_and_upload_replaces_existing_titles(self):
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
                self.assertEqual(result["replaced"], 1)
            else:
                self.assertEqual(result["replaced"], 0)
        self.assertEqual((self.books / "管理员上传" / "新上传1" / "重复书（1）.txt").read_bytes(), b"book")
        self.assertTrue(any(path.name == "重复书.txt" for path in LibraryHandler.replaced_root.rglob("*")))

    def test_upload_does_not_replace_a_different_book_with_five_shared_characters(self):
        headers = {"Cookie": "cabin_admin=test-admin-token"}
        names = ("霸总爱上小秘书.txt", "霸总爱上小秘书的姐姐.txt")
        for name in names:
            request = urllib.request.Request(
                self.base + "/api/admin/upload",
                data=name.encode("utf-8"),
                headers={**headers, "X-Filename": urllib.parse.quote(name), "X-Relative-Path": urllib.parse.quote(name)},
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.loads(response.read())["replaced"], 0)
        with urllib.request.urlopen(self.base + "/assets/catalog.json") as response:
            titles = {item["title"] for item in json.loads(response.read())}
        self.assertTrue(set(name[:-4] for name in names).issubset(titles))

    def test_folder_upload_preserves_root_name_and_retry_is_idempotent(self):
        body = '文件夹上传专用正文'.encode('utf-8')
        headers = {'Cookie': 'cabin_admin=test-admin-token', 'X-Filename': urllib.parse.quote('文件夹书.txt'),
                   'X-Relative-Path': urllib.parse.quote('我的分类/子目录/文件夹书.txt'),
                   'X-Upload-Mode': 'folder', 'X-Content-SHA256': hashlib.sha256(body).hexdigest()}
        for attempt in range(2):
            request = urllib.request.Request(self.base + '/api/admin/upload', data=body, headers=headers)
            with urllib.request.urlopen(request) as response:
                result = json.loads(response.read())
            if attempt:
                self.assertTrue(result['unchanged'])
        self.assertEqual((self.books / '管理员上传/我的分类/子目录/文件夹书.txt').read_bytes(), body)
        headers['X-Content-SHA256'] = '0' * 64
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(self.base + '/api/admin/upload', data=body, headers=headers))
        self.assertEqual(error.exception.code, 400)

    def test_replacement_restore_is_admin_only_and_preserves_current_version(self):
        headers={'Cookie': 'cabin_admin=test-admin-token', 'Content-Type': 'application/json'}
        original=self.books / 'restore-test.txt'
        original.write_text('current', encoding='utf-8')
        backup=LibraryHandler.replaced_root / '20261023T000000Z' / 'restore-test.txt'
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text('old', encoding='utf-8')
        url=self.base+'/api/admin/replacements/restore'
        body=json.dumps({'id':'20261023T000000Z/restore-test.txt'}).encode()
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url,data=body))
        self.assertEqual(error.exception.code,403)
        with urllib.request.urlopen(urllib.request.Request(url,data=body,headers=headers)) as response:
            self.assertTrue(json.loads(response.read())['ok'])
        self.assertEqual(original.read_text(), 'old')
        self.assertTrue(any(p.read_text()=='current' for p in LibraryHandler.replaced_root.rglob('restore-test.txt')))
        bad=json.dumps({'id':'../restore-test.txt'}).encode()
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url,data=bad,headers=headers))
        self.assertEqual(error.exception.code,400)
        preview=json.dumps({'names':['restore-test.txt','not-existing.txt']}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.base+'/api/admin/upload-preview',data=preview,headers=headers)) as response:
            items=json.loads(response.read())['items']
        self.assertTrue(items[0]['matches'])
        self.assertFalse(items[1]['matches'])

    def test_identical_content_under_different_name_is_replaced(self):
        body = '完全相同正文用于检查改名副本'.encode('utf-8')
        for index, name in enumerate(['内容副本甲.txt', '内容副本乙.txt']):
            request = urllib.request.Request(self.base + '/api/admin/upload', data=body,
                headers={'Cookie': 'cabin_admin=test-admin-token', 'X-Filename': urllib.parse.quote(name)})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.loads(response.read())['replaced'], index)

    def test_catalog_keeps_the_newest_copy_when_legacy_duplicates_remain(self):
        old_copy = self.books / "管理员上传" / "旧目录" / "同一本书.txt"
        new_copy = self.books / "管理员上传" / "新目录" / "同一本书（2）.txt"
        old_copy.parent.mkdir(parents=True)
        new_copy.parent.mkdir(parents=True)
        old_copy.write_text("旧正文", encoding="utf-8")
        new_copy.write_text("新正文", encoding="utf-8")
        os.utime(old_copy, (100, 100))
        os.utime(new_copy, (200, 200))
        request = urllib.request.Request(self.base + "/api/admin/rebuild-catalog", data=b"", headers={"Cookie": "cabin_admin=test-admin-token"})
        with urllib.request.urlopen(request) as response:
            self.assertTrue(json.loads(response.read())["ok"])
        with urllib.request.urlopen(self.base + "/assets/catalog.json") as response:
            matches = [item for item in json.loads(response.read()) if item["title"] == "同一本书"]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["searchablePath"].endswith("同一本书（2）.txt"))

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

    def test_admin_batch_upload_can_defer_catalog_until_one_final_rebuild(self):
        headers = {
            "Cookie": "cabin_admin=test-admin-token",
            "X-Filename": urllib.parse.quote("批量新书.txt"),
            "X-Relative-Path": urllib.parse.quote("批量新书.txt"),
            "X-Defer-Catalog": "1",
        }
        request = urllib.request.Request(self.base + "/api/admin/upload", data="新书正文".encode(), headers=headers)
        with urllib.request.urlopen(request) as response:
            result = json.loads(response.read())
        self.assertTrue(result["catalogDeferred"])
        self.assertIsNone(result["catalogCount"])
        rebuild = urllib.request.Request(self.base + "/api/admin/rebuild-catalog", data=b"", headers={"Cookie": "cabin_admin=test-admin-token"})
        with urllib.request.urlopen(rebuild) as response:
            self.assertGreater(json.loads(response.read())["catalogCount"], 0)

    def test_admin_login_is_temporarily_limited_after_repeated_failures(self):
        for _ in range(8):
            request = urllib.request.Request(
                self.base + "/api/admin/login",
                data=json.dumps({"code": "wrong-password"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(request)
            self.assertEqual(denied.exception.code, 401); denied.exception.close()
        request = urllib.request.Request(
            self.base + "/api/admin/login",
            data=json.dumps({"code": "wrong-password"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as limited:
            urllib.request.urlopen(request)
        self.assertEqual(limited.exception.code, 429); limited.exception.close()

    def test_card_entry_is_temporarily_limited_after_repeated_failures(self):
        headers = {"Content-Type": "application/json", "X-Real-IP": "203.0.113.27"}
        for _ in range(20):
            request = urllib.request.Request(
                self.base + "/api/unlock",
                data=json.dumps({"code": "NOT-A-CARD"}).encode(),
                headers=headers,
            )
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(request)
            self.assertEqual(denied.exception.code, 401); denied.exception.close()
        request = urllib.request.Request(
            self.base + "/api/unlock",
            data=json.dumps({"code": "NOT-A-CARD"}).encode(),
            headers=headers,
        )
        with self.assertRaises(urllib.error.HTTPError) as limited:
            urllib.request.urlopen(request)
        self.assertEqual(limited.exception.code, 429); limited.exception.close()

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

    def test_member_messages_are_saved_in_order_for_readers(self):
        headers = {"Content-Type": "application/json", "Cookie": "cabin_admin=test-admin-token"}
        for title in ("第一条", "第二条"):
            request = urllib.request.Request(self.base + "/api/admin/announcement", data=json.dumps({"title": title, "body": "会一直保留", "imageUrl": ""}).encode(), headers=headers)
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.loads(response.read())["ok"])
        reader = self.client(); self.unlock(reader, "TEST-READER")
        with reader.open(self.base + "/api/messages") as response:
            items = json.loads(response.read())["items"]
        self.assertEqual([item["title"] for item in items[:2]], ["第二条", "第一条"])

    def test_admin_can_delete_selected_messages_requests_reviews_and_cards(self):
        headers = {"Content-Type": "application/json", "Cookie": "cabin_admin=test-admin-token"}
        for title in ("保留", "删除"):
            request = urllib.request.Request(self.base + "/api/admin/announcement", data=json.dumps({"title": title, "body": "一条长期消息", "imageUrl": ""}).encode(), headers=headers)
            with urllib.request.urlopen(request):
                pass
        with sqlite3.connect(self.db) as db:
            message_id = json.loads(db.execute("SELECT value FROM settings WHERE key='member_messages'").fetchone()[0])[0]["id"]
            db.execute("INSERT INTO book_requests(title,author,edition,section,purpose,contact,note,visitor,created_at) VALUES(?,?,?,?,?,?,?,?,?)", ("要删除的书", "作者", "", "", "", "", "", "visitor", datetime.now(timezone.utc).isoformat()))
            request_id = db.execute("SELECT id FROM book_requests").fetchone()[0]
            db.execute("INSERT INTO reviews(book,title,body,visitor,created_at) VALUES(?,?,?,?,?)", ("想看类型", "要删除的留言", "这是一条足够长的测试读者留言。", "visitor", datetime.now(timezone.utc).isoformat()))
            review_id = db.execute("SELECT id FROM reviews").fetchone()[0]
            db.execute("INSERT INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)", ("TEST-DELETE", "reader", 0, 0, 0, (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(), 1, datetime.now(timezone.utc).isoformat()))
        for endpoint, payload in (("/api/admin/messages/delete", {"ids": [message_id]}), ("/api/admin/requests/delete", {"ids": [request_id]}), ("/api/admin/reviews/delete", {"ids": [review_id]}), ("/api/admin/cards/delete", {"codes": ["TEST-DELETE"]})):
            request = urllib.request.Request(self.base + endpoint, data=json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.loads(response.read())["deleted"], 1)
        reader = self.client(); self.unlock(reader, "TEST-READER")
        with reader.open(self.base + "/api/messages") as response:
            self.assertEqual(json.loads(response.read())["items"][0]["title"], "保留")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM book_requests").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM access_cards WHERE code='TEST-DELETE'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
