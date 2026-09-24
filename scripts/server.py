import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import base64
import gzip
import io
import re
import shutil
import zipfile
import mimetypes
from html.parser import HTMLParser
from xml.etree import ElementTree
from datetime import timedelta
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs, urlencode, quote
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from http.cookies import SimpleCookie


def decode_book_text(raw):
    """Decode the common encodings used by collected Chinese TXT books."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    # Some TXT exports are UTF-16 without a BOM.  A high proportion of NUL
    # bytes on one side of each byte pair is a reliable signal for Chinese
    # prose while avoiding false positives for ordinary UTF-8/GBK files.
    sample = raw[:8192]
    if len(sample) >= 8:
        even_nuls = sample[0::2].count(0)
        odd_nuls = sample[1::2].count(0)
        pairs = max(1, len(sample) // 2)
        if odd_nuls / pairs > 0.18:
            try:
                return raw.decode("utf-16-le")
            except UnicodeDecodeError:
                pass
        if even_nuls / pairs > 0.18:
            try:
                return raw.decode("utf-16-be")
            except UnicodeDecodeError:
                pass
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别正文编码，请将该书另存为 UTF-8 后重新上传")


def decode_docx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    paragraphs = []
    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        text = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


class _BookHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "br", "h1", "h2", "h3", "h4", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def decode_epub(raw):
    sections = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = sorted(name for name in archive.namelist() if name.lower().endswith((".xhtml", ".html", ".htm")))
        for name in names:
            try:
                parser = _BookHTMLParser()
                parser.feed(decode_book_text(archive.read(name)))
                text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
                if text:
                    sections.append(text)
            except (KeyError, UnicodeError, ValueError):
                continue
    if not sections:
        raise ValueError("No readable text in EPUB")
    return "\n\n".join(sections)


def decode_zip_book(raw, depth=0):
    if depth > 4:
        raise ValueError("Archive nesting is too deep")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/") and name.lower().endswith((".txt", ".docx", ".epub", ".zip"))]
        if not names:
            raise ValueError("No readable document in archive")
        names.sort(key=lambda name: (0 if name.lower().endswith(".txt") else 1, len(name)))
        for name in names:
            content = None
            for password in (None, b"1", b"123"):
                try:
                    content = archive.read(name, pwd=password)
                    break
                except (RuntimeError, NotImplementedError, zipfile.BadZipFile):
                    continue
            if content is None:
                continue
            try:
                suffix = Path(name).suffix.lower()
                if suffix == ".docx":
                    text = decode_docx(content)
                elif suffix == ".epub":
                    text = decode_epub(content)
                elif suffix == ".zip":
                    text = decode_zip_book(content, depth + 1)
                else:
                    text = decode_book_text(content)
                if text.strip():
                    return text
            except (KeyError, UnicodeError, ValueError, zipfile.BadZipFile):
                continue
    raise ValueError("No readable document in archive")


def decode_readable_book(path):
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return decode_book_text(raw)
    if suffix == ".docx":
        return decode_docx(raw)
    if suffix == ".epub":
        return decode_epub(raw)
    if suffix == ".zip":
        return decode_zip_book(raw)
    raise ValueError("Unsupported online reading format")


class LibraryHandler(SimpleHTTPRequestHandler):
    site_root = Path.cwd().resolve()
    textbook_root = Path.cwd().resolve()
    access_token = ""
    admin_token = ""
    admin_key = ""
    db_path = Path("library.db")
    read_cache_root = Path(".read-cache")
    read_cache_lock = threading.Lock()

    def cached_readable_book(self, source):
        stat = source.stat()
        fingerprint = f"{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
        key = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        plain = self.read_cache_root / f"{key}.txt"
        compressed = self.read_cache_root / f"{key}.txt.gz"
        if plain.exists() and compressed.exists():
            return plain, compressed
        with self.read_cache_lock:
            if plain.exists() and compressed.exists():
                return plain, compressed
            body = decode_readable_book(source).encode("utf-8")
            plain_tmp = plain.with_suffix(".tmp")
            gzip_tmp = compressed.with_suffix(".tmp")
            plain_tmp.write_bytes(body)
            gzip_tmp.write_bytes(gzip.compress(body, compresslevel=5))
            plain_tmp.replace(plain)
            gzip_tmp.replace(compressed)
        return plain, compressed

    def send_cached_text(self, source):
        plain, compressed = self.cached_readable_book(source)
        use_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        payload = compressed if use_gzip else plain
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("Vary", "Accept-Encoding")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(payload.stat().st_size))
        self.end_headers()
        with payload.open("rb") as stream:
            try:
                shutil.copyfileobj(stream, self.wfile, length=1024 * 256)
            except (BrokenPipeError, ConnectionResetError):
                # A reader may close or change pages before a large book finishes.
                return

    def send_json(self, status, payload, cookie=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
            self.send_header("Set-Cookie", cookie + secure)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = min(int(self.headers.get("Content-Length", "0")), 65536)
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def has_admin_access(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        item = cookie.get("cabin_admin")
        return bool(item and secrets.compare_digest(item.value, self.admin_token))

    def save_event(self, event_type, visitor, detail):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO events(type, visitor, detail, created_at) VALUES(?,?,?,?)", (event_type[:30], visitor[:80], detail[:1000], datetime.now(timezone.utc).isoformat()))

    def admin_data(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            totals = {row["type"]: row["count"] for row in db.execute("SELECT type, COUNT(*) count FROM events GROUP BY type")}
            visitors = db.execute("SELECT COUNT(DISTINCT visitor) FROM events WHERE visitor <> ''").fetchone()[0]
            recent = [dict(row) for row in db.execute("SELECT type, visitor, detail, created_at FROM events ORDER BY id DESC LIMIT 100")]
            reviews = [dict(row) for row in db.execute("SELECT book, title, body, visitor, created_at FROM reviews ORDER BY id DESC LIMIT 100")]
            requests = [dict(row) for row in db.execute("SELECT title, author, edition, section, purpose, contact, note, visitor, created_at FROM book_requests ORDER BY id DESC LIMIT 200")]
            cards = [dict(row) for row in db.execute("SELECT code, card_type, download_limit, remaining, used, expires_at, active, created_at FROM access_cards ORDER BY id DESC LIMIT 500")]
        return {"totals": totals, "visitors": visitors, "recent": recent, "reviews": reviews, "requests": requests, "cards": cards}

    def download_session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        item = cookie.get("cabin_download")
        return item.value if item else ""

    def access_info(self):
        if self.has_admin_access():
            return {"id": 0, "card_type": "reader", "remaining": -1, "expires_at": "", "active": 1}
        token = self.download_session()
        if not token: return None
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT c.id,c.card_type,c.remaining,c.expires_at,c.active FROM download_sessions s JOIN access_cards c ON c.id=s.card_id WHERE s.token=?", (token,)).fetchone()
        if not row or not row["active"]: return None
        if row["card_type"] == "reader" and row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc).isoformat(): return None
        if row["card_type"] == "download" and row["remaining"] == 0: return None
        return dict(row)

    def consume_download(self):
        token = self.download_session()
        if not token:
            return False
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT c.id, c.remaining, c.card_type FROM download_sessions s JOIN access_cards c ON c.id=s.card_id WHERE s.token=? AND c.active=1", (token,)).fetchone()
            if not row or row["card_type"] != "download" or row["remaining"] == 0:
                return False
            if row["remaining"] > 0:
                db.execute("UPDATE access_cards SET remaining=remaining-1, used=used+1 WHERE id=? AND remaining>0", (row["id"],))
            else:
                db.execute("UPDATE access_cards SET used=used+1 WHERE id=?", (row["id"],))
            db.execute("UPDATE download_sessions SET last_used_at=? WHERE token=?", (datetime.now(timezone.utc).isoformat(), token))
        return True

    def create_cards(self, count, download_limit=0, card_type="download", months=0):
        now = datetime.now(timezone.utc).isoformat()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30 * months)).isoformat() if card_type == "reader" else ""
        cards = []
        with sqlite3.connect(self.db_path) as db:
            for _ in range(count):
                while True:
                    code = "MUMU-" + secrets.token_hex(3).upper() + "-" + secrets.token_hex(2).upper()
                    try:
                        db.execute("INSERT INTO access_cards(code,card_type,download_limit,remaining,used,expires_at,active,created_at) VALUES(?,?,?,?,?,?,?,?)", (code, card_type, download_limit, download_limit, 0, expires_at, 1, now))
                        cards.append(code)
                        break
                    except sqlite3.IntegrityError:
                        continue
        return cards

    def verify_admin_password(self, password):
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT value FROM settings WHERE key='admin_password_hash'").fetchone()
        return bool(row and secrets.compare_digest(row[0], digest))

    def do_POST(self):
        api_path = urlsplit(self.path).path
        if api_path == "/api/admin/upload":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            filename = Path(unquote(self.headers.get("X-Filename", ""))).name
            relative_header = unquote(self.headers.get("X-Relative-Path", ""))
            allowed = {".txt", ".docx", ".zip", ".rar", ".7z", ".pdf", ".epub", ".mobi"}
            length = int(self.headers.get("Content-Length", "0"))
            if not filename or Path(filename).suffix.lower() not in allowed or length < 1 or length > 104857600:
                self.send_json(400, {"ok": False}); return
            target_dir = self.textbook_root / "管理员上传"
            relative = Path(relative_header.replace("\\", "/")) if relative_header else Path(filename)
            if len(relative.parts) > 1 and relative.parts[0].casefold() in {"textbook", "书库"}:
                relative = Path(*relative.parts[1:])
            if relative.is_absolute() or not relative.parts or any(part in ("", ".", "..") for part in relative.parts) or len(relative.parts) > 30:
                self.send_json(400, {"ok": False}); return
            target = (target_dir / relative).resolve()
            try:
                if os.path.commonpath((str(target_dir.resolve()), str(target))) != str(target_dir.resolve()):
                    self.send_json(400, {"ok": False}); return
            except ValueError:
                self.send_json(400, {"ok": False}); return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.rfile.read(length))
            self.send_json(200, {"ok": True, "filename": filename, "relativePath": relative.as_posix()}); return
        try:
            payload = self.read_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400)
            return
        if api_path == "/api/events":
            event_type = str(payload.get("type", ""))
            if event_type not in ("visit", "search", "download"):
                self.send_error(400); return
            self.save_event(event_type, str(payload.get("visitor", "")), str(payload.get("detail", "")))
            self.send_json(200, {"ok": True}); return
        if api_path == "/api/reviews":
            body = str(payload.get("body", "")).strip()
            if len(body) < 10:
                self.send_json(400, {"ok": False}); return
            with sqlite3.connect(self.db_path) as db:
                db.execute("INSERT INTO reviews(book,title,body,visitor,created_at) VALUES(?,?,?,?,?)", (str(payload.get("book", ""))[:300], str(payload.get("title", ""))[:300], body[:10000], str(payload.get("visitor", ""))[:80], datetime.now(timezone.utc).isoformat()))
            self.save_event("review", str(payload.get("visitor", "")), str(payload.get("book", "")))
            self.send_json(200, {"ok": True}); return
        if api_path == "/api/requests":
            title = str(payload.get("title", "")).strip()
            author = str(payload.get("author", "")).strip()
            if not title or not author:
                self.send_json(400, {"ok": False}); return
            visitor = str(payload.get("visitor", ""))[:80]
            with sqlite3.connect(self.db_path) as db:
                db.execute("INSERT INTO book_requests(title,author,edition,section,purpose,contact,note,visitor,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (title[:300], author[:300], "", "", "", "", "", visitor, datetime.now(timezone.utc).isoformat()))
            self.save_event("request", visitor, title)
            self.send_json(200, {"ok": True}); return
        if api_path == "/api/admin/login":
            if not self.verify_admin_password(str(payload.get("code", ""))):
                self.send_json(401, {"ok": False}); return
            self.send_json(200, {"ok": True}, f"cabin_admin={self.admin_token}; Path=/; HttpOnly; SameSite=Strict"); return
        if api_path == "/api/admin/password":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            new_password = str(payload.get("newPassword", ""))
            if len(new_password) < 8 or len(new_password) > 128:
                self.send_json(400, {"ok": False, "message": "密码需要 8 至 128 位"}); return
            digest = hashlib.sha256(new_password.encode("utf-8")).hexdigest()
            with sqlite3.connect(self.db_path) as db:
                db.execute("INSERT INTO settings(key,value) VALUES('admin_password_hash',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (digest,))
            self.send_json(200, {"ok": True}); return
        if api_path == "/api/admin/cards":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            try:
                count = max(1, min(500, int(payload.get("count", 1))))
                card_type = "reader" if payload.get("cardType") == "reader" else "download"
                download_limit = max(1, min(10000, int(payload.get("downloadLimit", 1)))) if card_type == "download" else 0
                months = max(1, min(36, int(payload.get("months", 1)))) if card_type == "reader" else 0
            except (TypeError, ValueError):
                self.send_json(400, {"ok": False, "message": "数量必须是整数"}); return
            self.send_json(200, {"ok": True, "codes": self.create_cards(count, download_limit, card_type, months), "downloadLimit": download_limit, "months": months, "cardType": card_type}); return
        if api_path == "/api/admin/cards/delete":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            codes = payload.get("codes", [])
            if not isinstance(codes, list):
                self.send_json(400, {"ok": False, "message": "卡密列表格式不正确"}); return
            codes = list(dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip()))[:500]
            if not codes:
                self.send_json(400, {"ok": False, "message": "请先选择卡密"}); return
            now = datetime.now(timezone.utc).isoformat()
            placeholders = ",".join("?" for _ in codes)
            with sqlite3.connect(self.db_path) as db:
                rows = db.execute(
                    f"SELECT id FROM access_cards WHERE code IN ({placeholders}) AND (active=0 OR (card_type='reader' AND expires_at<>'' AND expires_at<=?) OR (card_type<>'reader' AND remaining=0))",
                    (*codes, now),
                ).fetchall()
                card_ids = [row[0] for row in rows]
                if card_ids:
                    id_placeholders = ",".join("?" for _ in card_ids)
                    db.execute(f"DELETE FROM download_sessions WHERE card_id IN ({id_placeholders})", card_ids)
                    db.execute(f"DELETE FROM access_cards WHERE id IN ({id_placeholders})", card_ids)
            self.send_json(200, {"ok": True, "deleted": len(card_ids)}); return
        if api_path == "/api/admin/cards/disable":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            codes = payload.get("codes", [])
            if not isinstance(codes, list):
                self.send_json(400, {"ok": False, "message": "卡密列表格式不正确"}); return
            codes = list(dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip()))[:500]
            if not codes:
                self.send_json(400, {"ok": False, "message": "请先选择卡密"}); return
            placeholders = ",".join("?" for _ in codes)
            with sqlite3.connect(self.db_path) as db:
                card_ids = [row[0] for row in db.execute(f"SELECT id FROM access_cards WHERE code IN ({placeholders}) AND active=1", codes)]
                if card_ids:
                    id_placeholders = ",".join("?" for _ in card_ids)
                    db.execute(f"UPDATE access_cards SET active=0 WHERE id IN ({id_placeholders})", card_ids)
                    db.execute(f"DELETE FROM download_sessions WHERE card_id IN ({id_placeholders})", card_ids)
            self.send_json(200, {"ok": True, "disabled": len(card_ids)}); return
        if api_path != "/api/unlock":
            self.send_error(404); return
        code = str(payload.get("code", "")).strip()
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            card = db.execute("SELECT id, card_type, remaining, expires_at FROM access_cards WHERE code=? AND active=1", (code.upper(),)).fetchone()
            expired = bool(card and card["card_type"] == "reader" and card["expires_at"] and card["expires_at"] < datetime.now(timezone.utc).isoformat())
            if not card or expired or (card["card_type"] == "download" and card["remaining"] == 0):
                self.send_json(401, {"ok": False}); return
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO download_sessions(token,card_id,created_at,last_used_at) VALUES(?,?,?,?)", (token, card["id"], datetime.now(timezone.utc).isoformat(), ""))
        if card["card_type"] == "reader" and card["expires_at"]:
            cookie_expires = datetime.fromisoformat(card["expires_at"])
            max_age = max(0, int((cookie_expires - datetime.now(timezone.utc)).total_seconds()))
        else:
            cookie_expires = datetime.now(timezone.utc) + timedelta(days=30)
            max_age = 30 * 24 * 60 * 60
        cookie = f"cabin_download={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}; Expires={format_datetime(cookie_expires, usegmt=True)}"
        self.send_json(200, {"ok": True, "remaining": card["remaining"], "cardType": card["card_type"], "expiresAt": card["expires_at"]}, cookie); return

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == "/api/admin/data":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            self.send_json(200, self.admin_data()); return
        if request_path == "/health":
            self.send_json(200, {"ok": True}); return
        if request_path == "/api/access":
            info = self.access_info()
            self.send_json(200, {"ok": bool(info), "cardType": info["card_type"] if info else "", "remaining": info["remaining"] if info else 0, "expiresAt": info["expires_at"] if info else ""}); return
        protected_pages = {
            "/pages/search.html", "/pages/request.html",
            "/pages/review.html", "/pages/bookshelf.html", "/pages/reader.html",
        }
        if request_path in protected_pages and not self.access_info():
            self.send_response(302)
            self.send_header("Location", "/pages/home.html?card=required")
            self.end_headers()
            return
        if request_path == "/assets/catalog.json":
            source = self.site_root / "assets" / "catalog.json"
            try:
                stat = source.stat()
                etag = '"%x-%x"' % (stat.st_mtime_ns, stat.st_size)
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                body = source.read_bytes()
            except OSError:
                self.send_error(404, "Catalog unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            if "gzip" in self.headers.get("Accept-Encoding", "").lower() and len(body) > 1024:
                body = gzip.compress(body, compresslevel=5)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path == "/go/read":
            if not self.access_info():
                self.send_response(302)
                self.send_header("Location", "/pages/home.html?card=required")
                self.end_headers()
                return
            query = parse_qs(urlsplit(self.path).query).get("q", [""])[0].strip()[:200]
            scope = "(site:gutenberg.org OR site:wenyuange.org OR site:book5678.com OR site:owlook.com.cn OR site:kgbook.com)"
            destination = "https://www.bing.com/search?" + urlencode({"q": query + " 在线阅读 " + scope})
            self.send_response(302)
            self.send_header("Location", destination)
            self.end_headers()
            return
        if request_path.startswith("/books/"):
            info = self.access_info()
            if not info or info["card_type"] != "download":
                self.send_error(403, "Download card required or download limit exhausted")
                return
            source = Path(self.translate_path(self.path))
            try:
                raw = source.read_bytes()
            except OSError:
                self.send_error(404, "Book file unavailable")
                return
            if source.suffix.lower() == ".txt":
                try:
                    body = b"\xef\xbb\xbf" + decode_book_text(raw).encode("utf-8")
                except ValueError as error:
                    self.send_error(422, str(error))
                    return
                content_type = "text/plain; charset=utf-8"
            else:
                body = raw
                content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            if not self.consume_download():
                self.send_error(403, "Download limit exhausted")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", "attachment; filename=book%s; filename*=UTF-8''%s" % (source.suffix, quote(source.name)))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path.startswith("/read/"):
            info = self.access_info()
            if not info or info["card_type"] != "reader": self.send_error(403, "Reader card required"); return
            if Path(request_path).suffix.lower() in {".txt", ".docx", ".epub", ".zip"}:
                try:
                    self.send_cached_text(Path(self.translate_path(self.path)))
                except (OSError, UnicodeError, ValueError, KeyError, zipfile.BadZipFile):
                    self.send_error(404, "Book text unavailable")
                return
        super().do_GET()

    def do_HEAD(self):
        super().do_HEAD()

    def list_directory(self, path):
        self.send_error(404, "Directory listing is disabled")
        return None

    def translate_path(self, path):
        request_path = unquote(urlsplit(path).path)
        if request_path in ("/books", "/books/", "/read", "/read/"):
            return str(self.textbook_root / "__directory_listing_disabled__")

        if request_path.startswith("/books/"):
            relative = request_path[len("/books/"):]
            root = self.textbook_root
        elif request_path.startswith("/read/"):
            relative = request_path[len("/read/"):]
            root = self.textbook_root
        else:
            relative = request_path.lstrip("/")
            root = self.site_root

        candidate = (root / relative).resolve()
        try:
            if os.path.commonpath((str(root), str(candidate))) != str(root):
                return str(root / "__invalid_path__")
        except ValueError:
            return str(root / "__invalid_path__")
        return str(candidate)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        request_path = urlsplit(self.path).path
        if request_path == "/assets/catalog.json":
            self.send_header("Cache-Control", "private, max-age=60, stale-while-revalidate=300")
        elif request_path.startswith("/assets/") or request_path.endswith((".css", ".js")):
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


def watch_textbook(handler):
    extensions = {".txt", ".docx", ".zip", ".rar", ".7z", ".pdf", ".epub", ".mobi"}
    previous = None
    while True:
        try:
            files = [p for p in handler.textbook_root.rglob("*") if p.is_file() and p.suffix.lower() in extensions and not {"novel-library", "node_modules", ".git"}.intersection(p.parts)]
            signature = tuple(sorted((str(p.relative_to(handler.textbook_root)), p.stat().st_size, p.stat().st_mtime_ns) for p in files))
            if signature != previous:
                items = []
                seen_books = set()
                def normalized_title(path):
                    return re.sub(r"(?:\s*[（(]\s*\d+\s*[）)])+$", "", path.stem).strip()

                for path in sorted(files, key=lambda item: (bool(re.search(r"[（(]\s*\d+\s*[）)]$", item.stem)), len(item.relative_to(handler.textbook_root).parts), str(item))):
                    relative = path.relative_to(handler.textbook_root).as_posix()
                    stat = path.stat()
                    title = normalized_title(path) or path.stem
                    duplicate_key = (title.casefold(), path.suffix.lower())
                    if duplicate_key in seen_books:
                        continue
                    seen_books.add(duplicate_key)
                    category_path = Path(relative).parent
                    category = "uncategorized" if str(category_path) == "." else " / ".join(category_path.parts)
                    items.append({"title": title, "category": category, "format": path.suffix.lstrip(".").lower(), "size": stat.st_size, "modifiedAt": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"), "searchablePath": relative})
                items.sort(key=lambda item: (item["title"], item["searchablePath"]))
                output = handler.site_root / "assets" / "catalog.json"
                temporary = output.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                temporary.replace(output)
                previous = signature
        except (OSError, ValueError):
            pass
        time.sleep(max(5, int(os.environ.get("CATALOG_SCAN_INTERVAL", "15"))))


class LibraryHTTPServer(ThreadingHTTPServer):
    """Accept short traffic bursts without intermittently refusing readers."""
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "4173")))
    parser.add_argument("--site", default=os.environ.get("SITE_ROOT", str(Path.cwd())))
    parser.add_argument("--textbook", default=os.environ.get("TEXTBOOK_ROOT", str(Path.cwd() / "textbook")))
    parser.add_argument("--data", default=os.environ.get("CABIN_DATA_DIR", ""))
    args = parser.parse_args()

    LibraryHandler.site_root = Path(args.site).resolve()
    LibraryHandler.textbook_root = Path(args.textbook).resolve()
    LibraryHandler.textbook_root.mkdir(parents=True, exist_ok=True)
    LibraryHandler.access_token = hashlib.sha256(b"xiaomuwu-download-session-v1").hexdigest()
    session_secret = os.environ.get("CABIN_SESSION_SECRET") or secrets.token_urlsafe(32)
    LibraryHandler.admin_token = hashlib.sha256(session_secret.encode("utf-8")).hexdigest()
    LibraryHandler.admin_key = os.environ.get("CABIN_ADMIN_KEY", "MUMU-ADMIN-2026")
    data_dir = Path(args.data).resolve() if args.data else LibraryHandler.site_root / ".data"
    data_dir.mkdir(parents=True, exist_ok=True)
    LibraryHandler.db_path = data_dir / "library.db"
    LibraryHandler.read_cache_root = data_dir / "read-cache"
    LibraryHandler.read_cache_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(LibraryHandler.db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, type TEXT, visitor TEXT, detail TEXT, created_at TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, book TEXT, title TEXT, body TEXT, visitor TEXT, created_at TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS book_requests(id INTEGER PRIMARY KEY, title TEXT, author TEXT, edition TEXT, section TEXT, purpose TEXT, contact TEXT, note TEXT, visitor TEXT, created_at TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS access_cards(id INTEGER PRIMARY KEY, code TEXT UNIQUE, download_limit INTEGER NOT NULL, remaining INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS download_sessions(token TEXT PRIMARY KEY, card_id INTEGER NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT, FOREIGN KEY(card_id) REFERENCES access_cards(id))")
        db.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(access_cards)")}
        if "card_type" not in columns: db.execute("ALTER TABLE access_cards ADD COLUMN card_type TEXT NOT NULL DEFAULT 'download'")
        if "expires_at" not in columns: db.execute("ALTER TABLE access_cards ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''")
        initial_admin_hash = hashlib.sha256(LibraryHandler.admin_key.encode("utf-8")).hexdigest()
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('admin_password_hash',?)", (initial_admin_hash,))
    threading.Thread(target=watch_textbook, args=(LibraryHandler,), daemon=True).start()
    server = LibraryHTTPServer(("0.0.0.0", args.port), LibraryHandler)
    print(f"Serving on port {args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
