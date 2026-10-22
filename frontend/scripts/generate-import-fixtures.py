#!/usr/bin/env python3
"""Generate tiny, deterministic local-import test inputs using only Python stdlib.

No network, application data, personal files, or source-repository reads are used.
Files are regenerated in Tests/Fixtures/ImportFixtures by default.
"""
from pathlib import Path
import argparse
import binascii
import hashlib
import json
import struct
import zipfile
import zlib
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]


def png(width=16, height=12):
    def chunk(kind, content):
        return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', binascii.crc32(kind + content) & 0xffffffff)
    pixels = b''.join(b'\0' + bytes((124, 58, 237)) * width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def pdf(objects):
    result = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(result))
        result += f'{number} 0 obj\n'.encode() + body + b'\nendobj\n'
    start = len(result)
    result += f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]:
        result += f'{offset:010d} 00000 n \n'.encode()
    result += f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
    return bytes(result)


def stream(data, dictionary=b''):
    return b'<< /Length ' + str(len(data)).encode() + b' ' + dictionary + b' >>\nstream\n' + data + b'\nendstream'


def text_pdf():
    return pdf([
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>',
        stream(b'BT /F1 14 Tf 30 350 Td (Native learning page one) Tj ET'),
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
        stream(b'BT /F1 14 Tf 30 350 Td (Native learning page two) Tj ET'),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ])


def scan_pdf():
    pixels = bytes((124, 58, 237)) * 16 * 12
    return pdf([
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>',
        stream(b'q 240 0 0 180 30 100 cm /Im0 Do Q'),
        stream(zlib.compress(pixels), b'/Type /XObject /Subtype /Image /Width 16 /Height 12 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode'),
    ])


def package(path, entries):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value.encode() if isinstance(value, str) else value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'Tests/Fixtures/ImportFixtures')
    args = parser.parse_args()
    folder = args.output
    folder.mkdir(parents=True, exist_ok=True)
    fixtures = {
        'text.pdf': text_pdf(), 'scan.pdf': scan_pdf(), 'image.png': png(),
        'plain.txt': '本地学习资料\nDNA 在细胞分裂前复制一次。\n原始文字必须保留。\n'.encode(),
        'notes.md': '# 章节笔记\n\n- 同源染色体\n- 姐妹染色单体\n'.encode(),
        'table.csv': '概念,页码\n减数分裂,16\nDNA复制,18\n'.encode(),
        'long.txt': ('中文分段测试\n' + '遗传信息传递与细胞分裂。' * 300).encode(),
        'empty.txt': b'', 'corrupt.pdf': b'This is not a PDF file.',
        'corrupt.png': b'This is not an image file.', 'corrupt.docx': b'This is not a ZIP archive.',
        'unsupported.bin': b'Original content must remain available after extraction fails.',
        'invalid-utf8.txt': b'\xff\xfe\x80\x81',
    }
    for name, data in fixtures.items():
        (folder / name).write_bytes(data)
    types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>'
    package(folder / 'document.docx', {'[Content_Types].xml': types, 'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Office 文档导入</w:t></w:r></w:p><w:p><w:r><w:t>DNA </w:t></w:r><w:r><w:t>复制一次</w:t></w:r></w:p></w:body></w:document>'})
    slides = {'[Content_Types].xml': types}
    for number in [1, 2, 10]:
        slides[f'ppt/slides/slide{number}.xml'] = f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Slide {number} 教材</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
    package(folder / 'slides.pptx', slides)
    package(folder / 'workbook.xlsx', {
        '[Content_Types].xml': types,
        'xl/sharedStrings.xml': '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>概念</t></si><si><r><t>DNA </t></r><r><t>复制</t></r></si></sst>',
        'xl/worksheets/sheet1.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>页码</t></is></c></row><row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>18</v></c><c r="C2"><f>20+22</f><v>42</v></c></row></sheetData></worksheet>',
        'xl/worksheets/sheet2.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>第二张表</t></is></c></row></sheetData></worksheet>',
    })
    package(folder / 'missing-part.docx', {'[Content_Types].xml': types})
    package(folder / 'malformed-xml.docx', {'word/document.xml': '<w:document><w:p>broken'})
    package(folder / 'no-slides.pptx', {'[Content_Types].xml': types})
    package(folder / 'oversized-entry.docx', {'word/document.xml': '<document><p>guard before extraction</p></document>'})
    oversized = bytearray((folder / 'oversized-entry.docx').read_bytes())
    central = oversized.index(b'PK\x01\x02')
    struct.pack_into('<I', oversized, central + 24, 129 * 1024 * 1024)
    (folder / 'oversized-entry.docx').write_bytes(oversized)
    manifest = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(folder.iterdir()) if p.is_file() and p.name != 'manifest.json'}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'Generated {len(manifest)} fixtures, {sum(p["bytes"] for p in manifest.values())} bytes, at {folder}')

if __name__ == '__main__':
    main()
