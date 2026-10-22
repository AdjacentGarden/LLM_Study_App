#!/usr/bin/env python3
"""Run the existing physical-rotation UI test and observe its real simulator framebuffer.

XCT app.screenshot() can be clipped/sideways on iPadOS 26. Preserve those attachments,
but use unmodified simctl screenshots taken immediately before each named attachment.
This script does not build, change app sources, rotate images, or access user data.
"""
import argparse, hashlib, json, subprocess, time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--udid',required=True)
p.add_argument('--derived-data',type=Path,default=ROOT/'DerivedData')
p.add_argument('--result',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.udid=='booted':p.error('An explicit dedicated simulator UDID is required')
a.output.mkdir(parents=True,exist_ok=True)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
app=Path(subprocess.check_output(['xcrun','simctl','get_app_container',a.udid,'com.littleri.bookcourseai.swiftui','app'],text=True).strip())
metadata={'deviceUDID':a.udid,'resultBundle':str(a.result.resolve()),'appDebugDylibSHA256':digest(app/'BookCourse.debug.dylib'),'frames':[],'captureMethod':'Unmodified simctl screen framebuffer during XCUIDevice landscapeLeft test; no image rotation or cropping'}
command=['xcodebuild','-project','BookCourse.xcodeproj','-scheme','BookCourse','-configuration','Debug','-destination','platform=iOS Simulator,id='+a.udid,'-derivedDataPath',str(a.derived_data),'-clonedSourcePackagesDirPath',str(ROOT/'.build/packages'),'-disableAutomaticPackageResolution','-collect-test-diagnostics','never','-parallel-testing-enabled','NO','test-without-building','-only-testing:BookCourseUITests/BookCourseUITests/testLandscapeCorePages','-resultBundlePath',str(a.result),'ARCHS=arm64','CODE_SIGNING_ALLOWED=NO']
with (a.output/'xctest.log').open('w') as log:
 proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
 while proc.poll() is None:
  time.sleep(.7);path=a.output/f'{len(metadata["frames"])+1:04d}.png';started=time.time()
  result=subprocess.run(['xcrun','simctl','io',a.udid,'screenshot','--type=png',str(path)],capture_output=True)
  if result.returncode==0:metadata['frames'].append({'file':str(path.resolve()),'startedAt':started,'completedAt':time.time(),'sha256':digest(path)})
  (a.output/'frames.json').write_text(json.dumps(metadata,indent=2)+'\n')
metadata['testExitCode']=proc.returncode
installed_after=Path(subprocess.check_output(['xcrun','simctl','get_app_container',a.udid,'com.littleri.bookcourseai.swiftui','app'],text=True).strip())
metadata['installedAppDebugDylibSHA256After']=digest(installed_after/'BookCourse.debug.dylib')
if metadata['installedAppDebugDylibSHA256After']!=metadata['appDebugDylibSHA256']:metadata['testExitCode']=2
(a.output/'frames.json').write_text(json.dumps(metadata,indent=2)+'\n')
raise SystemExit(metadata['testExitCode'])
