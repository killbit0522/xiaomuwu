"""A small, isolated reader for openly licensed Wikisource texts."""
import hashlib
import html
import json
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://zh.wikisource.org/w/api.php"
SOURCE_NAME = "中文维基文库"
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


def search(query, cache_root, requester=None):
    query = re.sub(r"\s+", " ", str(query or "")).strip()[:80]
    if len(query) < 1:
        raise OnlineLibraryError(400, "请输入书名或作者")
    cache = _cache_path(cache_root, "search", query.casefold(), "json")
    cached = _read_json_cache(cache, 15 * 60)
    if cached is not None:
        return dict(cached, cached=True)
    requester = requester or _request_json
    try:
        payload = requester({"action": "query", "list": "search", "srsearch": query, "srnamespace": "0", "srlimit": "15", "srprop": "snippet|size"})
        rows = payload.get("query", {}).get("search", [])
        items = []
        for row in rows[:15]:
            title = str(row.get("title", "")).strip()
            if not title:
                continue
            parser = _ReadableHTML(); parser.feed(str(row.get("snippet", "")))
            items.append({"id": title, "title": title, "snippet": parser.text()[:240], "source": SOURCE_NAME, "sourceUrl": "https://zh.wikisource.org/wiki/" + title.replace(" ", "_")})
        result = {"query": query, "items": items, "source": SOURCE_NAME, "cached": False}
        with lock:
            _write_atomic(cache, json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return result
    except OnlineLibraryError:
        try:
            stale = json.loads(cache.read_text(encoding="utf-8"))
            return dict(stale, cached=True, stale=True)
        except (OSError, ValueError):
            raise


def read(title, cache_root, requester=None):
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
