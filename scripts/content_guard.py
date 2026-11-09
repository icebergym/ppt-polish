#!/usr/bin/env python3
"""Conservative content snapshots for editable PPTX; not a visual/OCR validator."""
import argparse
import hashlib
import json
import posixpath
import re
import sys
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
RID = '{' + NS['r'] + '}id'
SCHEMA = 'ppt-pptx-content-v1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def ordered(values):
    return sorted(values, key=canonical)


def text_content(node):
    if node is None:
        return ''
    paragraphs = node.findall('.//a:p', NS)
    if not paragraphs:
        return ''.join(t.text or '' for t in node.findall('.//a:t', NS))
    lines = []
    for para in paragraphs:
        pieces = []
        for e in para.iter():
            if e.tag == '{' + NS['a'] + '}t':
                pieces.append(e.text or '')
            elif e.tag == '{' + NS['a'] + '}br':
                pieces.append('\n')
        lines.append(''.join(pieces))
    return re.sub(r'\s+', ' ', '\n'.join(lines)).strip()


def number(value):
    try:
        d = Decimal(value)
        if d.is_finite():
            return format(d, 'f').rstrip('0').rstrip('.') if '.' in format(d, 'f') else format(d, 'f')
    except InvalidOperation:
        pass
    return value


def relationships(z, part):
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    if name not in z.namelist():
        return {}
    result = {}
    for e in ET.fromstring(z.read(name)):
        target = e.get('Target', '')
        external = e.get('TargetMode') == 'External'
        resolved = target if external else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
        if target.startswith('/') and not external:
            resolved = target.lstrip('/')
        if not external and (resolved.startswith('../') or resolved.startswith('/')):
            raise ValueError('Relationship escapes package: ' + target)
        result[e.get('Id')] = {'target': resolved, 'external': external, 'type': e.get('Type', '').rsplit('/', 1)[-1]}
    return result


def chart_content(z, part):
    root = ET.fromstring(z.read(part))
    ser = []
    incomplete = False
    for se in root.findall('.//c:ser', NS):
        tx = se.find('c:tx', NS)
        names = [] if tx is None else [x.text or '' for x in tx.findall('.//c:v', NS)]
        item = {'name': names, 'data': {}}
        for key in ('cat', 'val', 'xVal', 'yVal', 'bubbleSize'):
            data = se.find('c:' + key, NS)
            if data is None:
                continue
            levels = data.findall('.//c:lvl', NS)
            groups = levels or [data]
            points = []
            for group in groups:
                rows = []
                for pt in group.findall('.//c:pt', NS):
                    val = pt.find('c:v', NS)
                    value = '' if val is None else val.text or ''
                    rows.append([int(pt.get('idx', '0')), number(value) if key != 'cat' else value])
                points.append(sorted(rows))
            if data.findall('.//c:f', NS) and not any(points):
                incomplete = True
            item['data'][key] = {'levels': points, 'formats': [v.text for v in data.findall('.//c:formatCode', NS)]}
        ser.append(item)
    titles = [text_content(t) for t in root.findall('.//c:title', NS)]
    label_text = [text_content(t) for t in root.findall('.//c:dLbl/c:tx', NS)]
    formats = ordered([dict(e.attrib) for e in root.findall('.//c:numFmt', NS)])
    rels = relationships(z, part)
    workbooks = []
    for r in rels.values():
        if r['type'] == 'package':
            workbooks.append({'external': r['external'], 'identity': r['target'] if r['external'] else digest(z.read(r['target']))})
    provenance = {'formulas': [f.text for f in root.findall('.//c:f', NS)], 'workbooks': ordered(workbooks)}
    semantic = {'series': ser, 'titles': titles, 'data_label_text': label_text, 'number_formats': formats}
    return semantic, provenance, incomplete or not ser


def shape_texts(root, notes=False):
    result = []
    for sp in root.findall('.//p:sp', NS):
        ph = sp.find('.//p:ph', NS)
        if notes and ph is not None and ph.get('type') in ('sldNum', 'dt', 'sldImg'):
            continue
        t = text_content(sp.find('p:txBody', NS))
        if t:
            result.append(t)
    return sorted(result)


def snapshot(path):
    with zipfile.ZipFile(path) as z:
        if 'ppt/presentation.xml' not in z.namelist():
            raise ValueError('A native PPTX is required; PDF/images require a visual content inventory.')
        pr = ET.fromstring(z.read('ppt/presentation.xml'))
        prs = relationships(z, 'ppt/presentation.xml')
        slides = []
        for item in pr.findall('p:sldIdLst/p:sldId', NS):
            part = prs[item.get(RID)]['target']
            root = ET.fromstring(z.read(part))
            rels = relationships(z, part)
            tables = []
            for table in root.findall('.//a:tbl', NS):
                rows = []
                for row in table.findall('a:tr', NS):
                    rows.append([{'text': text_content(cell.find('a:txBody', NS)), 'merge': {k: cell.get(k) for k in ('gridSpan', 'rowSpan', 'hMerge', 'vMerge') if cell.get(k) is not None}} for cell in row.findall('a:tc', NS)])
                tables.append(rows)
            charts, provenance, manual = [], [], []
            for c in root.findall('.//c:chart', NS):
                rel = rels[c.get(RID)]
                if rel['external']:
                    manual.append('external_chart')
                    continue
                content, prov, incomplete = chart_content(z, rel['target'])
                charts.append(content)
                provenance.append(prov)
                if incomplete:
                    manual.append('uncached_or_unsupported_chart_data')
            images = []
            for pic in root.findall('.//p:pic', NS):
                blip = pic.find('.//a:blip', NS)
                if blip is None:
                    continue
                rel = rels.get(blip.get('{' + NS['r'] + '}embed') or blip.get('{' + NS['r'] + '}link'))
                if not rel:
                    manual.append('unresolved_image')
                    continue
                crop = pic.find('.//a:srcRect', NS)
                images.append({'identity': rel['target'] if rel['external'] else digest(z.read(rel['target'])), 'external': rel['external'], 'crop': {} if crop is None else dict(crop.attrib)})
            unknown = []
            for gd in root.findall('.//a:graphicData', NS):
                uri = gd.get('uri', '')
                if not uri.endswith('/chart') and not uri.endswith('/table'):
                    unknown.append(digest(ET.tostring(gd)))
                    manual.append('opaque_graphic_object')
            notes = []
            note_links = []
            for r in rels.values():
                if r['type'] == 'notesSlide' and not r['external']:
                    notes += shape_texts(ET.fromstring(z.read(r['target'])), notes=True)
                    note_links += [x['target'] for x in relationships(z, r['target']).values() if x['type'] == 'hyperlink']
            hyperlinks = ordered([r['target'] for r in rels.values() if r['type'] == 'hyperlink'])
            text = shape_texts(root)
            if images and not text and not tables and not charts:
                manual.append('image_only_slide_requires_transcription')
            slides.append({'texts': text, 'tables': ordered(tables), 'charts': ordered(charts), 'notes': sorted(notes), 'hyperlinks': hyperlinks, 'note_hyperlinks': sorted(note_links), 'images': ordered(images), 'chart_provenance': ordered(provenance), 'opaque_graphics': sorted(unknown), 'coverage_flags': sorted(set(manual))})
    return {'schema': SCHEMA, 'source_name': Path(path).name, 'source_sha256': digest(Path(path).read_bytes()), 'slide_count': len(slides), 'slides': slides, 'limits': ['Not OCR or visual validation.', 'Inherited master/layout content and dynamic objects require visual review.', 'Chart caches are compared; workbook formulas are not recalculated.']}


def compare(before, after, allow=()):
    if before.get('schema') != SCHEMA or after.get('schema') != SCHEMA:
        raise ValueError('Incompatible snapshot schema')
    allowed = set(allow)
    count = max(len(before['slides']), len(after['slides']))
    if any(i < 1 or i > count for i in allowed):
        raise ValueError('Allowed slide outside deck')
    changes, reviews, scoped = [], [], []
    if before['slide_count'] != after['slide_count']:
        changes.append({'field': 'slide_count', 'before': before['slide_count'], 'after': after['slide_count']})
    semantic_fields = ('texts', 'tables', 'charts', 'notes', 'hyperlinks', 'note_hyperlinks')
    for i, (a, b) in enumerate(zip(before['slides'], after['slides']), 1):
        for field in semantic_fields:
            if a[field] != b[field]:
                entry = {'slide': i, 'field': field, 'before': a[field], 'after': b[field]}
                (scoped if i in allowed else changes).append(entry)
        for field in ('images', 'chart_provenance', 'opaque_graphics'):
            if a[field] != b[field]:
                reviews.append({'slide': i, 'field': field, 'reason': 'Visual or provenance review required', 'within_authorized_content_scope': i in allowed})
        flags = sorted(set(a['coverage_flags'] + b['coverage_flags']))
        if flags:
            reviews.append({'slide': i, 'field': 'coverage', 'flags': flags})
    status = 'content_changed' if changes else 'review_required' if reviews else 'passed_for_extracted_content'
    return {'schema': 'ppt-pptx-comparison-v1', 'status': status, 'changes': changes, 'review_required': reviews, 'authorized_scope_changes': scoped, 'allowed_content_slides': sorted(allowed), 'always_required': 'Render and visually compare each relevant slide; this report is not a claim of full fidelity.'}


def write_json(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    sp = subs.add_parser('snapshot')
    sp.add_argument('pptx')
    sp.add_argument('output')
    cp = subs.add_parser('compare')
    cp.add_argument('before')
    cp.add_argument('after')
    cp.add_argument('--report', required=True)
    cp.add_argument('--allow-content-on', default='')
    args = parser.parse_args()
    try:
        if args.command == 'snapshot':
            result = snapshot(args.pptx)
            write_json(args.output, result)
            print(json.dumps({'saved': args.output, 'slide_count': result['slide_count']}, ensure_ascii=False))
            return 0
        allowed = [int(x) for x in args.allow_content_on.split(',') if x.strip()]
        result = compare(json.loads(Path(args.before).read_text()), json.loads(Path(args.after).read_text()), allowed)
        write_json(args.report, result)
        print(json.dumps({'status': result['status'], 'changes': len(result['changes']), 'review_required': len(result['review_required']), 'report': args.report}, ensure_ascii=False))
        return 0 if result['status'] == 'passed_for_extracted_content' else 2
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
