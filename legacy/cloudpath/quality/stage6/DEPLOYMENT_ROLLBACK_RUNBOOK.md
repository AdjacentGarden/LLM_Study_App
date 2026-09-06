# CloudPath MinerU/RAG 部署与回退手册

> 状态：发布准备完成，尚未执行生产发布  
> 适用版本：阶段 0～6 实现  
> 生产数据库 migration、生产配置切换和历史批量重解析：均需各自单独批准

## 1. 发布门禁

发布操作开始前必须同时满足：

- `quality/stage6/acceptance_evaluation.json` 全部通过。
- 保存当前应用配置、前后端构建产物、数据库 schema/数据备份和原始文件存储快照。
- 明确生产 PostgreSQL/pgvector 目标身份；禁止使用阶段 4 的 `127.0.0.1:55432/cloudpath_stage4` 测试库。
- 数据库 migration 已单独获批并先在生产快照演练；应用进程不会自动执行 migration。
- 已确认 MinerU 与 CloudPath 后端位于受信任网络；当前实现只允许 loopback MinerU endpoint。
- 历史批量重解析保持关闭，除非另有独立批准。

## 2. 生产关键配置

生产配置必须显式设置并纳入配置审计，不能依赖开发默认值：

```env
BOOKCOURSE_PARSER_PROVIDER=mineru
BOOKCOURSE_MINERU_ENDPOINT=http://127.0.0.1:8001
BOOKCOURSE_MINERU_BACKEND=pipeline
BOOKCOURSE_MINERU_PARSE_METHOD=auto
BOOKCOURSE_MINERU_TIMEOUT_SECONDS=900
BOOKCOURSE_MINERU_CONNECT_TIMEOUT_SECONDS=10
BOOKCOURSE_MINERU_MAX_RETRIES=2
BOOKCOURSE_OCR_PROVIDER=paddleocr
BOOKCOURSE_OCR_LANGUAGE=ch
BOOKCOURSE_OCR_PROCESS_ISOLATION=true
BOOKCOURSE_CHUNK_VERSION=v2
BOOKCOURSE_EMBEDDING_PROVIDER=bge_m3
BOOKCOURSE_EMBEDDING_DEVICE=cuda:0
BOOKCOURSE_EMBEDDING_DIMENSIONS=1024
BOOKCOURSE_RAG_INDEX_PROVIDER=pgvector
BOOKCOURSE_DATABASE_URL=<approved production DSN>
```

其余请求、上传、PDF/图片、ZIP/OOXML 和 Office 资源上限采用 `backend/.env.example` 中已审核值。密钥和完整 DSN 不得写入普通日志或审核附件。

## 3. 启动顺序

1. **PostgreSQL/pgvector**：确认备份完成；在单独批准后执行 `backend/migrations/001_pgvector_v2.sql`；验证 PostgreSQL、pgvector extension、两张 RAG 表和索引。
2. **MinerU**：启动 `127.0.0.1:8001` 服务；确认 CUDA 可见、协议版本 2、`max_concurrent_requests=1`。
3. **CloudPath 后端**：加载显式生产环境变量后启动 API/worker；启动过程不得自动迁移数据库。
4. **前端**：发布与后端同一版本的 accept/提示契约，避免白名单漂移。
5. **Canary**：先用隔离 canary 课程依次验证原生 PDF、扫描 PDF、图片、DOCX、PPTX、XLSX，再逐步开放新上传。

## 4. 健康检查

MinerU：

```text
GET http://127.0.0.1:8001/health
```

必须满足 `status=healthy`、`protocol_version=2`、`max_concurrent_requests=1`；记录 `queued_tasks`、`processing_tasks`、`failed_tasks`、任务 TTL 和 GPU 指标。

CloudPath：

```text
GET http://127.0.0.1:8000/api/health
```

必须返回 `status=ok`、`service=bookcourse-ai-backend`。该接口只证明 API 存活，不替代 MinerU、数据库和索引 readiness 检查。

数据库/索引检查：

- 只读确认 pgvector extension 版本和 migration 表结构。
- 用 canary 课程解析后确认 `rag_index_status=ready`、generation 单调、chunk 数与向量行数一致。
- 对 canary 执行 RAG 查询，核对 PDF 页、PPTX 幻灯片、XLSX 工作表/区域和 DOCX 块标签。
- 检查错误日志不含文件正文、完整本地路径、上游错误 body、API key 或 DSN。

## 5. 发布观察窗口

Canary 阶段至少观察一个完整解析/查询周期。任一条件出现即停止扩大流量：

- GPU OOM 大于 0。
- MinerU 健康失败、协议不匹配或队列持续增长。
- parse/index job 错误地报告成功、向量数不一致或 stale generation 可见。
- warm pipeline P95 超过 30 秒，或真实 MinerU fixture P95 超过 10 秒且连续两个窗口不恢复。
- Recall@5、引用准确率或查询覆盖率低于冻结值 1.0。
- 新格式出现错误的 PDF-only 预览或 DOCX 虚构页码。

## 6. 路由级快速回退

路由回退不删除原始文件、产物或向量：

1. 暂停接收新的解析任务，等待正在提交 generation 的 worker 到达安全状态。
2. 前端关闭 DOCX/PPTX/XLSX 及阶段 5 新增图片入口；后端白名单必须随同一发布单元回退。
3. 设置 `BOOKCOURSE_PARSER_PROVIDER=pymupdf` 并重启后端。PDF 优先使用 PyMuPDF，无法获得语义的扫描 PDF/图片再进入 PaddleOCR。
4. Office 在该路由下没有兼容解析器，必须明确不可新建/重解析，不能送入 PDF renderer 或伪造成功。
5. 保持 `BOOKCOURSE_CHUNK_VERSION=v2` 和当前 index generation；解析器回退不应同时制造新旧 chunk/向量混用。
6. 使用原生 PDF、扫描 PDF 和图片 canary 验证降级及引用，然后再恢复允许的旧格式流量。

恢复 MinerU 时创建新的 parse generation；迟到旧任务会被 generation guard 拒绝，不能覆盖新结果。

## 7. 完整版本回退

若必须回退应用二进制：

1. 先停止新写入并保存当前配置、任务状态和数据库快照。
2. 部署上一已知良好前后端版本；前后端文件白名单作为一个整体回退。
3. 不对 additive pgvector schema 执行破坏性 down migration。若旧版本确实与 schema 不兼容，只能在单独数据操作批准后从已验证备份恢复。
4. 保留所有原始上传、MinerU 原始返回和 generation artifacts。新格式课程可暂时只读/不可解析，但不得删除或让旧版本错误渲染。
5. 通过旧版本 canary 后恢复流量，并记录受影响 generation，供后续前向修复。

## 8. 数据与索引回退原则

- 单本课程回退使用 generation 与 build ID 发布门禁，不直接覆盖当前 ready generation。
- cache failure 使用同 generation retry；数据库写入失败不得让任务报告完成。
- 删除/重建向量、恢复数据库备份或批量重解析均属于独立数据操作，必须另行批准。
- 原文件是最终恢复源；任何回退步骤都不得修改或删除原文件。

本手册只形成可执行说明。本轮没有执行生产配置切换、生产 migration、数据恢复或历史重解析。
