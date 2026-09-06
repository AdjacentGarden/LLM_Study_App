"""Render real first-page previews without modifying source PDFs."""
import json
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')


def main():
    from PIL import Image, ImageDraw
    destination = ROOT/'data/covers'
    destination.mkdir(exist_ok=True)
    catalog = json.loads((ROOT/'output/examples-20260905/inventory.json').read_text())
    ids = ['biology-required-2'] + [x['book_id'] for x in catalog if x['book_id'] != '6b0c596886a5']
    sheet = Image.new('RGB',(1000,640),'#f0edf7')
    draw = ImageDraw.Draw(sheet)
    with sqlite3.connect('file:'+str(ROOT/'data/state/ocr_jobs.sqlite3')+'?mode=ro',uri=True) as db:
        for i, book_id in enumerate(ids):
            name, source = db.execute('SELECT original_name,file_path FROM books WHERE book_id=?',(book_id,)).fetchone()
            prefix = destination/book_id
            subprocess.run(['pdftoppm','-f','1','-l','1','-singlefile','-scale-to','800',
                            '-jpeg','-jpegopt','quality=85',source,str(prefix)],check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            with Image.open(prefix.with_suffix('.jpg')) as cover:
                spread = cover.width > cover.height * 1.2
                half_width = cover.width // 2
            if spread:
                # This collection's landscape first page is a complete jacket;
                # the inspected front cover is the right-hand PDF region.
                subprocess.run(['pdftoppm','-f','1','-l','1','-singlefile','-scale-to','800',
                                '-x',str(half_width),'-W',str(half_width),'-jpeg','-jpegopt',
                                'quality=85',source,str(prefix)],check=True,stderr=subprocess.PIPE)
            with Image.open(prefix.with_suffix('.jpg')) as cover:
                cover.thumbnail((172,275))
                x,y = (i%5)*200+14,(i//5)*320+10
                sheet.paste(cover,(x+(172-cover.width)//2,y))
                draw.text((x,y+280),f'{i+1:02}  {book_id}',fill='#302b49')
            print(json.dumps({'book_id':book_id,'source_page':1,'title':name},ensure_ascii=False),flush=True)
    sheet.save(destination/'contact-sheet.jpg')


if __name__ == '__main__':
    main()
