"""Low-priority, read-only library inspection, with a persistent latest report."""
import json
import threading
import time
import zipfile
from pathlib import Path

lock = threading.Lock()
running = False


def report(handler):
    path = handler.db_path.parent / 'library-audit.json'
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        result = {'checked': 0, 'issues': [], 'total': 0}
    return dict(result, running=running)


def start(handler, decode):
    global running
    with lock:
        if running:
            return False
        running = True
    def run():
        global running
        result = {'checked': 0, 'issues': [], 'total': 0, 'started': time.time()}
        destination = handler.db_path.parent / 'library-audit.json'
        def persist():
            temp = destination.with_suffix('.tmp')
            temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            temp.replace(destination)
        try:
            books = json.loads((handler.site_root / 'assets/catalog.json').read_text(encoding='utf-8'))
            result['total'] = len(books)
            persist()
            for book in books:
                relative = book.get('searchablePath', '')
                path = (handler.textbook_root / relative).resolve()
                message = ''
                try:
                    if not path.is_relative_to(handler.textbook_root.resolve()) or not path.is_file():
                        message = '文件不存在或路径无效'
                    elif path.stat().st_size > 32 * 1024 * 1024:
                        message = '大文件：仅确认存在，正文需单独检查'
                    elif path.suffix.lower() in {'.txt', '.docx', '.epub', '.zip'}:
                        if path.suffix.lower() != '.txt':
                            with zipfile.ZipFile(path) as archive:
                                if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024 or any(item.filename.lower().endswith('.zip') for item in archive.infolist()):
                                    raise ValueError('Archive needs manual inspection')
                        if not decode(path).strip():
                            message = '正文为空'
                    elif path.suffix.lower() == '.pdf':
                        with path.open('rb') as stream:
                            if not stream.read(1024).lstrip().startswith(b'%PDF-'):
                                message = 'PDF 文件头异常'
                    else:
                        message = '该格式暂不支持在线阅读'
                except Exception as error:
                    message = '正文解析失败：' + type(error).__name__
                if message:
                    result['issues'].append({'title': book.get('title', relative), 'path': relative, 'message': message})
                result['checked'] += 1
                if result['checked'] % 20 == 0:
                    persist()
                time.sleep(.05)
        except Exception as error:
            result['error'] = type(error).__name__
        finally:
            result['finished'] = time.time()
            try:
                persist()
            finally:
                running = False
    threading.Thread(target=run, daemon=True).start()
    return True
