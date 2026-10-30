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


def repair(handler, rebuild_catalog):
    """Repair only derived state; original books are never modified or deleted."""
    actions = []
    removed = 0
    cache_root = handler.read_cache_root
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        for path in cache_root.glob('*.tmp'):
            path.unlink(missing_ok=True)
            removed += 1
    except OSError:
        actions.append('部分临时缓存正被使用，已保留')
    if removed:
        actions.append(f'清理了 {removed} 个未完成的正文缓存')
    count = rebuild_catalog(handler)
    actions.append(f'重新核对目录，共收录 {count} 本书')
    return {'ok': True, 'actions': actions, 'catalogCount': count}


def start(handler, decode):
    global running
    with lock:
        if running:
            return False
        running = True
    def run():
        global running
        result = {'checked': 0, 'issues': [], 'total': 0, 'errors': 0, 'warnings': 0, 'started': time.time()}
        destination = handler.db_path.parent / 'library-audit.json'
        def persist():
            temp = destination.with_suffix('.tmp')
            temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            temp.replace(destination)
        try:
            books = json.loads((handler.site_root / 'assets/catalog.json').read_text(encoding='utf-8'))
            if not isinstance(books, list):
                raise ValueError('catalog must be a list')
            result['total'] = len(books)
            persist()
            for index, book in enumerate(books):
                if not isinstance(book, dict):
                    result['issues'].append({'title': f'目录记录 {index + 1}', 'path': '', 'message': '目录记录格式无效', 'severity': 'error', 'suggestion': '点击“安全修复并复检”重建目录；不会删除原书。'})
                    result['errors'] += 1
                    result['checked'] += 1
                    continue
                relative = book.get('searchablePath', '')
                if not isinstance(relative, str) or not relative.strip() or '\x00' in relative:
                    relative = ''
                path = (handler.textbook_root / relative).resolve()
                message = ''
                severity = 'warning'
                suggestion = ''
                try:
                    if not path.is_relative_to(handler.textbook_root.resolve()) or not path.is_file():
                        message = '文件不存在或路径无效'
                        severity = 'error'
                        suggestion = '点击“安全修复并复检”重建目录；若仍出现，请重新上传这本书。'
                    elif path.stat().st_size > 32 * 1024 * 1024:
                        message = '大文件：仅确认存在，未判定为损坏'
                        suggestion = '请在读者端试开；打不开时再重新上传。'
                    elif path.suffix.lower() in {'.txt', '.docx', '.epub', '.zip'}:
                        if path.suffix.lower() != '.txt':
                            with zipfile.ZipFile(path) as archive:
                                names = [item.filename.lower() for item in archive.infolist() if not item.is_dir()]
                                if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
                                    message = '解压后体积较大：已跳过正文检查，不代表文件损坏'
                                    suggestion = '请在读者端试开；能正常阅读即可保留。'
                                elif any(name.endswith('.zip') for name in names):
                                    message = '包含嵌套压缩包：待进一步检查，不代表文件损坏'
                                    suggestion = '建议解压出 TXT、DOCX 或 EPUB 后重新上传，打开会更稳定。'
                                elif path.suffix.lower() == '.zip' and not any(name.endswith(('.txt', '.docx', '.epub')) for name in names):
                                    message = ('图片型压缩包：当前文字阅读器不支持' if any(name.endswith(('.png', '.jpg', '.jpeg', '.webp')) for name in names) else '压缩包内没有当前支持的正文格式')
                                    severity = 'error'
                                    suggestion = '请改为 TXT、DOCX、EPUB，或保留为仅下载书籍。'
                                elif path.suffix.lower() == '.docx' and not decode(path).strip():
                                    message = ('图片型 Word：未提取到文字，需要图片阅读支持' if any(name.startswith('word/media/') for name in names) else '未提取到文字：需要进一步检查文档结构')
                                    severity = 'error'
                                    suggestion = '请另存为含文字的 DOCX 或 TXT 后重新上传。'
                        if not message and not decode(path).strip():
                            message = '未提取到文字：需要进一步检查'
                            severity = 'error'
                            suggestion = '请另存为 UTF-8 TXT 后重新上传。'
                    elif path.suffix.lower() == '.pdf':
                        with path.open('rb') as stream:
                            if not stream.read(1024).lstrip().startswith(b'%PDF-'):
                                message = 'PDF 文件头异常'
                                severity = 'error'
                                suggestion = '文件可能扩展名不正确或不完整，请重新下载并上传。'
                    else:
                        message = '该格式暂不支持在线阅读，可继续提供下载'
                        suggestion = '如需在线阅读，请转换成 TXT、DOCX、EPUB 或 PDF。'
                except Exception as error:
                    message = '正文解析失败：' + type(error).__name__
                    severity = 'error'
                    suggestion = '先点“安全修复并复检”；仍失败时请转换成 UTF-8 TXT 后重新上传。'
                if message:
                    result['issues'].append({'title': str(book.get('title') or relative or f'目录记录 {index + 1}'), 'path': relative, 'message': message, 'severity': severity, 'suggestion': suggestion})
                    result['errors' if severity == 'error' else 'warnings'] += 1
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
