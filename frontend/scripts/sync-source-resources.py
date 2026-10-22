#!/usr/bin/env python3
"""Explicit allowlist import of public resources; never scans user/device/environment data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Read-only original repository')
    parser.add_argument('--audit-only', action='store_true', help='Write provenance/audit only; require native resources already match')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if source == ROOT:
        parser.error('Source must be a separate repository')
    mappings = [('public/assets', 'Resources/Content/assets'), ('public/icons', 'Resources/Content/public-icons'), ('public/rag', 'Resources/Content/rag')]
    files = []
    for origin, destination in mappings:
        base = source / origin
        if not base.is_dir():
            parser.error(f'Missing required source directory: {origin}')
        for path in sorted(base.rglob('*')):
            if path.is_symlink():
                raise ValueError(f'Symlink is not an importable fixture: {path.relative_to(source)}')
            if not path.is_file():
                continue
            relative = path.relative_to(base)
            # Native ONNX replaces the old WASM runtime. Do not bundle JavaScript runtime binaries.
            if origin == 'public/rag' and relative.parts[0] == 'runtime':
                continue
            if any(part.startswith('.') for part in relative.parts) or path.suffix == '.env':
                raise ValueError(f'Unexpected hidden/private file in public resources: {relative}')
            target = ROOT / destination / relative
            if args.audit_only:
                if not target.is_file() or digest(target) != digest(path):
                    raise ValueError(f'Native copy differs from source: {target.relative_to(ROOT)}')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            files.append({'source': str(path.relative_to(source)), 'path': str(target.relative_to(ROOT)), 'bytes': path.stat().st_size, 'sha256': digest(path)})
    native_mappings = [
        ('ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png', 'Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png'),
        ('ios/App/App/Base.lproj/LaunchScreen.storyboard', 'Resources/LaunchScreen.storyboard'),
        ('ios/App/App/Assets.xcassets/Splash.imageset/Contents.json', 'Resources/Assets.xcassets/Splash.imageset/Contents.json'),
        ('ios/App/App/Assets.xcassets/Splash.imageset/splash-logo.png', 'Resources/Assets.xcassets/Splash.imageset/splash-logo.png'),
    ]
    for origin, destination in native_mappings:
        path, target = source / origin, ROOT / destination
        if not path.is_file():
            continue
        if args.audit_only:
            if not target.is_file() or digest(target) != digest(path):
                raise ValueError(f'Native copy differs from source: {destination}')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        files.append({'source': origin, 'path': destination, 'bytes': path.stat().st_size, 'sha256': digest(path)})
    published = source / 'src/data/published-citation-source-page-assets.json'
    target = ROOT / 'Resources/Data/published-pages.json'
    if args.audit_only:
        if json.loads(published.read_text()) != json.loads(target.read_text()):
            raise ValueError('published-pages.json differs from source')
    else:
        shutil.copyfile(published, target)
    sources = json.loads((ROOT / 'Resources/Data/sources.json').read_text())
    missing = []
    for content in sources:
        for asset in content.get('assets', []):
            url = asset.get('image_url', '')
            if not url.startswith('/assets/'):
                continue
            if not (source / 'public' / url.lstrip('/')).is_file():
                missing.append({'book_id': content['summary']['book_id'], 'asset_id': asset.get('asset_id'), 'url': url})
    report = {'schema_version': 1, 'scope': 'Explicit public/assets, public/icons, public/rag excluding runtime, native iOS app icon and launch resources; repository fixtures only', 'excluded': ['.env', 'device databases', 'personal attachments', 'node_modules', 'public/rag/runtime'], 'files': files, 'known_missing_source_assets': sorted(missing, key=lambda x: (x['book_id'], x['asset_id'] or '', x['url']))}
    (ROOT / 'References/resource-copy-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    # Preserve exact original license/notice files found in public; allowlist has no private data.
    print(f'{len(files)} resources verified/copied; {len(missing)} missing source asset references documented (no substitute images).')

if __name__ == '__main__':
    main()
