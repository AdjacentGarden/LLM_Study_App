#!/usr/bin/env python3
"""Deterministically generate a self-contained Xcode project using Python's standard library."""
from pathlib import Path
import hashlib,json,plistlib,shutil
root=Path(__file__).resolve().parent.parent
objects={}
def ident(s):return hashlib.sha1(s.encode()).hexdigest()[:24].upper()
def quote(s):return json.dumps(str(s),ensure_ascii=False)
def obj(key,body):i=ident(key);objects[i]=body;return i
def arr(items):return '('+','.join(items)+',)' if items else '()'
def ref(key,path,type):return obj(key,f'isa = PBXFileReference; lastKnownFileType = {type}; path = {quote(path)}; sourceTree = SOURCE_ROOT;')
def build(key,file):return obj(key,f'isa = PBXBuildFile; fileRef = {file};')
products=[];groups=[];targetIDs=[]
packages=[];deps=[]
for name,url,version in [('onnxruntime','https://github.com/microsoft/onnxruntime-swift-package-manager','1.24.2'),('ZIPFoundation','https://github.com/weichsel/ZIPFoundation','0.9.20')]:
 package=obj('package-'+name,f'isa = XCRemoteSwiftPackageReference; repositoryURL = {quote(url)}; requirement = {{ kind = exactVersion; version = {quote(version)}; }};');packages.append(package)
 deps.append(obj('product-'+name,f'isa = XCSwiftPackageProductDependency; package = {package}; productName = {quote(name)};'))
frameworkFiles=[obj('framework-'+d,f'isa = PBXBuildFile; productRef = {d};') for d in deps]
resourceFiles=[]
for folder in ['Content','Data','Fonts']:
 r=ref('resource-'+folder,'Resources/'+folder,'folder');resourceFiles.append(build('resourcebuild-'+folder,r));groups.append(r)
r=ref('launch-screen','Resources/LaunchScreen.storyboard','file.storyboard');groups.append(r);resourceFiles.append(build('launch-screen-build',r))
icon=root/'Resources/Assets.xcassets/AppIcon.appiconset';icon.mkdir(parents=True,exist_ok=True)
sourceIcon=root/'Resources/Content/assets/brand/app-icon.png'
if not (icon/'AppIcon.png').exists():
 if sourceIcon.exists():shutil.copyfile(sourceIcon,icon/'AppIcon.png')
 else:raise SystemExit('Missing committed Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png. Restore repository resources, or run scripts/import-source.sh explicitly.')
(icon/'Contents.json').write_text(json.dumps({'images':[{'filename':'AppIcon.png','idiom':'universal','platform':'ios','size':'1024x1024'}],'info':{'author':'xcode','version':1}},indent=2))
(root/'Resources/Assets.xcassets/Contents.json').write_text('{"info":{"author":"xcode","version":1}}\n')
r=ref('assetcatalog','Resources/Assets.xcassets','folder.assetcatalog');groups.append(r);resourceFiles.append(build('assetsbuild',r))
for folder in ['App','Core','DesignSystem','Features']:
 files=[]
 for path in sorted((root/folder).rglob('*.swift')):
  files.append(ref('file-'+str(path.relative_to(root)),str(path.relative_to(root)),'sourcecode.swift'))
 groups.append(obj('group-'+folder,f'isa = PBXGroup; name = {folder}; children = {arr(files)}; sourceTree = "<group>";'))
mainID=ident('target-BookCourse')
for name,sourceDirs,productType,extension in [('BookCourse',['App','Core','DesignSystem','Features'],'com.apple.product-type.application','app'),('BookCourseTests',['Tests'],'com.apple.product-type.bundle.unit-test','xctest'),('BookCourseUITests',['UITests'],'com.apple.product-type.bundle.ui-testing','xctest')]:
 sourceBuilds=[];refs=[]
 for directory in sourceDirs:
  for path in sorted((root/directory).rglob('*.swift')):
   rel=str(path.relative_to(root));r=ref('file-'+rel,rel,'sourcecode.swift');refs.append(r);sourceBuilds.append(build('sourcebuild-'+rel,r))
 if name!='BookCourse':groups.append(obj('group-'+name,f'isa = PBXGroup; name = {name}; children = {arr(refs)}; sourceTree = "<group>";'))
 phase=obj('sources-'+name,f'isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = {arr(sourceBuilds)}; runOnlyForDeploymentPostprocessing = 0;')
 resources=resourceFiles if name=='BookCourse' else []
 if name=='BookCourseTests' and (root/'Tests/Fixtures/ImportFixtures').exists():
  r=ref('importfixtures','Tests/Fixtures/ImportFixtures','folder');groups.append(r);resources=[build('importfixturesbuild',r)]
 resourcePhase=obj('resources-'+name,f'isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = {arr(resources)}; runOnlyForDeploymentPostprocessing = 0;')
 frameworks=obj('frameworks-'+name,f'isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = {arr(frameworkFiles if name=="BookCourse" else [])}; runOnlyForDeploymentPostprocessing = 0;')
 product=obj('built-'+name,f'isa = PBXFileReference; explicitFileType = {"wrapper.application" if extension=="app" else "wrapper.cfbundle"}; includeInIndex = 0; path = {quote(name+"."+extension)}; sourceTree = BUILT_PRODUCTS_DIR;');products.append(product)
 configs=[]
 for mode in ['Debug','Release']:
  settings={'PRODUCT_NAME':'$(TARGET_NAME)','PRODUCT_BUNDLE_IDENTIFIER':'com.littleri.bookcourseai.swiftui'+('' if name=='BookCourse' else '.'+name),'SWIFT_VERSION':'5.0','IPHONEOS_DEPLOYMENT_TARGET':'17.0','TARGETED_DEVICE_FAMILY':'1,2','SDKROOT':'iphoneos','SUPPORTED_PLATFORMS':'iphoneos iphonesimulator','SUPPORTS_MACCATALYST':'NO','CODE_SIGN_STYLE':'Automatic','DEVELOPMENT_TEAM':'','SWIFT_STRICT_CONCURRENCY':'minimal','CLANG_ENABLE_MODULES':'YES','ENABLE_TESTABILITY':'YES' if mode=='Debug' else 'NO','ONLY_ACTIVE_ARCH':'YES' if mode=='Debug' else 'NO','SWIFT_OPTIMIZATION_LEVEL':'-Onone' if mode=='Debug' else '-O','DEBUG_INFORMATION_FORMAT':'dwarf' if mode=='Debug' else 'dwarf-with-dsym','SWIFT_ACTIVE_COMPILATION_CONDITIONS':'DEBUG' if mode=='Debug' else '', 'OTHER_LDFLAGS':'$(inherited) -lsqlite3','GENERATE_INFOPLIST_FILE':'YES' if name!='BookCourse' else 'NO'}
  if name=='BookCourse':settings.update({'INFOPLIST_FILE':'App/Info.plist','ASSETCATALOG_COMPILER_APPICON_NAME':'AppIcon','MARKETING_VERSION':'0.1.0','CURRENT_PROJECT_VERSION':'1','LD_RUNPATH_SEARCH_PATHS':'$(inherited) @executable_path/Frameworks'})
  if name=='BookCourseTests':settings.update({'TEST_HOST':'$(BUILT_PRODUCTS_DIR)/BookCourse.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/BookCourse','BUNDLE_LOADER':'$(TEST_HOST)'})
  if name=='BookCourseUITests':settings.update({'TEST_TARGET_NAME':'BookCourse'})
  configs.append(obj('config-'+name+mode,'isa = XCBuildConfiguration; name = '+mode+'; buildSettings = {'+' '.join(k+' = '+quote(v)+';' for k,v in settings.items())+'};'))
 configList=obj('configs-'+name,f'isa = XCConfigurationList; buildConfigurations = {arr(configs)}; defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
 targetDeps=[]
 if name!='BookCourse':
  proxy=obj('proxy-'+name,f'isa = PBXContainerItemProxy; containerPortal = {ident("project")}; proxyType = 1; remoteGlobalIDString = {mainID}; remoteInfo = BookCourse;')
  targetDeps=[obj('targetdep-'+name,f'isa = PBXTargetDependency; target = {mainID}; targetProxy = {proxy};')]
 targetIDs.append(obj('target-'+name,f'isa = PBXNativeTarget; buildConfigurationList = {configList}; buildPhases = {arr([phase,frameworks,resourcePhase])}; buildRules = (); dependencies = {arr(targetDeps)}; name = {quote(name)}; productName = {quote(name)}; productReference = {product}; productType = {quote(productType)}; packageProductDependencies = {arr(deps if name=="BookCourse" else [])};'))
productsGroup=obj('products',f'isa = PBXGroup; name = Products; children = {arr(products)}; sourceTree = "<group>";')
mainGroup=obj('main',f'isa = PBXGroup; children = {arr(groups+[productsGroup])}; sourceTree = "<group>";')
configs=[]
for mode in ['Debug','Release']:
 configs.append(obj('projectconfig-'+mode,f'isa = XCBuildConfiguration; name = {mode}; buildSettings = {{ CLANG_ENABLE_MODULES = YES; CLANG_ENABLE_OBJC_ARC = YES; SDKROOT = iphoneos; IPHONEOS_DEPLOYMENT_TARGET = 17.0; SWIFT_VERSION = 5.0; }};'))
projectConfigs=obj('projectconfigs',f'isa = XCConfigurationList; buildConfigurations = {arr(configs)}; defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
project=obj('project',f'isa = PBXProject; attributes = {{ BuildIndependentTargetsInParallel = YES; LastUpgradeCheck = 1600; }}; buildConfigurationList = {projectConfigs}; compatibilityVersion = "Xcode 14.0"; developmentRegion = "zh-Hans"; hasScannedForEncodings = 0; knownRegions = ("zh-Hans",en,Base); mainGroup = {mainGroup}; productRefGroup = {productsGroup}; projectDirPath = ""; projectRoot = ""; packageReferences = {arr(packages)}; targets = {arr(targetIDs)};')
projectDir=root/'BookCourse.xcodeproj';projectDir.mkdir(exist_ok=True)
(projectDir/'project.pbxproj').write_text('// !$*UTF8*$!\n{ archiveVersion = 1; classes = {}; objectVersion = 60; objects = {\n'+''.join(f'{i} = {{ {body} }};\n' for i,body in objects.items())+f'}}; rootObject = {project}; }}\n')
schemes=projectDir/'xcshareddata/xcschemes';schemes.mkdir(parents=True,exist_ok=True)
def buildable(name):return f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{ident("target-"+name)}" BuildableName="{name}.{"app" if name=="BookCourse" else "xctest"}" BlueprintName="{name}" ReferencedContainer="container:BookCourse.xcodeproj"/>'
(schemes/'BookCourse.xcscheme').write_text('<?xml version="1.0" encoding="UTF-8"?><Scheme LastUpgradeVersion="1600" version="1.3"><BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">'+buildable('BookCourse')+'</BuildActionEntry></BuildActionEntries></BuildAction><TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables>'+''.join('<TestableReference skipped="NO">'+buildable(t)+'</TestableReference>' for t in ['BookCourseTests','BookCourseUITests'])+'</Testables></TestAction><LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">'+buildable('BookCourse')+'</BuildableProductRunnable></LaunchAction><ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES"><BuildableProductRunnable runnableDebuggingMode="0">'+buildable('BookCourse')+'</BuildableProductRunnable></ProfileAction><AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/></Scheme>')
plist={'CFBundleDisplayName':'CloudPath','CFBundleExecutable':'$(EXECUTABLE_NAME)','CFBundleIdentifier':'$(PRODUCT_BUNDLE_IDENTIFIER)','CFBundleName':'CloudPath','CFBundlePackageType':'APPL','CFBundleShortVersionString':'$(MARKETING_VERSION)','CFBundleVersion':'$(CURRENT_PROJECT_VERSION)','LSRequiresIPhoneOS':True,'NSMicrophoneUsageDescription':'CloudPath 使用麦克风录制语音笔记和教材语音批注，录音保存在本机。','UIApplicationSceneManifest':{'UIApplicationSupportsMultipleScenes':False},'UILaunchStoryboardName':'LaunchScreen','UIAppFonts':['Fonts/BookCourseNotoSansSC-Regular.ttf','Fonts/BookCourseNotoSansSC-Bold.ttf'],'UISupportedInterfaceOrientations':['UIInterfaceOrientationPortrait','UIInterfaceOrientationLandscapeLeft','UIInterfaceOrientationLandscapeRight'],'UISupportedInterfaceOrientations~ipad':['UIInterfaceOrientationPortrait','UIInterfaceOrientationPortraitUpsideDown','UIInterfaceOrientationLandscapeLeft','UIInterfaceOrientationLandscapeRight'],'UIUserInterfaceStyle':'Light','ITSAppUsesNonExemptEncryption':False}
plist['NSLocalNetworkUsageDescription']='CloudPath 连接你配置的本地学习服务，用于登录、教材上传与学习进度同步。'
plist['NSAppTransportSecurity']={
 'NSAllowsLocalNetworking':True,
 'NSExceptionDomains':{address:{'NSExceptionAllowsInsecureHTTPLoads':True}
  for address in ['10.249.44.137','100.86.143.88','127.0.0.1']},
}
(root/'App/Info.plist').write_bytes(plistlib.dumps(plist))
print(f'Generated {len(objects)} Xcode project objects.')
