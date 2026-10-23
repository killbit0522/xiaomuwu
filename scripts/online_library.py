"""A small, isolated multi-source reader for openly readable texts."""
import hashlib
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

try:
    from .book_sources import BookSourceAdapter, BookSourceRegistry
except ImportError:
    from book_sources import BookSourceAdapter, BookSourceRegistry

API_URL = "https://zh.wikisource.org/w/api.php"
SOURCE_NAME = "中文维基文库"
GUTENDEX_URL = "https://gutendex.com/books"
GUTENBERG_SOURCE = "Project Gutenberg（公版书）"
OPEN_LIBRARY_URL = "https://openlibrary.org/search.json"
ARCHIVE_METADATA_URL = "https://archive.org/metadata/"
ARCHIVE_SOURCE = "Open Library / Internet Archive（公开阅读）"
USER_AGENT = "XiaomuwuReader/1.0 (https://xiaomuwuread.top/)"
MAX_RESPONSE = 6 * 1024 * 1024
lock = threading.Lock()
slots = threading.BoundedSemaphore(4)


class OnlineLibraryError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class _ReadableHTML(HTMLParser):
    block_tags = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "blockquote", "tr"}
    ignored = {"script", "style", "noscript", "table", "nav"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            self.skip += 1
        elif not self.skip and tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.ignored and self.skip:
            self.skip -= 1
        elif not self.skip and tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        value = html.unescape("".join(self.parts)).replace("\xa0", " ")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def _request_json(params):
    url = API_URL + "?" + urlencode(dict(params, format="json", formatversion="2"))
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with slots, urlopen(request, timeout=10) as response:
            if response.headers.get_content_type() != "application/json":
                raise OnlineLibraryError(502, "公开书源返回了无法识别的内容")
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        raise OnlineLibraryError(503, "公开书源暂时不可用") from error
    except (URLError, TimeoutError, OSError) as error:
        raise OnlineLibraryError(503, "连接公开书源超时") from error
    if len(raw) > MAX_RESPONSE:
        raise OnlineLibraryError(413, "公开书源返回的内容过大")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise OnlineLibraryError(502, "公开书源数据解析失败") from error


def _request_gutendex(params):
    url = GUTENDEX_URL + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with slots, urlopen(request, timeout=5) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        raise OnlineLibraryError(503, "Project Gutenberg 书源暂时不可用") from error
    except (URLError, TimeoutError, OSError) as error:
        raise OnlineLibraryError(503, "连接 Project Gutenberg 超时") from error
    if len(raw) > MAX_RESPONSE:
        raise OnlineLibraryError(413, "Project Gutenberg 返回内容过大")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise OnlineLibraryError(502, "Project Gutenberg 数据解析失败") from error


def _request_open_library(params):
    url = OPEN_LIBRARY_URL + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with slots, urlopen(request, timeout=7) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        raise OnlineLibraryError(503, "Open Library 书源暂时不可用") from error
    except (URLError, TimeoutError, OSError) as error:
        raise OnlineLibraryError(503, "连接 Open Library 超时") from error
    if len(raw) > MAX_RESPONSE:
        raise OnlineLibraryError(413, "Open Library 返回内容过大")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise OnlineLibraryError(502, "Open Library 数据解析失败") from error


def _gutenberg_items(query, requester):
    payload = requester({"search": query, "languages": "zh", "copyright": "false", "mime_type": "text/plain"})
    items = []
    for row in payload.get("results", [])[:12]:
        book_id = row.get("id")
        title = str(row.get("title", "")).strip()
        if not isinstance(book_id, int) or not title:
            continue
        authors = "、".join(str(author.get("name", "")).strip() for author in row.get("authors", []) if isinstance(author, dict) and author.get("name"))
        summary = next((str(value).strip() for value in row.get("summaries", []) if str(value).strip()), "")
        snippet = (authors + (" · " if authors and summary else "") + summary)[:240] or "Project Gutenberg 公版电子书"
        items.append({"id": f"gutenberg:{book_id}", "title": title, "snippet": snippet, "source": GUTENBERG_SOURCE, "sourceUrl": f"https://www.gutenberg.org/ebooks/{book_id}"})
    return items


def _wikisource_items(query, requester):
    payload = requester({"action": "query", "list": "search", "srsearch": query, "srnamespace": "0", "srlimit": "15", "srprop": "snippet|size"})
    items = []
    for row in payload.get("query", {}).get("search", [])[:15]:
        title = str(row.get("title", "")).strip()
        if not title:
            continue
        parser = _ReadableHTML(); parser.feed(str(row.get("snippet", "")))
        items.append({"id": title, "title": title, "snippet": parser.text()[:240], "source": SOURCE_NAME, "sourceUrl": "https://zh.wikisource.org/wiki/" + title.replace(" ", "_")})
    return items


def _open_library_items(query, requester):
    payload = requester({
        "q": query,
        "limit": "20",
        "fields": "key,title,author_name,first_publish_year,public_scan_b,ia,language",
    })
    items = []
    for row in payload.get("docs", [])[:20]:
        if not isinstance(row, dict) or row.get("public_scan_b") is not True:
            continue
        archive_ids = row.get("ia") or []
        archive_id = next((str(value).strip() for value in archive_ids if re.fullmatch(r"[A-Za-z0-9._-]{1,100}", str(value).strip())), "")
        title = str(row.get("title", "")).strip()
        if not archive_id or not title:
            continue
        authors = "、".join(str(value).strip() for value in (row.get("author_name") or [])[:3] if str(value).strip())
        year = str(row.get("first_publish_year") or "").strip()
        snippet = " · ".join(value for value in (authors, year and "初版 " + year, "可公开阅读全文") if value)
        work_key = str(row.get("key") or "").strip()
        source_url = "https://openlibrary.org" + work_key if work_key.startswith("/works/") else "https://archive.org/details/" + archive_id
        items.append({
            "id": "archive:" + archive_id,
            "title": title[:200],
            "snippet": snippet[:240],
            "source": ARCHIVE_SOURCE,
            "sourceUrl": source_url,
        })
        if len(items) >= 10:
            break
    return items


def _cache_path(root, prefix, value, suffix):
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return Path(root) / f"{prefix}-{digest}.{suffix}"


def _read_json_cache(path, lifetime):
    try:
        if time.time() - path.stat().st_mtime <= lifetime:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def _write_atomic(path, content, binary=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if binary:
        temporary.write_bytes(content)
    else:
        temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _plain_wikitext(value):
    """Turn ordinary Wikisource chapter markup into readable plain text."""
    value = re.sub(r"<!--[\s\S]*?-->", "", str(value or ""))
    value = re.sub(r"<ref\b[^>]*>[\s\S]*?</ref\s*>", "", value, flags=re.I)
    value = re.sub(r"<ref\b[^>]*/\s*>", "", value, flags=re.I)
    value = re.sub(r"\[\[(?:File|Image|Category|文件|圖像|图像|分類|分类):[^\]]+\]\]", "", value, flags=re.I)
    # Keep the visible value of common one-line formatting templates.
    value = re.sub(r"\{\{(?:center|left|right|larger|smaller|粗體|粗体)\|(?:[^{}|]*\|)*([^{}|]+)\}\}", r"\1", value, flags=re.I)
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[(?:https?://\S+)\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"'{2,5}", "", value)
    value = re.sub(r"^\s*=+\s*(.*?)\s*=+\s*$", r"\1", value, flags=re.M)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</?(?:poem|div|span|p|section|blockquote)[^>]*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _subpage_texts(titles, requester):
    contents = {}
    for start in range(0, len(titles), 40):
        batch = titles[start:start + 40]
        payload = requester({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "redirects": "1", "titles": "|".join(batch)})
        for page in payload.get("query", {}).get("pages", []):
            revisions = page.get("revisions") or []
            main = revisions[0].get("slots", {}).get("main", {}) if revisions else {}
            raw = main.get("content", main.get("*", ""))
            if raw:
                contents[str(page.get("title", ""))] = _plain_wikitext(raw)
    parts = []
    for title in titles:
        text = contents.get(title, "").strip()
        if text:
            parts.append(title.rsplit("/", 1)[-1] + "\n\n" + text)
        if sum(len(part) for part in parts) > 5_000_000:
            break
    return "\n\n".join(parts)


def search(query, cache_root, requester=None, gutenberg_requester=None, openlibrary_requester=None):
    query = re.sub(r"\s+", " ", str(query or "")).strip()[:80]
    if len(query) < 1:
        raise OnlineLibraryError(400, "请输入书名或作者")
    cache = _cache_path(cache_root, "search", query.casefold(), "json")
    cached = _read_json_cache(cache, 15 * 60)
    if cached is not None:
        return dict(cached, cached=True)
    wiki_requester = requester or _request_json
    include_gutenberg = requester is None or gutenberg_requester is not None
    include_openlibrary = requester is None or openlibrary_requester is not None
    items, source_errors = [], []
    registry = BookSourceRegistry([
        BookSourceAdapter("wikisource", SOURCE_NAME, lambda value: _wikisource_items(value, wiki_requester)),
    ])
    if include_gutenberg:
        registry.register(BookSourceAdapter("gutenberg", GUTENBERG_SOURCE, lambda value: _gutenberg_items(value, gutenberg_requester or _request_gutendex)))
    if include_openlibrary:
        registry.register(BookSourceAdapter("archive", ARCHIVE_SOURCE, lambda value: _open_library_items(value, openlibrary_requester or _request_open_library)))
    providers = registry.adapters()
    provider_items = {}
    with ThreadPoolExecutor(max_workers=len(providers), thread_name_prefix="online-book-source") as executor:
        pending = {executor.submit(adapter.searcher, query): adapter for adapter in providers}
        for future in as_completed(pending):
            adapter = pending[future]
            try:
                provider_items[adapter.key] = future.result()
            except OnlineLibraryError as error:
                source_errors.append(adapter.name + "：" + str(error))
            except Exception:
                source_errors.append(adapter.name + "：暂时不可用")
    for adapter in providers:
        items.extend(provider_items.get(adapter.key, []))
    if not items and source_errors:
        try:
            stale = json.loads(cache.read_text(encoding="utf-8"))
            return dict(stale, cached=True, stale=True)
        except (OSError, ValueError):
            raise OnlineLibraryError(503, "所有公开书源暂时不可用，请稍后重试")
    unique = []
    seen = set()
    for item in items:
        key = (item["source"], item["id"])
        if key not in seen:
            seen.add(key); unique.append(item)
    result = {
        "query": query,
        "items": unique[:30],
        "source": "多书源",
        "sources": [adapter.name for adapter in providers],
        "sourceErrors": source_errors,
        "cached": False,
        "partial": bool(source_errors),
    }
    with lock:
        _write_atomic(cache, json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return result


def _read_wikisource(title, cache_root, requester=None):
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:200]
    if not title or any(character in title for character in "\r\n\0"):
        raise OnlineLibraryError(400, "书目编号无效")
    text_path = _cache_path(cache_root, "book", title, "txt")
    meta_path = _cache_path(cache_root, "book", title, "json")
    fresh = False
    try:
        fresh = text_path.is_file() and time.time() - text_path.stat().st_mtime <= 7 * 86400
    except OSError:
        pass
    if fresh:
        return {"title": title, "path": text_path, "source": SOURCE_NAME, "sourceUrl": "https://zh.wikisource.org/wiki/" + title.replace(" ", "_"), "cached": True}
    requester = requester or _request_json
    try:
        payload = requester({"action": "parse", "page": title, "prop": "text|links", "redirects": "1", "disableeditsection": "1", "disablelimitreport": "1"})
        parsed = payload.get("parse", {})
        raw_html = parsed.get("text", "")
        if isinstance(raw_html, dict):
            raw_html = raw_html.get("*", "")
        parser = _ReadableHTML(); parser.feed(str(raw_html))
        text = parser.text()
        canonical = str(parsed.get("title") or title).strip()[:200]
        links = []
        for item in parsed.get("links", []):
            linked = str(item.get("title", "") if isinstance(item, dict) else item).strip()
            if linked.startswith(canonical + "/") and linked not in links:
                links.append(linked)
        if len(text) < 2000 and links:
            combined = _subpage_texts(links[:300], requester)
            if len(combined) >= 40:
                text = combined
        if len(text) < 40:
            raise OnlineLibraryError(422, "该公开书目暂时没有可阅读正文")
        source_url = "https://zh.wikisource.org/wiki/" + canonical.replace(" ", "_")
        with lock:
            _write_atomic(text_path, text)
            _write_atomic(meta_path, json.dumps({"title": canonical, "source": SOURCE_NAME, "sourceUrl": source_url}, ensure_ascii=False))
        return {"title": canonical, "path": text_path, "source": SOURCE_NAME, "sourceUrl": source_url, "cached": False}
    except OnlineLibraryError:
        if text_path.is_file():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                metadata = {"title": title, "source": SOURCE_NAME, "sourceUrl": "https://zh.wikisource.org/wiki/" + title.replace(" ", "_")}
            return dict(metadata, path=text_path, cached=True, stale=True)
        raise


def _allowed_gutenberg_url(value):
    parsed = urlsplit(str(value or ""))
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "gutenberg.org" or host.endswith(".gutenberg.org"))


def _download_gutenberg_text(url):
    if not _allowed_gutenberg_url(url):
        raise OnlineLibraryError(502, "公版书正文地址不安全")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain"})
    try:
        with slots, urlopen(request, timeout=18) as response:
            if response.headers.get_content_maintype() != "text":
                raise OnlineLibraryError(502, "公版书没有可识别的纯文本正文")
            raw = response.read(MAX_RESPONSE + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
        raise OnlineLibraryError(503, "公版书正文暂时不可用") from error
    except (URLError, TimeoutError, OSError) as error:
        raise OnlineLibraryError(503, "公版书正文连接超时") from error
    if len(raw) > MAX_RESPONSE:
        raise OnlineLibraryError(413, "公版书正文过大")
    for encoding in (charset, "utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeError):
            continue
    raise OnlineLibraryError(502, "公版书正文编码无法识别")


def _read_gutenberg(book_id, cache_root, metadata_requester=None, content_requester=None):
    if not re.fullmatch(r"[1-9]\d{0,8}", str(book_id or "")):
        raise OnlineLibraryError(400, "公版书编号无效")
    identifier = "gutenberg:" + str(book_id)
    text_path = _cache_path(cache_root, "book", identifier, "txt")
    meta_path = _cache_path(cache_root, "book", identifier, "json")
    try:
        if text_path.is_file() and time.time() - text_path.stat().st_mtime <= 7 * 86400:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            return dict(metadata, path=text_path, cached=True)
    except (OSError, ValueError):
        pass
    try:
        requester = metadata_requester or _request_gutendex
        payload = requester({"ids": str(book_id), "copyright": "false"})
        rows = payload.get("results", [])
        if not rows:
            raise OnlineLibraryError(404, "没有找到这本公版书")
        row = rows[0]
        title = str(row.get("title", "")).strip() or ("公版书 " + str(book_id))
        formats = row.get("formats", {}) if isinstance(row.get("formats"), dict) else {}
        candidates = [(kind, url) for kind, url in formats.items() if str(kind).lower().startswith("text/plain") and _allowed_gutenberg_url(url) and not str(url).lower().endswith(".zip")]
        candidates.sort(key=lambda pair: ("utf-8" not in pair[0].lower(), pair[0]))
        if not candidates:
            raise OnlineLibraryError(422, "该公版书暂时没有纯文本正文")
        text = (content_requester or _download_gutenberg_text)(candidates[0][1])
        if len(text.strip()) < 40:
            raise OnlineLibraryError(422, "该公版书正文过短")
        metadata = {"title": title[:200], "source": GUTENBERG_SOURCE, "sourceUrl": f"https://www.gutenberg.org/ebooks/{book_id}"}
        with lock:
            _write_atomic(text_path, text)
            _write_atomic(meta_path, json.dumps(metadata, ensure_ascii=False))
        return dict(metadata, path=text_path, cached=False)
    except OnlineLibraryError:
        if text_path.is_file():
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                metadata = {"title": "公版书 " + str(book_id), "source": GUTENBERG_SOURCE, "sourceUrl": f"https://www.gutenberg.org/ebooks/{book_id}"}
            return dict(metadata, path=text_path, cached=True, stale=True)
        raise


def _allowed_archive_url(value):
    parsed = urlsplit(str(value or ""))
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == "archive.org"


def _request_archive_metadata(archive_id):
    request = Request(ARCHIVE_METADATA_URL + archive_id, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with slots, urlopen(request, timeout=10) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        raise OnlineLibraryError(503, "Internet Archive 书源暂时不可用") from error
    except (URLError, TimeoutError, OSError) as error:
        raise OnlineLibraryError(503, "连接 Internet Archive 超时") from error
    if len(raw) > MAX_RESPONSE:
        raise OnlineLibraryError(413, "Internet Archive 元数据过大")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise OnlineLibraryError(502, "Internet Archive 元数据解析失败") from error


def _download_archive_text(url):
    if not _allowed_archive_url(url):
        raise OnlineLibraryError(502, "公开书正文地址不安全")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain"})
    try:
        with slots, urlopen(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        raise OnlineLibraryError(503, "公开书正文暂时不可用") from error
    except (URLError, TimeoutError, OSError) as error:
        raise OnlineLibraryError(503, "公开书正文连接超时") from error
    if len(raw) > MAX_RESPONSE:
        raise OnlineLibraryError(413, "公开书正文过大")
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise OnlineLibraryError(502, "公开书正文编码无法识别")


def _read_archive(archive_id, cache_root, metadata_requester=None, content_requester=None):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", str(archive_id or "")):
        raise OnlineLibraryError(400, "公开书编号无效")
    identifier = "archive:" + archive_id
    text_path = _cache_path(cache_root, "book", identifier, "txt")
    meta_path = _cache_path(cache_root, "book", identifier, "json")
    try:
        if text_path.is_file() and time.time() - text_path.stat().st_mtime <= 7 * 86400:
            return dict(json.loads(meta_path.read_text(encoding="utf-8")), path=text_path, cached=True)
    except (OSError, ValueError):
        pass
    try:
        payload = (metadata_requester or _request_archive_metadata)(archive_id)
        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        title = str(metadata.get("title") or ("公开书 " + archive_id)).strip()[:200]
        candidates = []
        for item in payload.get("files", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or "/" in name or "\\" in name:
                continue
            lowered = name.lower()
            if not (lowered.endswith("_djvu.txt") or lowered.endswith(".txt")):
                continue
            if any(token in lowered for token in ("readme", "meta", "files.xml", "scandata")):
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if size and size > MAX_RESPONSE:
                continue
            candidates.append((not lowered.endswith("_djvu.txt"), size == 0, size, name))
        if not candidates:
            raise OnlineLibraryError(422, "该公开书暂时没有可识别的纯文本正文")
        candidates.sort()
        filename = candidates[0][3]
        url = "https://archive.org/download/" + archive_id + "/" + quote(filename, safe="")
        text = (content_requester or _download_archive_text)(url)
        if len(text.strip()) < 40:
            raise OnlineLibraryError(422, "该公开书正文过短")
        book_meta = {"title": title, "source": ARCHIVE_SOURCE, "sourceUrl": "https://archive.org/details/" + archive_id}
        with lock:
            _write_atomic(text_path, text)
            _write_atomic(meta_path, json.dumps(book_meta, ensure_ascii=False))
        return dict(book_meta, path=text_path, cached=False)
    except OnlineLibraryError:
        if text_path.is_file():
            try:
                book_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                book_meta = {"title": "公开书 " + archive_id, "source": ARCHIVE_SOURCE, "sourceUrl": "https://archive.org/details/" + archive_id}
            return dict(book_meta, path=text_path, cached=True, stale=True)
        raise


def read(identifier, cache_root, requester=None, gutenberg_requester=None, content_requester=None, archive_metadata_requester=None, archive_content_requester=None):
    identifier = str(identifier or "").strip()
    registry = BookSourceRegistry([
        BookSourceAdapter("gutenberg", GUTENBERG_SOURCE, lambda _: [], lambda native_id: _read_gutenberg(native_id, cache_root, gutenberg_requester, content_requester)),
        BookSourceAdapter("archive", ARCHIVE_SOURCE, lambda _: [], lambda native_id: _read_archive(native_id, cache_root, archive_metadata_requester, archive_content_requester)),
    ])
    adapter = registry.reader_for(identifier)
    if adapter:
        return adapter.reader(adapter.native_id(identifier))
    return _read_wikisource(identifier, cache_root, requester)
