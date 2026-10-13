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


class PromptLibrary:
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
            return self.builtin + list(self.catalog['entries'])

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
        counts = {s: sum(e['stage'] == s for e in entries) for s in STAGES}
        items = [e for e in entries if (not stage or e['stage'] == stage or stage == '漫剧' and e['stage'] != '扩展')
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
        summaries = [{k: v for k, v in e.items() if k != 'text'} | {'preview': e['text'][:130]}
                     for e in items[(page - 1) * 20:page * 20]]
        with self.lock:
            return {'items': summaries, 'total': total, 'page': page, 'pages': max(1, (total + 19) // 20),
                    'counts': counts, 'private_count': len(self.catalog['entries']),
                    'builtin_count': len(self.builtin), 'report': self.catalog['report'],
                    'folder': self.catalog.get('folder', ''), 'favorites': list(self.favorites)}

    def import_folder(self, folder):
        root = Path(folder).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise ValueError('请输入已下载资料的绝对文件夹路径')
        root = root.resolve()
        # Avoid accidentally importing this program's own private catalog or credential folders.
        if root == Path(root.anchor) or any(p.lower() in ('.git', '.aws', '.ssh', '.codex') for p in root.parts):
            raise ValueError('请选择具体的提示词资料文件夹')
        with self.lock:
            found, issues, ignored, files = {}, [], 0, 0
            for path in sorted(root.rglob('*')):
                if not path.is_file() or path.is_symlink() or any(p.startswith('.') for p in path.relative_to(root).parts):
                    continue
                rel = path.relative_to(root).as_posix()
                if '.downloading' in path.name.lower() or path.suffix.lower() in ('.zip', '.rar', '.7z'):
                    issues.append({'file': rel, 'reason': '下载未完成' if '.downloading' in path.name.lower() else
                                   '压缩包请先解压'})
                    continue
                if path.suffix.lower() not in SUPPORTED:
                    ignored += 1
                    continue
                try:
                    before = path.stat()
                    if before.st_size > LIMIT:
                        raise ValueError('文件超过 32 MB 限制')
                    if path.suffix.lower() == '.doc':
                        derived = self.private_dir / 'extracted' / (hashlib.sha256(path.read_bytes()).hexdigest() + '.txt')
                        if not derived.exists():
                            raise ValueError('旧 Word .doc 请运行 scripts/convert_legacy_docs.ps1，或转换为 .docx')
                        items = [(path.stem, decode_text(derived.read_bytes()), '资料原文')]
                    else:
                        items = read_entries(path)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('文件仍在写入，请稍后重新导入')
                    if not any(text.strip() for _, text, _ in items):
                        raise ValueError('未提取到文本，可能只有图片或空表格')
                    files += 1
                    for title, text, kind in items:
                        if not text.strip():
                            continue
                        entry = make_entry(title, text, kind, rel, root.name)
                        if entry['id'] in found:
                            if entry['sources'][0] not in found[entry['id']]['sources']:
                                found[entry['id']]['sources'].extend(entry['sources'])
                        else:
                            found[entry['id']] = entry
                except Exception as exc:
                    issues.append({'file': rel, 'reason': str(exc)[:200]})
            # Preserve previously read material from files temporarily broken during download.
            if self.catalog.get('folder') == str(root):
                failed = {i['file'] for i in issues}
                for old in self.catalog['entries']:
                    if any(s['file'] in failed for s in old['sources']) and old['id'] not in found:
                        found[old['id']] = old
            report = {'read_files': files, 'entries': len(found), 'ignored_media': ignored, 'issues': issues,
                      'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                      'primary': sum(e['stage'] != '扩展' for e in found.values())}
            catalog = {'folder': str(root), 'entries': list(found.values()), 'report': report}
            self.private_dir.mkdir(parents=True, exist_ok=True)
            temp = self.private_dir / 'library.json.tmp'
            temp.write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, self.private_dir / 'library.json')
            self.catalog = catalog
            return report
