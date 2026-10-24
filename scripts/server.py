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
from contextlib import contextmanager
from html.parser import HTMLParser
from xml.etree import ElementTree
from datetime import timedelta
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs, urlencode, quote
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
try:
    from . import library_audit
    from . import online_library
except ImportError:
    import library_audit
    import online_library


@contextmanager
def open_database(path):
    """Commit or roll back each short request transaction and always close it."""
    db = sqlite3.connect(path, timeout=15)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def decode_book_text(raw):
    """Decode the common encodings used by collected Chinese TXT books."""
    # Some exports prepend ASCII whitespace before the Unicode BOM.
    prefixed = raw.lstrip(b"\r\n\t ")
    if prefixed.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raw = prefixed
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
    # Do not disclose the underlying Python release in every HTTP response.
    server_version = "Cabin"
    sys_version = ""
    site_root = Path.cwd().resolve()
    textbook_root = Path.cwd().resolve()
    access_token = ""
    admin_token = ""
    admin_key = ""
    db_path = Path("library.db")
    read_cache_root = Path(".read-cache")
    online_cache_root = Path(".online-cache")
    read_cache_lock = threading.Lock()
    catalog_cache_lock = threading.Lock()
    catalog_build_lock = threading.Lock()
    upload_lock = threading.Lock()
    auth_attempt_lock = threading.Lock()
    auth_attempts = {}
    auth_last_cleanup = 0.0

    def client_identity(self):
        forwarded = self.headers.get("X-Real-IP", "").strip()
        if not forwarded:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        return forwarded[:80] or str(self.client_address[0])[:80]

    def auth_rate_limited(self, scope, limit, window=600):
        key = (scope, self.client_identity())
        now = time.monotonic()
        owner = type(self)
        with self.auth_attempt_lock:
            if now - owner.auth_last_cleanup >= 60:
                for existing_key, attempts in list(owner.auth_attempts.items()):
                    recent_attempts = [stamp for stamp in attempts if now - stamp < window]
                    if recent_attempts:
                        owner.auth_attempts[existing_key] = recent_attempts
                    else:
                        owner.auth_attempts.pop(existing_key, None)
                owner.auth_last_cleanup = now
            recent = [stamp for stamp in owner.auth_attempts.get(key, ()) if now - stamp < window]
            if recent:
                owner.auth_attempts[key] = recent
            else:
                owner.auth_attempts.pop(key, None)
            return len(recent) >= limit

    def record_auth_failure(self, scope):
        key = (scope, self.client_identity())
        with self.auth_attempt_lock:
            type(self).auth_attempts.setdefault(key, []).append(time.monotonic())

    def clear_auth_failures(self, scope):
        with self.auth_attempt_lock:
            type(self).auth_attempts.pop((scope, self.client_identity()), None)

    def cached_gzip_file(self, source, prefix="asset"):
        """Build large gzip responses once instead of recompressing per visitor."""
        stat = source.stat()
        fingerprint = f"{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
        key = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        compressed = self.read_cache_root / f"{prefix}-{key}.gz"
        if compressed.exists():
            return compressed
        with self.catalog_cache_lock:
            if compressed.exists():
                return compressed
            temporary = compressed.with_suffix(".tmp")
            with source.open("rb") as input_stream, gzip.open(temporary, "wb", compresslevel=5) as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 256)
            temporary.replace(compressed)
        return compressed

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
        source_stat = source.stat()
        etag = '"%x-%x"' % (source_stat.st_mtime_ns, source_stat.st_size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, max-age=3600, stale-while-revalidate=86400")
            self.end_headers()
            return
        plain, compressed = self.cached_readable_book(source)
        use_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        payload = compressed if use_gzip else plain
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "private, max-age=3600, stale-while-revalidate=86400")
        self.send_header("ETag", etag)
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

    def send_file_range(self, source, content_type=None):
        """Serve large readable files in byte ranges so PDFs open immediately."""
        stat = source.stat()
        size = stat.st_size
        etag = '"%x-%x"' % (stat.st_mtime_ns, size)
        if self.headers.get("If-None-Match") == etag and not self.headers.get("Range"):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        start, end = 0, max(0, size - 1)
        range_header = self.headers.get("Range", "")
        partial = False
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip()) if range_header else None
        if match and size:
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(size - 1, int(last)) if last else size - 1
            elif last:
                start = max(0, size - int(last))
                end = size - 1
            if start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            partial = True
        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, max-age=3600, stale-while-revalidate=86400")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with source.open("rb") as stream:
            stream.seek(start)
            remaining = length
            try:
                while remaining:
                    chunk = stream.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
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

    def send_online_text(self, page):
        source = page["path"]
        stat = source.stat()
        etag = '"online-%x-%x"' % (stat.st_mtime_ns, stat.st_size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, max-age=3600, stale-while-revalidate=86400")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, max-age=3600, stale-while-revalidate=86400")
        self.send_header("X-Book-Source", quote(page["source"]))
        self.end_headers()
        with source.open("rb") as stream:
            try:
                shutil.copyfileobj(stream, self.wfile, length=1024 * 256)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def read_json(self):
        length = min(int(self.headers.get("Content-Length", "0")), 65536)
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def has_admin_access(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        item = cookie.get("cabin_admin")
        return bool(item and secrets.compare_digest(item.value, self.admin_token))

    def save_event(self, event_type, visitor, detail):
        with open_database(self.db_path) as db:
            db.execute("INSERT INTO events(type, visitor, detail, created_at) VALUES(?,?,?,?)", (event_type[:30], visitor[:80], detail[:1000], datetime.now(timezone.utc).isoformat()))

    def admin_data(self):
        disk = shutil.disk_usage(self.textbook_root)
        with open_database(self.db_path) as db:
            db.row_factory = sqlite3.Row
            totals = {row["type"]: row["count"] for row in db.execute("SELECT type, COUNT(*) count FROM events GROUP BY type")}
            visitors = db.execute("SELECT COUNT(DISTINCT visitor) FROM events WHERE visitor <> ''").fetchone()[0]
            recent = [dict(row) for row in db.execute("SELECT type, visitor, detail, created_at FROM events ORDER BY id DESC LIMIT 100")]
            reviews = [dict(row) for row in db.execute("SELECT id, book, title, body, visitor, created_at, admin_reply, replied_at FROM reviews ORDER BY id DESC LIMIT 100")]
            requests = [dict(row) for row in db.execute("SELECT id, title, author, edition, section, purpose, contact, note, visitor, created_at FROM book_requests ORDER BY id DESC LIMIT 200")]
            cards = [dict(row) for row in db.execute("SELECT code, card_type, download_limit, remaining, used, expires_at, active, created_at FROM access_cards ORDER BY id DESC LIMIT 500")]
        return {"totals": totals, "visitors": visitors, "recent": recent, "reviews": reviews, "requests": requests, "cards": cards,
                "storage": {"free": disk.free, "used": disk.used, "total": disk.total}}

    def download_session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        item = cookie.get("cabin_download")
        return item.value if item else ""

    def access_info(self):
        if self.has_admin_access():
            return {"id": 0, "card_type": "reader", "remaining": -1, "expires_at": "", "active": 1}
        token = self.download_session()
        if not token: return None
        with open_database(self.db_path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT c.id,c.card_type,c.remaining,c.expires_at,c.active FROM download_sessions s JOIN access_cards c ON c.id=s.card_id WHERE s.token=?", (token,)).fetchone()
        if not row or not row["active"]: return None
        if row["card_type"] == "reader" and row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc).isoformat(): return None
        if row["card_type"] == "download" and row["remaining"] == 0: return None
        return dict(row)

    def reserve_download(self):
        """Reserve one use before streaming, so concurrent downloads cannot overrun a card."""
        token = self.download_session()
        if not token:
            return None
        with open_database(self.db_path) as db:
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE IF NOT EXISTS download_reservations(id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL, token TEXT NOT NULL, created_at TEXT NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT c.id, c.remaining, c.card_type FROM download_sessions s JOIN access_cards c ON c.id=s.card_id WHERE s.token=? AND c.active=1", (token,)).fetchone()
            if not row or row["card_type"] != "download" or row["remaining"] == 0:
                return None
            changed = db.execute("UPDATE access_cards SET remaining=remaining-1, used=used+1 WHERE id=? AND remaining>0", (row["id"],)).rowcount
            if changed != 1:
                return None
            db.execute("UPDATE download_sessions SET last_used_at=? WHERE token=?", (datetime.now(timezone.utc).isoformat(), token))
            cursor = db.execute("INSERT INTO download_reservations(card_id,token,created_at) VALUES(?,?,?)", (row["id"], token, datetime.now(timezone.utc).isoformat()))
        return {"card_id": row["id"], "token": token, "reservation_id": cursor.lastrowid}

    def refund_download(self, reservation):
        """Return a reserved use when the server could not finish sending the file."""
        if not reservation:
            return
        with open_database(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT card_id FROM download_reservations WHERE id=?", (reservation["reservation_id"],)).fetchone()
            if pending:
                db.execute("UPDATE access_cards SET remaining=MIN(download_limit, remaining+1), used=MAX(0, used-1) WHERE id=?", (pending[0],))
                db.execute("DELETE FROM download_reservations WHERE id=?", (reservation["reservation_id"],))

    def complete_download(self, reservation):
        if not reservation:
            return
        with open_database(self.db_path) as db:
            db.execute("DELETE FROM download_reservations WHERE id=?", (reservation["reservation_id"],))

    def create_cards(self, count, download_limit=0, card_type="download", months=0):
        now = datetime.now(timezone.utc).isoformat()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30 * months)).isoformat() if card_type == "reader" else ""
        cards = []
        with open_database(self.db_path) as db:
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
        with open_database(self.db_path) as db:
            row = db.execute("SELECT value FROM settings WHERE key='admin_password_hash'").fetchone()
        return bool(row and secrets.compare_digest(row[0], digest))

    def do_POST(self):
        api_path = urlsplit(self.path).path
        if api_path in {'/api/admin/upload-preview', '/api/admin/replacements/restore'}:
            if not self.has_admin_access():
                self.send_json(403, {'ok': False}); return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1048576:
                    raise ValueError('请求过大')
                payload = json.loads(self.rfile.read(length))
                if api_path.endswith('upload-preview'):
                    names = payload.get('names', [])
                    if not isinstance(names, list) or len(names) > 2000:
                        raise ValueError('每批最多预览2000本')
                    index = {}
                    for path in self.textbook_root.rglob('*'):
                        if path.is_file() and path.suffix.lower() in BOOK_EXTENSIONS:
                            index.setdefault(duplicate_title_key(path), []).append(path.relative_to(self.textbook_root).as_posix())
                    self.send_json(200, {'items': [{'name': name, 'matches': index.get(duplicate_title_key(name), [])} for name in names if isinstance(name, str)]}); return
                relative = Path(payload.get('id', ''))
                source = (self.replaced_root / relative).resolve()
                if len(relative.parts) < 2 or relative.is_absolute() or not source.is_relative_to(self.replaced_root.resolve()) or not source.is_file():
                    raise ValueError('备份不存在')
                target = (self.textbook_root / Path(*relative.parts[1:])).resolve()
                if not target.is_relative_to(self.textbook_root.resolve()):
                    raise ValueError('路径无效')
                with self.upload_lock:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name('.restore-' + secrets.token_hex(8))
                    shutil.copy2(source, temporary)
                    if target.exists() and archive_replaced_book(type(self), target) is None:
                        temporary.unlink(missing_ok=True)
                        raise ValueError('当前版本无法备份，请稍后重试')
                    temporary.replace(target)
                    os.utime(target, None)
                    rebuild_catalog(type(self))
                self.send_json(200, {'ok': True}); return
            except (ValueError, OSError, TypeError) as error:
                self.send_json(400, {'ok': False, 'message': str(error)}); return
        if api_path == '/api/admin/audit':
            if not self.has_admin_access():
                self.send_json(403, {'ok': False}); return
            library_audit.start(type(self), decode_readable_book)
            self.send_json(202, {'ok': True}); return
        if api_path == '/api/admin/audit/repair':
            if not self.has_admin_access():
                self.send_json(403, {'ok': False}); return
            result = library_audit.repair(type(self), rebuild_catalog)
            library_audit.start(type(self), decode_readable_book)
            self.send_json(200, result); return
        if api_path == "/api/admin/rebuild-catalog":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            self.send_json(200, {"ok": True, "catalogCount": rebuild_catalog(type(self))}); return
        if api_path == "/api/admin/upload":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            filename = Path(unquote(self.headers.get("X-Filename", ""))).name
            relative_header = unquote(self.headers.get("X-Relative-Path", ""))
            allowed = {".txt", ".docx", ".zip", ".rar", ".7z", ".pdf", ".epub", ".mobi"}
            length = int(self.headers.get("Content-Length", "0"))
            if not filename or Path(filename).suffix.lower() not in allowed or length < 1 or length > 104857600:
                self.send_json(400, {"ok": False}); return
            relative = Path(relative_header.replace("\\", "/")) if relative_header else Path(filename)
            if relative.is_absolute() or not relative.parts or any(part in ("", ".", "..") for part in relative.parts) or len(relative.parts) > 30:
                self.send_json(400, {"ok": False}); return
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_json(400, {"ok": False, "message": "文件传输不完整，请重试"}); return
            expected_hash = self.headers.get('X-Content-SHA256', '')
            if expected_hash and hashlib.sha256(body).hexdigest() != expected_hash:
                self.send_json(400, {"ok": False, "message": "文件校验失败，请重试"}); return
            defer_catalog = self.headers.get("X-Defer-Catalog", "") == "1"
            upload_root = self.textbook_root / "管理员上传"
            with self.upload_lock:
                duplicates = find_duplicate_books(self.textbook_root, filename, body)
                if self.headers.get('X-Upload-Mode') != 'folder':
                    for existing in duplicates:
                        if existing.name == filename and existing.read_bytes() == body:
                            self.send_json(200, {"ok": True, "replaced": 0, "unchanged": True}); return
                upload_root.mkdir(parents=True, exist_ok=True)
                numbered = []
                for child in upload_root.iterdir():
                    match = re.fullmatch(r"新上传(\d+)", child.name) if child.is_dir() else None
                    if match:
                        numbered.append((int(match.group(1)), child))
                group_number, target_dir = max(numbered, default=(0, None), key=lambda item: item[0])
                current_count = sum(1 for item in target_dir.rglob("*") if item.is_file()) if target_dir else 20
                if target_dir is None or current_count >= 20:
                    group_number += 1
                    target_dir = upload_root / f"新上传{group_number}"
                if self.headers.get('X-Upload-Mode') == 'folder' and len(relative.parts) > 1:
                    target_dir = upload_root / relative.parts[0]
                    relative = Path(*relative.parts[1:])
                target = (target_dir / relative).resolve()
                if not target.is_relative_to(upload_root.resolve()):
                    self.send_json(400, {"ok": False}); return
                identical = next((p for p in duplicates if p.resolve() == target and p.read_bytes() == body), None)
                if identical is not None:
                    self.send_json(200, {"ok": True, "replaced": 0, "unchanged": True}); return
                try:
                    if os.path.commonpath((str(target_dir.resolve()), str(target))) != str(target_dir.resolve()):
                        self.send_json(400, {"ok": False}); return
                except ValueError:
                    self.send_json(400, {"ok": False}); return
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary_upload = target.with_name("." + target.name + ".upload-" + secrets.token_hex(4))
                temporary_upload.write_bytes(body)
                replaced_paths = []
                for duplicate in duplicates:
                    backup = archive_replaced_book(type(self), duplicate)
                    if backup is not None:
                        replaced_paths.append(duplicate.relative_to(self.textbook_root).as_posix())
                    elif duplicate.resolve() == target:
                        temporary_upload.unlink(missing_ok=True)
                        self.send_json(500, {"ok": False, "message": "旧文件暂时无法安全备份，请稍后重试"}); return
                temporary_upload.replace(target)
                catalog_count = None if defer_catalog else rebuild_catalog(type(self))
            grouped_path = (Path(target_dir.name) / relative).as_posix()
            self.send_json(200, {"ok": True, "filename": filename, "relativePath": grouped_path, "uploadGroup": target_dir.name, "catalogCount": catalog_count, "catalogDeferred": defer_catalog, "replaced": len(replaced_paths), "replacedPaths": replaced_paths}); return
        if api_path == "/api/admin/announcement-image":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            filename = Path(unquote(self.headers.get("X-Filename", ""))).name
            length = int(self.headers.get("Content-Length", "0"))
            if Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"} or length < 1 or length > 5 * 1024 * 1024:
                self.send_json(400, {"ok": False, "message": "请选择 5MB 以内的 JPG、PNG、WEBP 或 GIF 图片"}); return
            safe_name = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4) + Path(filename).suffix.lower()
            image_dir = self.site_root / "assets" / "announcements"
            image_dir.mkdir(parents=True, exist_ok=True)
            (image_dir / safe_name).write_bytes(self.rfile.read(length))
            self.send_json(200, {"ok": True, "imageUrl": "/assets/announcements/" + safe_name}); return
        if api_path == "/api/admin/community-image":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            filename = Path(unquote(self.headers.get("X-Filename", ""))).name
            length = int(self.headers.get("Content-Length", "0"))
            if Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"} or length < 1 or length > 3 * 1024 * 1024:
                self.send_json(400, {"ok": False, "message": "请选择 3MB 以内的 JPG、PNG、WEBP 或 GIF 图片"}); return
            image_body = self.rfile.read(length)
            suffix = Path(filename).suffix.lower()
            signatures = {".jpg": image_body.startswith(b"\xff\xd8\xff"), ".jpeg": image_body.startswith(b"\xff\xd8\xff"), ".png": image_body.startswith(b"\x89PNG\r\n\x1a\n"), ".gif": image_body.startswith((b"GIF87a", b"GIF89a")), ".webp": image_body.startswith(b"RIFF") and image_body[8:12] == b"WEBP"}
            if len(image_body) != length or not signatures.get(suffix, False):
                self.send_json(400, {"ok": False, "message": "图片内容与文件格式不匹配，请重新选择图片"}); return
            safe_name = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4) + Path(filename).suffix.lower()
            image_dir = self.community_image_root
            image_dir.mkdir(parents=True, exist_ok=True)
            (image_dir / safe_name).write_bytes(image_body)
            self.send_json(200, {"ok": True, "imageUrl": "/api/community/image/" + safe_name}); return
        try:
            payload = self.read_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400)
            return
        if api_path == "/api/events":
            event_type = str(payload.get("type", ""))
            if event_type not in ("visit", "search", "download", "reading_error", "reading_feedback"):
                self.send_error(400); return
            if event_type.startswith('reading_') and not self.access_info():
                self.send_json(403, {'ok': False}); return
            self.save_event(event_type, str(payload.get("visitor", ""))[:80], str(payload.get("detail", ""))[:2000])
            self.send_json(200, {"ok": True}); return
        if api_path == "/api/reviews":
            body = str(payload.get("body", "")).strip()
            if len(body) < 10:
                self.send_json(400, {"ok": False}); return
            with open_database(self.db_path) as db:
                db.execute("INSERT INTO reviews(book,title,body,visitor,created_at) VALUES(?,?,?,?,?)", (str(payload.get("book", ""))[:300], str(payload.get("title", ""))[:300], body[:10000], str(payload.get("visitor", ""))[:80], datetime.now(timezone.utc).isoformat()))
            self.save_event("review", str(payload.get("visitor", "")), str(payload.get("book", "")))
            self.send_json(200, {"ok": True}); return
        if api_path == "/api/admin/reviews/reply":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            try:
                review_id = int(payload.get("id", 0))
            except (TypeError, ValueError):
                review_id = 0
            reply = str(payload.get("reply", "")).strip()
            if review_id < 1 or not reply or len(reply) > 3000:
                self.send_json(400, {"ok": False, "message": "回复内容需为 1 至 3000 个字"}); return
            with open_database(self.db_path) as db:
                updated = db.execute("UPDATE reviews SET admin_reply=?, replied_at=? WHERE id=?", (reply, datetime.now(timezone.utc).isoformat(), review_id)).rowcount
            if updated != 1:
                self.send_json(404, {"ok": False, "message": "这条留言已不存在"}); return
            self.send_json(200, {"ok": True, "reply": reply}); return
        if api_path == "/api/admin/reviews/delete":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            ids = payload.get("ids", [])
            if not isinstance(ids, list): self.send_json(400, {"ok": False, "message": "留言列表格式不正确"}); return
            ids = list(dict.fromkeys(int(value) for value in ids if str(value).isdigit()))[:500]
            if not ids: self.send_json(400, {"ok": False, "message": "请先选择留言"}); return
            placeholders = ",".join("?" for _ in ids)
            with open_database(self.db_path) as db:
                deleted = db.execute(f"DELETE FROM reviews WHERE id IN ({placeholders})", ids).rowcount
            self.send_json(200, {"ok": True, "deleted": deleted}); return
        if api_path == "/api/admin/requests/delete":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            ids = payload.get("ids", [])
            if not isinstance(ids, list): self.send_json(400, {"ok": False, "message": "登记列表格式不正确"}); return
            ids = list(dict.fromkeys(int(value) for value in ids if str(value).isdigit()))[:500]
            if not ids: self.send_json(400, {"ok": False, "message": "请先选择登记"}); return
            placeholders = ",".join("?" for _ in ids)
            with open_database(self.db_path) as db:
                deleted = db.execute(f"DELETE FROM book_requests WHERE id IN ({placeholders})", ids).rowcount
            self.send_json(200, {"ok": True, "deleted": deleted}); return
        if api_path == "/api/admin/announcement":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            title = str(payload.get("title", "")).strip()
            body = str(payload.get("body", "")).strip()
            image_url = str(payload.get("imageUrl", "")).strip()
            if not title or not body or len(title) > 80 or len(body) > 3000 or (image_url and not image_url.startswith("/assets/announcements/")):
                self.send_json(400, {"ok": False, "message": "请填写标题、内容，并使用本后台上传的图片"}); return
            announcement = {"id": secrets.token_hex(8), "title": title, "body": body, "imageUrl": image_url, "updatedAt": datetime.now(timezone.utc).isoformat()}
            with open_database(self.db_path) as db:
                row = db.execute("SELECT value FROM settings WHERE key='member_messages'").fetchone()
                try:
                    messages = json.loads(row[0]) if row else []
                except (ValueError, TypeError):
                    messages = []
                if not isinstance(messages, list): messages = []
                # Preserve the former single announcement when this is the first saved message.
                if not messages:
                    legacy = db.execute("SELECT value FROM settings WHERE key='member_announcement'").fetchone()
                    try:
                        old = json.loads(legacy[0]) if legacy else None
                    except (ValueError, TypeError):
                        old = None
                    if isinstance(old, dict) and old.get("title") and old.get("body"):
                        messages.append(old)
                messages.insert(0, announcement)
                db.execute("INSERT INTO settings(key,value) VALUES('member_messages',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(messages[:500], ensure_ascii=False),))
                db.execute("INSERT INTO settings(key,value) VALUES('member_announcement',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(announcement, ensure_ascii=False),))
            self.send_json(200, {"ok": True, "announcement": announcement}); return
        if api_path == "/api/admin/community/posts":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            title = str(payload.get("title", "")).strip()
            body = str(payload.get("body", "")).strip()
            image_url = str(payload.get("imageUrl", "")).strip()
            if not title or not body or len(title) > 80 or len(body) > 3000 or (image_url and not re.fullmatch(r"/api/community/image/[0-9]{14}-[a-f0-9]{8}\.(?:jpe?g|png|webp|gif)", image_url)):
                self.send_json(400, {"ok": False, "message": "请填写标题、感受，并使用本后台上传的图片"}); return
            with open_database(self.db_path) as db:
                cursor = db.execute("INSERT INTO community_posts(title,body,image_url,created_at) VALUES(?,?,?,?)", (title, body, image_url, datetime.now(timezone.utc).isoformat()))
            self.send_json(200, {"ok": True, "id": cursor.lastrowid}); return
        if api_path == "/api/community/comments":
            info = self.access_info()
            if not info or info["card_type"] != "reader": self.send_json(403, {"ok": False, "message": "需要有效阅读卡"}); return
            try:
                post_id = int(payload.get("postId", 0))
            except (TypeError, ValueError):
                post_id = 0
            body = str(payload.get("body", "")).strip()
            display_id = str(payload.get("displayId", "")).strip()
            if post_id < 1 or not body or len(body) > 500:
                self.send_json(400, {"ok": False, "message": "评论需为 1 至 500 个字"}); return
            if not re.fullmatch(r"[\w\u3400-\u9fff.-]{2,20}", display_id, flags=re.UNICODE):
                self.send_json(400, {"ok": False, "message": "广场 ID 请用 2 至 20 位中英文、数字或 - _ ."}); return
            token = self.download_session()
            visitor = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16] if token else "admin"
            with open_database(self.db_path) as db:
                exists = db.execute("SELECT 1 FROM community_posts WHERE id=?", (post_id,)).fetchone()
                if not exists: self.send_json(404, {"ok": False, "message": "这条动态已不存在"}); return
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                recent_comments = db.execute("SELECT COUNT(*) FROM community_comments WHERE visitor=? AND created_at>=?", (visitor, cutoff)).fetchone()[0]
                if recent_comments >= 20: self.send_json(429, {"ok": False, "message": "留言太频繁，请稍后再试"}); return
                cursor = db.execute("INSERT INTO community_comments(post_id,body,visitor,display_id,created_at) VALUES(?,?,?,?,?)", (post_id, body, visitor, display_id, datetime.now(timezone.utc).isoformat()))
            self.send_json(200, {"ok": True, "id": cursor.lastrowid}); return
        if api_path == "/api/admin/community/posts/delete":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            try: post_id = int(payload.get("id", 0))
            except (TypeError, ValueError): post_id = 0
            if post_id < 1: self.send_json(400, {"ok": False}); return
            with open_database(self.db_path) as db:
                image_row = db.execute("SELECT image_url FROM community_posts WHERE id=?", (post_id,)).fetchone()
                db.execute("DELETE FROM community_comments WHERE post_id=?", (post_id,))
                deleted = db.execute("DELETE FROM community_posts WHERE id=?", (post_id,)).rowcount
                image_url = image_row[0] if image_row else ""
                still_used = db.execute("SELECT 1 FROM community_posts WHERE image_url=? LIMIT 1", (image_url,)).fetchone() if image_url else None
            if deleted and image_url and not still_used:
                filename = image_url.rsplit("/", 1)[-1]
                if re.fullmatch(r"[0-9]{14}-[a-f0-9]{8}\.(?:jpe?g|png|webp|gif)", filename):
                    try: (self.community_image_root / filename).unlink(missing_ok=True)
                    except OSError: pass
            self.send_json(200, {"ok": True, "deleted": deleted}); return
        if api_path == "/api/admin/community/comments/delete":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            try: comment_id = int(payload.get("id", 0))
            except (TypeError, ValueError): comment_id = 0
            if comment_id < 1: self.send_json(400, {"ok": False}); return
            with open_database(self.db_path) as db:
                deleted = db.execute("DELETE FROM community_comments WHERE id=?", (comment_id,)).rowcount
            self.send_json(200, {"ok": True, "deleted": deleted}); return
        if api_path == "/api/admin/messages/delete":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            ids = payload.get("ids", [])
            if not isinstance(ids, list): self.send_json(400, {"ok": False, "message": "消息列表格式不正确"}); return
            ids = {str(value).strip() for value in ids if str(value).strip()}
            if not ids: self.send_json(400, {"ok": False, "message": "请先选择消息"}); return
            with open_database(self.db_path) as db:
                row = db.execute("SELECT value FROM settings WHERE key='member_messages'").fetchone()
                try:
                    messages = json.loads(row[0]) if row else []
                except (ValueError, TypeError):
                    messages = []
                messages = messages if isinstance(messages, list) else []
                kept = [message for message in messages if not isinstance(message, dict) or str(message.get("id", "")) not in ids]
                deleted = len(messages) - len(kept)
                db.execute("INSERT INTO settings(key,value) VALUES('member_messages',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(kept, ensure_ascii=False),))
                latest = kept[0] if kept else None
                db.execute("INSERT INTO settings(key,value) VALUES('member_announcement',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(latest, ensure_ascii=False),))
            self.send_json(200, {"ok": True, "deleted": deleted}); return
        if api_path == "/api/requests":
            request_text = str(payload.get("requestText", payload.get("body", ""))).strip()
            if request_text:
                if len(request_text) > 10000:
                    self.send_json(400, {"ok": False, "message": "内容不能超过 10000 字"}); return
                visitor = str(payload.get("visitor", ""))[:80]
                title = "批量缺书登记"
                with open_database(self.db_path) as db:
                    db.execute("INSERT INTO book_requests(title,author,edition,section,purpose,contact,note,visitor,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (title, "", "", "", "", "", request_text, visitor, datetime.now(timezone.utc).isoformat()))
                self.save_event("request", visitor, title)
                self.send_json(200, {"ok": True}); return
            title = str(payload.get("title", "")).strip()
            author = str(payload.get("author", "")).strip()
            if not title or not author:
                self.send_json(400, {"ok": False}); return
            visitor = str(payload.get("visitor", ""))[:80]
            with open_database(self.db_path) as db:
                db.execute("INSERT INTO book_requests(title,author,edition,section,purpose,contact,note,visitor,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (title[:300], author[:300], "", "", "", "", "", visitor, datetime.now(timezone.utc).isoformat()))
            self.save_event("request", visitor, title)
            self.send_json(200, {"ok": True}); return
        if api_path == "/api/admin/login":
            if self.auth_rate_limited("admin", 8):
                self.send_json(429, {"ok": False, "message": "尝试次数过多，请 10 分钟后再试", "retryAfter": 600}); return
            if not self.verify_admin_password(str(payload.get("code", ""))):
                self.record_auth_failure("admin")
                self.send_json(401, {"ok": False}); return
            self.clear_auth_failures("admin")
            expires = datetime.now(timezone.utc) + timedelta(days=7)
            cookie = f"cabin_admin={self.admin_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800; Expires={format_datetime(expires, usegmt=True)}"
            self.send_json(200, {"ok": True}, cookie); return
        if api_path == "/api/admin/password":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            new_password = str(payload.get("newPassword", ""))
            if len(new_password) < 8 or len(new_password) > 128:
                self.send_json(400, {"ok": False, "message": "密码需要 8 至 128 位"}); return
            digest = hashlib.sha256(new_password.encode("utf-8")).hexdigest()
            with open_database(self.db_path) as db:
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
            placeholders = ",".join("?" for _ in codes)
            with open_database(self.db_path) as db:
                rows = db.execute(f"SELECT id FROM access_cards WHERE code IN ({placeholders})", codes).fetchall()
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
            with open_database(self.db_path) as db:
                card_ids = [row[0] for row in db.execute(f"SELECT id FROM access_cards WHERE code IN ({placeholders}) AND active=1", codes)]
                if card_ids:
                    id_placeholders = ",".join("?" for _ in card_ids)
                    db.execute(f"UPDATE access_cards SET active=0 WHERE id IN ({id_placeholders})", card_ids)
                    db.execute(f"DELETE FROM download_sessions WHERE card_id IN ({id_placeholders})", card_ids)
            self.send_json(200, {"ok": True, "disabled": len(card_ids)}); return
        if api_path != "/api/unlock":
            self.send_error(404); return
        if self.auth_rate_limited("card", 20):
            self.send_json(429, {"ok": False, "message": "尝试次数过多，请 10 分钟后再试", "retryAfter": 600}); return
        code = str(payload.get("code", "")).strip()
        with open_database(self.db_path) as db:
            db.row_factory = sqlite3.Row
            card = db.execute("SELECT id, card_type, remaining, expires_at FROM access_cards WHERE code=? AND active=1", (code.upper(),)).fetchone()
            expired = bool(card and card["card_type"] == "reader" and card["expires_at"] and card["expires_at"] < datetime.now(timezone.utc).isoformat())
            if not card or expired or (card["card_type"] == "download" and card["remaining"] == 0):
                self.record_auth_failure("card")
                self.send_json(401, {"ok": False}); return
            self.clear_auth_failures("card")
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
        if request_path == "/api/recent":
            if not self.access_info(): self.send_json(403, {"ok": False}); return
            source = self.site_root / "assets" / "catalog.json"
            try:
                books = json.loads(source.read_text(encoding="utf-8"))
                books = [book for book in books if isinstance(book, dict) and book.get("title")]
                books.sort(key=lambda book: (str(book.get("modifiedAt", "")), str(book.get("searchablePath", ""))), reverse=True)
                self.send_json(200, {"items": books[:50]})
            except (OSError, ValueError, TypeError):
                self.send_json(200, {"items": []})
            return
        if request_path == "/api/type-requests":
            if not self.access_info(): self.send_json(403, {"ok": False}); return
            with open_database(self.db_path) as db:
                db.row_factory = sqlite3.Row
                items = [dict(row) for row in db.execute("SELECT id, book, title, body, created_at, admin_reply, replied_at FROM reviews ORDER BY id DESC LIMIT 80")]
            self.send_json(200, {"items": items}); return
        if request_path == "/api/announcement":
            if not self.access_info(): self.send_json(403, {"ok": False}); return
            with open_database(self.db_path) as db:
                row = db.execute("SELECT value FROM settings WHERE key='member_announcement'").fetchone()
            try:
                announcement = json.loads(row[0]) if row else None
            except (ValueError, TypeError):
                announcement = None
            self.send_json(200, {"announcement": announcement}); return
        if request_path in {"/api/messages", "/api/admin/messages"}:
            if request_path.startswith("/api/admin/"):
                if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            elif not self.access_info():
                self.send_json(403, {"ok": False}); return
            with open_database(self.db_path) as db:
                row = db.execute("SELECT value FROM settings WHERE key='member_messages'").fetchone()
            try:
                messages = json.loads(row[0]) if row else []
            except (ValueError, TypeError):
                messages = []
            self.send_json(200, {"items": messages if isinstance(messages, list) else []}); return
        if request_path in {"/api/community", "/api/admin/community"}:
            if request_path.startswith("/api/admin/"):
                if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            else:
                info = self.access_info()
                if not info or info["card_type"] != "reader": self.send_json(403, {"ok": False}); return
            with open_database(self.db_path) as db:
                db.row_factory = sqlite3.Row
                posts = [dict(row) for row in db.execute("SELECT id,title,body,image_url,created_at FROM community_posts ORDER BY id DESC LIMIT 40")]
                post_ids = [post["id"] for post in posts]
                comments_by_post = {post_id: [] for post_id in post_ids}
                if post_ids:
                    for post_id in post_ids:
                        rows = db.execute("SELECT id,post_id,body,visitor,display_id,created_at FROM community_comments WHERE post_id=? ORDER BY id DESC LIMIT 50", (post_id,))
                        comments = list(reversed([dict(row) for row in rows]))
                        for comment in comments:
                            comment["display_id"] = comment["display_id"] or ("读者" + str(comment.get("visitor", ""))[:4])
                            comment.pop("visitor", None)
                        comments_by_post[post_id] = comments
            for post in posts:
                post["comments"] = comments_by_post.get(post["id"], [])
            self.send_json(200, {"items": posts}); return
        if request_path == "/api/updates":
            source = self.site_root / "assets" / "catalog.json"
            try:
                books = json.loads(source.read_text(encoding="utf-8"))
                books = [book for book in books if isinstance(book, dict) and book.get("title") and book.get("modifiedAt")]
                books.sort(key=lambda book: (str(book.get("modifiedAt", "")), str(book.get("searchablePath", ""))), reverse=True)
                latest = books[:2]
                fingerprint = hashlib.sha256(json.dumps(
                    [(book.get("searchablePath", ""), book.get("modifiedAt", "")) for book in latest],
                    ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")).hexdigest()[:16] if latest else "empty"
                self.send_json(200, {"version": fingerprint, "items": [
                    {"title": book["title"], "category": book.get("category", "")}
                    for book in latest
                ]})
            except (OSError, ValueError, TypeError):
                self.send_json(200, {"version": "empty", "items": []})
            return
        if request_path == "/api/online/search":
            query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
            try:
                self.send_json(200, online_library.search(query, self.online_cache_root))
            except online_library.OnlineLibraryError as error:
                self.send_json(error.status, {"ok": False, "message": str(error)})
            return
        if request_path == "/api/catalog/search":
            query = parse_qs(urlsplit(self.path).query).get("q", [""])[0].strip().casefold()
            if not query or len(query) > 100:
                self.send_json(200, {"items": [], "total": 0}); return
            source = self.site_root / "assets" / "catalog.json"
            try:
                books = json.loads(source.read_text(encoding="utf-8"))
                items = [book for book in books if isinstance(book, dict) and query in " ".join(str(book.get(key, "")) for key in ("title", "author", "category", "searchablePath")).casefold()]
                self.send_json(200, {"items": items[:100], "total": len(items)})
            except (OSError, ValueError, TypeError):
                self.send_json(200, {"items": [], "total": 0})
            return
        if request_path == "/api/online/read":
            info = self.access_info()
            if not info or info["card_type"] != "reader":
                self.send_json(403, {"ok": False, "message": "需要有效阅读卡"}); return
            title = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            try:
                self.send_online_text(online_library.read(title, self.online_cache_root))
            except online_library.OnlineLibraryError as error:
                self.send_json(error.status, {"ok": False, "message": str(error)})
            return
        if request_path == '/api/admin/reading-errors':
            if not self.has_admin_access():
                self.send_json(403, {'ok': False}); return
            with open_database(self.db_path) as db:
                db.row_factory = sqlite3.Row
                rows = [dict(row) for row in db.execute("SELECT type,detail,created_at FROM events WHERE type IN ('reading_error','reading_feedback') ORDER BY id DESC LIMIT 100")]
            self.send_json(200, {'items': rows}); return
        if request_path == '/api/admin/audit':
            if not self.has_admin_access():
                self.send_json(403, {'ok': False}); return
            self.send_json(200, library_audit.report(type(self))); return
        if request_path == '/api/admin/replacements':
            if not self.has_admin_access():
                self.send_json(403, {'ok': False}); return
            items = []
            for path in self.replaced_root.rglob('*'):
                if path.is_file() and path.suffix.lower() in BOOK_EXTENSIONS:
                    relative = path.relative_to(self.replaced_root)
                    items.append({'id': relative.as_posix(), 'title': path.name, 'original': Path(*relative.parts[1:]).as_posix(), 'time': relative.parts[0]})
            self.send_json(200, {'items': sorted(items, key=lambda item: item['time'], reverse=True)[:200]}); return
        if request_path == "/api/admin/data":
            if not self.has_admin_access(): self.send_json(403, {"ok": False}); return
            self.send_json(200, self.admin_data()); return
        if request_path == "/health":
            self.send_json(200, {"ok": True}); return
        if request_path.startswith("/api/community/image/"):
            info = self.access_info()
            if not info or info["card_type"] != "reader": self.send_error(403, "Reader card required"); return
            filename = request_path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[0-9]{14}-[a-f0-9]{8}\.(?:jpe?g|png|webp|gif)", filename):
                self.send_error(404); return
            source = self.community_image_root / filename
            if not source.is_file(): self.send_error(404); return
            try:
                content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
                size = source.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "private, max-age=300")
                self.end_headers()
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile, length=1024 * 128)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            return
        if request_path == "/api/access":
            info = self.access_info()
            expires_in_seconds = None
            if info and info["card_type"] == "reader" and info["expires_at"]:
                try:
                    expires_in_seconds = max(0, int((datetime.fromisoformat(info["expires_at"]) - datetime.now(timezone.utc)).total_seconds()))
                except (TypeError, ValueError):
                    expires_in_seconds = None
            self.send_json(200, {"ok": bool(info), "cardType": info["card_type"] if info else "", "remaining": info["remaining"] if info else 0, "expiresAt": info["expires_at"] if info else "", "expiresInSeconds": expires_in_seconds}); return
        protected_pages = {
            "/pages/search.html", "/pages/request.html",
            "/pages/review.html", "/pages/bookshelf.html", "/pages/reader.html",
            "/pages/recent.html", "/pages/messages.html", "/pages/community.html",
        }
        page_access = self.access_info() if request_path in protected_pages else None
        if request_path in protected_pages and (not page_access or (request_path == "/pages/community.html" and page_access["card_type"] != "reader")):
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
            except OSError:
                self.send_error(404, "Catalog unavailable")
                return
            use_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower() and stat.st_size > 1024
            try:
                payload = self.cached_gzip_file(source, "catalog") if use_gzip else source
            except OSError:
                self.send_error(503, "Catalog cache unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(payload.stat().st_size))
            self.end_headers()
            with payload.open("rb") as stream:
                try:
                    shutil.copyfileobj(stream, self.wfile, length=1024 * 256)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            return
        external_member_destinations = {
            "/go/ao3": "https://aoya.moe/works/search?page=1&view_adult=true&work_search%5Bquery%5D=%E7%A6%85%E9%99%A2%E7%9B%B4%E5%93%89",
            "/go/comics": "https://appgu.cc/rank/",
        }
        if request_path in external_member_destinations:
            if not self.access_info():
                self.send_response(302)
                self.send_header("Location", "/pages/home.html?card=required")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self.send_response(302)
            self.send_header("Location", external_member_destinations[request_path])
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
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
            if not source.is_file():
                self.send_error(404, "Book file unavailable")
                return
            if source.suffix.lower() == ".txt":
                try:
                    raw = source.read_bytes()
                    body = b"\xef\xbb\xbf" + decode_book_text(raw).encode("utf-8")
                except (OSError, ValueError) as error:
                    self.send_error(422, str(error))
                    return
                # Keep the .txt extension and UTF-8 bytes, but tell mobile browsers
                # this is a file attachment so they do not render it as a reader page.
                content_type = "application/octet-stream"
            else:
                body = None
                content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            try:
                if body is None:
                    stream = source.open("rb")
                    content_length = os.fstat(stream.fileno()).st_size
                else:
                    stream = None
                    content_length = len(body)
            except OSError:
                self.send_error(503, "Book file temporarily unavailable")
                return
            reservation = self.reserve_download()
            if not reservation:
                if stream is not None:
                    stream.close()
                self.send_error(403, "Download limit exhausted")
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Disposition", "attachment; filename=book%s; filename*=UTF-8''%s" % (source.suffix, quote(source.name)))
                self.send_header("Content-Length", str(content_length))
                self.end_headers()
                if body is not None:
                    self.wfile.write(body)
                else:
                    sent = 0
                    while True:
                        chunk = stream.read(1024 * 256)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        sent += len(chunk)
                    if sent != content_length:
                        raise OSError("incomplete file read")
                self.wfile.flush()
                self.complete_download(reservation)
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.refund_download(reservation)
            finally:
                if stream is not None:
                    stream.close()
            return
        if request_path.startswith("/read/"):
            info = self.access_info()
            if not info or info["card_type"] != "reader": self.send_error(403, "Reader card required"); return
            if Path(request_path).suffix.lower() in {".txt", ".docx", ".epub", ".zip"}:
                try:
                    self.send_cached_text(Path(self.translate_path(self.path)))
                except FileNotFoundError:
                    self.send_error(404, "Book text unavailable")
                except (UnicodeError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
                    self.send_error(422, "Book text could not be parsed")
                except OSError:
                    self.send_error(503, "Book storage temporarily unavailable")
                return
            if Path(request_path).suffix.lower() == ".pdf":
                source = Path(self.translate_path(self.path))
                if not source.is_file():
                    self.send_error(404, "Book file unavailable")
                    return
                self.send_file_range(source, "application/pdf")
                return
            self.send_error(415, "This format is not available for online reading")
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
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")
        self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; frame-src 'self'")
        if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        request_path = urlsplit(self.path).path
        if request_path == "/assets/catalog.json":
            self.send_header("Cache-Control", "private, no-cache, max-age=0, must-revalidate")
        elif request_path.startswith("/read/"):
            # Reader responses set their own private cache policy and ETag.
            pass
        elif request_path.startswith("/api/community/image/"):
            # Community images are private to active reader sessions.
            pass
        elif request_path == "/api/online/read":
            # Online reader responses set their own private cache policy and ETag.
            pass
        elif request_path.startswith("/assets/") or request_path.endswith((".css", ".js")):
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


BOOK_EXTENSIONS = {".txt", ".docx", ".zip", ".rar", ".7z", ".pdf", ".epub", ".mobi"}


def normalized_book_title(path):
    title = path.stem.strip()
    # Windows/browser downloads routinely append (1), （2）, empty brackets or
    # “副本”.  Remove only these trailing copy markers; meaningful brackets in
    # the actual title (for example 1V1) must stay intact.
    copy_suffix = r"(?:\s*(?:[（(]\s*\d*\s*[）)]|[-_ ]?(?:副本|复制|copy)(?:\s*\d+)?))+$"
    previous = None
    while title != previous:
        previous = title
        title = re.sub(copy_suffix, "", title, flags=re.IGNORECASE).strip()
    return title


def duplicate_title_key(value):
    """Return a conservative identity key for true copies of one book.

    Do not use partial/five-character overlap here: it deletes distinct books
    from series and books whose titles share a common opening phrase.
    """
    title = normalized_book_title(Path(value)).casefold()
    title = re.sub(r"(?:作者|author)\s*[:：].*$", "", title, flags=re.IGNORECASE)
    status = r"(?:完结|已完结|完本|全文(?:完)?|精校版?|校对版?|补档|更新至[^\]】]{1,16}|更\s*\d+)"
    title = re.sub(r"^(?:\s*[\[【]\s*" + status + r"\s*[\]】]\s*)+", "", title)
    title = re.sub(r"[丨|].{0,80}(?:作品集|作者合集|爱发电).*$", "", title)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", title)


def titles_overlap_enough(left, right):
    return bool(left and right and left == right)


def find_duplicate_books(root, filename, body=None):
    identity = duplicate_title_key(filename)
    digest = hashlib.sha256(body).digest() if body is not None else None
    matches = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in BOOK_EXTENSIONS:
            same_content = False
            if body is not None:
                try:
                    same_content = path.stat().st_size == len(body) and hashlib.sha256(path.read_bytes()).digest() == digest
                except OSError:
                    pass
            if same_content or titles_overlap_enough(identity, duplicate_title_key(path)):
                matches.append(path)
    return matches


def archive_replaced_book(handler, path):
    """Move a superseded copy out of the live library without destroying it."""
    try:
        relative = path.resolve().relative_to(handler.textbook_root.resolve())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = handler.replaced_root / stamp / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
        return destination
    except (OSError, ValueError):
        return None


def rebuild_catalog(handler):
    extensions = BOOK_EXTENSIONS
    with handler.catalog_build_lock:
        try:
            files = [p for p in handler.textbook_root.rglob("*") if p.is_file() and p.suffix.lower() in extensions and not {"novel-library", "node_modules", ".git"}.intersection(p.parts)]
            newest_by_title = {}
            for path in files:
                try:
                    stat = path.stat()
                    relative = path.relative_to(handler.textbook_root)
                except (OSError, ValueError):
                    continue
                key = duplicate_title_key(path.name) or normalized_book_title(path).casefold()
                copy_marked = bool(re.search(r"(?:[（(]\s*\d*\s*[）)]|副本|复制|copy)\s*$", path.stem, re.IGNORECASE))
                rank = (stat.st_mtime_ns, not copy_marked, -len(relative.parts), relative.as_posix())
                current = newest_by_title.get(key)
                if current is None or rank > current[0]:
                    newest_by_title[key] = (rank, path, stat)
            items = []
            for _, path, stat in newest_by_title.values():
                relative = path.relative_to(handler.textbook_root).as_posix()
                title = normalized_book_title(path) or path.stem
                category_path = Path(relative).parent
                category = "uncategorized" if str(category_path) == "." else " / ".join(category_path.parts)
                items.append({"title": title, "category": category, "format": path.suffix.lstrip(".").lower(), "size": stat.st_size, "modifiedAt": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"), "searchablePath": relative})
            def catalog_sort_key(item):
                match = re.search(r"(?:^| / )新上传(\d+)(?: / |$)", item["category"])
                # New-upload folders are intentionally shown first; the newest numbered folder leads.
                return (0, -int(match.group(1)), item["title"], item["searchablePath"]) if match else (1, 0, item["title"], item["searchablePath"])
            items.sort(key=catalog_sort_key)
            output = handler.site_root / "assets" / "catalog.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temporary.replace(output)
            return len(items)
        except (OSError, ValueError):
            return 0


def watch_textbook(handler):
    extensions = BOOK_EXTENSIONS
    previous = None
    while True:
        try:
            files = [p for p in handler.textbook_root.rglob("*") if p.is_file() and p.suffix.lower() in extensions and not {"novel-library", "node_modules", ".git"}.intersection(p.parts)]
            signature = tuple(sorted((str(p.relative_to(handler.textbook_root)), p.stat().st_size, p.stat().st_mtime_ns) for p in files))
            if signature != previous:
                rebuild_catalog(handler)
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
    LibraryHandler.online_cache_root = data_dir / "online-cache"
    LibraryHandler.online_cache_root.mkdir(parents=True, exist_ok=True)
    LibraryHandler.community_image_root = data_dir / "community-images"
    LibraryHandler.community_image_root.mkdir(parents=True, exist_ok=True)
    LibraryHandler.replaced_root = data_dir / "replaced-books"
    LibraryHandler.replaced_root.mkdir(parents=True, exist_ok=True)
    with open_database(LibraryHandler.db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, type TEXT, visitor TEXT, detail TEXT, created_at TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, book TEXT, title TEXT, body TEXT, visitor TEXT, created_at TEXT, admin_reply TEXT NOT NULL DEFAULT '', replied_at TEXT NOT NULL DEFAULT '')")
        db.execute("CREATE TABLE IF NOT EXISTS book_requests(id INTEGER PRIMARY KEY, title TEXT, author TEXT, edition TEXT, section TEXT, purpose TEXT, contact TEXT, note TEXT, visitor TEXT, created_at TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS community_posts(id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS community_comments(id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, body TEXT NOT NULL, visitor TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, FOREIGN KEY(post_id) REFERENCES community_posts(id))")
        comment_columns = {row[1] for row in db.execute("PRAGMA table_info(community_comments)")}
        if "display_id" not in comment_columns: db.execute("ALTER TABLE community_comments ADD COLUMN display_id TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE INDEX IF NOT EXISTS community_comments_post_id ON community_comments(post_id, id)")
        db.execute("CREATE TABLE IF NOT EXISTS access_cards(id INTEGER PRIMARY KEY, code TEXT UNIQUE, download_limit INTEGER NOT NULL, remaining INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS download_sessions(token TEXT PRIMARY KEY, card_id INTEGER NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT, FOREIGN KEY(card_id) REFERENCES access_cards(id))")
        db.execute("CREATE TABLE IF NOT EXISTS download_reservations(id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL, token TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(card_id) REFERENCES access_cards(id))")
        db.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(access_cards)")}
        if "card_type" not in columns: db.execute("ALTER TABLE access_cards ADD COLUMN card_type TEXT NOT NULL DEFAULT 'download'")
        if "expires_at" not in columns: db.execute("ALTER TABLE access_cards ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''")
        review_columns = {row[1] for row in db.execute("PRAGMA table_info(reviews)")}
        if "admin_reply" not in review_columns: db.execute("ALTER TABLE reviews ADD COLUMN admin_reply TEXT NOT NULL DEFAULT ''")
        if "replied_at" not in review_columns: db.execute("ALTER TABLE reviews ADD COLUMN replied_at TEXT NOT NULL DEFAULT ''")
        pending_downloads = [row[0] for row in db.execute("SELECT id FROM download_reservations")]
        for reservation_id in pending_downloads:
            pending = db.execute("SELECT card_id FROM download_reservations WHERE id=?", (reservation_id,)).fetchone()
            if pending:
                db.execute("UPDATE access_cards SET remaining=MIN(download_limit, remaining+1), used=MAX(0, used-1) WHERE id=?", (pending[0],))
                db.execute("DELETE FROM download_reservations WHERE id=?", (reservation_id,))
        now = datetime.now(timezone.utc)
        stale_session = (now - timedelta(days=45)).isoformat()
        db.execute("DELETE FROM download_sessions WHERE created_at < ? OR card_id NOT IN (SELECT id FROM access_cards WHERE active=1 AND (card_type <> 'reader' OR expires_at='' OR expires_at >= ?))", (stale_session, now.isoformat()))
        initial_admin_hash = hashlib.sha256(LibraryHandler.admin_key.encode("utf-8")).hexdigest()
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('admin_password_hash',?)", (initial_admin_hash,))
    threading.Thread(target=watch_textbook, args=(LibraryHandler,), daemon=True).start()
    server = LibraryHTTPServer(("0.0.0.0", args.port), LibraryHandler)
    print(f"Serving on port {args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
