# Demo 前端移植 P0 基线

记录时间：2026-09-27（截图索引时间 `2026-09-27T12:21:30.829Z`）。本文件记录代码移植前的状态和独立运行的 Demo 视觉参考，不把 Demo 数据或基线测试视为目标项目验收。

## 仓库、锁文件和用户工作树

| 项目 | P0 起点 |
| --- | --- |
| 来源 `D:/code/study_app_demo` | `main`，HEAD `4168b88f73639cec76d27711dce8640582e235b9`；工作树干净，保持只读 |
| 目标 `D:/code/LLM_Study_App` | `main`，HEAD `c3580323602a8080dd65cb8ae254eaf1c65fc34a`；起点存在下列 13 个未提交文件，前端代码在起点无修改 |
| 来源 `package-lock.json` | SHA-256 `DA1DF55E71AD0274AF5BC00B3EB43762802A9205CE0D193EC1DE68C26A8192D6` |
| 目标 `frontend/package-lock.json` | **HEAD 原始版本** SHA-256 `D6E4E7569A1996393E5045DF20DB2086AE1BC5A225F5F835048DE4435F833514`；实施代理随后会修改工作树锁文件，不应把新哈希当成 P0 起点 |
| 目标 `backend/uv.lock` | SHA-256 `38A7739842845B837EA54ECA349BE68B179C881226B920591F36044173963F7A`；起点未修改 |
| 目标 `backend/pyproject.toml` | 起点有用户改动；快照 SHA-256 见 `baseline-hashes.json`，不能用 HEAD 代替 |

来源锁文件解析版本：React/React DOM `19.2.7`、Vite `8.0.16`、TypeScript `6.0.3`、`@vitejs/plugin-react` `6.0.2`、Playwright `1.61.0`、GSAP `3.15.0`、`@gsap/react` `2.1.2`、Lucide `1.18.0`。目标 HEAD 锁文件对应 React/React DOM `19.2.8`、Vite `8.2.2`、TypeScript `7.0.2`、`@vitejs/plugin-react` `6.1.0`、`@playwright/test` `1.61.0`；目标起点没有 GSAP/Lucide。依赖应只补实际使用项，并以目标锁文件解析结果为准，不整体复制 Demo 锁文件。

P0 起点的用户未提交文件为：

- `backend/pyproject.toml`
- `backend/src/adaptive_learning/api/account_guard.py`
- `backend/src/adaptive_learning/api/account_routes.py`
- `backend/src/adaptive_learning/api/app.py`
- `backend/src/adaptive_learning/api/community_routes.py`
- `backend/src/adaptive_learning/community.py`
- `backend/src/adaptive_learning/ingestion/ocr_job.py`
- `backend/src/adaptive_learning/llm/client.py`
- `backend/tests/test_accounts.py`
- `backend/tests/test_community.py`
- `backend/tests/test_ocr_job.py`
- 未跟踪 `backend/src/adaptive_learning/api/origin.py`
- 未跟踪 `docs/FRONTEND_DEMO_MIGRATION_PLAN.md`

主代理已在 `C:/Users/asd25/.codex/artifacts/frontend-migration-20260927/baseline/` 保存这 13 个文件，文件名和相对目录不变。`baseline-status.txt` 保存原始 `git status --short`，`baseline-hashes.json` 给出每个文件的 SHA-256。P0 已重新核对快照：13/13 文件存在且哈希相符。快照只含这些代码/计划文件，不含 Secret、数据库、教材、索引或用户记录。实施开始后的其他未提交文件属于移植工作，不应混入上述初始用户改动。

## 来源文件建议与排除

`C:/Users/asd25/.codex/artifacts/frontend-migration-20260927/reference/source-manifest.json` 逐文件记录 **119 个明确候选** 的相对路径、字节数、SHA-256、分组和处理方式，来源均为上面的 Demo HEAD；清单本身 SHA-256 为 `CD70766C79224094189734B775F9CE6B4708C3E46FBBB343726399DD76A40AD2`。它是迁移选材和逐批核对依据，不表示 119 个文件都可原样复制。

| 批次 | 具体来源 | 对应目标职责与处理 |
| --- | --- | --- |
| 外壳和基础控件（10） | `src/components/ui.tsx`、`IosStatusBar.tsx`、`ErrorBoundary.tsx`、`components/home/*`、`components/study/ChapterToolCards.tsx`、`layouts/{DeviceChrome,PhoneChrome,PadChrome,useDeviceLayout}` | 拆开混合 UI/聊天/静态数据依赖，迁入 `components/ui/`、`layouts/` 等；保留目标账号门禁 |
| 页面（29） | 清单中的 `src/screens/*.tsx`、`src/screens/sheets/{Chat,BookSwitcher,Source,Note}SheetContent.tsx` | 逐页迁入 `screens/`，接目标 Repository 与真实状态；上传、课程、报告、社区页面不能继承 Demo 固定成功值 |
| 页面助手（20） | `src/screens/{homeBookModel,homeNextStep,studyDirectory,lessonReading,flashcardGestures,assignmentExercises,...}.ts` 等，详见清单 | 仅保留可复用展示/导航/手势逻辑；生物教材和本地保存假设逐项替换 |
| 动效（23） | `src/motion/*` 中非测试 TypeScript 文件 | 按新入口迁入，保持键盘、返回和 reduced-motion 行为；依赖按需安装 |
| 样式（16） | `src/styles/{tokens,base,glass,responsive,home,study,motion,card-system,...}.css` | 放入新入口专用样式域；不与旧入口全局 CSS 同时加载 |
| 入口与协议参考（7） | `src/{App,main}.tsx`、`context/{AppContext,BookCourseRepositoryContext}.tsx`、`types/{app,api}.ts`、`api/bookcourseApi.ts` | 只作导航/类型参考，按目标 `api/client.ts`、`api/transport.ts` 与真实 FastAPI 重新设计，不整体覆盖同名文件 |
| 品牌装饰候选（10） | 清单中的聊天云朵状态图、成功/解析动效 strip、`home-learning-path.webp`、`daily-task-cloud-bg.png` 等 | 仅在迁入组件实际引用时复制并统一“云径 CloudPath”文案；目标已有 `cloud-mascot-home.png` 与 `cloud-mascot-parsing.png` |
| 设备预览（4） | `src/preview/{DevicePreviewStudio,DevicePreviewToolbar,devicePreview}` 与 `styles/device-preview.css` | 仅独立开发/验收入口使用；根路径直接运行应用 |

明确排除原样复制的内容：`.env*`、`node_modules/`、`dist/`、`.cache/`、`tmp/`、录屏与输出目录；整套 `android/` 和 Capacitor 配置；`public/rag/` 的 ONNX/WASM/生物固定索引；`public/assets/textbook/pages/`、生物页图、固定教材封面与演示课程素材；`src/data/{mockBook,demoShelfBooks,seed,generated,...}`；`src/services/{DemoRepository,DeepSeekRag,TextbookRetriever}.ts`、`src/rag/`、客户端模型 Key/本地检索 hooks；`src/screens/{MistakeBookScreen,ExportPreviewScreen}.tsx` 和 `sheets/EditChapterSheetContent.tsx` 的未接通成功操作；Demo 的宣传/内容生成脚本和测试 harness。目标现有 `api/accounts.ts`、`api/client.ts`、`api/transport.ts`、`api/social.ts`、`api/retention.ts`、`api/learningStudio.ts`、`types/api.ts` 是真实协议基础，需保留。

## 当前业务保留矩阵

| 已有能力与现有入口 | 迁移需保留的行为 | 现有验证依据（回归入口） |
| --- | --- | --- |
| `AccountGate`、`RegisterPage`、`UserProfilePage` | 登录注册、资料、会话恢复、账号切换、缓存隔离 | `frontend/tests/accountStorage.test.ts`；`frontend/e2e/{accounts,auth-design}.spec.ts`；`backend/tests/{test_accounts,test_accounts_email,test_user_profile}.py` |
| `LibraryHub`、`LibraryShelf`、`BookUpload`、`ChapterMap` | 个人书架、PDF 上传/解析、目录、移出书架、跨书切换 | `frontend/tests/bookContext.test.ts`；`frontend/e2e/{shelf-qa,shelf-edge}.spec.ts`；`backend/tests/{test_api,test_ocr_jobs_api,test_chaptering_api,test_library_profile_api}.py` |
| `DiagnosticJourney`、`LearningHome`、`CourseTabs` | 主动诊断、画像确认、个性化章节课程及旧会话恢复 | `frontend/tests/learningPlan.test.ts`；`backend/tests/{test_interview,test_personalization,test_course_compiler,test_learning_records}.py` |
| `PracticeFeedback`、`FlashcardDeck` | 服务端练习评分、复习提交、FSRS 与题目证据 | `frontend/tests/cardGesture.test.ts`；`backend/tests/{test_assessment,test_flashcard_review,test_learning_records}.py` |
| `TutorChat` | 单书问答、证据不足、跨书引用隔离 | `frontend/e2e/shelf-qa.spec.ts`；`backend/tests/{test_qa_api,test_grounded_qa,test_cross_book_repairs}.py` |
| `CommunityPage`、`CommunitySharing` | 书籍/笔记发布、搜索、领取、撤回与去重 | `backend/tests/test_community.py`；`frontend/e2e/social.spec.ts` 的分享/领取路径 |
| `SocialPage`、`FriendChat`、`FriendSharePicker` | 好友申请、私信、分享、屏蔽和双账号权限 | `frontend/tests/social.test.ts`；`frontend/e2e/{social,accounts}.spec.ts`；`backend/tests/test_social.py` |
| `LearningReturn` | 到期复习、疑问回访、解决/重开、刷新后恢复 | `frontend/e2e/learning-return.spec.ts`；`backend/tests/test_learning_return.py` |
| `LearningStudio` | 手写、语音、图解/短片任务、原始内容与版本状态 | `frontend/e2e/{learning-studio,studio-complete,studio-media-playback}.spec.ts`；`backend/tests/test_studio.py` |

这是现有测试**映射**，并非所有能力在当前 P0 重新跑通。原计划记录的移植前结果为 Demo 319 项单元测试和 TypeScript 检查通过、目标前端 28 项单元测试和 TypeScript 检查通过；该结果只说明当时基线，不能替代 P1–P6 的联调、模型/OCR、浏览器和回退验收。新增原文页接口与 Demo 独有目录编辑、错题本、日历计划、导出等能力没有现有业务回归可直接证明，应按计划另行验收或隐藏未接通操作。

## 实测视觉参考

独立 Vite 服务：`http://127.0.0.1:5178/`，仅监听 `127.0.0.1`，监听进程为 Node PID `44592`（启动时 exec session `5541`）。启动命令在 Demo 目录执行，显式以空白值覆盖个人模型 Key；没有读取或打印任何 `.env` 内容：

```powershell
$env:VITE_DEEPSEEK_API_KEY=' '
$env:VITE_BOOKCOURSE_API_BASE_URL='http://127.0.0.1:5178'
npm run dev -- --port 5178 --strictPort
```

截图脚本从目标仓库执行：

```powershell
cd D:/code/LLM_Study_App/frontend
node scripts/migration-reference-capture.mjs 'C:/Users/asd25/.codex/artifacts/frontend-migration-20260927/reference'
```

截图位于 `C:/Users/asd25/.codex/artifacts/frontend-migration-20260927/reference/`；`capture-index.json` 含每张截图的实际 URL、视口、状态选择器及 SHA-256。脚本经真实浏览器在 Demo 本地 Repository 数据下进入页面，等待内容而非加载骨架出现后截图。已人工检查首页、目录、抽屉、闪卡、练习和主要视口画面。

| 视口 | 已实际截图的页面状态 |
| --- | --- |
| 手机竖屏 `402×874` | `phone-portrait-{home,course-directory,book-switcher-drawer,flashcard-front,flashcard-revealed,assignment,lesson-reading}.png` |
| 手机横屏 `874×402` | `phone-landscape-{home,course-directory}.png` |
| 平板竖屏 `834×1194` | `tablet-portrait-{home,course-directory}.png` |
| 桌面 `1440×900` | `desktop-{home,course-directory}.png` |

共 13 张，`capture-index.json` 记录 0 个浏览器 pageerror。横屏参考能看到低高度下底部导航覆盖可视内容，迁入时需特别检查可滚动区域。此次没有截图平板横屏、窄于 402px 的手机、深色主题、完整聊天交互、动态过渡过程或实际 Android；也没有连接目标后端。因此这些场景不应据本批截图声称通过。

## 入口和回退

目标 HEAD 的原入口由 `frontend/src/main.tsx` 加载 `App`/`AccountGate`，并在宽视口使用 `PhoneSimulator`；开发端口为 `5174`，Vite 将 `/api` 代理至 `http://127.0.0.1:8100`。前端构建由 `npm run build` 生成 `frontend/dist`，部署方式为后端同源提供前端和 `/api`。P0 起点不存在 `?ui=next` 入口；移植阶段采用完整页面导航的 `?ui=next` 预览，默认仍为旧界面，两套全局 CSS 按入口互斥加载。

迁移期回退：移除 URL 中 `ui=next` 后整页重新加载旧入口，检查同一账号、教材与学习记录仍可打开。正式切换前应保存上一版 `frontend/dist` 和入口默认值；若新界面成为默认后出问题，切回旧入口并重新构建/部署前端，或恢复上一版前端构建。回退只更换静态前端资源，不清空数据库、教材、索引、会话或用户记录，也不重置前述用户未提交文件。若要恢复 P0 起点的某个用户改动，只从 `baseline/` 按单文件比对后处理，并用 `baseline-hashes.json` 核验；不得对整个工作树执行强制重置。
