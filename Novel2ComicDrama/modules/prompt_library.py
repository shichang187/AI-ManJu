"""Local, private prompt catalog. Standard library only; documents are inert data."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile

STAGES = ['角色', '场景', '剧本', '分镜', '运镜', '风格', '扩展']
SUPPORTED = {'.txt', '.md', '.csv', '.docx', '.xlsx', '.doc', '.pdf'}
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
X = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
LIMIT = 32 * 1024 * 1024

PURPOSES = {'instruction': '给对话模型的指令', 'shot': '直接用于镜头的模板', 'reference': '参考原文'}


def purpose(entry):
    """Conservative content suggestions; original documents stay untouched."""
    if entry.get('purpose_override') in PURPOSES and entry.get('id', '').startswith('personal_'):
        return entry['purpose_override'], '作者为个人版本选择的用途；效果未验证'
    if entry.get('collection') == 'curated':
        return entry['purpose_override'], '经过内容审核的适配版；实际生成效果另记'
    body = entry.get('text', '')
    usage = entry.get('usage', '')
    original = entry.get('original_kind') == '资料原文' and len(body) > 6000
    procedural = any(w in body[:3000] for w in ('安装', '点击', '操作步骤', '使用方法', '教程'))
    visual = any(w in body for w in ('镜头', '画面', '运镜', '构图', '光影', '服装', '配色', '风格', 'camera', 'dolly', 'pan'))
    if original or procedural or entry.get('kind') in ('教程', '示例', '参考链接', '资料原文') and usage != '先给对话模型':
        return 'reference', '原文、教程、示例或链接，需阅读选取'
    if usage == '先给对话模型' and visual:
        return 'instruction', '内容含模型任务要求与画面创作约束'
    if (entry.get('kind') == '入门模板' or len(body) <= 2000) and visual and usage in ('镜头模板', '填写后使用', '组合进镜头', '选取并改写'):
        return 'shot', '内容为画面模板或短片段，需编辑后应用'
    return 'reference', '尚未核实为可用创作指令或镜头模板'


def decode_text(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError('文本编码无法识别')


def xml_part(archive, name):
    info = archive.getinfo(name)
    if info.file_size > LIMIT:
        raise ValueError('文档 XML 超过 32 MB 限制')
    return ET.fromstring(archive.read(name))


def docx_text(path):
    with zipfile.ZipFile(path) as archive:
        root = xml_part(archive, 'word/document.xml')
        lines = []
        for p in root.iter(W + 'p'):
            text = ''.join(n.text or '' if n.tag == W + 't' else '\t' if n.tag == W + 'tab' else '\n'
                           for n in p.iter() if n.tag in (W + 't', W + 'tab', W + 'br'))
            if text.strip():
                lines.append(text)
        return '\n'.join(lines)


def xlsx_rows(path):
    with zipfile.ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(n.text or '' for n in si.iter(X + 't'))
                       for si in xml_part(archive, 'xl/sharedStrings.xml')]
        rels = {n.get('Id'): n.get('Target') for n in xml_part(archive, 'xl/_rels/workbook.xml.rels')}
        for sheet in xml_part(archive, 'xl/workbook.xml').iter(X + 'sheet'):
            target = rels.get(sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'), '')
            # Only read package-local worksheets; never follow external relationships.
            name = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if '..' in name.split('/') or not name.startswith('xl/worksheets/'):
                continue
            rows = []
            for row in xml_part(archive, name).iter(X + 'row'):
                cells = {}
                for cell in row:
                    letters = re.match(r'[A-Z]+', cell.get('r', 'A1')).group()
                    col = 0
                    for letter in letters:
                        col = col * 26 + ord(letter) - 64
                    value = cell.findtext(X + 'v', '')
                    if cell.get('t') == 's':
                        value = strings[int(value)] if value else ''
                    elif cell.get('t') == 'inlineStr':
                        value = ''.join(n.text or '' for n in cell.iter(X + 't'))
                    elif cell.find(X + 'f') is not None and not value:
                        value = '[公式未计算]'
                    if col > 512:
                        raise ValueError('表格超过 512 列限制')
                    cells[col - 1] = value
                if any(v.strip() for v in cells.values()):
                    rows.append((int(row.get('r', len(rows) + 1)),
                                 [cells.get(i, '') for i in range(max(cells, default=-1) + 1)]))
            yield sheet.get('name', '工作表'), rows


def table_entries(rows, sheet=''):
    if not rows:
        return []
    # Treat a short first row with multiple cells as headings. A one-column list is data.
    first = rows[0][1]
    headings = len(first) > 1 and all(len(v) < 50 for v in first) and not first[0].strip().isdigit()
    headers = first if headings else []
    result = []
    for number, values in rows[1:] if headings else rows:
        body = '\n'.join(f'{headers[i] if i < len(headers) and headers[i] else "字段" + str(i + 1)}：{v}'
                         if len(values) > 1 else v for i, v in enumerate(values) if v.strip())
        if body.strip():
            preferred = next((i for i, h in enumerate(headers) if h.strip() in ('名称', '标题', '场景', '题材', '人物', '角色', '风格提示词')), None)
            title = values[preferred] if preferred is not None and preferred < len(values) and values[preferred].strip() else next((v for v in values if v.strip() and not v.strip().isdigit()), f'第 {number} 行')
            if preferred is not None and headers[preferred] == '风格提示词':
                title = re.split('[：:\n]', title, maxsplit=1)[0]
            result.append((f'{title[:70]} · {sheet + " " if sheet else ""}第 {number} 行', body, '表格条目'))
    return result


def read_entries(path):
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('PDF 需安装可选依赖 pypdf，或先转换为文本') from None
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('加密 PDF 无法读取，请提供未加密文本')
        pages = [page.extract_text() or '' for page in reader.pages]
        if not any(text.strip() for text in pages):
            raise ValueError('PDF 没有文本层，暂不支持 OCR')
        text = '\n\n'.join(f'【第 {i + 1} 页】\n{text or "[本页没有可提取文本]"}' for i, text in enumerate(pages))
        return [(path.stem, text, '资料原文')]
    if suffix == '.docx':
        return [(path.stem, docx_text(path), '资料原文')]
    if suffix == '.xlsx':
        return [item for sheet, rows in xlsx_rows(path) for item in table_entries(rows, sheet)]
    text = decode_text(path.read_bytes())
    if suffix == '.csv':
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        return table_entries(list(enumerate(csv.reader(io.StringIO(text), dialect), 1)))
    return [(path.stem, text, '资料原文')]


def classify(title, source):
    # Directory context first: general business collections should not become manga cards.
    if any(word in source for word in ('多行业', '面试', '营销', '带货', '行业精准')):
        return '扩展'
    for stage, words in [('角色', ('角色', '人物', '形象', '三视图')),
                         ('场景', ('场景一致', '环境', '白模')),
                         ('运镜', ('运镜', '镜头技巧', '电影感')),
                         ('分镜', ('分镜', '故事板', '九宫格', '四宫格')),
                         ('剧本', ('剧本', '脚本', '剧情', '编导')),
                         ('风格', ('风格', '真实感', '美术'))]:
        if any(word in title or word in Path(source).stem for word in words):
            return stage
    return '扩展'


def make_entry(title, text, kind, source, source_label):
    body = text.strip()
    return {'id': hashlib.sha256(body.encode('utf-8')).hexdigest()[:24], 'title': title,
            'text': body, 'kind': kind, 'stage': classify(title, source), 'private': True,
            'sources': [{'label': source_label, 'file': source}],
            'models': [m for m in ('豆包', '即梦', '可灵', 'Seedance', 'Midjourney', 'Sora')
                       if m.lower() in (title + '\n' + body[:1200]).lower()],
            'usage': '参考链接' if re.fullmatch(r'https?://\S+', body) else
                     '先给对话模型' if any(w in body[:3000] for w in ('系统指令', '系统级', '你是一', '你是一位', '角色定义')) else
                     '模板参考', 'verified': False}


from .catalog_import import CatalogImportMixin
from .curated_prompts import approved


class PromptLibrary(CatalogImportMixin):
    def __init__(self, builtin_path, private_dir):
        self.builtin = json.loads(Path(builtin_path).read_text(encoding='utf-8'))
        self.private_dir = Path(private_dir)
        self.lock = threading.RLock()
        self.catalog = {'entries': [], 'report': None, 'folder': ''}
        path = self.private_dir / 'library.json'
        if path.exists():
            self.catalog = json.loads(path.read_text(encoding='utf-8'))
        prefs = self.private_dir / 'preferences.json'
        self.favorites = json.loads(prefs.read_text(encoding='utf-8')).get('favorites', []) if prefs.exists() else []

        self._init_import()
        instructions = Path(builtin_path).with_name('curated-instructions.json')
        selections = self.private_dir / 'curated.json'
        self.curated = (json.loads(instructions.read_text(encoding='utf-8')) if instructions.exists() else []) + (json.loads(selections.read_text(encoding='utf-8')) if selections.exists() else [])
        self.curated = [dict(e, collection='curated') for e in self.curated if approved(e)]

    def save_favorites(self, entry_ids):
        if not isinstance(entry_ids, list) or len(entry_ids) > 10000 or not all(isinstance(i, str) and len(i) <= 80 for i in entry_ids):
            raise ValueError('收藏列表格式不正确')
        with self.lock:
            favorites = list(dict.fromkeys(entry_ids))
            self.private_dir.mkdir(parents=True, exist_ok=True)
            temp = self.private_dir / 'preferences.json.tmp'
            temp.write_text(json.dumps({'favorites': favorites}), encoding='utf-8')
            os.replace(temp, self.private_dir / 'preferences.json')
            self.favorites = favorites
            return {'favorites': favorites}

    def all_entries(self):
        with self.lock:
            entries = self.curated + [dict(e, collection='reference') for e in self.builtin + list(self.catalog['entries']) + list(self.personal)]
            return [dict(e, purpose=purpose(e)[0], purpose_label=PURPOSES[purpose(e)[0]],
                         purpose_basis=purpose(e)[1]) for e in entries]

    def detail(self, entry_id):
        entry = next((e for e in self.all_entries() if e['id'] == entry_id), None)
        if entry is None:
            raise KeyError('提示词不存在')
        return entry

    def query(self, params):
        def value(key, default=''):
            return params.get(key, [default])[0]
        q, stage = value('q').strip().lower()[:200], value('stage')
        page = max(1, int(value('page', '1')))
        entries = self.all_entries()
        collection = value('collection')
        if collection not in ('', 'curated', 'reference'):
            raise ValueError('集合不存在')
        if collection:
            entries = [e for e in entries if e['collection'] == collection]
        selected_purpose = value('purpose')
        if selected_purpose and selected_purpose not in PURPOSES:
            raise ValueError('资料用途不存在')
        purpose_counts = {p: sum(e['purpose'] == p for e in entries) for p in PURPOSES}
        counts = {s: sum(e['stage'] == s for e in entries) for s in STAGES}
        items = [e for e in entries if (not selected_purpose or e['purpose'] == selected_purpose)
                 and (not stage or e['stage'] == stage or stage == '漫剧' and e['stage'] != '扩展')
                 and (not q or q in (e['title'] + '\n' + e['text'] + '\n' + str(e['sources'])).lower())]
        if value('ids'):
            ids = set(value('ids').split(','))
            items = [e for e in items if e['id'] in ids]
        if value('favorites') == '1':
            items = [e for e in items if e['id'] in set(self.favorites)]
        # Favor concrete primary material, then starter templates; deterministic paging.
        items.sort(key=lambda e: (e['stage'] == '扩展', e['title'].casefold(), e['id']))
        total = len(items)
        page = min(page, max(1, (total + 19) // 20))
        summaries = [{k: v for k, v in e.items() if k not in ('text', 'original_text', 'adapted_text')} | {'preview': e.get('preview') or e['text'][:130]}
                     for e in items[(page - 1) * 20:page * 20]]
        with self.lock:
            return {'items': summaries, 'total': total, 'page': page, 'pages': max(1, (total + 19) // 20),
                    'counts': counts, 'purpose_counts': purpose_counts, 'private_count': len(self.catalog['entries']),
                    'curated_count': len(self.curated), 'builtin_count': len(self.builtin), 'report': self.catalog['report'],
                    'folder': self.catalog.get('folder', ''), 'favorites': list(self.favorites)}

    def import_folder(self, folder):
        return self._import_folder(folder)
