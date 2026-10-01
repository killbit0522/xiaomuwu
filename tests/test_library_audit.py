import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from scripts import library_audit
from scripts.server import decode_readable_book


class AuditTests(unittest.TestCase):
    def test_report_distinguishes_missing_empty_and_readable_books(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'assets').mkdir()
            (root / 'good.txt').write_text('可读正文', encoding='utf-8')
            (root / 'empty.txt').write_text('', encoding='utf-8')
            books = [{'title': name, 'searchablePath': name} for name in ['good.txt','empty.txt','missing.txt']]
            (root / 'assets/catalog.json').write_text(json.dumps(books), encoding='utf-8')
            handler = SimpleNamespace(site_root=root, textbook_root=root, db_path=root/'library.db')
            self.assertTrue(library_audit.start(handler, decode_readable_book))
            self.assertFalse(library_audit.start(handler, decode_readable_book))
            deadline = time.monotonic() + 5
            while library_audit.running and time.monotonic() < deadline:
                time.sleep(.02)
            result = library_audit.report(handler)
            self.assertFalse(result['running'])
            self.assertEqual(result['checked'],3)
            self.assertEqual({issue['path'] for issue in result['issues']},{'empty.txt','missing.txt'})
            self.assertEqual((root/'good.txt').read_text(encoding='utf-8'),'可读正文')
