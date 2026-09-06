# BookCourse AI 后端 P0–P1 安全优化方案与审核标准

本文件描述针对当前前后端代码审查发现的 P0/P1 级别问题所采取的优化措施，以及审核人应据此执行的验收清单。所有改动遵循“向后兼容、可被测试覆盖、显式可审计”的原则。

## 一、范围

仅覆盖 `/api` 业务接口与前端鉴权配置；OCR、RAG 索引质量等非安全议题留待后续阶段。

影响代码：
- `BookCourseAI_backend/app/core/auth.py`
- `BookCourseAI_backend/app/core/config.py`
- `BookCourseAI_backend/app/core/limits.py`
- `BookCourseAI_backend/app/core/errors.py`
- `BookCourseAI_backend/app/main.py`
- `BookCourseAI_backend/app/api/routes_*.py`
- `BookCourseAI_backend/app/services/job_store.py`
- `BookCourseAI_backend/app/assignments/service.py`
- `BookCourseAI_backend/app/study_plan/service.py`
- `BookCourseAI_frontend_project/src/api/bookcourseApi.ts`
- `BookCourseAI_frontend_project/src/config/runtime.ts`
- `BookCourseAI_frontend_project/src/App.tsx`
- 新增 `BookCourseAI_backend/app/core/logging.py`
- 新增 `BookCourseAI_backend/app/core/ratelimit.py`
- 新增 `BookCourseAI_backend/app/core/worker.py`
- 新增 `BookCourseAI_backend/app/services/persistence.py`
- 新增 `BookCourseAI_backend/app/tests/test_audit_p0p1.py`

## 二、优化项

### P0-1 移除前端硬编码 API Key，改进密钥比较

#### 问题
- 前端 `runtime.ts` 通过 `import.meta.env.VITE_BOOKCOURSE_API_KEY` 把后端密钥内联到构建产物。
- 后端 `require_api_key` 使用 `!=` 比较 API Key，存在时序攻击。

#### 措施
- 前端：删除 `runtimeConfig.apiKey` 及其在 `requestJson` 中的 `X-BookCourse-Api-Key` 注入；前端不再持有后端密钥。
- 后端：使用 `secrets.compare_digest` 进行常量时间比较；空字符串与 `None` 同样视为缺失。
- 提供新的“可选管理员密钥”头 `X-BookCourse-Admin-Token`（仍由反向代理或服务器注入路径控制），前端不写入。
- 配置项改动：保留 `BOOKCOURSE_API_KEY`，但**不再**通过 `import.meta.env` 传给前端；前端发包时不携带该头。

### P0-2 默认强制鉴权 + 修复越权 IDOR

#### 问题
- `auth_mode` 默认 `optional`，未配置 `BOOKCOURSE_API_KEY` 时所有业务接口免鉴权。
- 业务接口没有用户维度，`DELETE /books/{book_id}`、`PATCH /study-tasks/{task_id}`、`/users/{user_id}/mistakes` 等可被任意调用。

#### 措施
- `auth_mode` 新枚举：`strict | optional`（保留旧值 `api_key` 当作 `strict`）。
- 默认值改为 `strict`，要求服务端配置 `BOOKCOURSE_API_KEY`，否则业务接口全部返回 503 `api_key_not_configured`；`/api/health` 保持公开。
- 引入 `X-BookCourse-User-Id` 请求头作为用户上下文（在 `strict` 模式下必须与 `BOOKCOURSE_API_KEY` 一同提供）。
  - 兼容旧测试的 `optional` 模式：不提供用户上下文时退化为 `local_user`。
- 持久化 book 的 owner：`books/{book_id}/original/_owner.json` 记录 `user_id`。
  - `DELETE /books/{book_id}`、`POST /books/{book_id}/parse` 等写入操作要求调用者 `user_id` 与 owner 一致；只读接口允许跨用户但不返回 owner 元数据。
- `/users/{user_id}/mistakes`、`/users/{user_id}/learning-state`：调用者 `user_id` 必须与路径一致，否则 403 `forbidden_user_mismatch`。
- `PATCH /study-tasks/{task_id}`：从持久层查找 task owner，调用者必须匹配，否则 403。
- 不影响现有 fixture/演示场景：在 `optional` 模式下 `user_id` 缺省回退到 `local_user` 以保证演示数据可见。

### P0-3 关键运行态持久化

#### 问题
- `job_store`、`assignments/submissions|mistakes`、`study_plan/plans` 全部为内存 dict，进程重启即丢失。

#### 措施
- 新增 `app/services/persistence.py`：
  - 提供 `JsonStateStore(base_dir, name)`：写入 `{base_dir}/{name}.json`，原子重命名（tmp + os.replace），重入安全。
  - 提供配合内存缓存的 `load()/save()` 调用。
- 改造：
  - `JobStore`、`LessonBuildJobStore`、`ImageGenerationJobStore` 在每次 `create/update` 调用后 `save()`；启动时调用 `load()` 回放历史。
  - `assignments/submissions`、`mistakes` 同上。
  - `study_plan/plans` 改为 key->dict JSON 文件。
- 状态路径：`storage_root/_state/*.json`，受 `resolve_under_root` 约束。
- 仅在持久化配置开关开启时回写：`BOOKCOURSE_PERSIST_STATE=true`（默认 `true`），单测可用 `false` 加快。
- 进程启动时回放，恢复任务记录为 `failed`（中断），并视作幂等审计点。

### P1-1 全接口限流

#### 问题
- 当前仅 `heavy_task_limiter` 限制 OCR/lesson/image 任务，其他写接口（`/api/uploads/init`、`/api/rag/query`、`/api/assignments/*`、`/api/books`）无限流。

#### 措施
- 新增 `app/core/ratelimit.py`：基于 IP + 用户 ID 的滑动窗口令牌桶，纯进程内（满足单实例；多实例需迁移到 Redis，留 TODO）。
- 新增 FastAPI 中间件：按 `BOOKCOURSE_GLOBAL_RATE_PER_MINUTE`、`BOOKCOURSE_WRITE_RATE_PER_MINUTE` 限制；写接口（POST/PATCH/DELETE）使用更严的桶。
- 默认值：global 120/min、write 30/min，可通过环境变量覆盖。
- 触发限流返回 429 `rate_limited`，包含 `retry_after`。
- 已存在的重任务限流器逻辑保留，不重复套用。

### P1-2 结构化日志 + 请求追踪

#### 问题
- `unhandled_error_handler` 直接吐 `exc.__class__.__name__` 给客户端且不打日志。

#### 措施
- 新增 `app/core/logging.py`：
  - 使用标准库 `logging` 配置 JSON 行格式（`time, level, event, request_id, logger, message, fields`）。
  - `RequestIdMiddleware`：注入 `X-Request-Id`（如未提供则生成 UUID），并写入 contextvar，所有日志自动带上。
  - `unhandled_error_handler` 改为：记录完整 traceback 到日志，对客户端仅返回 `code=internal_server_error, message=服务器内部错误, details={"request_id": ...}`。
- 业务层 `AppError` 也会被 `app_error_handler` 记录为 warning。
- `/api/health` 不被中间件断言依赖，但仍打 access log。

### P1-3 任务队列脱离客户端连接

#### 问题
- `BackgroundTasks` 跑在 Starlette 线程池，客户端断开可能影响任务执行；任务记录只在内存。

#### 措施
- 新增 `app/core/worker.py`：
  - 一个进程内 `TaskQueue` 使用 `queue.Queue` + 单独 daemon 线程。
  - `enqueue(fn, *args)` 把任务丢进队列；worker 线程顺序执行。
  - `lifespan` 启动时初始化 worker；shutdown 时设置停止标志并 join。
- 业务路由 `_reserve_heavy_task` + `enqueue(...)` 替换 `BackgroundTasks.add_task`，对客户端立即返回 `job_id`。
- 失败/异常由 `worker` 统一记录日志，并写回 `job_store`/`lesson_job_store`/`image_job_store` 状态。
- 任务完成后保留持久化记录（见 P0-3）。

### P0/P1 前端

- `runtime.ts` 删除 `apiKey`；`bookcourseApi.ts` 删 `X-BookCourse-Api-Key` 头注入。
- 默认 `defaultUserId` 从环境变量读入；保留 `local_user` 兜底以维持演示。
- 新增 `ErrorBoundary` 组件包裹 `App`，避免子组件抛错整页白屏。
- `App.tsx` 的 `pollParseJob` 使用 `AbortController` 在卸载时取消请求。

## 三、审核标准（验收清单）

### A 鉴权与越权
1. 启动后端未设置 `BOOKCOURSE_API_KEY`，访问 `/api/books` 必须返回 503 `api_key_not_configured`；`/api/health` 必须返回 200。
2. 设置 `BOOKCOURSE_API_KEY=secret` 访问 `/api/books` 不带 API Key 必须返回 401 `invalid_api_key`；带错误 Key 同样 401。
3. 带正确 API Key 但 `X-BookCourse-User-Id` 与 `/users/{user_id}/mistakes` 路径不一致，必须返回 403 `forbidden_user_mismatch`。
4. `X-BookCourse-User-Id=userA` 创建并上传 book，再用 `userB` 删除必须 403。
5. `PATCH /study-tasks/{task_id}` 调用者非 task owner 必须返回 403。
6. API Key 比较使用 `secrets.compare_digest`（在 `app/core/auth.py` 中可直接看到）。
7. 构建产物中搜索 `apiKey` 必须无明文密钥（前端 `dist/` 中 grep `X-BookCourse-Api-Key` 无结果）。

### B 持久化
8. 启动后端，提交一次解析任务至 `status=processing`，强制 kill 进程；重启进程后调用 `GET /api/jobs/{job_id}` 仍能返回记录（状态可能为 `failed`）。状态文件 `storage_root/_state/*.json` 存在。
9. 重启进程后 `GET /api/users/{user_id}/mistakes` 能返回重启前的 mistake 记录。
10. 重启进程后 `GET /api/books/{book_id}/plan?user_id=...` 能恢复 plan。

### C 限流
11. 配置 `BOOKCOURSE_GLOBAL_RATE_PER_MINUTE=5`，连续请求 6 次 `/api/health`，第 6 次应返回 429 `rate_limited`，`retry_after` 字段存在。
12. 配置 `BOOKCOURSE_WRITE_RATE_PER_MINUTE=2`，连续 3 次 `POST /api/uploads/init` 第 3 次返回 429。
13. `/api/health` 与重任务限流器均不互相误伤（这两者各走自己的桶）。

### D 日志与请求追踪
14. 任一请求的响应头必须包含 `X-Request-Id`；若客户端提供则原样回传。
15. 视发 500 的请求，服务端日志中能 grep 到 `event=unhandled_exception, request_id=...` 与对应 traceback；客户端返回体不含 `exc.__class__.__name__`。
16. `AppError` 抛出时服务端会记录 `event=app_error, code=..., status_code=...`。

### E 任务队列
17. 配置 `BOOKCOURSE_USE_WORKER=true`，发起一次 parse 任务后立即关闭客户端连接（断开 fetch），后端仍能继续把该 job 推进到 `done` 或 `failed`。
18. worker 队列在 lifespan 关闭时优雅退出，不会丢已 `enqueue` 的任务（持久化记录中至少出现 `failed/interrupted`）。

### F 前端
19. `src/config/runtime.ts` 无 `apiKey` 字段。
20. `src/api/bookcourseApi.ts` 不发送 `X-BookCourse-Api-Key` 头。
21. 前端有 `ErrorBoundary` 包裹根组件，子组件抛错时显示兜底 UI 而非白屏。
22. `pollParseJob` 卸载时调用 `AbortController.abort`，不再发起后续 fetch。

### G 测试与回归
23. `pytest app/tests -q` 全绿；新增的 `test_audit_p0p1.py` 覆盖 1–10、13、14、15、19–20。
24. 前端 `npm run lint` 与 `tsc -b` 通过。

## 四、不在本阶段范围
- 拆分 `BookCourseScreens.tsx` 与 React Context 重构（P2）。
- BM25/vector 检索缓存（P2）。
- 锁定依赖版本（P3）。
- 替换 BackgroundTasks 到 Redis/Celery 等外部队列（P3）。