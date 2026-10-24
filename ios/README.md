# 知我原生 iPhone 客户端

> 本目录是保留的独立单用户兼容与诊断客户端。仓库当前主客户端为 [`../frontend/BookCourse.xcodeproj`](../frontend/BookCourse.xcodeproj)，主应用的构建和发布遵循 [`../frontend/README.md`](../frontend/README.md)。两套 App 使用不同的 Bundle ID 和本机数据结构；本次分支整合不替换主应用，也不执行跨 App 数据迁移。本目录的历史验收仅适用于此客户端，不代表主客户端已接入相同功能。

真实 SwiftUI iOS application target，直接用 URLSession 对接本项目后端，无 WKWebView、无模拟手机外壳、无产品假数据。当前提供源码与 Xcode 工程，**没有已签名 IPA，也未在 iPhone 上安装或验收**。本机仅有 CLT/macOS SDK，无法编译 iOS target；安装条件与免费 Personal Team 步骤见 [README-build.md](README-build.md)。

## 已实现的交互

- 可编辑服务根地址、健康检查与游客身份。默认填写局域网服务器 `http://10.249.186.201:8100`，HTTP 必须由用户显式允许且限私网；HTTPS 可直接使用。手机需要同局域网/VPN与本地网络权限。
- 原生书架、系统 PDF 文件导入。先拷贝 PDF 到 App 私有目录，再上传；multipart 在磁盘分块构建并由 URLSession 文件上传，避免把整本书复制成多份内存 Data。
- 逐阶段恢复上传/绑定/OCR/整理/去重入库；保留书号、步骤和本机文件。上传回执不明时明确提示重传，不自动重复创建；已知任务只查询进度，失败需明确重试。服务端任务在关闭 App 后继续，上传本身为前台传输，尚未实现系统 background URLSession 上传。
- 已拥有教材的章节或诊断题失效时，可直接在教材页重新整理；退出后恢复准备状态，不重新上传或重跑 OCR。书架和已读取的章节目录在本机保留，支持导航到已保存的历史课程与原 PDF。
- 目标访谈与诊断、章节课程后台生成、退出后恢复 job ID 与请求 ID、按服务端真实 study_units 选择学习单元、PDFKit 原书全文阅读/章节起页/引用页跳转、原文选读、闪卡翻面与评级、练习提交/反馈、教材答疑及依据不足状态。
- 文字草稿自动原子保存，可同步服务器。原生 AAC 录音、停止落盘、重启恢复、本机试听、上传与从服务端重新下载试听。转写出现疑词时可进入独立核对区、修改并明确确认后继续整理；确认转写与普通笔记草稿分开保存。
- 独立服务地址的数据空间；手工持久化游客 Cookie，禁止把 Cookie 跟随 HTTP 重定向。手机不保存模型供应商密钥。

连接服务、前台恢复和进入章节时，会通过纯 GET 核验本机课程缓存。409/404 会停止把旧稿作为当前课程展示，只有明确操作才重新准备；断网时保留并标记本机历史副本，核验成功前不提交练习评分或闪卡评级。新版本 POST 回执不明会持久化“未确认”状态，显式重试沿用同一请求编号；不会为了重连自动生成新版本。

尚未覆盖社交、生成图解/视频、邮箱注册等完整 Web 功能；本版是单用户学习主流程。原生 Vision OCR 没有替换服务器整书 GPU OCR。原书通过带游客 Cookie 的 `GET /api/library/books/{id}/source` 下载到 App 私有目录，再由 PDFKit 阅读；导入书优先使用已经保存在本机的原 PDF。卸载 App 会删除本机游客凭证与本机草稿，当前没有邮箱账号恢复入口。

## 本机验证

```sh
./scripts/check-core-host.sh
```

`core-test-results.json` 为 Foundation core 的可执行断言结果：配置约束、Cookie/身份恢复、原文件删除后导入副本可用、损坏状态显式失败、后台课程请求契约、错误不自动重试、断传恢复、OCR→整理分阶段保存、练习稳定事件、磁盘 multipart、严格语音笔记字段与 revision、待确认事件重启恢复。测试使用隔离的 HTTP transport fixtures，不调用付费模型，也不是线上端到端验收。

`host-validation.json` 记录 SwiftUI 在 Mac SDK 下的 typecheck、真实 AAC 文件持久化检查、工程/plist 静态检查，以及 iOS 工具链缺失结果。仍需用完整 Xcode 编译 iOS，并在实际 iPhone 验证：本地网络权限、服务器可达、麦克风权限、来电/后台中断、上传/退出重启、诊断与课程/笔记实际 API 全流程。

`app-model-test-results.json` 另用 Mac AppModel 实际状态与隔离 HTTP fixtures 检查：缓存 GET 503/断网不提交任务；409 停止旧轮询且需显式刷新；首次或新版本 POST 丢回包后重建 AppModel 仍复用落盘 request_id；前台断网与冷启动保留未核验历史；迟到的旧 GET 不得覆盖新课程；已有教材 prepare 的重连、404及显式重试；瞬时网络失败触发轮询退避。每次任务 POST 都现场检查请求编号已先写入磁盘。

语音 `recognize` 和 `complete` 只分析已上传录音，独立文字草稿不会作为 transcript 发送；12001 字和20000字草稿的保留与这两种请求契约继续有独立 fixture 验证。`improve` 只接收用户在独立核对区明确确认的转写，不借用草稿、不自动绕过 ASR 的 `needs_confirmation` 门控。修改核对文字会撤销确认；空白、未补全的 `[待确认]`、不足2字符或超过12000 Unicode scalar 的文字不能提交，不会悄悄截断。

r4 的确认文字绑定 note ID、revision、recording ID 与来源 job ID；提交前把固定 request_id 和完整确认文字快照落盘。磁盘写入失败不会 POST；回执未知或任务进行中不能更换确认内容。退出/重连只 GET，用户点击“按原编号恢复提交”才沿用原编号与原文字；重复点击不能并发创建第二个请求。普通草稿编辑不会清掉该编号。后台中间状态也显示处理中，失败后的新尝试由用户明确触发。服务器既有语音笔记也能进入同一核对编辑器并下载试听，不需要重新上传或覆盖录音。

`voice-review-app-model-results.json` 的6项 Mac 可执行回归覆盖未知回执重启、先落盘后POST、写盘失败零请求、双击与旧录音迟到回包、GET过滤与保留人工修改、终态重复同编号、服务器旧录音与未上传新录音的隔离，以及未知任务禁止换录音。`core-test-results.json` 的21项检查还验证确认文字的精确请求体、字符上限及零请求拒绝、旧草稿兼容。笔记每次重入恢复查询，新录音只展示匹配 note ID 和 revision 的结果，迟到旧结果不当作当前转写。SwiftUI 取消/重入、本地网络及麦克风权限仍待 iPhone 实机或 iOS 模拟器验证，Mac fixtures 不代表这些 UI 生命周期已实测。

另外 `APISmoke` 用真实 Mac URLSession 做只读线上 schema 检查，不触发上传或模型：

```sh
swift run APISmoke --base-url http://127.0.0.1:18126 --allow-local-http --report native-urlsession-smoke.json
```

首次真实私网 HTTP 测试发现 Foundation 会把 cookie 属性 `secure="FALSE"` 仍解释为 Secure，造成游客恢复失败；已经修复为 false 时省略属性，并增加回归。成功结果见 `native-urlsession-smoke.json`，修复记录见 `native-smoke-regression.json`。readiness 的配置/worker 通过单独标注为未验证外部推理，不能据此宣称模型功能通过。

在经授权的同一验收游客上，`native-acceptance-readonly-smoke.json` 记录真实书架/125页结构/原 PDF Range 与全文下载、Mac PDFKit 首末页读取及 Cookie 恢复；`native-acceptance-audio-smoke.json` 记录私有音频真实下载与 Mac AVFoundation 解码，未播放声音。`native-upload-live-results.json` 记录原生 multipart 磁盘上传 38,847,801 字节原书及所有权绑定成功，上传 28.39 秒；未启动 OCR、未 claim、未触发模型。Mac 的网络耗时不代表 iPhone 的上传或推理耗时。

`native-voice-live-results.json` 保留此前的 Mac 原生写链路：既有 27.41 秒 AIFF 语音由 AVFoundation 转为 AAC，创建全新笔记、上传/下载字节 SHA 一致、完整解码、GPU `recognize` 返回132字。全流程9.52秒，识别提交至确认3.78秒（含网络/轮询）。这是合成语音验收，不是 iPhone 麦克风或识别准确率成绩。

`native-voice-improve-*-results.json` 记录 r4 经修复的 APIClient 和真实 URLSession 核对路径。首次 improve 前，原 complete 的 `needs_confirmation`、原笔记版本和 AAC 哈希均须匹配。验收输入由根代理明确批准为人工编辑的合成测试段落；原始 say 稿没有找到，不能称其为从录音忠实恢复的原稿。首次仅提交一次固定计划，发送前验证恰好五个字段及 canonical SHA；任何未知回执都停止并交由 GET/SQL 对账，不自动重发。单独的已知终态重复阶段必须显式指定原 job ID，且使用同一计划、同一请求编号。脚本不会保存覆盖源笔记或 PUT 音频；模型整理内容的质量与 SQL 全字段不变由独立验收另行判定，不能只凭 HTTP200 宣称通过。

测试身份和下载原文件仅在 `.runtime/`；构建缓存仅在 `.build/`。两者都不能进入交付。可用 `python3 scripts/package-source.py` 生成源码/证据 ZIP，脚本强制排除这些目录与凭证文件；该 ZIP 不是可安装 IPA。

## r5 结果提示

任务结果与服务器已保存整理版会显示 `quality_notice` 和 `excluded_optional_count`。服务器仅在删除有精确定位的 AI 可选建议、且对新候选重新完成完整审核后，才返回“已保留原笔记，部分补充因依据不足未加入。”；这些字段不是用户错误计数，也不表示原失败稿变成通过。任务失败、仍在审核、`provisional=true` 或明确 `review.passed=false` 时，界面显示尚未通过完整审核，不展示成功提示。旧版本无这些字段的 JSON 仍可恢复，GET 恢复不会新增提交。

`./scripts/check-note-notice-host.sh` 可独立复验6组离线解码、旧数据兼容、失败状态门控、GET/历史整理版及草稿持久化检查，亦纳入 `check-core-host.sh`。这是 Mac SDK 下的验证，尚无 iOS 构建、安装、真机提示展示或 v5 模型质量通过的证据。r5 不修改 APIClient、AppModel、确认文字、音频、固定请求编号或五字段请求协议；尚未授权的 followup05 计划仍留在排除打包的 `.runtime/`，本次没有提交。

## 后端契约

所有端点通过同一个 APIClient 与同一个游客 Cookie；先 `GET /api/auth/me`。现有 Web API 类型是实现依据。新增异步课程与 prepare 接口由同一候选后端提供。

| 流程 | 端点 |
| --- | --- |
| 书架 | `GET /api/library` |
| 原书 | `GET /api/library/books/{id}/source`，按书架身份校验，支持 Range；客户端使用 PDFKit |
| PDF | `POST /api/books` multipart，`POST /api/library/books/{id}/bind-upload` |
| OCR | `POST /api/books/{id}/process`，`GET /api/books/{id}/status`；显式失败重试 `/process/retry` |
| 整理 | `POST/GET /api/books/{id}/prepare`；失败显式 `?retry=true`；完成后 `/api/library/books/{id}/claim` |
| 诊断 | `/api/interviews/start`，`/{session}`，`/profile`，`/next`，`/respond`，`/confirm` |
| 章节课程 | `POST /api/interviews/{session}/course-jobs`；`GET .../course-jobs/{job}`，仅 succeeded course 展示 |
| 练习/闪卡 | `/api/interviews/{session}/courses/{course}/practice/{item}`、`/flashcards/{card}`，稳定 event_id，选择 ID 为零基数字字符串 |
| 教材答疑 | `POST /api/books/{id}/qa` |
| 文字笔记 | `POST /api/library/notes`，更新使用已保存 resource_id |
| 语音 | `/api/studio/notes`、`/{note}`、`/{note}/audio`、`/{note}/analyze`、`/api/studio/jobs?book_id=...` |

源文件入口：`App/CloudPathApp.swift`；业务状态：`App/AppModel.swift`；纯 Foundation core：`Sources/LearningCore/`。SwiftPM 与 Xcode 共享核心源码。核心 JSON 模型忽略额外响应字段，必需字段不符合时显示版本不兼容，不以空列表掩盖失败。
