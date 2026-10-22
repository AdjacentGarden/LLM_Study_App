#!/usr/bin/env python3
"""Unwrap the existing TrueType WOFF without changing outlines; repair instance names."""
from pathlib import Path
import argparse,shutil,json,hashlib
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',nargs='?',type=Path,help='Directory containing original WOFF files and OFL.txt')
parser.add_argument('--output-root',type=Path,help='Alternative repository layout for deterministic verification')
args=parser.parse_args()
try:
 from fontTools.ttLib import TTFont
except ImportError:
 raise SystemExit('Font refresh requires fonttools==4.60.2 (python3 -m pip install -r scripts/requirements-fonts.txt). Ordinary Xcode builds use the committed TTFs.')
repository=Path(__file__).resolve().parent.parent
root=args.output_root.resolve() if args.output_root else repository
source=args.source if args.source else repository/'Resources/Content/assets/fonts'
(root/'Resources/Fonts').mkdir(parents=True,exist_ok=True)
(root/'References').mkdir(parents=True,exist_ok=True)
report=[]
for weight,style in [(400,'Regular'),(700,'Bold')]:
 p=source/f'NotoSansSC-{weight}.woff'; f=TTFont(p,recalcTimestamp=False); f.flavor=None
 name=f['name']; family='BookCourse Noto Sans SC'; ps=f'BookCourseNotoSansSC-{style}'
 for record in name.names:
  if record.nameID in (1,2,3,4,6,16,17):
   value={1:family,2:style,3:f'{ps};1.0',4:f'{family} {style}',6:ps,16:family,17:style}[record.nameID]
   record.string=value.encode(record.getEncoding(),errors='replace')
 f['OS/2'].usWeightClass=weight
 if weight==700:
  f['head'].macStyle|=1; f['OS/2'].fsSelection=(f['OS/2'].fsSelection|32)&~64
 else:
  f['head'].macStyle&=~1; f['OS/2'].fsSelection=(f['OS/2'].fsSelection|64)&~32
 out=root/'Resources/Fonts'/f'{ps}.ttf'; f.save(out)
 report.append({'source':p.name,'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'output':out.name,'output_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'postscript_name':ps,'glyphs':len(f.getGlyphOrder()),'weight':weight})
shutil.copyfile(source/'OFL.txt',root/'Resources/Fonts/OFL.txt')
(root/'References/font-conversion.json').write_text(json.dumps(report,indent=2)+'\n')
print('Prepared native Regular/Bold fonts with distinct PostScript names.')
