# 阶段 3 审核报告：Chunker V2 与内容质量控制

> 状态：通过  
> 执行日期：2026-07-14  
> 真实流水线 run：`run_20260714T043219Z`  
> Golden run：`run_20260714T042508Z`  
> 机器总审：20/20 通过，失败列表为空  
> 数据操作：未迁移或修改 pgvector，未重解析历史课程，未删除用户数据

## 1. 阶段结论

阶段 3 已完成并达到方案规定的全部 14 项审核标准，可以进入阶段 4。

本阶段将默认切片器升级为 Chunker V2，并保留显式 `v1` 回退；实现了冻结 BGE-M3 tokenizer 计数、标题路径、跨页合并、表格/公式/图片类型化切片、质量评分与隔离、稳定 ID、Asset 双向绑定及课程消费兼容。同时补齐 Assets/Chunks 一致代发布、解析和章节重建期间的 fail-closed 可见性、AI 生图来源重绑，以及外部向量结果不得绕过当前 bundle 与质量门禁的约束。

最终机器证据：

- `acceptance_evaluation.json`：20/20 检查通过。
- `real_v2_probe.json`：18/18 检查通过，5 个真实场景和 8 条冻结查询全部通过。
- `chunker_golden.json`：所有 golden checks 通过。
- `tokenizer_probe.json`：模型 revision 和依赖版本锁定检查通过。
- `test_results.json`：后端、阶段专项、前端测试、lint 和 build 全部通过。

## 2. 冻结协议与关键实现

切片协议严格使用阶段 0 冻结值：

| 项目 | 冻结值 | 最终结果 |
|---|---:|---|
| tokenizer | `BAAI/bge-m3` | 通过 |
| revision | `5617a9f61b028005a4858fdac845db406aefb181` | 通过 |
| transformers | `4.57.6` | 通过 |
| tokenizers | `0.22.2` | 通过 |
| 目标 token | 450 | 作为普通正文软目标生效 |
| 普通正文最大 token | 700 | Golden 最大值 700 |
| 普通正文最小 token | 120 | 合法短尾均记录 `short_tail` |
| overlap 上限 | 80 | 逐 chunk 校验通过 |
| 原子内容硬上限 | 900 | 表格/公式/图片全部通过 |
| quality 阈值 | 0.45 | 持久化并由所有消费者统一执行 |
| quality 公式 | `quality-v1` | 五项权重及重算通过 |

`token_count` 对最终 embedding 文本计数，包含 `heading_path`、overlap 和正文。计数失败、依赖版本不一致或锁定 tokenizer 不可用时不会使用近似计数降级。

## 3. 新旧 Chunk 对比

代表性 Golden 输入的对比结果：

| 指标 | V1 | V2 |
|---|---:|---:|
| chunk 数 | 62 | 10 |
| 数量变化 | — | 减少 83.87% |
| 默认可索引 | 62 | 9 |
| 质量隔离 | 0 | 1 |
| token 范围 | 未按冻结 tokenizer 持久化 | 14～700 |
| 精确重复数 | 未建立冻结协议 | 0 |
| overlap 重复 token 比例 | 无 | 0.054147 |
| Asset 数 / 双向 source links | 非统一代产物 | 2 / 3 |

V2 的短 chunk 来自表格/公式/图片等原子内容或无法安全合并的正文尾部；所有低于 120 token 的普通正文均有合法 `short_tail` 说明。大表格拆分后每段保留表头，数据行同时保留结构化 `table_rows` metadata 和连续可检索语义，例如 `G2 replicated`。

## 4. 真实 MinerU → Chunker V2 → RAG 验证

最新真实运行覆盖 native PDF、复杂 PDF、mixed PDF、扫描 PDF 和图片五类场景：

| 场景 | 耗时（秒） | V2 chunks | Assets |
|---|---:|---:|---:|
| native | 5.152 | 2 | 0 |
| complex | 3.856 | 2 | 1 |
| mixed | 23.658 | 3 | 1 |
| scanned | 12.334 | 2 | 1 |
| image | 8.351 | 2 | 1 |

真实产物合计 11 个 active V2 chunks、4 个 Assets：

- 所有 chunk 均为 V2，token 复算一致。
- token 总数 350，最大值 81。
- 精确重复数 0，overlap 超限数 0。
- Asset 与 Chunk 的已有关系全部双向一致；无语义 caption/OCR/analysis 的装饰性 chart 不会被伪造为 figure chunk。
- 所有 source block 的页码范围可由 `pages.json` 反查。
- 检索结果中没有非 active ID 或隔离 chunk。

冻结 8 条查询的最终指标均为 1.0：

| 指标 | 门槛 | 实测 |
|---|---:|---:|
| Recall@5 | 1.0 | 1.0 |
| 全查询引用页准确率 | 1.0 | 1.0 |
| 命中后的引用页准确率 | 1.0 | 1.0 |
| 查询结果覆盖率 | 1.0 | 1.0 |

性能仍满足阶段 0 门槛：warm CloudPath parse/chunk pipeline P95 为 21.393 秒（门槛 30 秒），GPU 峰值显存 2401 MiB，OOM 为 0。

## 5. 方案 14 项审核标准

| # | 审核标准 | 结论 | 主要证据 |
|---:|---|---|---|
| 1 | 普通正文符合目标、最大和最小 token 规则 | 通过 | Golden、短尾专项测试 |
| 2 | overlap、精确重复率和 overlap 比例符合冻结阈值 | 通过 | Golden 0 / 0.054147；逐 chunk 探针 |
| 3 | 使用冻结 tokenizer 和完整 embedding 文本计数 | 通过 | tokenizer probe、真实 token 复算 |
| 4 | 短尾、表格和公式超限行为符合规则 | 通过 | 700/900 上限及故障测试 |
| 5 | quality score 按 `quality-v1` 计算并记录版本 | 通过 | 五组件权重与重算 |
| 6 | 标题路径进入 embedding 文本和 metadata | 通过 | Golden、真实前缀检查 |
| 7 | chunk 可跨页且页码范围准确 | 通过 | Golden cross-page、source block 反查 |
| 8 | 大表格拆分后每段保留表头 | 通过 | table split golden/tests |
| 9 | 公式保留 LaTeX 和附近语义 | 通过 | formula golden/tests |
| 10 | 图片 chunk 正确绑定 Asset | 通过 | Golden 双向绑定与真实关系检查 |
| 11 | 页眉、页脚和纯页码不进入正文 | 通过 | furniture golden/tests |
| 12 | 低质量内容默认不参与检索 | 通过 | BM25、向量、融合、reranker、lesson 全链过滤 |
| 13 | 同输入和配置产生稳定 chunk ID | 通过 | 重复构建和章节顺序反转测试 |
| 14 | 课程生成可读取新 Chunk 模型 | 通过 | V2 source package 及 lesson job 端到端测试 |

## 6. 阻断项返工记录

最终独立审计曾发现并已修复以下问题：

1. 外部 dense 结果可能用精简/旧 Chunk 覆盖当前 V2 Chunk，绕过质量阈值。现在外部索引只贡献分数，结果必须与 active、可索引 artifact ID 求交，返回实体始终采用当前 bundle 的 canonical Chunk。
2. `v2.1` 等子版本及 Mapping/object config 曾可能退化为 `v2` 或错误进入 V1。现在版本统一解析、冲突明确拒绝，并把实际子版本写入 chunk ID 和 `chunk_version`。
3. 章节删除后 AI Asset 曾可能保留已删除章节引用。现在仍有效的 source chunk 会重新绑定；来源消失时设置 `chapter_id=null`、清空 source IDs，并记录 orphan metadata。
4. bundle 构建中课程摘要曾读取旧兼容镜像并显示 ready。现在 summary 与 API ensure 只依据 active bundle state；building 为 `processing`，invalid 为 `error`。
5. 新解析和章节变更曾存在旧 chunks 可见窗口。现在 parse 在 detect/router 前标记 building；章节变更按 `mark building → write chapters → reserved build` 执行。
6. AI provider 已写图但 bundle 提交失败时会留下文件。现在未提交成功的图片和缩略图会尽力清理。
7. 表格行中的分隔符会使冻结语义短语匹配失败。现在表头保留 pipe 结构，数据行提供连续检索文本并保留结构化 metadata。

## 7. 自动化测试

| 测试 | 结果 |
|---|---:|
| 后端全量 pytest | 264 passed，0 failed |
| 阶段 3 专项 pytest | 58 passed，0 failed |
| 前端 Vitest | 1 file / 4 tests passed |
| 前端 ESLint | passed |
| 前端 TypeScript + Vite build | passed，1777 modules transformed |
| Stage 3 machine acceptance | 20/20 passed |
| 真实 V2 probe | 18/18 passed |

所有命令、退出码、stdout 和耗时已写入 `test_results.json`。

## 8. 主要修改范围

业务实现主要涉及：

- `backend/app/document/chunk_protocol.py`
- `backend/app/document/chunker_v2.py`
- `backend/app/document/chunker.py`
- `backend/app/document/pipeline.py`
- `backend/app/document/rebuilder.py`
- `backend/app/services/artifact_store.py`
- `backend/app/rag/embedding.py`
- `backend/app/rag/bm25.py`
- `backend/app/rag/index_base.py`
- `backend/app/rag/retrieval.py`
- `backend/app/rag/reranker.py`
- `backend/app/rag/service.py`
- `backend/app/lessons/source_builder.py`
- `backend/app/image_generation/service.py`
- `backend/app/schemas/books.py`
- `backend/app/core/config.py`
- `backend/pyproject.toml`、`backend/.env.example`、`backend/README.md`

另新增 Chunker、协议、consumer、一致发布、并发、orphan、生图清理、课程端到端测试，以及 `quality/stage3` 的 tokenizer、golden、真实产物、测试矩阵和机器总审脚本。

## 9. 回退方式

- 设置 `BOOKCOURSE_CHUNK_VERSION=v1` 可显式回退到旧 Chunker；未知版本会拒绝，不会静默选择实现。
- 原始上传文件、MinerU 原始产物、pages 和 chapters 未因 V2 被删除。
- RAG bundle 通过 manifest 发布；building/损坏代默认 fail closed，不回退到不一致旧镜像。
- 本阶段未改变 pgvector 数据，因此数据库无需回滚。

## 10. 剩余风险与下一阶段门禁

以下内容明确留到后续阶段，不能被视为已经完成：

- 当前 `PgVectorIndex` 尚未实现按 book 原子替换、完整 V2 metadata、embedding revision/dimension/index generation 和旧向量删除；阶段 4 必须修复后才能把 pgvector 作为可信主索引。
- artifact bundle 锁和本地缓存失效目前以单 CloudPath 进程为边界；跨进程一致性由阶段 4 的数据库 CAS/index generation 处理。
- BGE-M3 本阶段只冻结并验证 tokenizer；实际 embedding 模型、revision、dimension 与查询端一致性属于阶段 4。
- DOCX/PPTX/XLSX 的真实上传、引用定位和安全边界属于阶段 5；本阶段只验证类型化 chunk 规则。
- 未执行历史课程批量重解析，也未对现有共享/生产数据库执行迁移或删除。

阶段 4 只能在明确的一次性隔离 PostgreSQL/pgvector 测试库中执行建表、迁移、替换和删除证据。对现有共享/生产数据库的任何操作仍需用户另行明确批准。

