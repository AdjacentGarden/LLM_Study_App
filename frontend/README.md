# CloudPath · SwiftUI

**服务接入：** 普通启动默认连接 Windows 网关 `http://10.249.44.137:8100`，使用邮箱验证码登录。登录页右上角「设置 → 学习服务地址」可切换服务，VPN 地址为 `http://100.86.143.88:8100`；本机开发需显式配置回环地址。PDF 原件保留本机，教材准备和课程生成通过可恢复的后台任务查询；服务未完成或审核失败会显示真实原因。Debug 的 `--fixture` 仅用于独立测试场景，Release 忽略该参数。配置与验收边界见 [原生云端接入](docs/CLOUD_INTEGRATION.md)。

首次登录及退出账号后重新登录，会展示称呼、学习目标、每日时间、学习卡点、帮助方式、计划支持六步个性化问题，选择题采用四卡片单选，点击后自动进入下一题；保留返回与跳过，支持草稿恢复及个人页修改。普通重开 App 保留已完成的问答状态；主动退出后会重新填写，旧课程和学习记录保留。主导航为「学习、发现、我的」；学习页可创建课程，选中未完成课程后在原学习计划与目录区域继续整理或查看进度，选中已就绪课程后自动定位上次学习的小节。向已有课程添加资料的入口位于「全部课程」的各课程卡片。“发现”已接入原项目生物课程，可导入 15 个课时、90 张闪卡和 45 道练习，无需先安装 OCR 模型；原文查看使用已有的 125 张教材页图片。

上传教材生成的个人课程会使用账号学习阶段、目标、每日时间、学习卡点、引导方式、计划支持，以及本课程的用途、紧迫程度、时间类型、内容侧重、学习辅助和巩固方式。课程设置优先于账号默认值，诊断中明确填写的目标和时间也会参与解析。正式生成和内容修复使用同一份有效偏好，学习阶段与主观偏好不作为掌握度证据。阅读页按后端单元顺序展示，保留学前检查、理解检查、主动回忆和原文引用，并展示本章实际采用的目标、引导方式与时间安排。修改偏好后，已有内容继续可读；打开该章可点击「按当前偏好更新本章」生成新版本，学习进度与复习记录保留。已导入的固定公开课程仍沿用原教学内容，其每日安排使用个人偏好。

在线课程的学习页与学习计划页使用后端每日排程：按本机日期、每日时间预算和已生成课程安排学习、练习与到期复习；共同规划模式先确认当天安排。任务可进入对应内容并标记完成，练习提交与指定闪卡复习成功后也会回写任务完成状态。计划与待同步完成记录按账号保存，断网重试复用同一事件编号；已确认或已经开始的当天计划保留，偏好变更用于后续计划。离线 fixtures 仍使用自带的历史计划。

本目录是 `LLM_Study_App` 的主原生 iPhone / iPad 前端，最低 iOS 17；统一启动入口见 [根 README](../README.md)，迁移来源及验收见 [迁移说明](../docs/SWIFTUI_MIGRATION.md)。采用 SwiftUI、PDFKit、PencilKit、AVFAudio 和本地 SQLite；教材检索使用原生 ONNX Runtime。现有页面、动效与资源沿用迁入版本，界面中文使用苹方、英文和数字使用 Helvetica，参考截图在 `References/VisualBaselines/`。应用运行和日常构建不需要原 SwiftUI 仓库、Node.js、Vite 或 WebView。

按最新要求，本次以**功能流程与动效完整可用**作为验收标准，不再进行与原网页一比一或逐像素还原。已有视觉参考只用于追溯；实际功能验证与平台限制见 [验证记录](docs/VALIDATION.md)。

迁入前的历史统一验证：**67项单元／集成测试和16项UI测试全部通过**；iPhone iOS26运行、iPad专项和无签名设备构建已验证。迁移后的最新结果见根目录迁移说明。iOS17部署目标已检查，iOS17运行时与真实麦克风/Pencil尚未执行，详见验证记录。

本次迁移范围为**原仓库内的 fixtures 与公开静态资源**。没有导出旧设备、浏览器 IndexedDB、个人笔记、录音、账号、API key 或 `.env`。可用能力与演示边界见 [功能边界](docs/FEATURE_BOUNDARIES.md)。

课程封面按资料导入顺序选取，优先使用第一份资料的封面；第一份缺失时依次检查后续资料。课程封面不随当前阅读资料切换。全部缺失或图片加载失败时，使用内置的六款 SVG 图书封面，按课程 ID 稳定选择样式；书架、列表、课程切换及学习页标题共用同一策略。

AI 对话列表中的回复内容区最多展示 240 点高，超出时渐隐并显示省略提示，完整回答与操作记录通过右上角全屏按钮查看，气泡内不单独滚动。全屏正文基准字号为 20 点并增加行距，记录与引用同步放大，支持系统动态字体；历史保存与模型上下文仍使用完整内容。Debug 可通过 `--fixture --assistant --fixture-assistant-long-reply` 检查长回答展示，使用独立的 `--fixture-session` 隔离测试数据。

应用在 iPhone 和 iPad 上统一仅支持正向竖屏，横持启动或旋转设备时不会自动切换横屏。方向限制同时配置于 Info.plist、工程生成脚本及 UIApplicationDelegate；iPad 使用全屏兼容模式。视觉 fixtures 也遵循相同限制。旋转回归入口为 `BookCourseUITests/testCorePagesRemainPortraitWhenDeviceRotates`，旧横屏采集及其历史验证记录不代表当前行为。

## 构建与运行

需要 macOS、完整 Xcode、iOS 17 或更新的 Simulator runtime，以及 Python 3（脚本只用标准库）。请选择支持目标 runtime 的 Xcode：

```bash
xcode-select -p
xcrun simctl list devices available
python3 scripts/validate-content.py
./scripts/build.sh
./scripts/run-simulator.sh --device SIMULATOR_UDID --no-build
```

也可以直接用 Xcode 打开 `BookCourse.xcodeproj`，选择 `BookCourse` scheme 运行。首次构建需要联网下载锁定的 ONNX Runtime **1.24.2**、ZIPFoundation **0.9.20** 与 AlertToast **1.3.9**；源码、资源、模型均已提交。真机运行需要在 Xcode 配置自己的签名团队，仓库不提供开发者证书。

日常提示统一使用顶部 HUD：沿用 AlertToast 的胶囊布局，iOS 26 及以上直接使用原生 `glassEffect(.regular, in: Capsule())`，不叠加旧模糊背景、手工描边或阴影；iOS 17–25 使用 AlertToast 原有的系统毛玻璃。成功、信息、警告、错误按图标区分，可带副标题。出现 240ms、完整停留 3 秒、消失 200ms；连续触发只保留最新消息并重新计时。加载提示保留至任务结束；现有上传、解析和同步进度继续使用各自的进度界面。HUD 不拦截点击，支持 VoiceOver 播报和减少动态效果，展示时避开顶部同步状态。

Debug 录屏可用 `--fixture --route lesson --chapter c2s1 --fixture-session toast-gallery --fixture-toast-gallery`，依次展示六种真实 HUD；该入口仅使用隔离的演示数据，Release 不包含它。各场景的录屏定位时间写入该演示会话目录的 `toast-gallery-events.json`。

`build.sh` 默认构建 Debug Simulator。通过 `--configuration Release` 切换配置，通过 `--destination 'generic/platform=iOS'` 构建真机目标；额外 Xcode 参数放在 `--` 后。如新增 Swift 源文件，运行 `python3 scripts/generate-project.py`，或 `./scripts/build.sh --regenerate-project`，更新可复现工程文件。工程生成器只读取本仓库。

## 测试与固定场景

```bash
# 默认只运行单元与集成测试：持久化、资料导入、课程、笔记、学习与原生检索
./scripts/test.sh --device SIMULATOR_UDID
# 完整 XCTest + UI 自动化；生成可在 Xcode 打开的结果包
mkdir -p output
./scripts/test.sh --device SIMULATOR_UDID --all --result output/acceptance.xcresult
# 固定课时场景；使用隔离的临时 fixture 目录，避免影响正常应用数据
./scripts/run-simulator.sh --device SIMULATOR_UDID --no-build -- \
  --fixture --route lesson --chapter c2s1 --fixture-session lesson-review --fixture-reduce-motion
xcrun simctl io SIMULATOR_UDID screenshot output/lesson.png
```

没有指定 `--device` 时脚本优先选择已启动的 iOS 模拟器；多人或多任务测试应始终显式指定 UDID。脚本不会抹除设备或删除应用数据。找不到Simulator图形应用时仍通过simctl安装和启动，适用于命令行环境。`--fixture-session` 相同名称会重用该测试会话的临时数据库；需要全新场景时换一个名称。`--fixture` 强制离线助手，即使本机配置了个人在线 key。还支持 `--book`、`--sheet` 等固定场景参数；具体路径与入口见 [数据与复现](docs/DATA_AND_REPRODUCIBILITY.md)。

可用环境变量 `BOOKCOURSE_DERIVED_DATA`、`BOOKCOURSE_PACKAGES`、`BOOKCOURSE_CONFIGURATION` 调整本地构建缓存目录与配置。结果目录必须是尚未存在的路径。测试应同时覆盖 iPhone 和 iPad，最低 iOS 17 与当前可用系统分别回归；仓库中的可运行工程不代表已经在每一个系统版本与真机上验证。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `App/` | 应用入口、路由、全局弹层与布局 |
| `Core/` | wire models、SQLite、文件导入、六域 FeatureStates、AppStore 协调、积分事务、检索与助手 |
| `DesignSystem/` | 字体、颜色、原图标、按钮与响应式共享组件 |
| `Features/` | Courses、Learning、Notes、Community、Assistant、Profile 原生界面 |
| `Resources/Data/` | 导出的示例课程、书架、社区、原页清单与测试金标 |
| `Resources/Content/` | 原公开素材、125 张教材原页、BGE 模型与检索索引 |
| `Resources/Fonts/` | 由原 WOFF 转换的原生字体，附 OFL |
| `Tests/`、`UITests/` | XCTest、导入格式 fixtures 与 UI 场景 |
| `References/` | 来源清单、字体转换记录、资源哈希、WebKit 视觉基线 |
| `scripts/` | 构建、测试、模拟器、内容校验与显式 fixtures 更新 |

## 数据、检索与配置

- [数据来源、刷新步骤、文件支持与可复现校验](docs/DATA_AND_REPRODUCIBILITY.md)
- [原生 BGE / BM25 检索、tokenizer 与 50 条评测](docs/NATIVE_RAG.md)
- [历史视觉参考及范围调整](docs/VISUAL_PARITY.md)
- [当前字体、字号、字重与行距](docs/TYPOGRAPHY.md)
- [统一按钮尺寸与使用规范](docs/BUTTONS.md)
- [功能与动效操作清单](docs/FUNCTIONAL_ACCEPTANCE.md)
- [功能覆盖与验证证据](docs/VALIDATION.md)
- [真实能力、演示能力与已知资源缺失](docs/FEATURE_BOUNDARIES.md)
- [第三方许可与模型来源](THIRD_PARTY_NOTICES.md)

普通账号模式使用自己的后端，模型配置与密钥由服务器管理。离线 fixtures 无需 API key，助手从可靠原文片段生成摘录式回答，无证据时不附教材页码。原有个人调试路径仍支持通过设备 Keychain 或进程环境变量 `BOOKCOURSE_DEEPSEEK_API_KEY` 提供个人 key；不要写入资源、源码或提交的 scheme。账号模式不使用该个人 key，三个教材答疑入口均调用 FastAPI。

解析中的取消操作使用 `cancelImport` 终止任务并使过期回调失效，原文件保留供重试。目录编辑先写入按书隔离的章节草稿，点击确认生成课程后才提交到资料目录；返回前一页不会提前覆盖已确认目录。六个独立状态域分别负责课程、学习会话、阅读、笔记、导入任务与助手会话，`AppStore` 统一协调路由和持久化。

原仓库没有提供覆盖全部业务源码与教材内容的统一开源授权。第三方组件许可不自动授予教材、封面、视频或业务设计的再分发权；详见 notices 中的来源记录。此仓库用于本次迁移开发与界面验收，发布前需确认这些内容的使用权。
