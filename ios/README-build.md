# 原生 iPhone 工程与安装条件

此目录提供 SwiftUI 原生 iPhone 应用源码与 `CloudPath.xcodeproj`，核心逻辑同时放在可由 Mac 编译测试的 Swift Package 中。应用直接访问自部署服务，没有 WebView，也不把模型 API 密钥存入手机。

**当前交付状态：源码与工程；尚未完成 iOS 编译、签名、真机安装或真机验收。** 当前机器只有 Command Line Tools 与 macOS SDK，没有完整 Xcode、iPhoneOS SDK 和可用签名身份。Mac 上的核心测试通过不能证明 iOS 应用已构建或安装。

## 编译检查

安装完整 Xcode 和 iOS 平台支持后，在此目录运行：

```sh
swift run CoreChecks --report core-test-results.json
./scripts/check-core-host.sh
./scripts/build-ios.sh check
./scripts/build-ios.sh simulator
```

工程格式使用 Xcode 16 起支持的文件系统同步组：`App/` 与 `Sources/LearningCore/` 中的 Swift 文件会直接编译进入 App target。**当前源码构建要求 Xcode 26 或更新版本**（含新命名的 AVAudioSession Bluetooth 选项）；还须匹配 iPhone 17 已安装系统的设备支持版本。部署目标为 iOS 18，目标设备为 iPhone。`simulator` 只编译，不启动预览或模拟器。

当前 CLT 没有 XCTest 或 Swift Testing 模块，因此核心回归使用 Swift Package 可执行测试目标 `CoreChecks`：逐项断言，失败返回非零并写 JSON；这不是 `swift test` 成绩。`check-core-host.sh` 另检查原生 SwiftUI 源码在 Mac SDK 可编译的分支与录音文件恢复。iOS 专属分支仍需要 iPhoneOS SDK。

若 `xcode-select` 仍选择 Command Line Tools，应切换到已安装的 Xcode Developer 目录。构建脚本遇到缺少工具链、SDK 或签名团队会明确失败，不会输出虚假的 IPA 成功提示。

## 无付费开发者账号时的本人真机安装

完整 Xcode 可使用你自己的 Apple ID 的 Personal Team，为连接到该 Mac 的本人 iPhone 做开发签名。先在 Xcode 账号设置中添加 Apple ID，然后打开工程，在 Signing & Capabilities 选择自己的团队，必要时把 Bundle Identifier 改成自己唯一的值；连接、信任 iPhone，并按系统要求开启 Developer Mode，再选该设备运行。

Personal Team 的开发签名会过期，需要重新签名安装，也存在设备和能力限制。它不提供 App Store、TestFlight 或可随意分发的长期 IPA。Apple 当前说明开发描述文件七天过期、每台设备最多安装三个此类应用；实际限制以 Xcode 当前显示为准。此工程没有为你创建账号、证书或设备描述文件。依据：[Apple 开发者账号与 Personal Team 说明](https://developer.apple.com/help/account/basics/about-your-developer-account)、[Xcode 团队配置](https://help.apple.com/xcode/mac/current/en.lproj/dev23aab79b4.html)。

已有有效团队和描述文件时，也可以编译真机产物：

```sh
DEVELOPMENT_TEAM=你的团队ID ./scripts/build-ios.sh device
DEVELOPMENT_TEAM=你的团队ID ./scripts/build-ios.sh archive
```

`device` 成功仅表示编译和签名成功；仍需独立验证安装与实际功能。`archive` 生成 `.xcarchive`，不自动导出 IPA，导出方式必须与自己的签名权限及描述文件相匹配。Windows 上可以运行后端，但本工程的标准 iOS 编译和签名仍需要完整 Xcode 所在的 Mac。

## 网络与数据

服务地址优先使用 HTTPS。为了允许用户在设置中明确启用私网 HTTP 开发连接，Info.plist 设置了 `NSAllowsArbitraryLoads`；应用核心仍限制 HTTP 地址，只接受明确许可的 localhost、`.local` 或私网 IPv4。正式部署应启用可信 HTTPS，并移除此开发例外。手机与服务器需要能够直接互通；Mac 或 Windows 上可访问的 `localhost` 不代表手机可访问该服务。

App 只保存服务地址、自己的会话与学习草稿。模型密钥由后端保管。局域网访问与麦克风都使用系统权限说明；系统拒绝权限时需要在设置中重新允许。没有声明后台音频常驻能力。

## 语音笔记的持久化

`AudioCapture` 使用原生 AVAudioRecorder，把单声道 24 kHz、64 kbps AAC 写入 `Application Support/CloudPath/recordings/<UUID>.m4a`，同时原子写入 JSON 元数据。录音最长十分钟，上传前检查 20 MB 限制；MIME 为 `audio/mp4`。本地文件不会因上传失败而删除。

调用方在离开页面、保存草稿或上传前必须调用 `stop()`；它同步终结音频文件并保存状态。App 失去前台或遭遇音频中断也会自动终结。调用方把 `recordingID` 写入自己的笔记草稿，下次用 `recover(id:)` 恢复；`recoverLatest()` 仅用于还没有选定草稿时恢复最近录音。`recordingData()` 从已终结文件读取上传数据，`playback()` 在本机试听。只有用户明确删除时才调用 `delete()`。

录音运行中会写入磁盘并每秒保存元数据；若进程被强制杀死、系统崩溃或存储故障，AAC 容器可能未能终结。恢复会验证可播放性，失败时保留原文件并显示错误，不能保证这种情况下未终结音频的全部内容。麦克风采集、电话中断、蓝牙音频、权限拒绝、后台终结、重启恢复与上传重试仍需在 iPhone 真机验证。

`./scripts/check-audio-host.sh` 在 Mac 上生成真实 AAC 测试音频，检查字节读取、中断草稿恢复、重新创建对象后的恢复、损坏文件保留和显式删除。它不请求麦克风、不播放音频，也不验证 iOS 生命周期或权限。

r4 将人工核对转写存为独立 `VoiceTranscriptReview`，不把普通文字草稿当转写。只有用户确认的当前录音版本才能发 `improve`；`recognize`/`complete` 仍传空 transcript 由服务器识别音频。`NoteAnalysisSubmission` 在网络提交前保存固定编号、版本和确认文字快照；未知回执恢复沿用原数据，编辑普通草稿不会重置语音任务。`bash scripts/check-voice-review-host.sh` 用隔离 HTTP fixtures 实测 AppModel 与磁盘恢复，不使用模型或麦克风。
