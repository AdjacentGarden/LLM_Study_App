# 构建、功能与动效验证记录

最终统一结果完成于 **2026-10-21 05:24:55 PDT**。按用户最新要求，取消与旧Web界面一比一和逐像素还原，以真实功能操作、状态流转、持久化、错误/取消及动效完成后的可操作性验收。历史视觉资料保留追溯，不作为当前门槛。

## 最终结果与构建

**67项单元／集成测试＋16项UI测试＝83项全部通过，0失败、0跳过。** 使用本仓库`scripts/test.sh --all`构建和实际执行，未用静态检查或零测试选择器代替运行。统一结果包为`output/Functional-Delivery.xcresult`，日志为`output/functional-delivery.log`。可随仓库审查的原始[摘要JSON](../References/Validation/functional-delivery-summary.json)、[完整测试树](../References/Validation/functional-delivery-tests.json)、[逐项结果与耗时](../References/Validation/functional-delivery-tests.txt)均已归档。

| 检查 | 实际结果 | 证据与边界 |
| --- | --- | --- |
| iPhone统一构建/测试 | 通过，83项 | iPhone17Pro / iOS26.0，arm64，无签名；单元3.669秒，UI609.580秒 |
| generic iOS设备构建 | BUILD SUCCEEDED | `output/build-device-delivery-functional.log`；未配置团队、未签名，不能视为真机启动 |
| 最低系统版本 | 设备IOS和模拟器IOSSIMULATOR均minos17.0 | [build-verification.json](../References/build-verification.json)记录实际Mach-O、SDK27.1及产物SHA256 |
| 内置数据校验 | 1795项通过 | 6来源、171检索块、125教材页逐页SHA256；来源本来缺失的267个图示引用如实保留 |
| iPad课程专项 | 3项全部通过 | iPad Pro11 / iPadOS26；[原始摘要](../References/Validation/course-functional-summary.json)及[测试树](../References/Validation/course-functional-tests.json) |
| iPad学习专项 | 3项全部通过 | iPad Pro13 / iPadOS26；[原始摘要](../References/Validation/learning-functional-summary.json)，后补[新错题专项](../References/Validation/learning-new-mistake-summary.json)通过 |
| 手机键盘专项 | 章节1项、练习1项分别通过 | [章节摘要](../References/Validation/course-phone-keyboard-summary.json)、[练习摘要](../References/Validation/learning-phone-keyboard-summary.json)；相同修复随后纳入最终83项统一通过 |
| iOS17 runtime实际运行 | 未执行 | 本机只有26/27运行时，部署目标与Mach-O检查不等于17运行通过 |
| 实体麦克风/Apple Pencil | 未执行 | 模拟器样例音频和手指PKDrawing不替代实体录音/Pencil测试 |

最终模拟器App debug dylib SHA256：`a688efdb792cd648deda7dc097ca23db30fa06f91fe6fa28c211423038badc54`。功能代码冻结后完成上述统一回归；之后仅更新文档、证据和启动脚本的Simulator图形界面回退处理；App产物未变。启动脚本随后已分别在iPhone和iPad安装并启动相同产物，见[最终启动记录](../References/Validation/final-simulator-launches.json)。本机未提供Simulator GUI，脚本通过simctl完成运行。

## 单元／集成覆盖

| 套件 | 数量 | 已执行行为 |
| --- | ---: | --- |
| CoreTests | 15 | 内置内容ID/关联、125页哈希、章节草稿提交隔离、ImportJob运行快照重开/中断/重试、SQLite重开、课程/资料位置与引用隔离、积分预留/退款/故障/中断恢复、跨资料引用返回原课程原课时、不可用资料拒绝、字体唯一名称、28路由6Sheet、客观题判断、演示笔记删除不重种 |
| CourseTests | 8 | 章节越界/重叠/层级/循环、自由复习互斥、推荐时间预算/已完成排除/偏好、引导草稿恢复、首页下一步及章节容器跳过、展示mock任务与真实学习进度分离 |
| ImporterTests | 11 | PDF文本/页码、扫描PDF和图片无虚构文字、DOCX/PPTX/XLSX提取、TXT/MD/CSV、长中文切块、异步身份、损坏/ZIP/XML错误、重复文件、失败保留原件、空/100MB/目录拒绝、取消可重试 |
| LearningTests | 8 | 正式目录/本地页/前言排除、插图分配、扫描图排除、工具偏好、章节进度、印刷页范围、显式题型、原字体断行金标 |
| NoteTests | 10 | 音频附件和资料锚点重开、笔迹归一化、8秒真实可解码样例、页码身份隔离/引用优先、自动保存最新写入、同ID转语音、保存失败保留草稿、批注会话SQLite与旧JSON兼容、语音预览保留正文/确认转写 |
| CommunityAssistantTests | 4 | 原课程名、学科年级、目录、无匹配fallback及ID |
| RAGTests | 11 | 模型契约、61 tokenizer金标、BM25原排序/分数/可靠阈值、50查询、原生ONNX真实512维归一化推理、章节prior、空问题无证据、问候无教材引用、用户文件身份与跨书隔离 |

导入样本由`scripts/generate-import-fixtures.py`复现；不使用个人文件。运行中任务恢复测试真实保存running快照，重新打开仓库后核验interrupted和保留原件，再重试到completed。它不代表真实大文件强杀的所有时机均已测试。

## 16项真实UI操作

| 测试 | 实际操作与断言 | 统一批次结果 |
| --- | --- | --- |
| `testAllRoutesAndGlobalSheetsLaunch` | 28路由＋6类全局Sheet实际启动与内容渲染 | 通过，142.360s |
| `testAnnotationConversationAndGuideActionsRemainAvailable` | 批注局部问答、关闭重开与重启恢复；AI导学选择、导出预览和闪卡入口 | 通过，38.318s |
| `testAssistantDockAndFlashcardFlipMasterySurvivesRelaunch` | AI入口拖拽停靠/开关；闪卡双面翻转、掌握、横滑、重启恢复 | 通过，24.202s |
| `testCourseLessonCitationNoteSurvivesRelaunch` | 课程→章节→课时→PDF11页引用→唯一文字笔记保存→重启恢复 | 通过，30.129s |
| `testLandscapeCorePages` | XCUIDevice真实横屏旋转11路由；断言窗口方向和内容 | 通过，71.669s |
| `testNotesLocalPopupsHandwritingRegionAndVoiceSample` | 文字弹层、真实PKDrawing手指笔画、演示识别、圈选发送、8秒样例音频保存 | 通过，27.113s |
| `testOfflineAssistantGreeting` | 键盘可见时发送离线问候，不显示无依据教材引用 | 通过，9.282s |
| `testPlanCalendarKeepsSevenColumnsAndSelectsLastDay` | 计划日期第14天可达、可选并更新任务 | 通过，6.007s |
| `testSourceTextAutosaveSurvivesTermination` | 500ms自动保存，编辑器未关闭时终止后重启恢复唯一正文 | 通过，13.306s |
| `testTabsAndCourseLearningEntry` | 首页、学习、社区、个人Tab实际点击 | 通过，8.587s |
| `testCommunityImportCompletesQuestionnaireAndOpensStudy` | 社区导入→完整六步问卷→成功→进入学习 | 通过，31.950s |
| `testOnboardingCompletesAllThreeSteps` | 称呼→目标→每日时间三步引导，首页显示称呼 | 通过，14.460s |
| `testQuestionnaireChapterCancelSaveRemoveCancelAndConfirm` | 六步问卷→章节未保存关闭/保存重开/移除取消→确认目录→成功→学习 | 通过，61.392s |
| `testAssignmentDiagnosisMistakeRedoAndMasteryPersist` | 三类题型作答→诊断→本次新错题→空答案提示→重做/错因/掌握→重启恢复 | 通过，66.647s |
| `testLessonPagingConceptDismissalAndCompletion` | 全部5页前后翻动、概念弹层开关、完成返回目录、位置重启恢复 | 通过，27.496s |
| `testVideoCancelPreservesCreditsAndBundledMP4ActuallyPlays` | 视频取消不扣分、AVPlayer时间真实推进、播放仅扣10分、重启保持 | 通过，36.661s |

这些测试实际输入、点击、拖拽、旋转、播放、终止App和重启；以同一fixture会话的SQLite数据验证恢复，不在重启时注入所断言的笔记或掌握记录。交互测试未以静态截图替代动效可用性。视频使用仓库MP4，播放时间断言至少0.5秒；独立专项实际观察2.20秒，见[观测文本](../References/Validation/learning-video-playback.txt)。转写和手写识别仍为原演示。

此前`output/Functional-Acceptance.xcresult`曾有两个手机键盘流程失败，已保留失败日志。章节编辑增加明确的完成/收键盘操作，练习与错题增加“完成输入”；修复后分别专项通过，再重新运行本次83项统一通过。未把多个历史局部结果相加冒充统一成功。

## 原生检索结果

固定50-query教材评测中，原生BGE recall@5=1.0、top3=0.970588、接受引用precision=0.954545、非教材误引用率=0；BM25 recall@5=0.970588、top3=0.941176、precision=1.0。保守阈值下接受召回分别为0.617647和0.058824。原始附件和JSON来自最终`Functional-Delivery.xcresult`，见[来源记录](../References/Validation/rag-metrics-provenance.json)、[BGE指标](../References/Validation/native-bge-50-query-metrics.json)、[BM25指标](../References/Validation/bm25-50-query-metrics.json)。这些结果只针对当前教材与评测集。

## 外部环境与能力边界

- 本机没有iOS17 runtime；iOS17/实体设备启动尚未执行。实体录音、电话或音频路由中断、麦克风权限拒绝、低磁盘、Apple Pencil力度/倾斜、实体旋转分屏需要相应设备。
- 默认本地运行，fixture强制离线，原生模型和教材无需首次下载；尚未做系统飞行模式下完整人工长流程。个人Key在线模型未实际调用，限流/超时等真实服务分支未标为通过。
- 系统文件/照片提供者、真正大文件中途UI取消/强杀、系统磁盘故障未完成设备场景回归；格式、损坏、取消、原件保留与快照恢复已有本地测试。
- 转写/整理、手写识别、部分课程准备、规则诊断、日历Toast及PDF导出预览保持旧应用演示或规则能力；不提供真实OCR、语音识别、账号后端、云同步或真实PDF文件导出。
- 用户已取消像素校准；历史159张Web参考和早期Notes截图只是追溯资料，中断的原生截图采集未标为完成。

来源文件/旧仓库状态未修改、本地分支、无远程、无提交.env和密钥扫描结果见[最终审计](../References/repository-final-audit.json)。功能地图见[COVERAGE.md](COVERAGE.md)，操作清单见[FUNCTIONAL_ACCEPTANCE.md](FUNCTIONAL_ACCEPTANCE.md)。

## 复跑

```bash
python3 scripts/validate-content.py
./scripts/test.sh --device SIMULATOR_UDID --all --result output/new-functional.xcresult -- ARCHS=arm64
./scripts/build.sh --destination 'generic/platform=iOS' -- CODE_SIGNING_ALLOWED=NO
```

结果路径需尚未存在。显式指定模拟器，保留旧失败结果；测试脚本禁用并行设备测试并包含共享Scheme全部测试target。首次构建下载固定Swift Package依赖；日常运行不依赖旧项目或Node。
