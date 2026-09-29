# Demo 界面迁移前端交接（P1–P5）

记录日期：2026-09-27，入口状态更新于 2026-09-29。此文主体记录当时 P1–P5 的 Next 前端实现与验收边界；它后来已由原 Demo UI 的完整移植替代。当前实现以 `frontend/src/demo` 的原页面与样式、`frontend/src/demoPort` 的真实业务适配为准；默认入口为 `frontend/src/main.tsx` → `demoPort/DemoPortEntry.tsx`。P0 基线、来源文件和参考截图见 [FRONTEND_MIGRATION_BASELINE.md](./FRONTEND_MIGRATION_BASELINE.md)，当前复验见 [FRONTEND_MIGRATION_ACCEPTANCE.md](./FRONTEND_MIGRATION_ACCEPTANCE.md)。部署验收仍需单独执行。

## 入口、视觉和数据边界

- `frontend/src/main.tsx` 只加载迁移后的应用。`/` 和保留旧查询参数的 `/?ui=next` 都显示新版；`?device=iphone-16`、`iphone-17` 或 `ipad` 在独立视口中加载同一应用。旧入口、旧页面、旧全局样式和只针对旧界面的测试已删除；账号、教材和学习记录未改动。
- 新外壳采用 Demo 的紫色视觉层级、中央教材封面轮播、下一步工作区、圆角底导航、章节分页阅读、闪卡翻面与横向手势、抽屉、转场及 `prefers-reduced-motion`。手机、平板、桌面走响应式布局；设备参数才显示预览框，框内视口负责触发对应响应式规则。
- `services/contracts.ts` 定义独立的真实数据契约，`services/http/studyRepository.ts` 复用现有 `api/client.ts` 和 `api/transport.ts`；页面模型转换在 `services/mappers/viewModels.ts`。新入口不导入 DemoRepository、mockBook、前端 DeepSeek Key、本地 RAG 索引或固定生物页图。仅补装实际使用的 `lucide-react@1.18.0`，保留目标锁文件原有 React/Vite/TypeScript 解析版本。
- 原有 `AccountGate`、账号、社交、回访和 LearningStudio 的业务协议仍由当前应用模块负责；新页面和新入口专用 CSS 提供导航与视觉适配。账号改变时重挂载新应用，异步任务通过账号捕获缓存与上下文 epoch 防止旧结果覆盖新身份。

## 已接通的学习链路

| 阶段 | 真实行为与关键边界 |
| --- | --- |
| 个人书架 | `GET /api/library`；真实封面或通用封面、目录、空态、重试和移出书架。选书恢复该账号、该教材对应的已有 `learning_sessions`，不批量改写记录。领取或 claim 返回的最终 book ID 必须出现在刷新后的个人书架中才判定成功。 |
| PDF 上传 | 单 PDF 文件选择；upload → bind-upload → process/status → structure → claim → diagnostics。进度来自后端。待处理任务保存为账号作用域的 `cloudpath.next.pending-upload`，包含 uploaded/bound/processing/structuring/claiming/diagnostics 阶段；刷新自动恢复。绑定失败继续绑定，非 OCR 阶段失败恢复该阶段；仅后端 failed/review_required 且可重试时调用 OCR retry。claim 的 canonical book ID 用于后续诊断与选择，诊断完成后二次刷新书架。显式放弃才删除非终态任务。 |
| 学习前诊断 | 真实背景、目标、约束、自适应诊断与画像确认；使用当前账号 user ID、教材 ID 和会话 ID。诊断未准备好时显示真实状态，不伪造可开始。 |
| 章节课程 | 已有课程先 GET 恢复；无课程时 POST 编译，超时后 GET 核验。课程按章节、会话、版本关联；阅读区分导读、原文与讲解、例题和小结。课程页始终可“按当前画像检查更新”，POST 由服务端指纹及缓存判断是否生成新版，并保留旧版学习证据。 |
| 练习和闪卡 | 练习按真实 response_type、选项、信心和用时提交，由服务端评分；闪卡翻面、横向切卡，四档评分提交 FSRS。事件 ID 按账号、会话、课程版本与题目/卡片缓存，丢响应、切页或刷新重试仍用同一个 ID；服务端确认结果后释放该 ID，之后再次作答或复习使用新事件，避免新答案被旧评分覆盖。当前题的成功状态禁用再次提交。反馈绑定当前题目或卡片，下一题不显示上一题答案；切到下一张卡时保留简短成功提示。 |
| QA 与原文 | 单书 QA 保留 supported/insufficient 和逐 claim 引用；链接跳对应 PDF 页。原文页动态请求后端同身份校验的 JSON 与 PNG，PDF 页码从 1 开始，印刷页码缺失时不猜测；页图错误显示提取文字或可重试空态。课程阅读页也可预览有依据的原页。 |
| 资料与社区 | 当前账号的笔记、闪卡资料、创建/编辑/归档/撤销、发布和撤回；社区真实搜索、分页、教材/笔记领取、重复检查，领取后选中服务端 canonical ID。 |
| 保留能力 | “我的”可进入资料、学习报告、账号资料、好友与消息；学习页和课程可进入到期复习、疑问回访、手写/语音笔记及 Studio 图解/短片。现有社交、回访、Studio 业务组件继续调用真实服务，能力禁用时展示实际状态，不显示假成功。 |

## 已执行验证

- 在 `frontend` 执行 `npm run build` 通过，TypeScript 与 Vite 构建成功，分别产生新旧 CSS chunk；`npm run test:unit` 的 28 个测试通过。
- 主代理在隔离的 `127.0.0.1:5184` 前端与 `127.0.0.1:8114` 后端验证：两账号、两学科教材的诊断→画像→课程→练习/FSRS、重复事件去重与跨账号权限；真实 OCR/结构/claim/诊断链路；两书 supported QA 引用与 PDF 原页内容核对、insufficient；疑问延后/解决/重开；手写、实际 WebM 语音保存与重开播放；好友申请/接受/私信及丢响应重试；社区分享领取与笔记生命周期。手机 `360/402`、平板 `768`、桌面 `1440` 宽度未见水平溢出，相关浏览器检查未见 pageerror。隔离服务及截图是本地验收设施，不属于应用部署。
- `frontend/e2e/next-upload-recovery.spec.ts` 是隔离 `5184/8114`、显式 opt-in 的故障注入套件，3/3 通过：绑定首次 503 后刷新只补发一次绑定并走首次 process；结构、claim、诊断各首次 503 后刷新分别接续，未误调 OCR retry，最终清理 pending；旧身份迟到绑定响应不能写入新账号缓存。测试对所有非注入写请求直接报错，避免触碰真实测试书籍。

## 验收修正与后续边界

- 新上传教材的 `diagnostics_ready` 以 `GET /api/library` 为准。验收发现 claim 时缓存的 `false` 未随诊断生成更新，现已修复为按当前结构指纹匹配的单选题库动态计算；主代理已重启隔离服务并通过真实上传、刷新恢复及打开诊断复验。
- 外部图像/视频生成、语音识别供应商与真实 SMTP 投递未在本次环境执行；已验证能力状态、允许的原始内容保存以及相关自动化业务回归。详情见最终验收记录。
- 本文原始验收时，P6 默认入口切换及部署均未执行；2026-09-28 本地入口已切换并移除旧界面。原有新旧 URL 回退检查只属于历史结果，目标部署验收仍待执行。
- Demo 独有的目录编辑、独立错题本、日历打卡、PDF 导出、教材配图目录、多轮问答、Office/多文件和 Android 位于计划 E1–E8，首版没有未接通的“成功”入口。

## 2026-09-29 当前交接入口

上面的 `styles/next`、P1–P5 组件描述保留为当时的实施记录。当前请从 `frontend/src/main.tsx`、`frontend/src/demoPort/DemoPortEntry.tsx` 和 `frontend/src/demoPort/DemoPortApp.tsx` 阅读实际入口、真实数据接线与导航。`frontend/src/demo` 保存原 Demo 视觉实现；当前新增业务仍使用现有 `api/`、`app/useStudyState.ts`、`app/useStudyWorkspace.ts` 和 Studio/社交业务组件。部分 `styles/next` CSS 仍由账号、社交、Studio 和原文手写使用，不应按历史目录名直接删除。详细复验与未覆盖范围见 [FRONTEND_MIGRATION_ACCEPTANCE.md](./FRONTEND_MIGRATION_ACCEPTANCE.md)。

上传入口现在使用 `screens/UploadDialog.tsx` 与 `demoPort/DemoUploadFlowPanels.tsx`：上传文件后新增 `awaiting_parse` 持久阶段，必须从 Demo 风格的确认页手动开始解析；原有 `uploaded` 之后的刷新恢复阶段保持兼容。完成页只在后端诊断准备成功后显示。2026-09-29 的受控浏览器流程验收见同一验收记录；上文历史 P3 阶段列表不包含这次新加的确认阶段。
