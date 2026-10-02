# 最新 Demo 直接运行本轮验收记录

日期：2026-10-02。状态：本地实现与验收完成；外部真实提供方成功边界见末节。

后续前端增量已同步至来源提交 `dee6d691`，本记录末尾所述运行期间变化已在后续任务中处理；设置、贴纸图标和新页面的最新结果见 [2026-10-02 增量验收](FRONTEND_DEMO_DELTA_ACCEPTANCE_20261002.md)。以下保留首次移植的实际基线、过程和验收范围。

## 数据隔离与基线

来源 `D:/code/study_app_demo` 当前工作树只读。目标未提交变更按开始时状态保留。`tmp/demo_direct_port_20261002` 保存 Git 状态、来源与目标 SHA256 清单和被修改源文件备份。枚举目标时 `.pytest_cache` 访问受限，缓存未作为业务基线或迁移材料。

本轮测试后端使用 `tmp/demo_direct_port_20261002/e2e-data`、`e2e-data-fresh`、`e2e-final-20261002` 等新的隔离目录，分别监听 8187–8189；前端监听 5187–5189 并仅代理对应隔离后端。最终浏览器验收使用 8189/5189。根代理独立 HTTP 复核使用 8191 和新的 `latest-server-data`。服务启动未读取 `.env`，显式开启隔离演示邮箱 OTP，模型提供方设为 `rules`，不包含外部模型 Key。两份新验收资料为 `material-a.md` 与 `material-b.csv`，不使用既有生物示例教材。

## 现有回归基线

主代理独立运行现有账号、社区、原文、workspace、评分、FSRS、记录及 Studio 相关测试：124 passed；Studio 图解的三个测试因 Windows 未配置中文字体失败。仅对这三个测试显式设置 `STUDIO_CJK_FONT=C:/Windows/Fonts/msyh.ttc` 重跑，3 passed。合计 127 个相关基线测试通过。pytest 使用工作区下新的 `--basetemp` 并关闭 cacheprovider，绕开旧临时目录权限问题。

## 本轮检查

- `npm run build --prefix frontend`：完整原 Demo 页面树 TypeScript 与 Vite 生产构建通过；根代理独立重跑通过。旧 `tsconfig` 排除 Demo 的捷径已解除。
- `npm run test:demo --prefix frontend`：53 个测试文件、351 条通过，包括原动效契约、失败反馈、版本 ACK、旧账号队列取消和原账号笔迹/文字/录音请求边界。
- `npm run test:unit --prefix frontend`：20 条通过。
- 后端 `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_demo_port.py -p no:cacheprovider --basetemp <新的工作区临时目录>`：最新 18 条通过；ruff 检查通过。成功替身路径覆盖真实视觉字节、诊断题生成、评分 schema/引用、FSRS、语音识别→确认→整理、Studio 任务、章节范围、提交幂等；未配置路径返回实际能力错误。
- 根代理独立相关回归：新增 16 条与原相关 127 条合计 **143 passed**；最终在新增图片 OCR/诊断初始化两条之后，根代理重新运行完整 18 条兼容契约和独立 4 条配置替身检查，**22 passed**。因此通过的相关后端用例为 127 条既有回归、18 条新增契约，另有 4 条独立配置替身检查；这些数字不重复累加。
- 根代理真实 HTTP：两账号注册登录、PDF/MD 上传确认、状态 revision/409、来源/任务/笔记/文件/导出隔离、真实 PDF、积分 reserve/refund/幂等/拒绝伪造 spent、Origin 等 **34/34**。最终证据 `tmp/root-direct-port-review/results-7f773788.json`；配置模型替身独立 4/4，脚本 `tmp/root-direct-port-review/test_configured_contracts.py`。
- 根代理实际停止并重启隔离后端，以同一数据目录重新登录两个账号复核，**6/6**：课程修改、资料目录、原文文字笔记、另一账号课程/资料访问隔离和积分余额均保留。证据 `tmp/root-direct-port-review/post-restart-check.json`。
- 最终 Chromium 实际浏览器：`DIRECT_PORT_E2E=1 DIRECT_PORT_URL=http://127.0.0.1:5189 npx playwright test e2e/demo-direct-port-20261002.spec.ts --workers=1`（frontend 工作目录），**1 passed，8.3s**。覆盖登录/引导、两种真实资料/六项偏好、目录修改确认、原文文字批注、刷新持久化、报告无作答空态、真实 PDF 下载、问答未配置失败与积分不变、原 Profile 社交公开 ID，以及同一运行页面通过账号广播执行 **A→B→A**：B 没有 A 课程，回到 A 课程恢复。

截图在 `frontend/test-results/demo-direct-port-20261002--f420a-urse-with-two-new-materials/`：`original-mobile-home.png`、`original-mobile-course.png`、`original-820-course.png`、`original-1440-course.png`、`original-directory-1.png`、`original-ready-study-1.png`、`original-source-annotation.png`、`original-export-download.png`、`original-qa-capability-unavailable.png`、`original-account-social-tools.png`、`account-b-isolated.png`、`account-a-restored.png`。移动/平板/桌面原布局无横向溢出，使用 reduced-motion。

下载产物 `tmp/demo_direct_port_20261002/artifacts/browser-learning-note.pdf`：有效 `%PDF-`、1 页，文本提取确认包含实际“验收笔记”和“叶绿体”，连续排版；渲染检查图片同目录 `browser-learning-note.png`。根代理最终导出 `tmp/root-direct-port-review/export-7f773788.pdf`：1 页 49,405 bytes，中文清晰、真实笔记与 chapter1 ALPHA 存在，chapter2 OMEGA 不存在。

## 实施与清理

默认 `main.tsx → demo/DemoDirectEntry → demo/App`。以本轮开始时 P0 的 238 个 src 文件快照移植。早先复核一致，但结束时只读复核发现 31 个既有文件 SHA256 已变化、5 个新增文件；不能声明来源全程未变。原 App 页面/布局/动效直接保留，四个 repository 边界接后端；账号水合门按公开身份等待后再挂 App。已删除重复 `src/demoPort` 25 个文件（先备份至 `tmp/demo_direct_port_20261002/demoPort-final-backup`）；仍被既有组件使用的两个通用控件移至 `src/legacyControls`。运行树无旧 DemoPort 导入。浏览器模型 Key 环境读取已禁用，模型仅由后端配置。

课程/源资料/目录/笔记/积分/导出按账号持久化；资料确认同步既有真实 BookStructure。额外工具保留账号、好友私信、画像、诊断/个性化课程、回访、Studio。报告未知用时显示暂无记录，文本生成版与原稿分离，识别转写与用户确认稿分离。native 任务重启中断恢复可重试，Studio 真正任务结果决定积分结算，前端保存失败保留重试上下文。

本轮代理启动的隔离前后端服务在验收后已关闭；测试目录、截图、PDF 和检查结果保留供复核。本轮未提交 Git、未部署，也未将测试账号或资料写入既有正式数据目录。

## 外部能力边界

本轮隔离环境没有实际模型、语音转写、视觉、MinerU OCR 或媒体视频外部提供方配置。生产服务端实现已接通；本地成功契约使用显式配置替身、真实 WAV/图像/文本输入测试，不能据此声称外部提供方实际服务成功。真实浏览器验证了未配置问答错误与退款；真实上传/目录/基础阅读/笔记/PDF 不依赖外部提供方。尚未用实际外部提供方完成生成题卡、语音转写整理、视觉问答、扫描 OCR、Studio 图像/视频的端到端成功，亦未部署到 4090 或公网。

## 来源运行期间变化的复核

P0 清单保存时间为 17:12:53。结束时根代理和实施代理分别只读校验，均检测到原 238 文件中 31 个变化，集中于 main、UI、Home/Flow、SourceRegionAiPanel、screens、styles、api/types；文件 mtime 为 17:28–18:08。新增 `components/icons/StickerIcon.tsx`、`stickerArtwork.ts`、`styles/sticker-icons.css`、`data/demoPhysicsCourse.ts` 与对应测试 5 个文件。漂移和新增清单保存至 `tmp/demo_direct_port_20261002/source-drift-final.json` 与 `source-added-final.json`。

本轮实施路径只写目标仓库，复制方向为来源→目标；没有执行源仓库格式化、恢复或写入。上述变化的实际来源仅凭文件时间无法判定，可能存在用户其它任务并行。保留源仓库当前内容，不覆盖或回滚；没有静默同步这批后续 UI/图标/物理课程变化。因此本轮已测试目标对应开始快照加本轮后端适配，而不是结束时仍在变化的来源最新版。继续同步需由主任务另定范围，并重新集成/验收。
