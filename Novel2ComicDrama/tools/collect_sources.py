"""Import normally visible documentation snapshots; never treats cards as prompts.

Usage: python collect_sources.py --snapshots FILE [...] --destination PRIVATE_JSON
Snapshots use the official browser/search tool's numbered text representation.
Failures stay provenance records. Approval is a separate editorial step.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata


def fingerprint(text):
    normalized = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def parse_snapshots(text):
    records = []
    for part in text.split('--------------------------------------------------------------------------------'):
        urls = re.findall(r'https://youmind\.com/(?:zh-CN/)?video-prompts/[\w-]+', part)
        if not urls:
            continue
        url = urls[0]
        lines = [re.sub(r'L\d+:\s?', '', s).strip() for s in part.splitlines()]
        prompt = ''
        for i, line in enumerate(lines):
            if line == '提示词' or line.startswith('提示词') and len(line) < 90:
                following = next((s for s in lines[i+1:] if s), '')
                if len(following) >= 80 and not following.startswith(('###', '', 'Seedance')):
                    prompt = following
                    break
        complete = bool(prompt) and not prompt.endswith(('…', '...')) and '内容已隐藏' not in prompt
        author = re.search(r'†(@[^†]+)†https?://|†(@[^†]+)†x.com', part)
        source_model = 'Seedance 2.0' if 'Seedance 2.0' in '\n'.join(lines[:50]) else '未验证'
        if 'Seedance 2.0 Mini' in '\n'.join(lines[:30]):
            source_model = 'Seedance 2.0 Mini'
        languages = re.search(r'原始语言\s*(?:L\d+:\s*)*([A-Z]{2})', part)
        records.append({'id': 'candidate_' + hashlib.sha256(url.encode()).hexdigest()[:16],
            'title': lines[0].split(' - Seedance')[0], 'url': url, 'source_site': 'YouMind',
            'collected_at': '2026-10-14', 'visible_text': prompt,
            'completeness': 'complete' if complete else 'unavailable',
            'original_hash': hashlib.sha256(prompt.encode()).hexdigest() if prompt else '',
            'fingerprint': fingerprint(prompt) if prompt else '',
            'author': (author.group(1) or author.group(2)) if author else '页面署名待核对',
            'source_language': languages.group(1) if languages else '未验证',
            'captured_language': 'zh-CN 页面可见版本；非中文原作需另行取得原语言版本',
            'model_version': source_model, 'review_status': 'pending',
            'test_status': '未实测', 'license_status': '再发布许可未验证；仅本地私有',
            'sources': [{'label': 'YouMind 公开详情', 'file': url}],
            'snapshot_hash': hashlib.sha256(part.encode()).hexdigest()})
    return records


def merge(records):
    by_url = {}
    for record in records:
        previous = by_url.get(record['url'])
        if not previous or len(record['visible_text']) > len(previous['visible_text']):
            by_url[record['url']] = record
    result, by_hash = [], {}
    for record in by_url.values():
        fp = record['fingerprint']
        if fp and fp in by_hash:
            by_hash[fp]['sources'].extend(record['sources'])
        else:
            result.append(record)
            if fp:
                by_hash[fp] = record
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshots', nargs='+', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    records = merge([r for p in args.snapshots for r in parse_snapshots(Path(p).read_text(encoding='utf-8'))])
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    temp = args.destination.with_suffix('.tmp')
    temp.write_text(json.dumps({'records': records, 'review': 'pending'}, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(args.destination)
    print(json.dumps({'records': len(records), 'complete': sum(r['completeness']=='complete' for r in records)}))
