# P2 / PostgreSQL 运维与迁移手册

本轮提供可选 PostgreSQL 主存储、独立 Worker 和停写迁移工具。没有配置 `DATABASE_URL` 或 `DATABASE_HOST` 时仍用开发 SQLite；配置了 PostgreSQL 时，连接或 schema 失败会阻止启动，不回落 SQLite。未执行用户数据迁移或生产部署。

## 存储边界与配置

权威业务存储入口为 `adaptive_learning.database.connect`。Native、Assessment、Community，以及共享 Community 存储的 Accounts、Social、Studio 都通过此入口；OCR 使用同一入口。JSON 保留为 TEXT，以保持旧 HTTP/JSON 和原始迁移摘要兼容。时间保留 Unix epoch DOUBLE PRECISION；消息/任务 ID 和聊天读游标使用 64 位整数。`native_catalog_practice.rowid` 是明确的 PostgreSQL identity 列，保留 SQLite 的事件插入顺序。

PDF、页面图、OCR attempt 目录、原生课程素材、Studio 媒体、语音、模型和 RAG 索引仍放文件系统。`flashcard_quality.sqlite3` 仅为可重建的内容质量缓存，不是权威业务存储；无需导入 PostgreSQL。没有引入 Redis、消息代理、pgvector 或向量数据库迁移。

配置示例（由 Secret 管理连接与签名值，不提交真实值）：

```sh
export DATABASE_URL='postgresql://user:URL_ENCODED_PASSWORD@host:5432/database'
export DATABASE_POOL_SIZE=8
python -m adaptive_learning.migrate schema
OCR_WORKER_ENABLED=0 STUDIO_WORKER_ENABLED=0 uvicorn adaptive_learning.api.app:app --host 0.0.0.0 --port 8100
python -m adaptive_learning.worker --queue studio
python -m adaptive_learning.worker --queue ocr
```

也可以设置 `DATABASE_HOST / DATABASE_PORT / DATABASE_NAME / DATABASE_USER / DATABASE_PASSWORD`，由 psycopg 构造 DSN，密码无需 URL 编码。非空 `DATABASE_URL` 优先；Compose 显式清空该值并使用独立连接字段，避免 `.env` 中的旧 URL 指向外部数据库。连接池每进程最多 `DATABASE_POOL_SIZE` 个连接，范围 1–64、默认 8；部署总连接数需要乘 API/Worker 进程数。连接只在请求/任务上下文存活，退出时提交或回滚并归还池；退出上下文的连接及游标拒绝继续使用。

多机副本必须挂载一致的文件产物路径和权限，并共享 `state/account-otp.key`，或所有副本配置同一个 `ACCOUNT_SIGNING_KEY`（32 字节、64 个十六进制字符）。切换时备份原签名材料；不要为每个 API 副本生成独立密钥。数据库保留 `file_path`、`output_dir`、目录内产物引用等路径；换主机/容器必须保持相同路径及挂载，或在停写演练时完成明确路径映射。只导入数据库而不提供这些文件，会导致原文和 OCR/媒体无法读取。

## Schema 与事务

选择 psycopg 3 + 有界 psycopg_pool + 小型 DB-API 边界而非重写 ORM，是为了保留已存在的 SQL、事务边界和数据结构。适配只处理绑定占位符与 Row 的下标/字段读取，不在运行时替换 SQLite SQL 方言。业务冲突语句已改为显式 `ON CONFLICT`，消息插入用 `RETURNING`；SQLite 端显式注册 `strpos` 对齐搜索函数。

Schema 由 `migrations/001…005.sql` 和 `schema_migrations` 显式升级，当前版本 5。应用启动仅检查版本；不会在 PostgreSQL 上运行开发建表逻辑。升级在一个事务中运行，并用 advisory lock 排除同时升级。`test_schema_inventory.py` 检查权威表在源代码、migration、导入清单中一一对应；新增业务表必须同时更新三处。

兼容阶段的多步骤业务写事务保留 SQLite `BEGIN IMMEDIATE` 的串行保护：PostgreSQL 用数据库级事务 advisory key `2026102201`。验证码次数、全局 Studio 预算、revision 检查、Native FSRS 与事件等关键区因此保持先读后写不变量。**该锁仍是业务写热点**；当前并发正确性优先，不能宣称所有业务写已水平扩展。未来按主体拆锁时，要对 Studio 全局预算单独锁定并定义锁顺序。

OCR/Studio 领取不拿这个兼容锁，使用短事务 `FOR UPDATE SKIP LOCKED`。OCR 修改按照 job 行再 book 行的顺序；enqueue 用独立的每书 hash advisory lock 排除第一次插入竞争。任务耗时工作、模型调用和媒体下载均在数据库事务外。

## Worker、租约与文件产物

- `--queue studio`、`--queue ocr` 或 `--queue all` 可独立运行；API 的 `OCR_WORKER_ENABLED=0`、`STUDIO_WORKER_ENABLED=0` 关闭内嵌 Worker。本地单进程仍可按原配置开启。
- OCR 任务有心跳、过期领取恢复与 `max_attempts`。旧 owner、过期 owner 不能完成任务；失败处理读取时拿行锁，避免覆盖恢复后的任务。OCR subprocess 只写不可变 attempt；持有效租约的成功事务切换 `normalized` 目录指针。旧 attempt 不覆盖新结果。
- Studio 每次领取产生新 lease token，工作中独立心跳，正常 polling 不消耗失败次数，过期恢复最多 3 次。`planning` 可重新排队；过期 `submitting` 转 `uncertain`，**不会重发未确认的付费媒体请求**。已提交视频继续查询 provider ID。
- Studio 数据库结果更新与媒体文件发布都检查 lease token。媒体先写临时文件，再持任务行锁检查租约并原子发布，防止旧执行者覆盖新产物。启动新 Worker 不会重置另一个 Worker 的有效租约。
- 独立 Worker 的真实队列线程存活时，每 5 秒刷新一次 `worker_presence`，15 秒未刷新即失效；正常退出会删掉本进程记录。API 合并本进程与外部心跳，保留原 `ocr_worker_enabled/alive` 健康字段，使现有原生上传入口在独立 OCR 可用时继续工作。Worker 停止/心跳过期后会变为不可用，不能只把字段静态设成 true。该表是非权威就绪状态，不迁移旧心跳。
- DB/连接池暂时不可用时 Worker 有界等待后重试；模型故障与数据库故障不通过仅捕获 `sqlite3.Error` 来混为一谈。

Compose 启动 PostgreSQL 17、健康检查、一次性 schema 容器、API 与 CPU Studio Worker。GPU OCR 是 `gpu` profile 中的 `ocr-worker`：需要自行构建包含后端包、MinerU、模型依赖和 CUDA 的 `OCR_WORKER_IMAGE`，挂载同一 `/app/data` 与 `/models`；基础后端镜像不假装具备 OCR/GPU 运行时。部署工具会等待 schema 完成后再启动 API/Worker。没有 GPU Worker 时上传任务保留在数据库队列，不能宣称 OCR 处理已可用。GPU、真实模型、正式 Compose 部署均未在本轮机器验证。

## OCR 质量配置

所有实际阈值都来自 Settings，但两种评分模型不能混用：通用重建管线仍使用 `DOCUMENT_QUALITY_ACCEPT=0.90`、`DOCUMENT_QUALITY_REVIEW=0.72`；当前 OCR 页级 heuristic 评分使用 `OCR_PAGE_QUALITY_ACCEPT=0.82`、`OCR_PAGE_QUALITY_REVIEW=0.66`、结构化表格/公式特殊复核下限 `OCR_PAGE_STRUCTURED_REVIEW=0.50`。这保持原有默认判定，避免把先前未影响 OCR 的 DOCUMENT 环境变量突然应用到另一个评分模型。阈值次序、范围和 Worker 正数配置在启动时校验。

## 停写迁移与切换

`import-sqlite` 只接受空目标，不提供覆盖或合并现有目标数据的开关。

1. 安排维护窗口，关闭写入口、停 API 与所有 OCR/Studio Worker，等待在途请求结束。确认不会有另一台副本继续写入。工具要求 `--writes-stopped` 明确确认此前提；它不能替代运维停写。
2. 备份整个 APP_DATA_DIR、签名材料、PDF/OCR/媒体/模型及索引，保留原路径映射。工具随后用 SQLite backup API 为每个权威库创建一致快照，但跨库一致性依赖前一步停写。
3. 准备独立空 PostgreSQL 数据库，并确认连接目标。先做隔离演练，不指向正式库。
4. 执行：

```sh
DATABASE_URL='postgresql://…/empty_target' \
  python -m adaptive_learning.migrate import-sqlite \
  --data-dir /path/to/app-data \
  --backup-dir /path/to/new-private-backup-directory \
  --writes-stopped
```

5. 工具在同一 PostgreSQL 事务内升级 schema、拒绝目标仍有活 Worker 心跳，确认所有权威目标表为空、按依赖顺序导入。它拒绝未映射源表；对已知 JSON 列检查可解析性；逐表比较计数与规范化内容 SHA-256；由真实外键检查关联完整性。不会把普通教材正文、聊天文字误当 JSON。最后事务式重置 identity sequence，避免导入后新 ID 冲突。任何导入、JSON、约束、摘要校验失败都会回滚该事务，保留 SQLite 原文件与备份。
6. 查看备份目录中的 `report.json`：每表条数、摘要、备份清单及 `verified`。报告不包含凭证、原始记录、验证码或聊天正文。备份目录权限 0700，数据库快照权限 0600。
7. 对迁移目标验证登录、账户隔离、课程/页码、笔记、FSRS/学习事件、聊天和任务状态，并执行后续新写入。保留原数据，不删除旧库。
8. 在维护窗口内切 API 与 Worker 连接，保留相同文件路径与签名材料，启动 schema 检查、API 和 Worker，健康检查后恢复流量。

在 PostgreSQL **尚未接受新写入**时，可以停服务并恢复旧 SQLite 配置。如果 PostgreSQL 已接受新写入，不能直接切回旧 SQLite；需要迁回增量或修复前进。当前工具不提供反向增量合并。磁盘、数据库连接在提交阶段中断时应先检查目标及备份报告确认结果，不要盲目重复导入；空目标保护会拒绝覆盖已提交数据。

## 可重复测试

在 `backend/` 使用 `.venv/bin/python -m pip install -e '.[dev,pdf]'` 后：

```sh
.venv/bin/pytest
.venv/bin/ruff check src
.venv/bin/mypy src/adaptive_learning
TEST_DATABASE_URL='postgresql://…/disposable_test_db' .venv/bin/pytest tests/test_postgres.py tests/test_postgres_review.py
TEST_DATABASE_URL='postgresql://…/disposable_test_db' PYTHONPATH=tests \
  .venv/bin/pytest -p pg_contracts tests/test_accounts.py tests/test_accounts_email.py \
  tests/test_social.py tests/test_community.py tests/test_native_api.py tests/test_studio.py \
  tests/test_learning_design.py tests/test_assessment_repository.py tests/test_ocr_jobs.py
```

PG 专测与合同插件只在指定测试数据库中新建随机 schema，结束时删除该 schema，不 TRUNCATE public 数据。环境中的 SOCKS 代理需要 httpx 的可选 socks 支持；只跑本地测试时可清除 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 及其小写形式。外部 OCR 原始 `artifacts/ocr/biology-20260829/pages.jsonl` 缺失时，其专属回归明确 skip，其他合成章节 fixture 仍运行。

原生端在仓库根运行 `frontend/scripts/test-cloud.sh --unit`、`frontend/scripts/test-cloud.sh --ui`。补测本地默认账号模式时，unit 和 UI 应分别启动新的 test-cloud.sh fixture 服务：本地账号是共享的持久身份，把两种测试混在同一服务中会让前一测试完成的引导状态影响后一测试。首次构建需要先解析 Xcode Swift Package；CI 已包含此步骤。SQLite 默认测试、真实 PG 专测、已有 API 合同复用、原生测试在 `.github/workflows/test.yml` 中有可复用命令。
