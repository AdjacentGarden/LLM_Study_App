# SwiftUI 客户端 P0 接入

当前 SwiftUI 客户端位于本仓库 `frontend/`，替代原主 React 前端；React 代码归档在 `legacy/react-frontend/`。客户端通过 HTTP 接入 `backend/`，后端仅提供 API，不再加载 `FRONTEND_DIST_DIR` 或网页路由回退。迁移来源、验收及回退见 [SwiftUI 迁移](SWIFTUI_MIGRATION.md)。

## 服务启动

后端 Python 3.11+，安装 `.[dev,pdf]` 后运行：

```sh
cd backend
./scripts/run_native_dev.sh
```

默认地址 `http://127.0.0.1:8100`，可以通过 `CLOUDPATH_HOST`、`CLOUDPATH_PORT` 修改。默认开启本地默认账号，不需要邮箱验证码、SMTP 或 `.env`。SwiftUI App 自动登录后，首次展示六步个性化问题，完成后进入首页。账号身份固定，课程和进度仍使用现有持久化与鉴权。连接页面可切换服务地址，也支持本机运行环境变量 `BOOKCOURSE_API_BASE_URL`。

`POST /api/native/local-login` 仅在 `NATIVE_LOCAL_LOGIN_ENABLED=1` 且 `APP_ENV=development/local` 时可用，要求请求来源及 Host 都为回环地址，保留 Origin 校验。返回普通 HttpOnly 会话 Cookie，不移除工作区权限校验；关闭该模式后，已有默认账号会话也不可用。开发脚本默认打开，其他启动方式默认关闭。使用 `NATIVE_LOCAL_LOGIN_ENABLED=0 ./scripts/run_native_dev.sh` 可恢复邮箱登录，适用于正式认证或远程连接。

没有 OCR 模型时，PDF 仍可上传和保存；客户端提示解析服务未启动，并保留原文件及草稿。真实 PDF 处理需要启用 OCR worker 并配置 MinerU；目录/诊断需要文本服务；上传教材检索需要安装 `.[rag]` 及 BGE 向量与重排序模型；切回真实邮箱验证码时需要 SMTP。上述配置可以后续写入可选 `backend/.env`。代码不会用测试内容替代尚未配置的服务。

## 新增接口

所有原生工作区接口要求有效账号 Cookie（本地默认账号或邮箱账号），保留原站点 Origin 校验。

首次引导现为六步，偏好 JSON 在原 `displayName`、`primaryGoal`、`dailyTime` 基础上新增：
`learningBarriers`（`starting/focus/understanding/memory`，最多两项）、
`guidanceStyles`（`keyPoints/principles/examples/stepByStep`，最多两项）、
`planningSupport`（`selfDirected/collaborative/structured/explore` 或 null）、
`onboardingVersion`（新版完成为 2，旧版缺省为 1）。多选禁止重复，跳过使用空数组或 null。
旧账号保留原答案并补充后三步；旧客户端仅修改原字段时不覆盖新答案。原生端按后端、账号隔离草稿，冷启动等账号和缓存就绪后才恢复，成功保存才结束引导。

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/native/local-login` | 开发环境本机默认登录，无需 SMTP，复用固定账号与正常会话 |
| `PUT /api/native/learner-preferences` | 保存六步引导/个人页偏好：称呼、目标、每日时间、学习卡点、帮助方式、计划支持及完成版本；bootstrap 返回偏好或 null |
| `GET /api/native/catalog` | 发现中的已有生物课程目录 |
| `POST /api/native/catalog/{id}/import` | 以稳定工作区 ID 导入已有课程，保留课程来源并重置演示学习状态 |
| `GET /api/native/bootstrap` | contract_version=1；工作区、各资料、生成内容、进度和最后选择 |
| `PUT /api/native/selection` | 保存最后选择的课程资料，不增加学习进度版本 |
| `GET/POST /api/workspaces` | 列出或按稳定客户端 ID 保存课程名称和偏好 |
| `GET/DELETE /api/workspaces/{wid}` | 读取/删除自己的课程 |
| `GET /api/workspaces/{wid}/resources/{rid}` | 读取有权访问的资料、已有内容和进度 |
| `POST /api/workspaces/{wid}/resources` | 将有权访问的教材关联到课程，重复关联返回同一资料身份 |
| `DELETE /api/workspaces/{wid}/resources/{rid}` | 移除课程中的资料，不删除共享教材 |
| `POST /api/native/uploads` | 有界 PDF 上传并在响应前绑定当前账号 |
| `POST /api/native/books/{bid}/prepare` | 持久化目录并构建/发布该上传教材的检索索引 |
| `GET /api/native/books/{bid}/readiness` | 独立报告目录和上传索引就绪状态 |
| `POST /api/native/books/{bid}/cancel` | 取消排队/运行中的 OCR，原文件保留 |
| `GET /api/books/{bid}/source` | 账号鉴权的 PDF 下载，支持原生 PDFKit |
| `GET /api/books/{bid}/pages/{page}` | PDF 物理页及归一化原文/文本块；无印刷页依据时返回 null |
| `PUT /api/workspaces/{wid}/resources/{rid}/chapters` | 带目录 revision 的个人目录确认；修改范围后重新核验章节摘要与证据 |
| `POST /api/workspaces/{wid}/resources/{rid}/session` | 幂等创建/恢复该课程资料的诊断会话 |
| `PUT /api/workspaces/{wid}/resources/{rid}/progress` | 带进度 revision 和 mutation_id 的学习状态更新 |
| `POST /api/workspaces/{wid}/resources/{rid}/catalog-practice/{question}` | 已有课程客观题服务端评分、按 event_id 去重；主观题需要模型 |
| `GET /api/workspaces/{wid}/resources/{rid}/catalog-records` | 当前资料的练习记录，用于错题恢复 |

继续复用 `/api/auth/*`、`/api/books/{bid}/process`、`status`、`process/retry`、`qa` 和 `/api/interviews/*` 的真实诊断、课程编排、练习评分和学习记录接口。公开题目不包含答案。

## 六步答案驱动课程设计（已实现）

`learning_preferences.py` 定义有效配置及严格的课程覆盖字段；`learning_design.py` 负责账号、课程、诊断声明合并，以及每日计划和调整建议。知识掌握度仍由诊断与真实学习记录维护，不从时间预算、卡点或帮助偏好推断。

默认值 → 个人六步偏好 → 原课程问卷 → 课程专属覆盖 → 当前课程内明确回答，逐层覆盖。未设置项继承，空数组表示明确清空多选。每个诊断声明记录当时的课程偏好版本；用户后来修改课程目标/时间时，新设置覆盖旧声明，之后再次明确作答又以新声明为准。多选没有排名含义，点击顺序不改变策略或缓存。

`GET /api/workspaces/{wid}/learning-design` 返回 `revision`、`overrides`、`effective` 与 `fingerprint`。`PUT` 使用乐观锁，示例：

```json
{
  "revision": 0,
  "overrides": {
    "primary_goal": "exam",
    "minutes_per_day": 20,
    "learning_barriers": ["understanding", "memory"],
    "guidance_styles": ["examples", "stepByStep"],
    "planning_support": "collaborative"
  }
}
```

这是完整替换课程覆盖；省略/null 的项继承个人偏好，`[]` 清空对应多选。已确认的诊断证据和进度不受影响。创建学习会话时预填已有目标、时间，跳过已知的对应提问，保留章节重点与基础诊断。

### 课程、知识覆盖与版本

编排器输出 `design_config`、`design_fingerprint`、`units`、`coverage_point_ids`，同时保留原有平铺字段，兼容旧客户端。所有有证据的知识点均进入课程与单元队列；预计单次时长、教学深度和偏好控制单元组织，不再截掉剩余知识点。证据不足的内容明确标记待补证。

目标影响检查题侧重；帮助方式影响重点、原理、原文例证、步骤的区块与顺序；卡点增加前置概念检查、小任务或主动回忆。正文采用可核验原文与结构化导学指引，不给原文缺失的内容补造案例。闪卡模型重写也读取有效配置，发布前仍需独立审校。

有效配置纳入课程缓存指纹。相同教材、画像及有效配置复用版本；偏好变化后，下一次 `POST /api/interviews/{sid}/courses/{chapter}` 生成新版本，`GET` 仍读取已有版本。课程生成不覆盖阅读进度。闪卡和练习 ID 按会话、知识点稳定生成；旧版闪卡复习状态通过知识点关联到新版本，继续沿用真实 FSRS 到期时间。

### 每日计划与学习反馈

| 接口 | 行为 |
| --- | --- |
| `GET /api/workspaces/{wid}/study-plan?day=YYYY-MM-DD` | 当日建议、完整任务队列、预算、任务状态、偏好快照和调整建议 |
| `POST /api/workspaces/{wid}/study-plan/confirm` | 按 `day/plan_id/revision/task_ids` 确认或调整共同计划，校验预算和前序任务 |
| `POST /api/workspaces/{wid}/study-plan/tasks/{task_id}/complete` | 按 `event_id/day` 幂等记录完成，独立于知识掌握判断 |
| `GET /api/workspaces/{wid}/learning-design/feedback` | 根据可靠评分给出建议；至少同知识点最近三次作答均需补强才建议调整 |
| `POST /api/workspaces/{wid}/learning-design/adjustments` | 用 `suggestion_id/revision` 明确接受建议；原覆盖及证据一起保存 |
| `POST /api/workspaces/{wid}/learning-design/adjustments/{id}/revert` | 撤销本次建议；如果之后另有编辑，返回冲突以避免覆盖新设置 |

自主安排返回候选任务，共同规划返回待确认方案，完整计划返回清单，自由体验只建议下一步。任务选择遵守预算和已有前序约束，目标影响练习优先级。已有生物课程可直接生成个人任务计划，较长任务拆为稳定的预计阅读时间段，保留原任务引用。已学内容可按记忆支持偏好安排原课闪卡；已有真实复习状态优先用 FSRS，尚无状态时仅提供初始复习建议。

`day` 默认 UTC 日期，客户端可传本地日期。已确认或已经开始的当日计划保留原快照；偏好变化通过 `preferences_changed` 提示，后续日期使用新配置。计划确认带独立 `revision`，防止不同设备相互覆盖，重复相同确认可安全重试。完成记录跨课程新版本保留；建议确认与偏好写入使用同一 SQLite 事务。没有调用推送或自动开启提醒。

### 个性化教材答疑

`POST /api/workspaces/{wid}/resources/{rid}/qa`，或原书籍 QA 接口同时传 `workspace_id/resource_id`，由后端检查账号、课程、资料和教材对应关系后读取偏好。仅有书籍 ID 的旧请求，若该账号只有一份对应课程资料则自动定位；存在多门同书课程时只应用个人全局偏好。

偏好单独传给模型，不进入检索问题；当次问题中的明确表达要求优先。引用与语义校验继续执行。答案缓存按账号、课程资料和有效教学偏好隔离，检索缓存仍按教材与原问题复用。

### 客户端接入边界

六步偏好保存、旧版平铺课程和书籍 QA 保持兼容。现有 iOS 客户端已通过接口回归；新单元的分段呈现、服务端每日计划清单、建议确认/撤销界面还需在 SwiftUI 中接入上述字段与接口。本轮没有改动 SwiftUI 界面或动效。

## 持久化与版本

`state/native.sqlite3` 保存工作区、资料关系、个人目录、会话上下文、进度、去重 mutation ID 和最后选择。教材 OCR、账号、原有学习会话数据库继续沿用。

另保存明确完成的个性化偏好和已有课程练习记录。原项目生物课程打包在 `src/adaptive_learning/data/native_biology.json`，含原导出数据的 SHA-256；读取与导入均无需 OCR 或模型。原生端保留原项目课时、素材及 125 张原文页图片，原项目 AI 补充与待核验状态保持不变。服务端返回 `source_content` 适配旧课程树，题目答案不公开；任务、掌握度与演示完成记录在导入时重置，用户进度仍按资料独立保存。

生物课程是内置的永久目录，与个人工作区及学习状态独立；删除自己的课程不会移除发现中的条目。App 另打包 `frontend/Resources/Data/builtin-catalog.json`，在账号模式联网前加载，并按稳定 ID 合并服务端目录。目录响应为空或刷新失败时，保留内置入口；在线导入和进度保存仍需后端。

在仓库根目录使用 `backend/.venv/bin/python backend/scripts/import_native_biology.py --sources frontend/Resources/Data/sources.json --community frontend/Resources/Data/community.json` 显式更新快照，同时生成 App 内置目录，不会导出设备个人数据。

课程工作区与生成的章节教学内容使用不同 ID。每个资料会话保存目录和诊断题库快照，目录修改不会覆盖共享教材或其他课程；旧会话仍保留其原有上下文。删除课程只移除对应工作区关系，不删除教材和历史学习证据。

上传文件检查 PDF 后缀、文件头、非空和 100 MB 上限，文件归属在响应前写入。旧教材 API 对已绑定上传执行身份校验，防止绕过原生接口访问私有教材。

新上传索引位于 `books/{bid}/rag/{version}/`。版本绑定归一化内容摘要、模型路径和切块版本；完整写入后原子发布 `current.json`。回答时根据该教材指针加载索引，缓存按教材及索引版本隔离；归一化内容变化会使旧指针失效。无需把用户私有教材追加到 `PUBLISHED_BOOK_IDS` 或公开发布清单。

进度示例：

```json
{
  "revision": 0,
  "mutation_id": "a-stable-client-uuid",
  "page": 2,
  "chapter_id": "chapter-id",
  "lesson_id": "generated-course-id",
  "completed_chapters": [],
  "task_status": {},
  "mastered_ids": []
}
```

首次成功后 revision 增加；重复 mutation 返回当前状态；其他设备使用旧 revision 写入得到 409。服务端校验页码、章节和生成课时属于当前课程资料。

## 验收与边界

`tests/test_native_api.py` 使用真实路由、账号、SQLite、PDF、诊断状态机、课程编排和引用校验，仅替换外部 OCR/模型适配器。`tests/run_native_server.py` 仅在显式 `NATIVE_E2E_TEST_SERVER=1` 时启动，固定监听回环端口 18101，使用一次性临时数据目录，供原生 XCTest/UI 验收。

2026-10-22 个性化后端验收：441 项通过、5 项可选完整教材测试跳过；1 项依赖缺失 `artifacts/ocr/biology-20260829/pages.jsonl` 的既有测试排除。`tests/test_learning_design.py` 覆盖配置优先级、旧诊断声明与新课程设置的顺序、多选语义、单元覆盖、稳定题目 ID、版本缓存、计划预算/确认/幂等完成、复习建议间隔、旧复习状态迁移、建议接受/撤销、课程隔离和 QA 缓存隔离。全量测试使用临时取消代理环境变量的命令运行，避免本机 SOCKS 代理依赖影响模拟 HTTP 传输测试。

原生 `BookCourseTests/CloudTests`：3 项通过、默认账号专项在该轮认证模式下跳过，PDF → 诊断 → 课程 → 答疑 → 进度恢复及已有生物课程导入保持兼容。Ruff 通过，新增核心模块的严格 mypy 检查通过；全仓 mypy 还有 10 项既有问题（文本客户端、旧闪卡变量类型、Studio、社区及静态文件路由），本轮没有扩展到这些历史清理。实际开发服务已重启，并确认默认账号、bootstrap 和新接口可用；真实 OCR/GPU/模型联调仍需本机服务配置。

本次不接管旧浏览器 IndexedDB 或设备本地示例数据。笔记云同步、完整社区课程发布、好友客户端和积分计费仍属后续范围。目录、索引和题库生成目前使用可重入的请求，持久化结果支持中断恢复；进一步后台任务化属于后续工作。
