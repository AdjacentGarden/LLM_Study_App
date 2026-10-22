#!/usr/bin/env python3
"""Validate committed app content with Python stdlib, without the old repository or network."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
ERRORS = []
CHECKS = 0

def check(condition, message):
    global CHECKS
    CHECKS += 1
    if not condition:
        ERRORS.append(message)

def load(path):
    return json.loads((ROOT / path).read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def local_content(url):
    relative = Path(url.lstrip('/'))
    if '..' in relative.parts:
        raise ValueError(f'Unsafe fixture path: {url}')
    return ROOT / 'Resources/Content' / relative

def font_metadata(path):
    data = path.read_bytes()
    count = struct.unpack_from('>H', data, 4)[0]
    tables = {}
    for index in range(count):
        tag, checksum, offset, size = struct.unpack_from('>4sIII', data, 12 + index * 16)
        tables[tag] = (offset, size)
    names_offset, _ = tables[b'name']
    _, entries, storage = struct.unpack_from('>HHH', data, names_offset)
    postscript = set()
    for index in range(entries):
        platform, encoding, language, name_id, size, offset = struct.unpack_from('>HHHHHH', data, names_offset + 6 + index * 12)
        if name_id == 6:
            value = data[names_offset + storage + offset:names_offset + storage + offset + size]
            postscript.add(value.decode('utf-16-be' if platform in (0, 3) else 'mac_roman'))
    weight = struct.unpack_from('>H', data, tables[b'OS/2'][0] + 4)[0]
    return postscript, weight

def validate():
    for path in (ROOT / 'Resources/Data').glob('*.json'):
        json.loads(path.read_text())
        check(True, str(path))
    sources = load('Resources/Data/sources.json')
    builtin = load('Resources/Data/builtin-catalog.json')
    biology_entry = next((entry for entry in builtin if entry['id'] == 'community_genetics'), None)
    biology = next(source for source in sources if source['summary']['book_id'] == 'book_biology_2')
    check(biology_entry is not None and len({entry['id'] for entry in builtin}) == len(builtin), 'Permanent biology discovery listing missing or duplicated')
    if biology_entry:
        check(biology_entry['recommended'] and biology_entry['progress'] == 0 and biology_entry['learners'] == 0, 'Built-in course listing must not contain learner state')
        check(biology_entry['flashcardCount'] == len(biology['flashcards']) == 90, 'Built-in biology card count differs from preserved course')
        check(local_content(biology_entry['cover']).is_file(), 'Built-in biology cover is missing')
    check(len(biology['lessons']) == 15 and len(biology['quizzes']) == 45, 'Built-in biology lessons or exercises are incomplete')
    server_snapshot = ROOT.parent / 'backend/src/adaptive_learning/data/native_biology.json'
    if server_snapshot.is_file():
        server = json.loads(server_snapshot.read_text())
        check(builtin == [server['entry']], 'App and API permanent course listings differ; regenerate them together')
        check(server['provenance']['sources_sha256'] == sha(ROOT / 'Resources/Data/sources.json'), 'Server biology snapshot does not match bundled source data')
    check(len(sources) == 6, 'Expected six original fixture sources')
    books = [source['summary']['book_id'] for source in sources]
    check(len(books) == len(set(books)), 'Duplicate book IDs')
    missing = set()
    for source in sources:
        book = source['summary']['book_id']
        chapter_ids = [item['chapter_id'] for item in source['chapters']]
        check(len(chapter_ids) == len(set(chapter_ids)), f'{book}: duplicate chapter IDs')
        chapters = set(chapter_ids)
        for chapter in source['chapters']:
            check(not chapter.get('parent_id') or chapter['parent_id'] in chapters, f'{book}: missing parent of {chapter["chapter_id"]}')
        for collection, key in [('lessons', 'lesson_id'), ('assets', 'asset_id'), ('chunks', 'chunk_id'), ('flashcards', 'card_id')]:
            values = source.get(collection, [])
            ids = [item.get(key, item.get('flashcard_id', item.get('id'))) for item in values]
            check(len(ids) == len(set(ids)), f'{book}: duplicate {collection} IDs')
        for lesson in source.get('lessons', []):
            check(lesson['chapter_id'] in chapters, f'{book}: lesson references missing chapter')
        cover = source['summary'].get('cover_url')
        if cover:
            check(local_content(cover).is_file(), f'{book}: missing cover {cover}')
        for asset in source.get('assets', []):
            url = asset.get('image_url', '')
            if url.startswith('/assets/') and not local_content(url).is_file():
                missing.add((book, asset['asset_id'], url))
    audit = load('References/resource-copy-manifest.json')
    known = {(item['book_id'], item['asset_id'], item['url']) for item in audit['known_missing_source_assets']}
    check(missing == known, f'Asset availability changed: new missing={len(missing-known)}, newly available={len(known-missing)}. Refresh explicit source audit if intentional.')
    for item in audit['files']:
        path = ROOT / item['path']
        check(path.is_file(), f'Missing copied resource: {item["path"]}')
        if path.is_file():
            check(path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], f'Copied resource differs from provenance: {item["path"]}')
    published = load('Resources/Data/published-pages.json')
    check(published['page_count'] == 125 and len(published['assets']) == 125, 'Expected 125 published PDF page images')
    pages = set()
    for item in published['assets']:
        pages.add(item['pdf_page'])
        path = local_content(item['url'])
        check(path.is_file() and sha(path) == item['sha256'], f'Published page hash mismatch: {item["url"]}')
    check(pages == set(range(1, 126)), 'Published page coverage has gaps')
    corpus = 'Resources/Content/rag/biology-required-2-rag-v1/'
    manifest = load(corpus + 'manifest.json')
    for item in manifest['artifacts'].values():
        path = local_content(item['path'])
        check(path.is_file() and path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], f'RAG artifact hash/size mismatch: {item["path"]}')
    chunks = load(corpus + 'chunks.json')['chunks']
    count = manifest['chunking']['chunk_count']
    dimension = manifest['embeddings']['dimension']
    check(len(chunks) == count == 171, 'RAG chunk count mismatch')
    check((ROOT / corpus / 'vectors.f32').stat().st_size == count * dimension * 4, 'Vector dimensions/byte count mismatch')
    check(len(set(item['chunk_id'] for item in chunks)) == count, 'Duplicate RAG chunk IDs')
    check(all(1 <= item['page_start'] <= item['page_end'] <= 125 for item in chunks), 'RAG references pages outside the original PDF')
    check(len(load(corpus + 'evaluation-set.json')['queries']) == 50, 'Expected 50 fixed RAG evaluation queries')
    page_map = load(corpus + 'page-map.json')['pages']
    check({item['pdf_page'] for item in page_map} == set(range(1, 126)), 'RAG page map coverage incomplete')
    model_dir = 'Resources/Content/rag/models/Xenova/bge-small-zh-v1.5/'
    model = load(model_dir + 'model-manifest.json')
    check(model['revision'] == manifest['embeddings']['revision'], 'Model/corpus revision mismatch')
    for item in model['files']:
        path = ROOT / model_dir / item['path']
        check(path.is_file() and path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], f'Model file hash mismatch: {item["path"]}')
    tokenizer = load(model_dir + 'tokenizer.json')
    check(tokenizer['normalizer']['lowercase'] is False, 'BGE Chinese tokenizer must remain cased (lowercase:false)')
    check(model['dimension'] == 512 and model['pooling'] == 'cls' and model['normalize'], 'Native BGE model configuration changed')
    for name in ['home', 'study', 'community', 'profile']:
        selected = (ROOT / f'Resources/Content/icons/nav-{name}-selected.svg').read_text()
        white = (ROOT / f'Resources/Content/icons/nav-{name}-selected-white.svg').read_text()
        check(white == selected.replace('#7c3aed', '#ffffff'), f'{name}: selected-white SVG is not reproducible from original geometry')
    fonts = load('References/font-conversion.json')
    for item in fonts:
        original = ROOT / 'Resources/Content/assets/fonts' / item['source']
        output = ROOT / 'Resources/Fonts' / item['output']
        check(original.is_file() and sha(original) == item['source_sha256'], f'Font original changed: {item["source"]}')
        check(output.is_file() and output.read_bytes()[:4] in (b'\x00\x01\x00\x00', b'OTTO'), f'Native font missing/invalid: {item["output"]}')
        if output.is_file():
            names, weight = font_metadata(output)
            check(names == {item['postscript_name']} and weight == item['weight'], f'Native font instance name/weight mismatch: {item["output"]}')
        if item.get('output_sha256'):
            check(sha(output) == item['output_sha256'], f'Native font hash mismatch: {item["output"]}')
    check((ROOT / 'Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png').is_file(), 'Missing committed native app icon')
    pins = load('BookCourse.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved')['pins']
    check({item['identity']: item['state']['version'] for item in pins} == {'onnxruntime-swift-package-manager': '1.24.2', 'zipfoundation': '0.9.20'}, 'Swift package pins changed')
    for name in ['Lucide-ISC-and-Feather-MIT.txt', 'Phosphor-MIT.txt', 'NotoSansSC-OFL-1.1.txt', 'ONNX-Runtime-MIT.txt', 'ONNX-Runtime-Binary-MIT.txt', 'ONNX-Runtime-ThirdPartyNotices.txt', 'ZIPFoundation-MIT.txt', 'BGE-FlagEmbedding-MIT.txt', 'MODEL_AND_UPSTREAM_PROVENANCE.json']:
        notice = ROOT / 'LICENSES' / name
        embedded = ROOT / 'Resources/Content/licenses' / name
        check(notice.is_file() and embedded.is_file() and sha(notice) == sha(embedded), f'Missing or different bundled notice: {name}')
    upstream = load('LICENSES/MODEL_AND_UPSTREAM_PROVENANCE.json')
    check(sha(ROOT / 'LICENSES/BGE-FlagEmbedding-MIT.txt') == upstream['upstream_mit_license_sha256'], 'BGE upstream MIT notice hash mismatch')
    check(sha(ROOT / 'LICENSES/ONNX-Runtime-ThirdPartyNotices.txt') == upstream['onnx_third_party_notices_sha256'], 'ONNX upstream third-party notices hash mismatch')
    for path in (ROOT / 'Resources').rglob('*'):
        check(not path.is_symlink(), f'Bundle must not depend on external symlink: {path.relative_to(ROOT)}')
        check(not path.name.startswith('.env') and not path.name.endswith(('.env', '.xcconfig')), f'Potential local secret in resources: {path.relative_to(ROOT)}')
    print(f'Content validation: {CHECKS} checks, {len(sources)} sources, {len(chunks)} RAG chunks, 125 original page images.')
    print(f'Known original missing figure references: {len(known)}; preserved as source facts, not synthesized.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        validate()
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        ERRORS.append(str(error))
    if ERRORS:
        for message in ERRORS:
            print('ERROR: ' + message, file=sys.stderr)
        return 1
    print('PASS: committed native content is internally consistent; no source checkout is needed.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
