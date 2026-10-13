import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.prompt_library import PromptLibrary, read_entries, decode_text


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / '资料'
        self.source.mkdir()
        self.builtin = self.root / 'builtin.json'
        self.builtin.write_text('[]', encoding='utf-8')
        self.library = PromptLibrary(self.builtin, self.root / 'private')

    def test_import_deduplicates_and_updates_changed_file(self):
        (self.source / '角色.txt').write_text('人物：红衣少女', encoding='utf-8')
        (self.source / '角色(1).txt').write_text('人物：红衣少女', encoding='utf-8-sig')
        report = self.library.import_folder(str(self.source))
        self.assertEqual((report['read_files'], report['entries']), (2, 1))
        entry = self.library.all_entries()[0]
        self.assertEqual(len(entry['sources']), 2)
        self.assertEqual(entry['stage'], '角色')
        self.assertTrue(entry['private'])
        self.assertEqual(self.library.import_folder(str(self.source))['entries'], 1)
        (self.source / '角色(1).txt').unlink()
        (self.source / '角色.txt').write_text('人物：蓝衣少年', encoding='utf-8')
        self.library.import_folder(str(self.source))
        self.assertEqual(len(self.library.all_entries()), 1)
        self.assertIn('蓝衣', self.library.all_entries()[0]['text'])
        restored = PromptLibrary(self.builtin, self.root / 'private')
        self.assertEqual(restored.detail(restored.all_entries()[0]['id'])['text'], '人物：蓝衣少年')

    def test_pending_and_corrupt_documents_are_reported(self):
        (self.source / '旧.doc').write_bytes(b'legacy')
        (self.source / '未完.docx.downloading').write_bytes(b'partial')
        (self.source / '损坏.docx').write_bytes(b'partial')
        (self.source / '预览.png').write_bytes(b'image')
        report = self.library.import_folder(str(self.source))
        self.assertEqual(len(report['issues']), 3)
        self.assertEqual(report['ignored_media'], 1)
        self.assertEqual(report['entries'], 0)

    def test_docx_unicode_line_break_and_table(self):
        path = self.source / '分镜.docx'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('word/document.xml', '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>你是一位编导</w:t><w:br/><w:t>保持红衣</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>第二镜头</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>''')
        self.library.import_folder(str(self.source))
        entry = self.library.all_entries()[0]
        self.assertEqual(entry['text'], '你是一位编导\n保持红衣\n第二镜头')
        self.assertEqual(entry['usage'], '先给对话模型')

    def test_xlsx_sparse_cells_and_named_sheet(self):
        path = self.source / '分镜.xlsx'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="镜头" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>编号</t></is></c><c r="C1" t="inlineStr"><is><t>动作</t></is></c></row><row r="4"><c r="A4"><v>1</v></c><c r="C4" t="inlineStr"><is><t>转身回望</t></is></c></row></sheetData></worksheet>')
        entries = read_entries(path)
        self.assertEqual(len(entries), 1)
        self.assertIn('镜头 第 4 行', entries[0][0])
        self.assertIn('动作：转身回望', entries[0][1])

    def test_csv_quotes_search_pagination_and_extended_category(self):
        path = self.source / '运镜.csv'
        path.write_text('名称,提示词\n' + '\n'.join(f'推进{i},"缓慢推进，保持角色"' for i in range(25)), encoding='utf-8')
        extended = self.source / '多行业提示词'
        extended.mkdir()
        (extended / '人物面试.txt').write_text('请设计面试题', encoding='utf-8')
        self.library.import_folder(str(self.source))
        data = self.library.query({'stage': ['漫剧'], 'q': ['缓慢'], 'page': ['2']})
        self.assertEqual(data['total'], 25)
        self.assertEqual(len(data['items']), 5)
        self.assertEqual(data['counts']['扩展'], 1)
        self.assertNotIn('text', data['items'][0])
        self.assertEqual(self.library.query({'ids': ['missing']})['total'], 0)

    def test_favorites_survive_process_restart(self):
        (self.source / '角色.txt').write_text('红衣少女', encoding='utf-8')
        self.library.import_folder(str(self.source))
        entry_id = self.library.all_entries()[0]['id']
        self.library.save_favorites([entry_id, entry_id])
        restored = PromptLibrary(self.builtin, self.root / 'private')
        result = restored.query({'favorites': ['1']})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['favorites'], [entry_id])

    def test_relative_folder_rejected(self):
        with self.assertRaises(ValueError):
            self.library.import_folder('.')

    def test_gbk_and_utf16_bom_decode(self):
        self.assertEqual(decode_text('影视风格,电影提示词'.encode('gb18030')), '影视风格,电影提示词')
        self.assertEqual(decode_text('保持场景'.encode('utf-16')), '保持场景')

    def test_legacy_doc_only_uses_matching_private_extract(self):
        import hashlib
        path = self.source / '角色.doc'
        path.write_bytes(b'original')
        extract = self.root / 'private' / 'extracted'
        extract.mkdir(parents=True)
        (extract / (hashlib.sha256(b'original').hexdigest() + '.txt')).write_text('原角色提示词', encoding='utf-8')
        self.assertEqual(self.library.import_folder(str(self.source))['entries'], 1)
        path.write_bytes(b'changed')
        report = self.library.import_folder(str(self.source))
        self.assertEqual(len(report['issues']), 1)
        self.assertEqual(report['entries'], 1)  # Previous text is preserved while the update is pending.


if __name__ == '__main__':
    unittest.main()
