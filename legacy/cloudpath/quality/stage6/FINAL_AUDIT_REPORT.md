# 阶段 6 最终审核报告：集成验收、迁移决策与发布准备

> 状态：通过  
> 执行日期：2026-07-14  
> 机器总审：10/10 通过，失败列表为空  
> 全量测试：后端 346 passed / 0 failed，前端 7 passed，lint/build passed  
> 故障矩阵：72/72 passed  
> 数据与发布操作：未切换生产配置、未迁移共享/生产数据库、未删除历史数据、未批量重解析历史课程

## 1. 最终结论

阶段 6 已完成并达到方案规定的全部 10 项审核标准。结合阶段 0～5 的独立审核证据，本方案的代码实现、自动化回归、真实解析、Chunker V2、索引一致性、RAG 质量、扩展格式、安全边界、部署说明、监控说明和回退说明均已完成。

当前版本达到“可进入受控生产发布审批”的状态，不表示本轮已经发布到生产。以下操作仍然保持独立门禁：

- 生产/共享 PostgreSQL 的 migration。
- 生产配置与流量切换。
- 历史课程批量重解析或索引替换。
- 删除历史数据或旧 generation。

## 2. 新旧质量对比

阶段 0 旧基线为 `auto + 无 MinerU endpoint + mock OCR + hashing`；它的解析耗时很低，但扫描件/图片没有真实语义，因此速度不能与真实模型流水线直接比较。可比较的冻结查询质量如下：

| 指标 | 旧基线 | 新方案 | 变化 | 冻结门槛 |
|---|---:|---:|---:|---:|
| Recall@5 | 0.625 | 1.0 | +0.375 | 1.0 |
| 全查询引用准确率 | 0.625 | 1.0 | +0.375 | 1.0 |
| 命中后引用准确率 | 1.0 | 1.0 | 0 | 1.0 |
| 查询结果覆盖率 | 0.625 | 1.0 | +0.375 | 1.0 |
| 五场景 chunk 数 | 23 | 11 | -52.17% | 按质量协议，无数量硬门槛 |

新方案的 8 条冻结查询全部命中并正确引用；13 种目标格式真实解析/Chunk V2/索引/RAG 查询成功率为 1.0。Office 引用分别验证为：

- DOCX：`课程：整本文档导读 > 1. Cell membrane · 块 3`。
- PPTX：`幻灯片 2：ATP production`。
- XLSX：`工作表 Growth Data（A1:D5）`。

## 3. 性能与 GPU

| 指标 | 实测 | 冻结门槛 | 结果 |
|---|---:|---:|---|
| warm MinerU fixture P95 | 2.106 秒 | ≤ 10 秒 | 通过 |
| cold pipeline | 23.658 秒 | ≤ 180 秒 | 通过 |
| warm parse/chunk/index P95 | 21.393 秒 | ≤ 30 秒 | 通过 |
| 13 格式 pipeline P95 | 9.358 秒 | ≤ 30 秒 | 通过 |
| GPU 峰值显存 | 2401 MiB | ≤ 14500 MiB | 通过 |
| GPU OOM | 0 | 0 | 通过 |

最终运行态探针显示 MinerU 3.4.4、协议 2、queued=0、processing=0、failed=0、并发上限 1、任务 TTL 86400 秒。队列等待没有阶段 0 冻结数值门槛；监控清单已定义持续积压告警，不以此修改冻结验收标准。

## 4. 故障、降级与数据一致性

独立故障矩阵 72/72 通过，覆盖：

- MinerU health 5xx/4xx、连接失败、网络超时、总 deadline、429/Retry-After、任务 failed、结果 404/损坏/超限。
- 提交不确定、重启恢复轮询、迟到结果、旧 generation、并发 claim、TTL/timeout 终态。
- MinerU 离线时原生 PDF 使用 PyMuPDF，扫描 PDF/图片使用 PaddleOCR。
- Office MinerU 失败时明确失败，不进入 PyMuPDF、OCR 或 PDF-only renderer。
- pgvector/embedding/cache/删除/发布失败不会报告业务成功，stale generation 不能覆盖当前结果。

默认测试中的 9 个 skip 是 7 个真实 pgvector 原子性测试和 2 个业务生命周期测试，它们要求显式的 Stage-4 隔离库。阶段 4 已在 `127.0.0.1:55432/cloudpath_stage4` 一次性隔离库以 296 passed、0 failed、0 skipped 验证；测试后状态表和向量表均为 0 行，且明确记录未接触共享/生产数据库。因此这 9 项属于有独立通过证据的环境条件测试，不是未解释失败。

## 5. 最终自动化结果

| 测试 | 结果 |
|---|---:|
| 后端全量 pytest | 346 passed，0 failed，9 conditional skips |
| 阶段 6 故障矩阵 | 72 passed，0 failed |
| 前端 Vitest | 7 passed |
| 前端 ESLint | passed |
| 前端 TypeScript + Vite build | passed |
| Stage 5 格式验收 | 19/19 passed |
| Stage 6 machine acceptance | 10/10 passed |
| 真实目标格式流水线 | 13/13 passed |

命令、stdout、退出码、skip 原因和耗时均保存在 `quality/stage6/test_results.json`。

## 6. 阶段 6 十项审核标准

| # | 审核标准 | 结果 | 主要证据 |
|---:|---|---|---|
| 1 | 自动化测试通过或例外明确 | 通过 | 346/0；9 项隔离库 296/296 独立证据 |
| 2 | 目标格式成功率达到冻结门槛 | 通过 | 13/13，1.0 ≥ 1.0 |
| 3 | 引用定位到页/幻灯片/工作表 | 通过 | PDF query set + 三类 Office 真实引用 |
| 4 | Recall@5 与引用指标达到门槛 | 通过 | 四项均 1.0 |
| 5 | 耗时、队列/并发和 OOM 达标 | 通过 | 全部性能门槛通过；OOM 0 |
| 6 | MinerU 不可用时降级符合规则 | 通过 | 72 项故障矩阵；真实 fallback probe |
| 7 | 配置、启动顺序和健康检查明确 | 通过 | deployment runbook + monitoring checklist |
| 8 | 可回退且不破坏原文件 | 通过 | pymupdf/PaddleOCR 路由回退；generation 门禁 |
| 9 | 历史批量重解析未自动执行 | 通过 | inventory decision=`not_executed` |
| 10 | 剩余风险和优化项已列明 | 通过 | `REMAINING_RISKS.md` |

机器逐项结果见 `acceptance_evaluation.json`。

## 7. 发布、监控和回退交付

- `DEPLOYMENT_ROLLBACK_RUNBOOK.md`：生产门禁、显式配置、启动顺序、健康检查、canary、路由/版本/数据回退。
- `MONITORING_CHECKLIST.md`：MinerU/GPU、解析质量、Chunk/索引、RAG、性能和告警等级。
- `HISTORICAL_DATA_MIGRATION_DECISION.md`：只读盘点规则、优先级、影响评估、批次门禁和独立审批模板。
- `historical_reparse_inventory.json`：本地 0 条、生产未访问、`not_executed`；不得推断生产清单为空。
- `REMAINING_RISKS.md`：外部授权、技术边界、证据组合边界和后续优化。

## 8. 剩余风险摘要

- Office 文本 SVG fallback 不承诺高保真版式。
- MinerU 没有当前可依赖的任务取消 API；CloudPath timeout 只能停止等待并阻止迟到结果发布。
- 并发上限 1 控制显存但限制高峰吞吐，需要生产队列监控。
- 13 格式真实 E2E 使用显式 artifact fallback；真实 BGE-M3/pgvector 已在阶段 4 隔离库独立通过，生产 canary 仍需验证两者组合。
- 生产环境必须显式设置 BGE-M3/CUDA，不能把开发 hashing 默认值误用为生产配置。
- 生产历史课程数量未知，必须先获得只读快照再作批量决策。

这些风险已具有对应门禁和回退措施，不构成当前实现验收失败，但是真实生产发布前的必查项。

## 9. 最终判定

阶段 6 判定为 **通过**。阶段 0～6 的实现和独立审核均已完成，任务清单中的代码与发布准备工作可以标记完成。

本判定不授权也不声称已经执行生产发布、共享/生产数据库 migration、历史批量重解析或历史数据删除；这些操作继续保持独立审批状态。
