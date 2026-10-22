#!/usr/bin/env python3
"""Collect exact local license texts; never downloads or resolves packages by itself."""
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--packages', type=Path, default=ROOT / '.build/packages', help='Xcode clonedSourcePackagesDirPath')
parser.add_argument('--source', type=Path, help='Optional explicit original repository to refresh Lucide notice')
args = parser.parse_args()
licenses = ROOT / 'LICENSES'
licenses.mkdir(exist_ok=True)
lucide = (args.source / 'node_modules/lucide-react/LICENSE') if args.source else ROOT / 'Features/Notes/LICENSE.lucide.txt'
copies = {
    'Lucide-ISC-and-Feather-MIT.txt': lucide,
    'Phosphor-MIT.txt': ROOT / 'Resources/Content/icons/LICENSE.phosphor-icons.txt',
    'NotoSansSC-OFL-1.1.txt': ROOT / 'Resources/Content/assets/fonts/OFL.txt',
    'ONNX-Runtime-MIT.txt': args.packages / 'checkouts/onnxruntime-swift-package-manager/LICENSE',
    'ONNX-Runtime-Binary-MIT.txt': args.packages / 'artifacts/onnxruntime-swift-package-manager/onnxruntime/LICENSE',
    'ZIPFoundation-MIT.txt': args.packages / 'checkouts/ZIPFoundation/LICENSE',
}
for name, original in copies.items():
    target = licenses / name
    if original.is_file():
        shutil.copyfile(original, target)
    elif not target.is_file():
        parser.error(f'Missing original notice {original}. Resolve pinned packages, then rerun; existing committed notices work offline.')
bundle = ROOT / 'Resources/Content/licenses'
bundle.mkdir(parents=True, exist_ok=True)
for path in sorted(licenses.iterdir()):
    if path.is_file():
        shutil.copyfile(path, bundle / path.name)
notice = ROOT / 'THIRD_PARTY_NOTICES.md'
if notice.exists():
    shutil.copyfile(notice, bundle / notice.name)
print(f'Preserved {len(list(licenses.iterdir()))} license/provenance files and embedded them in the native content bundle.')
