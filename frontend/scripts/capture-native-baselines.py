#!/usr/bin/env python3
"""Capture one explicitly selected simulator; compare at logical-point scale without stretching.

Run using .tools/bin/python (Pillow). This script never builds or changes app sources,
never targets 'booted', and never reads or overwrites the user's production app data.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import plistlib
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
try:
    from PIL import Image, ImageChops, ImageDraw, ImageStat
except ImportError:
    raise SystemExit('Pillow is required. Use this repository\'s .tools/bin/python interpreter.')

ROOT = Path(__file__).resolve().parent.parent
ROUTES = ['home','onboarding','courseSetup','courseDetail','upload','parseReady','processing','courseImportProcessing','chapterConfirm','courseReady','courseImportReady','library','community','communityBook','communityImport','study','plan','flashcards','lesson','assignment','diagnosis','mistakes','notes','voiceNote','source','export','profile','settings']
SHEETS = ['chat','note','noteType','editChapter','bookSwitcher','addMaterials']
LANDSCAPE = ['home','onboarding','courseSetup','community','study','lesson','source','assignment','notes','profile','settings']
DEFAULT_UDID = 'C4116145-9865-4813-9CDA-33CCE36FA756'


def source_folder(family, orientation):
    if family == 'iphone':return 'iphone-402x874' if orientation == 'portrait' else 'iphone-landscape-874x402'
    return 'ipad-834x1194' if orientation == 'portrait' else 'ipad-landscape-1194x834'


def run(*args, optional=False, timeout=90):
    completed = subprocess.run([str(arg) for arg in args], text=True, capture_output=True, timeout=timeout)
    if completed.returncode and not optional:
        raise RuntimeError(f'{" ".join(map(str,args))}: {completed.stderr.strip() or completed.stdout.strip()}')
    return completed


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024*1024), b''): digest.update(data)
    return digest.hexdigest()


def scene_plan():
    result = [{'scene':route,'route':route,'orientation':'portrait'} for route in ROUTES]
    result += [{'scene':'sheet-'+sheet,'route':'study','sheet':sheet,'orientation':'portrait'} for sheet in SHEETS]
    result += [{'scene':'ai-assistant','route':'home','assistant':True,'orientation':'portrait'}]
    result += [{'scene':'onboarding-step-'+str(step+1),'route':'onboarding','fixtureStep':step,'orientation':'portrait'} for step in [1,2]]
    result += [{'scene':'courseSetup-step-'+str(step+1),'route':'courseSetup','fixtureStep':step,'orientation':'portrait'} for step in range(1,6)]
    result += [{'scene':route,'route':route,'orientation':'landscape'} for route in LANDSCAPE]
    return result


def comparison(native_path, source_path, target, metadata, source_scene, top, bottom):
    native_full = Image.open(native_path).convert('RGB')
    scale = metadata['scale']
    native = native_full.resize((round(native_full.width/scale), round(native_full.height/scale)), Image.Resampling.LANCZOS)
    source = Image.open(source_path).convert('RGB')
    logical_path = target / (metadata['scene']+'-native-1x.png')
    native.save(logical_path)
    width=max(native.width,source.width); height=max(native.height,source.height)
    board=Image.new('RGB',(width*2,height+28),'#ffffff'); draw=ImageDraw.Draw(board)
    draw.text((8,8),f'SOURCE {source.width}x{source.height}',fill='#20263a')
    draw.text((width+8,8),f'NATIVE {native.width}x{native.height}',fill='#20263a')
    # Gray area represents absent viewport pixels, not app content.
    draw.rectangle((0,28,width,height+28),fill='#dce0e6')
    draw.rectangle((width,28,width*2,height+28),fill='#dce0e6')
    board.paste(source,(0,28));board.paste(native,(width,28))
    board_path=target/(metadata['scene']+'-comparison.png');board.save(board_path)
    common=(min(native.width,source.width),min(native.height,source.height))
    a=source.crop((0,0,*common));b=native.crop((0,0,*common))
    mask=Image.new('L',common,255); mask_draw=ImageDraw.Draw(mask)
    source_top=source_scene.get('boxes',{}).get('.ios-status-bar',{}).get('height',0)
    source_bottom=source_scene.get('boxes',{}).get('.home-indicator',{}).get('height',0)
    excluded_top=min(common[1],math.ceil(max(top,source_top)))
    excluded_bottom_start=min(common[1],source.height-math.ceil(source_bottom),native.height-math.ceil(bottom))
    if excluded_top:mask_draw.rectangle((0,0,common[0],excluded_top-1),fill=0)
    if excluded_bottom_start<common[1]:mask_draw.rectangle((0,excluded_bottom_start,common[0],common[1]),fill=0)
    difference=ImageChops.difference(a,b)
    mean=ImageStat.Stat(difference,mask=mask).mean
    gray=difference.convert('L')
    heat=Image.merge('RGB',(gray.point(lambda v:min(255,v*4)),gray.point(lambda v:min(255,max(0,v*2-40))),gray.point(lambda v:0)))
    heat.paste((220,224,230),(0,0,common[0],excluded_top))
    if excluded_bottom_start<common[1]:heat.paste((220,224,230),(0,excluded_bottom_start,common[0],common[1]))
    heat_path=target/(metadata['scene']+'-difference.png');heat.save(heat_path)
    overlay=Image.blend(a,b,0.5);overlay_path=target/(metadata['scene']+'-overlay.png');overlay.save(overlay_path)
    pixels=list(difference.getdata());valid=list(mask.getdata());count=sum(bool(v) for v in valid)
    changed=sum(1 for pixel,keep in zip(pixels,valid) if keep and max(pixel)>24)
    return {
        'sourceFile':str(source_path.relative_to(ROOT)), 'nativeLogicalFile':str(logical_path.relative_to(ROOT)),
        'comparisonFile':str(board_path.relative_to(ROOT)), 'overlayFile':str(overlay_path.relative_to(ROOT)), 'differenceFile':str(heat_path.relative_to(ROOT)),
        'sourceLogicalSize':[source.width,source.height], 'nativeLogicalSize':[native.width,native.height],
        'viewportDeltaPoints':[native.width-source.width,native.height-source.height],
        'commonComparedRect':[0,excluded_top,common[0],max(0,excluded_bottom_start-excluded_top)],
        'systemBandsExcluded':{'sourceTop':source_top,'sourceBottom':source_bottom,'configuredNativeTop':top,'configuredNativeBottom':bottom},
        'meanAbsoluteRGBDifference':round(sum(mean)/3,4), 'fractionPixelsChannelDifferenceAbove24':round(changed/max(1,count),6),
        'sameViewport':source.size==native.size,
        'geometryToleranceResult':'NOT_ASSERTED',
        'interpretation':'Pixel statistics on the common logical rectangle only; no threshold pass/fail and no proof of 2-point geometry parity. CoreText/WebKit antialiasing and viewport differences remain visible.'
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udid',default=DEFAULT_UDID)
    parser.add_argument('--device-family',choices=['iphone','ipad'],default='ipad')
    parser.add_argument('--app',type=Path,default=ROOT/'DerivedData/Build/Products/Debug-iphonesimulator/BookCourse.app')
    parser.add_argument('--output',type=Path,default=ROOT/'output/NativeScreenshots')
    parser.add_argument('--comparisons',type=Path,default=ROOT/'output/VisualComparisons')
    parser.add_argument('--web-baselines',type=Path,default=ROOT/'References/VisualBaselines')
    parser.add_argument('--scale',type=float,help='Pixel scale (default iPad 2; iPhone 3).')
    parser.add_argument('--native-safe-top',type=float,help='Default iPad 24; iPhone 62. Override when actual device differs.')
    parser.add_argument('--native-safe-bottom',type=float,help='Default iPad 20; iPhone 34. Override when actual device differs.')
    parser.add_argument('--native-landscape-safe-top',type=float)
    parser.add_argument('--native-landscape-safe-bottom',type=float)
    parser.add_argument('--settle',type=float,default=2.8)
    parser.add_argument('--only',help='Comma-separated scene names; both orientations are included when planned.')
    parser.add_argument('--orientation',choices=['portrait','landscape','all'],default='all')
    parser.add_argument('--plan-only',action='store_true')
    parser.add_argument('--compare-only',action='store_true')
    parser.add_argument('--landscape-frame-index',type=Path,help='frames.json from capture-landscape-framebuffer.py; uses unmodified real display frames instead of defective XCT app screenshots')
    parser.add_argument('--landscape-xcresult',type=Path,help='Import genuine XCUIDevice-rotated landscape screenshots into the existing manifest, then compare.')
    args=parser.parse_args()
    if args.scale is None:args.scale=3 if args.device_family=='iphone' else 2
    if args.native_safe_top is None:args.native_safe_top=62 if args.device_family=='iphone' else 24
    if args.native_safe_bottom is None:args.native_safe_bottom=34 if args.device_family=='iphone' else 20
    if args.native_landscape_safe_top is None:args.native_landscape_safe_top=0 if args.device_family=='iphone' else 24
    if args.native_landscape_safe_bottom is None:args.native_landscape_safe_bottom=21 if args.device_family=='iphone' else 20
    if args.udid=='booted':parser.error('Use an explicit dedicated simulator UDID; "booted" can affect another test run.')
    plan=scene_plan()
    if args.only:plan=[item for item in plan if item['scene'] in set(args.only.split(','))]
    if args.orientation!='all':plan=[item for item in plan if item['orientation']==args.orientation]
    if args.plan_only:
        print(json.dumps({'udid':args.udid,'app':str(args.app),'requestedScenes':plan},ensure_ascii=False,indent=2));return 0
    args.output.mkdir(parents=True,exist_ok=True);args.comparisons.mkdir(parents=True,exist_ok=True)
    web_manifest=json.loads((args.web_baselines/'manifest.json').read_text())
    web_by_file={item['file']:item for item in web_manifest['scenes']}
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    manifest_path=args.output/f'capture-{args.udid}.json'
    previous=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest={'runID':run_id,'deviceUDID':args.udid,'deviceFamily':args.device_family,'capturedAtUTC':datetime.now(timezone.utc).isoformat(),'requestedSceneCount':len(plan),'scenes':[],'failures':[],
              'runtimeVerification':'simctl launch succeeded and screenshot dimensions were checked; requested route/sheet identity must also be reviewed in the image or XCUITest. This is not a substitute for functional flow tests.',
              'scale':args.scale,'requestedReduceMotion':True,'sourceBaselineCommit':web_manifest['sourceCommit'],
              'nativeSafeInsets':{'top':args.native_safe_top,'bottom':args.native_safe_bottom,'landscapeTop':args.native_landscape_safe_top,'landscapeBottom':args.native_landscape_safe_bottom,'provenance':'explicit command configuration; excluded from app-content pixel statistics'}}
    if args.landscape_xcresult:
        if not previous.get('scenes'):raise RuntimeError('Capture portrait first; landscape import requires its existing manifest')
        manifest=previous
        exported=args.output/('xcresult-attachments-'+run_id)
        run('xcrun','xcresulttool','export','attachments','--path',args.landscape_xcresult,'--output-path',exported,timeout=180)
        summary=json.loads(run('xcrun','xcresulttool','get','test-results','summary','--path',args.landscape_xcresult).stdout)
        if summary.get('totalTestCount',0)<1:raise RuntimeError('The xcresult contains zero tests; use the full Target/Class/test selector')
        if summary.get('result')!='Passed':manifest.setdefault('failures',[]).append({'scene':'landscape-xctest','orientation':'landscape','error':'XCTest did not pass: '+str(summary.get('result'))})
        manifest['landscapeXCTest']={'resultBundle':str(args.landscape_xcresult),'summary':summary,'importedAtUTC':datetime.now(timezone.utc).isoformat()}
        attachments=json.loads((exported/'manifest.json').read_text())
        frame_index=json.loads(args.landscape_frame_index.read_text()) if args.landscape_frame_index else None
        if frame_index:
            if frame_index['deviceUDID']!=args.udid:raise RuntimeError('Framebuffer index belongs to another simulator')
            if Path(frame_index['resultBundle']).resolve()!=args.landscape_xcresult.resolve():raise RuntimeError('Framebuffer index is from a different XCTest run')
            if frame_index['appDebugDylibSHA256']!=manifest.get('appDebugDylibSHA256'):raise RuntimeError('Framebuffer app hash differs from portrait app hash')
        for route in LANDSCAPE:
            attachment=next((a for test in attachments for a in test.get('attachments',[]) if a.get('suggestedHumanReadableName','').startswith('landscape-'+route)),None)
            try:
                if not attachment:raise RuntimeError('No named screenshot attachment found')
                if attachment.get('deviceId') != args.udid:raise RuntimeError('Attachment belongs to another simulator: '+attachment.get('deviceId','unknown'))
                original=exported/attachment['exportedFileName'];frame=None
                if frame_index:
                    frames=[f for f in frame_index['frames'] if f['completedAt']<=attachment['timestamp']-.1]
                    if not frames:raise RuntimeError('No screen frame precedes route attachment')
                    frame=max(frames,key=lambda f:f['completedAt'])
                    if attachment['timestamp']-frame['completedAt']>3:raise RuntimeError('Nearest screen frame is more than 3 seconds old')
                    original=Path(frame['file'])
                    if sha256(original)!=frame['sha256']:raise RuntimeError('Screen frame checksum changed')
                image=Image.open(original);logical=[round(image.width/args.scale),round(image.height/args.scale)]
                if logical[0]<=logical[1]:raise RuntimeError('Attachment is not a genuine landscape capture: '+str(logical))
                folder=args.output/(args.device_family+'-landscape-'+str(logical[0])+'x'+str(logical[1]));folder.mkdir(exist_ok=True);target=folder/(route+'.png');shutil.copyfile(original,target)
                scene={'scene':route,'route':route,'orientation':'landscape','scale':args.scale,'file':str(target.relative_to(ROOT)),'nativeLogicalSize':logical,'pixelSize':[image.width,image.height],'orientationVerified':True,'sha256':sha256(target),'verification':'XCUIDevice rotation; XCTest window aspect assertion; screenshot manual review pending','appExecutableSHA256':manifest.get('appExecutableSHA256'),'appDebugDylibSHA256':manifest.get('appDebugDylibSHA256'),'captureRunID':args.landscape_xcresult.stem,'captureProvenance':{'resultBundle':str(args.landscape_xcresult),'attachment':attachment,'screenFramebuffer':frame,'method':'unmodified simctl screen frame during real XCUIDevice rotation' if frame else 'XCT app.screenshot attachment; manual orientation review required'}}
                manifest['scenes']=[s for s in manifest['scenes'] if not(s['orientation']=='landscape' and s['scene']==route)]+[scene]
                manifest['failures']=[f for f in manifest.get('failures',[]) if not(f['orientation']=='landscape' and f['scene']==route)]
                print('imported '+scene['file'],flush=True)
            except Exception as error:
                manifest.setdefault('failures',[]).append({'scene':route,'orientation':'landscape','error':str(error)})
        manifest['requestedSceneCount']=len(scene_plan())
    elif args.compare_only:
        manifest=previous
        if not manifest.get('scenes'):raise RuntimeError('No previous native capture manifest to compare')
    else:
        devices=json.loads(run('xcrun','simctl','list','devices','available','--json').stdout)['devices']
        device=next((dict(item,runtime=runtime) for runtime,items in devices.items() for item in items if item['udid']==args.udid),None)
        if not device:raise RuntimeError('Dedicated simulator was not found among available devices')
        manifest['device']=device
        if ('iPhone' in device['name']) != (args.device_family=='iphone'):raise RuntimeError('Selected simulator does not match --device-family; pass the explicit correct UDID/family')
        if device['state']!='Booted':run('xcrun','simctl','boot',args.udid);run('xcrun','simctl','bootstatus',args.udid,'-b',timeout=180)
        info=plistlib.loads((args.app/'Info.plist').read_bytes());bundle=info['CFBundleIdentifier']
        manifest['bundleIdentifier']=bundle;manifest['appExecutableSHA256']=sha256(args.app/info['CFBundleExecutable'])
        dylib=args.app/(info['CFBundleExecutable']+'.debug.dylib')
        if dylib.exists():manifest['appDebugDylibSHA256']=sha256(dylib)
        if args.only:
            keys={(p['orientation'],p['scene']) for p in plan}
            for old in previous.get('scenes',[]):
                if (old['orientation'],old['scene']) not in keys:
                    old.setdefault('appExecutableSHA256',previous.get('appExecutableSHA256'))
                    old.setdefault('appDebugDylibSHA256',previous.get('appDebugDylibSHA256'))
                    old.setdefault('captureRunID',previous.get('runID'))
                    manifest['scenes'].append(old)
            manifest['failures']=[f for f in previous.get('failures',[]) if (f['orientation'],f['scene']) not in keys]
            if previous.get('landscapeXCTest'):manifest['landscapeXCTest']=previous['landscapeXCTest']
            manifest['requestedSceneCount']=len(scene_plan())
        run('xcrun','simctl','install',args.udid,args.app,timeout=120)
        run('xcrun','simctl','status_bar',args.udid,'override','--time','9:41','--dataNetwork','wifi','--wifiMode','active','--wifiBars','3','--batteryState','charged','--batteryLevel','100')
        for item in plan:
            scene=dict(item)
            web_folder=source_folder(args.device_family,item['orientation'])
            source_scene=web_by_file.get(web_folder+'/'+item['scene']+'.png',{})
            fixture=source_scene.get('fixtureState',{})
            chapter='c2s1' if item.get('sheet')=='editChapter' else fixture.get('chapterID') or 'c1s1'
            scene['sourceFixtureState']=fixture
            scene['fixtureDifferences']=['editChapter background selected chapter is c2s1 to match the explicit source editor target; source background remains c1s1'] if item.get('sheet')=='editChapter' else []
            command=['xcrun','simctl','launch','--terminate-running-process',args.udid,bundle,'--fixture','--fixture-session',run_id+'-'+item['orientation']+'-'+item['scene'],'--fixture-reduce-motion','--visual-baseline','--fixture-orientation',item['orientation'],'--route',item['route'],'--chapter',chapter]
            if item.get('sheet'):command+=['--sheet',item['sheet']]
            if item.get('assistant'):command+=['--assistant']
            if 'fixtureStep' in item:command+=['--fixture-step',str(item['fixtureStep'])]
            try:
                launched=run(*command,timeout=120);scene['launchOutput']=launched.stdout.strip();scene['launchArguments']=command[6:]
                time.sleep(max(0.8,args.settle))
                temporary=args.output/(item['orientation']+'-'+item['scene']+'-capture.png')
                run('xcrun','simctl','io',args.udid,'screenshot','--type=png',temporary)
                image=Image.open(temporary);logical=[round(image.width/args.scale),round(image.height/args.scale)]
                matches=(logical[0]<logical[1])==(item['orientation']=='portrait')
                if not matches:raise RuntimeError(f'Requested {item["orientation"]}, screenshot logical size is {logical}; fixture orientation did not settle')
                folder=args.device_family+('-' if item['orientation']=='portrait' else '-landscape-')+f'{logical[0]}x{logical[1]}'
                target=args.output/folder;target.mkdir(exist_ok=True);screenshot=target/(item['scene']+'.png');temporary.replace(screenshot)
                scene.update({'file':str(screenshot.relative_to(ROOT)),'nativeLogicalSize':logical,'pixelSize':[image.width,image.height],'scale':args.scale,'orientationVerified':matches,'verification':'captured; manual review pending','sha256':sha256(screenshot),'appExecutableSHA256':manifest['appExecutableSHA256'],'appDebugDylibSHA256':manifest.get('appDebugDylibSHA256'),'captureRunID':run_id})
                manifest['scenes'].append(scene);print('captured '+scene['file'],flush=True)
            except Exception as error:
                manifest['failures'].append({'scene':item['scene'],'orientation':item['orientation'],'error':str(error)});print(f'FAILED {item["orientation"]}/{item["scene"]}: {error}',file=sys.stderr,flush=True)
            finally:
                manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        run('xcrun','simctl','terminate',args.udid,bundle,optional=True)
    for scene in manifest['scenes']:
        family=manifest.get('deviceFamily',args.device_family)
        web_folder=source_folder(family,scene['orientation'])
        logical=scene['nativeLogicalSize']
        matching=family+('-' if scene['orientation']=='portrait' else '-landscape-')+str(logical[0])+'x'+str(logical[1])
        if matching+'/'+scene['scene']+'.png' in web_by_file:web_folder=matching
        web_file=web_folder+'/'+scene['scene']+'.png'
        if web_file not in web_by_file:
            scene['comparisonStatus']='NO_SOURCE_SCENE';continue
        target=args.comparisons/Path(scene['file']).parent.name;target.mkdir(exist_ok=True)
        top=args.native_safe_top if scene['orientation']=='portrait' else args.native_landscape_safe_top
        bottom=args.native_safe_bottom if scene['orientation']=='portrait' else args.native_landscape_safe_bottom
        scene['comparison']=comparison(ROOT/scene['file'],args.web_baselines/web_file,target,scene,web_by_file[web_file],top,bottom)
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    write_report(manifest,args.comparisons,manifest_path)
    print(f'{len(manifest["scenes"])} captured; {len(manifest["failures"])} failures. Manifest: {manifest_path}',flush=True)
    return 1 if manifest['failures'] else 0


def write_report(manifest,destination,manifest_path):
    lines=['# Native/source visual comparison inventory','',f'- Device: `{manifest["deviceUDID"]}`',f'- Capture manifest: `{manifest_path.relative_to(ROOT)}`',f'- Captured: {len(manifest["scenes"])}; failed: {len(manifest["failures"])}','',
           'These are capture/comparison artifacts, not a 2-point acceptance certificate. Images are compared at 1 logical point per pixel, without stretching either viewport. Gray areas in side-by-side images are absent viewport pixels. Pixel scores exclude only configured system chrome and cannot distinguish layout differences from font antialiasing.','',
           '| Orientation / scene | Source size | Native size | Viewport delta | RGB MAE | Changed >24 | Review |','|---|---:|---:|---:|---:|---:|---|']
    for scene in manifest['scenes']:
        c=scene.get('comparison')
        if c:lines.append(f'| [{scene["orientation"]} / {scene["scene"]}]({Path(scene["file"]).parent.name}/{scene["scene"]}-comparison.png) | {c["sourceLogicalSize"]} | {c["nativeLogicalSize"]} | {c["viewportDeltaPoints"]} | {c["meanAbsoluteRGBDifference"]:.2f} | {c["fractionPixelsChannelDifferenceAbove24"]:.1%} | {scene.get("manualReview",{}).get("status","pending manual geometry review")} |')
    if manifest['failures']:
        lines+=['','## Capture failures','']+[f'- {item["orientation"]}/{item["scene"]}: {item["error"]}' for item in manifest['failures']]
    (destination/'README.md').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print(str(error),file=sys.stderr);sys.exit(1)
