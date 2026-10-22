#!/usr/bin/env python3
"""Build an offline review index from the archived native capture manifests."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rows = []
for manifest_path in sorted((ROOT / 'References/NativeScreenshots').glob('capture-*.json')):
    manifest = json.loads(manifest_path.read_text())
    for scene in manifest.get('scenes', []):
        comparison = scene.get('comparison')
        if not comparison:
            continue
        rows.append({'device': manifest['deviceFamily'], 'scene': scene['scene'],
                     'orientation': scene['orientation'], 'size': scene['nativeLogicalSize'],
                     'image': '../../' + comparison['comparisonFile'],
                     'overlay': '../../' + comparison['overlayFile'],
                     'difference': '../../' + comparison['differenceFile'],
                     'native': '../../' + scene['file'],
                     'source': '../../' + comparison['sourceFile'],
                     'hash': scene.get('appDebugDylibSHA256'),
                     'geometry': comparison['geometryToleranceResult']})
rows.sort(key=lambda row: (row['device'], row['orientation'], row['scene']))
data = json.dumps(rows, ensure_ascii=False).replace('<', '\\u003c')
html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BookCourse 原生界面对照</title><style>
*{box-sizing:border-box}body{margin:0;background:#f6f8fb;color:#20263a;font:16px system-ui,sans-serif}header{position:sticky;top:0;padding:16px 24px;background:#fff;border-bottom:1px solid #d7dee8;z-index:1}h1{font-size:23px;margin:0 0 8px}p{line-height:1.6;margin:6px 0}label{display:inline-flex;gap:8px;align-items:center;margin:8px 16px 0 0}select{font:inherit;max-width:270px;padding:7px;border:1px solid #d7dee8;border-radius:8px;background:white}main{padding:20px;max-width:1600px;margin:auto}nav{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}a{color:#6d28d9}img{display:block;max-width:100%;height:auto;background:#fff;border:1px solid #d7dee8}code{overflow-wrap:anywhere}.meta{font-size:14px;color:#4f5e75}button{border:1px solid #d7dee8;background:#fff;border-radius:8px;padding:7px 14px;font:inherit;cursor:pointer}
</style><header><h1>BookCourse · Web / SwiftUI 对照</h1>
<p class="meta">左侧为旧项目当前工作区，右侧为原生模拟器实拍。仅按设备比例还原逻辑点，不拉伸正文或隐藏差异。截图覆盖与像素差异统计不等于所有边界 ≤2pt 验收。</p>
<label>设备<select id="device"><option value="iphone">iPhone</option><option value="ipad">iPad</option></select></label>
<label>方向<select id="orientation"><option value="portrait">竖屏</option><option value="landscape">真实横屏</option></select></label>
<label>页面<select id="scene"></select></label><button id="previous">上一页</button> <button id="next">下一页</button></header>
<main><p id="meta" class="meta"></p><nav id="links"></nav><img id="image" alt="旧界面与SwiftUI界面对照"></main>
<script>const rows=__DATA__;const device=document.querySelector('#device'),orientation=document.querySelector('#orientation'),scene=document.querySelector('#scene');
function options(){const current=scene.value;scene.replaceChildren();for(const row of rows.filter(r=>r.device===device.value&&r.orientation===orientation.value)){const option=document.createElement('option');option.value=row.scene;option.textContent=row.scene;scene.append(option)}if([...scene.options].some(o=>o.value===current))scene.value=current;show()}
function show(){const row=rows.find(r=>r.device===device.value&&r.orientation===orientation.value&&r.scene===scene.value);if(!row)return;document.querySelector('#image').src=row.image;document.querySelector('#meta').textContent=`${row.size.join(' × ')} pt · ${row.scene} · 几何阈值状态：${row.geometry} · App SHA-256: ${row.hash}`;const links=document.querySelector('#links');links.replaceChildren();for(const [label,key]of[['左右对照','image'],['50%叠图','overlay'],['差异热图','difference'],['原生原始图','native'],['Web基线','source']]){const a=document.createElement('a');a.textContent=label;a.href=row[key];a.target='_blank';links.append(a)}}
device.onchange=orientation.onchange=options;scene.onchange=show;for(const [id,delta]of[['previous',-1],['next',1]])document.querySelector('#'+id).onclick=()=>{scene.selectedIndex=(scene.selectedIndex+delta+scene.options.length)%scene.options.length;show()};options();</script></html>'''.replace('__DATA__', data)
target = ROOT / 'References/VisualComparisons/index.html'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(html)
print(f'Wrote {target.relative_to(ROOT)}: {len(rows)} scenes.')
