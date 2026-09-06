# CloudPath MinerU/RAG 监控指标清单

> 状态：发布监控设计完成；尚未接入生产告警系统

## 1. MinerU 与 GPU

- `/health`：status、version、protocol_version；协议必须为 2。
- queued/processing/completed/failed tasks；并发上限必须保持 1。
- 提交、排队、处理、取结果各阶段耗时和总耗时。
- 429、5xx、连接失败、轮询超时、结果 404/损坏/超限的计数与比例。
- GPU memory used/total、utilization、OOM 次数；OOM 冻结上限为 0。
- 任务 TTL 86400 秒、cleanup interval 300 秒及过期任务数量。
- CloudPath 停止等待后仍在上游运行的任务数量；当前 MinerU API 没有可依赖的取消能力。

## 2. 解析质量与降级

- 各格式上传量、验证成功率和拒绝错误码分布。
- valid fixture/task success rate，冻结下限 1.0。
- source page coverage 与 usable semantic page coverage，冻结下限均为 1.0。
- document quality score 下限 0.75、page quality score 下限 0.60。
- missing page count 上限 0、OCR pending ratio 上限 0、invalid character ratio 上限 0.01。
- MinerU→PyMuPDF、MinerU→PaddleOCR 的降级率、原因和最终 parser；Office 不允许进入这些降级。
- 动画 GIF、多页 TIFF、加密/损坏/超限/OOXML 安全拒绝计数。

## 3. Chunk、索引与数据库一致性

- active chunk 数、可索引/隔离 chunk 数、空 chunk、短尾、精确重复和 overlap 比例。
- 普通 chunk 最大 700 tokens、原子内容硬上限 900、quality threshold 0.45。
- index generation 状态：building、committed、ready、failed、cache_failed、deleted。
- artifact build ID 与数据库 generation 是否一致；stale worker/CAS 拒绝计数。
- input chunks、canonical texts、embeddings、indexed rows 四类数量差异。
- embedding provider/model/revision/dimension/device；生产必须为审核过的 BGE-M3 revision、1024 维、CUDA。
- 数据库连接/事务/回滚/删除失败；任何失败不得伴随业务 success。
- BM25、pgvector、artifact_fallback 使用比例和明确 fallback_reason。

## 4. RAG 质量

- 冻结查询集 Recall@5，门槛 1.0。
- 全查询引用准确率、命中后引用准确率、查询覆盖率，门槛均为 1.0。
- PDF 页、PPTX 幻灯片、XLSX 工作表/区域、DOCX 标题路径/块序号分别统计引用正确率。
- 检索结果中 inactive、quarantined、旧 generation chunk 的数量，必须为 0。
- 周期性离线回归使用冻结 fixture/query 版本；不得在看到结果后修改门槛。

## 5. 性能门槛

| 指标 | 冻结门槛 |
|---|---:|
| warm MinerU fixture P95 | ≤ 10 秒 |
| cold pipeline | ≤ 180 秒 |
| warm parse/chunk/index P95 | ≤ 30 秒 |
| GPU 峰值显存 | ≤ 14500 MiB |
| GPU OOM | 0 |
| progress update interval | ≤ 5 秒 |

队列深度没有阶段 0 冻结的数值门槛；运营初始告警建议为 `queued_tasks>0` 持续 10 分钟或持续增长，调整该建议不等同于调整质量验收门槛。

## 6. 告警等级

- **P0**：OOM、向量/代数据混合、stale generation 可见、原文件丢失、数据库失败却报告成功、敏感正文/凭据泄漏。
- **P1**：MinerU 连续两次健康失败、冻结性能门槛连续两个窗口超限、解析/检索冻结指标低于门槛、队列持续增长。
- **P2**：单个输入安全拒绝激增、Office preview fallback 异常、单格式失败率上升、artifact fallback 比例异常。

## 7. 发布与日常检查

- 发布前：运行 stage6 machine acceptance、全量测试、MinerU health/GPU probe 和 canary 六格式查询。
- 发布后：确认 30 分钟内无 P0/P1，逐步扩大流量。
- 每日：失败任务、fallback、queue、GPU、cache_failed、DB errors、上传拒绝码。
- 每周：冻结查询集、引用语义、重复率、chunk/token 分布和容量趋势。
- 每次配置/模型变更：记录模型 revision、门槛、前后结果和回退点。

日志必须只记录 task ID、generation、错误码和脱敏摘要；不得记录完整正文、用户文件内容、敏感路径、完整上游 body、API key 或数据库凭据。
