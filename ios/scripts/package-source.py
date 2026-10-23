#!/usr/bin/env python3
"""Package source/evidence only. Never include visitor credentials or build caches."""
from pathlib import Path
import hashlib
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
destination = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / '.build/CloudPath-native-source.zip'
destination.parent.mkdir(parents=True, exist_ok=True)
excluded = {'.build', '.runtime', '.swiftpm', 'xcuserdata', '.DS_Store'}
files = [path for path in root.rglob('*') if path.is_file()
         and not set(path.relative_to(root).parts).intersection(excluded)
         and path != destination
         and path.name not in {'cookies.json', 'private-client-state.json', 'acceptance-client-state.json'}]
with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, Path('CloudPath-iOS') / path.relative_to(root))
with zipfile.ZipFile(destination) as archive:
    assert not any('/.runtime/' in name or '/.build/' in name or name.endswith('/cookies.json') for name in archive.namelist())
print(f'Source archive: {destination}')
print(f'Files: {len(files)}; SHA256: {hashlib.sha256(destination.read_bytes()).hexdigest()}')
print('This archive is source and validation evidence, not a signed IPA.')
