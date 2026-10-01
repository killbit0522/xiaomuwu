import unittest
from scripts.server import decode_book_text


class TextBomTests(unittest.TestCase):
    def test_whitespace_before_bom(self):
        text = '第一章\n测试正文'
        for encoding, bom in [('utf-16-be', b'\xfe\xff'), ('utf-16-le', b'\xff\xfe'), ('utf-8', b'\xef\xbb\xbf')]:
            with self.subTest(encoding=encoding):
                self.assertEqual(decode_book_text(b'\r\n\r\n' + bom + text.encode(encoding)), text)

    def test_plain_text_whitespace_preserved(self):
        self.assertEqual(decode_book_text(b'\nhello'), '\nhello')
