# 阶段 4 审核报告：Embedding 与向量索引一致性

> 状态：通过  
> 执行日期：2026-07-14  
> 机器总审：13/13 通过，失败列表为空  
> 隔离库全量：296 passed  
> 数据操作：仅迁移并清理本机 loopback 隔离库；未接触共享/生产数据库，未批量重解析历史课程

## 1. 阶段结论

阶段 4 已完成方案规定的全部审核标准，可以进入阶段 5。

`rag_indexing` 已从展示用阶段变为真实发布门禁：解析、章节重建与 AI 图片回链会先绑定 artifact `build_id` 和单调 `index_generation`，生成兼容 embedding，再按书原子替换 pgvector；缓存屏障完成后才把 generation 标记为 `ready`。数据库、embedding、数量或缓存失败都会阻止任务报告成功。

普通应用请求不会自动执行数据库迁移。当前迁移和所有删除测试只在以下已声明隔离目标执行：

- Host：`127.0.0.1/32`
- Port：`55432`
- Database/User：`cloudpath_stage4`
- PostgreSQL：`16.14`
- pgvector：`0.8.3`
- Data directory：`%TEMP%\cloudpath-stage4-pgdata-20260714`

测试结束后，`rag_index_state=0`、`rag_chunk_vectors=0`。

## 2. 核心实现

### 2.1 Generation 与发布门禁

新实现使用以下状态机：

`building → committed → ready`

异常分支为 `failed`、`cache_failed` 和 `deleted`：

- `reserve_generation` 为同一本书原子递增 generation，并绑定唯一 `build_id`。
- `replace_generation` 在一个事务中删除旧向量、写入新向量、校验行数，并停在不可检索的 `committed`。
- 只有 book 级缓存全部失效后，`finalize_ready` 才允许查询读取该 generation。
- 缓存失败会持久化为 `cache_failed`；重试使用同一 generation，不重新 embedding。
- 删除会清空向量并保留单调 `deleted` tombstone，迟到旧 worker 的 CAS 提交被拒绝。

Artifact 侧同时增加跨进程短锁、build ID 校验和章节文件同代写入，避免“新章节 + 旧 chunks”或迟到文件发布覆盖新索引。删除课程前会写删除 sentinel、失效当前 MinerU generation，再删除向量；数据库删除失败时本地课程目录保留且 API 不返回成功。

### 2.2 完整 Chunk V2 与数量一致性

pgvector 新表保存完整 Chunk V2 字段，包括：

- parser / parser_version / chunk_version
- heading_path / source_block_ids
- quality_score / token_count / content_hash
- bbox / metadata / asset_ids / key_concepts
- book_id / chapter_id / page range / content_type / text

协调器只为 `is_chunk_indexable=True` 的内容生成 canonical embedding 文本。chunks、文本、embeddings 和事务提交行数均严格相等；维度必须为 1024，重复 ID、跨 book、旧 chunk version、NaN/Inf 和数量不匹配都会在 ready 前失败。

### 2.3 Embedding 契约与 CUDA

应用级 BGE-M3 探针结果：

| 项目 | 结果 |
|---|---|
| provider | `bge_m3` |
| model | `BAAI/bge-m3` |
| revision | `5617a9f61b028005a4858fdac845db406aefb181` |
| dimension | 1024 |
| device | `cuda:0` |
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER |
| PyTorch | `2.13.0+cu130` |
| 输出 | `2 × 1024`，两条范数均为 `1.0` |
| warm probe | 9.672 秒 |
| CUDA 显存峰值 | 2175.5 MiB |

模型缓存位于 `D:\code\MinerU\.cache\huggingface`。Hashing 模式仍可用于无模型测试，但会保存自身 `sha1-feature-hashing/algorithm-v1` 描述，绝不伪装成 BGE。

### 2.4 查询与 fallback

- BM25 与 artifact embedding cache 均按 `index_generation` 隔离；artifact embedding 额外按 embedding descriptor 隔离。
- 查询 embedding descriptor 必须与数据库 generation 的 model/revision/dimension 兼容。
- pgvector 合法空结果不会触发 artifact fallback；BM25 可以独立返回结果，同时 provider 仍真实记录为 pgvector。
- 只有 DSN 缺失、provider 不可用、状态未 ready、descriptor 不兼容或查询异常时才使用 `artifact_fallback`，并保存 `fallback_reason`。
- `GET /api/books/{book_id}/rag-index-status` 暴露无敏感信息的索引状态；`POST /api/books/{book_id}/rag-index/retry` 可重试 committed/cache_failed generation。

## 3. 审核标准逐项结果

| # | 审核标准 | 结果 | 主要证据 |
|---:|---|---|---|
| 1 | 解析完成即在 pgvector 可检索 | 通过 | pipeline hook；真实业务索引测试 |
| 2 | 可索引 chunk 数与数据库数一致 | 通过 | 事务内 count 校验；quarantine 排除测试 |
| 3 | 重解析无旧 ID/embedding | 通过 | 原子替换与直接 SQL 断言 |
| 4 | 章节调整同步更新 | 通过 | chapter rebuild generation 与 chapter_id SQL 断言 |
| 5 | 删除书籍清空向量 | 通过 | tombstone、vector count=0、迟到任务拒绝 |
| 6 | DB 写入失败不报告成功 | 通过 | coordinator 故障注入；parse job 状态测试 |
| 7 | 事务失败不混合新旧数据 | 通过 | insert 后抛错，事务回滚且旧行保持完整 |
| 8 | 迟到旧任务不能覆盖 | 通过 | 两连接 generation CAS；artifact build ID stale 拒绝 |
| 9 | embedding 可兼容、可审计 | 通过 | 冻结 revision、1024 维、CUDA 实机探针 |
| 10 | cache failure 未就绪且可重试 | 通过 | committed→cache_failed→同 generation ready |
| 11 | 数据操作仅限批准隔离库 | 通过 | loopback DB 身份断言；共享/生产 touched=false |
| 12 | Artifact fallback 显式 | 通过 | provider=`artifact_fallback` + reason；审计字段 |
| 13 | BM25 与向量均返回新 chunks | 通过 | 新 chunk 的 bm25_score、dense_score 均大于 0 |

机器可读结果见 `acceptance_evaluation.json`；`evaluate_acceptance.py` 输出 13/13 passed。

## 4. 测试结果

- 后端默认环境：287 passed，9 个真实隔离库测试按门禁跳过。
- 后端显式隔离库：296 passed，0 failed。
- pgvector 核心真实库：7 passed。
- 业务 parse/reparse/chapter/delete/cache-retry：2 passed。
- embedding/query/cache 专项：10 passed。
- coordinator 专项：8 passed。
- parse pipeline hook：1 passed。
- 前端：4 passed；lint passed；build passed。

## 5. 交付物

- `backend/app/rag/index_pgvector.py`：generation-aware pgvector 核心。
- `backend/app/rag/indexing.py`：业务协调器、状态、失败与重试门禁。
- `backend/migrations/001_pgvector_v2.sql`：幂等 schema/HNSW migration。
- `backend/app/rag/embedding.py`：冻结 BGE descriptor 与严格维度验证。
- `backend/app/rag/index_factory.py`、`retrieval.py`、`cache.py`：真实 provider、generation cache 与显式 fallback。
- `backend/app/document/pipeline.py`、`rebuilder.py`：解析和章节重建接入。
- `backend/app/image_generation/service.py`：AI Asset 回链后同步索引。
- `backend/app/api/routes_books.py`：删除门禁、状态与 retry API。
- `quality/stage4/*.json`：环境、测试和逐项验收证据。

## 6. 未授权且未执行的操作

以下操作仍需用户额外明确批准，本阶段没有执行：

- 对现有共享或生产 PostgreSQL 执行 migration。
- 删除、替换或批量修改现有用户向量。
- 对历史课程执行批量重解析/重建索引。
- 将阶段 5 的扩展上传白名单部署到生产。

## 7. 阶段判定

阶段 4：**通过**。可以按既定方案进入阶段 5“上传格式扩展与端到端兼容”。
