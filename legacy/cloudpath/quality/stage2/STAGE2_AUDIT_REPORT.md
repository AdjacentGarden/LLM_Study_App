# 阶段 2 审核报告：MinerU 主路由、结构映射与页面级降级

> 状态：通过（34/34 项机器验收通过）  
> 执行日期：2026-07-14  
> 实施范围：阶段 2  
> 真实 MinerU：3.4.4 / protocol 2 / CUDA  
> 下一步：依据用户后续授权，阶段审核通过后自动进入阶段 3  
> 数据操作：未迁移共享/生产数据库，未批量重解析历史课件

## 1. 结论

阶段 2 已通过审核。CloudPath 当前默认把已开放的 PDF 和图片提交给本地 MinerU HTTP 服务；对缺页或低质量页按文件类型执行页面级降级，并在一次统一发布中生成可追溯的 Page、Block、Asset 和 parser report。

已验证的实际路由为：

```text
PDF：MinerU → 仅问题页 PyMuPDF → 仍不可用页 PaddleOCR
图片：MinerU → PaddleOCR
Marker：仅显式选择，不进入默认链
```

真实 CUDA MinerU、真实 CPU PaddleOCR、MinerU 离线故障、混合 PDF、扫描 PDF、图片、结构化表格和冻结检索查询均已执行。机器验收读取阶段 0 冻结阈值，34 项检查全部通过，未发现为了通过审核而放宽阈值的情况。

## 2. 本阶段完成内容

### 2.1 MinerU 输出映射

- 将 MinerU 3.4.4 的 `middle_json`、`content_list` 和 base64 图片映射为 CloudPath 规范模型。
- 支持 `title`、正文、表格、公式、图片等结构类型；统一规范 bbox、页码、source block ID、content hash 和 Asset 关联。
- 保留原始 `mineru_middle.json`、`mineru_content_list.json`，同时生成 `pages.json`、`text_blocks.jsonl`、`layout_regions.jsonl` 和 `assets.json`。
- 表格保留原始 HTML 元数据，同时输出可检索的纯文本行。

### 2.2 主路由与质量门禁

- 默认 `BOOKCOURSE_PARSER_PROVIDER=mineru`，`auto` 使用相同的 MinerU-first 策略。
- MinerU 合格页保持不变；只对缺失或低质量 PDF 页调用 PyMuPDF/PaddleOCR，避免降级结果覆盖好页。
- mixed PDF 使用全页文本层检测，不再只采样前五页或用单页结论代表整本。
- 页面质量下限为 `0.60`，文档平均质量下限为 `0.75`；缺页、待 OCR 页或质量不达标时停止切片。
- Mock OCR 只产生 `ocr_pending` 诊断块，页面正文为空、质量门失败，并被 Chunk/RAG 消费端排除。

### 2.3 HTTP、任务与并发安全

- parser router 已接入阶段 1 的 MinerU protocol-v2 HTTP client。
- API 在任务入队前预留 generation，并绑定 CloudPath job；状态恢复沿用已持久化的 MinerU task ID。
- mapper、迟到结果处理和每个产物发布边界都会检查 generation；旧 generation 不会进入降级或覆盖新结果。
- 规范化产物通过同进程 compare-and-publish 锁提交，parser report 记录所有尝试、范围、耗时、状态、错误类别、最终解析器和页面决定。

### 2.4 PaddleOCR 响应性修正

- 默认 OCR provider 改为真实 `paddleocr`，依赖锁定为 PaddleOCR 3.7.0 / PaddlePaddle 3.3.1。
- Windows 下 `enable_mkldnn=false` 为已验证安全默认值；PaddleOCR 默认使用 CPU，使 MinerU 保持 CUDA 独占。
- Paddle 模型与推理放入一个常驻隔离子进程。原生 Paddle 推理不会再阻塞 API 主进程的进度心跳和 generation 检查。
- 最新真实流水线的最大进度更新间隔为 `4.012 s`，满足冻结的 `≤ 5 s` 门槛。

## 3. 主要修改文件

### 3.1 生产代码

```text
backend/app/api/routes_books.py
backend/app/core/config.py
backend/app/document/detector.py
backend/app/document/chunker.py
backend/app/document/ocr.py
backend/app/document/ocr_pipeline.py
backend/app/document/page_artifacts.py
backend/app/document/parser_report.py
backend/app/document/pipeline.py
backend/app/document/mineru/client.py
backend/app/document/mineru/mapper.py
backend/app/document/mineru/quality.py
backend/app/document/mineru/task_store.py
backend/app/document/parsers/base.py
backend/app/document/parsers/mineru_parser.py
backend/app/document/parsers/ocr_parser.py
backend/app/document/parsers/pdf_extractor.py
backend/app/document/parsers/pymupdf_parser.py
backend/app/document/parsers/router.py
backend/app/rag/embedding.py
backend/app/rag/tokenizer.py
backend/app/schemas/books.py
backend/app/services/job_store.py
backend/app/services/kv_store.py
backend/pyproject.toml
```

### 3.2 配置与文档

```text
backend/.env.example
backend/README.md
MINERU_RAG_IMPROVEMENT_PLAN.md
```

### 3.3 测试与审核产物

```text
backend/app/tests/test_mineru_mapper.py
backend/app/tests/test_mineru_quality.py
backend/app/tests/test_parser_router_stage2.py
backend/app/tests/test_parser_report_stage2.py
backend/app/tests/test_parse_generation_binding.py
backend/app/tests/test_phase5_ocr.py
quality/stage2/run_paddle_smoke.py
quality/stage2/run_real_pipeline.py
quality/stage2/run_fallback_probe.py
quality/stage2/run_retrieval_probe.py
quality/stage2/evaluate_acceptance.py
quality/stage2/paddle_smoke.json
quality/stage2/real_pipeline_probe.json
quality/stage2/fallback_probe.json
quality/stage2/retrieval_probe.json
quality/stage2/acceptance_evaluation.json
```

## 4. 自动化测试结果

| 检查 | 结果 |
|---|---:|
| 阶段 2 mapper/quality/router/report/generation/OCR 定向测试 | 44 passed |
| 后端全量测试 | 206 passed，0 failed |
| 前端测试 | 1 file / 4 tests passed |
| 前端 lint | passed |
| 前端 TypeScript + Vite build | passed，1777 modules transformed |
| 阶段 2 机器验收 | 34/34 passed |

机器验收可重复执行：

```powershell
python quality\stage2\evaluate_acceptance.py
```

输出为 `quality/stage2/acceptance_evaluation.json`；任何硬门槛失败时脚本返回非零退出码。

## 5. 真实端到端验证

最新主流水线 run ID：`run_20260714T023947Z`。

| 样本 | 结果 | 最终页面解析器 | 总耗时 |
|---|---|---|---:|
| native PDF（2 页） | passed | MinerU, MinerU | 2.136 s |
| complex PDF（表格/公式/双栏） | passed | MinerU | 2.064 s |
| mixed PDF（文本 + 扫描） | passed | MinerU, PaddleOCR | 21.535 s |
| scanned PDF | passed | PaddleOCR | 10.735 s |
| source image | passed | PaddleOCR | 6.655 s |

冻结阈值对比：

| 指标 | 实测 | 门槛 | 结论 |
|---|---:|---:|---|
| 有效样本任务成功率 | 1.00 | ≥ 1.00 | passed |
| 源页面覆盖率 | 1.00 | ≥ 1.00 | passed |
| 可用语义页面覆盖率 | 1.00 | ≥ 1.00 | passed |
| OCR pending 比率 | 0.00 | ≤ 0.00 | passed |
| 最大无效字符比率 | 0.00 | ≤ 0.01 | passed |
| 最低文档质量分 | 0.984871 | ≥ 0.75 | passed |
| 最低页面质量分 | 0.98185 | ≥ 0.60 | passed |
| 最少可用语义字符 | 49 | ≥ 20 | passed |
| 缺页数 | 0 | ≤ 0 | passed |
| 冻结样本精确重复率 | 0.00 | ≤ 0.00 | passed |
| MinerU 尝试 P95 | 2.107 s | ≤ 10 s | passed |
| CloudPath 流水线 P95 | 19.375 s | ≤ 30 s | passed |
| 最大进度间隔 | 4.012 s | ≤ 5 s | passed |
| GPU 峰值显存 | 2534 MiB | ≤ 14500 MiB | passed |
| GPU OOM | 0 | ≤ 0 | passed |

PaddleOCR 独立 smoke：真实 provider、CPU、31 个语义字符、平均置信度 `0.997`，通过。

MinerU 离线探针 run ID：`fallback_20260714T024444Z`：

- native PDF 两页均由 PyMuPDF 恢复。
- scanned PDF 由真实 PaddleOCR 恢复，49 个语义字符。
- image 由真实 PaddleOCR 恢复，49 个语义字符。
- 三种场景都先记录 MinerU 失败，再进入类型正确的降级分支。

## 6. RAG 影响验证

阶段 2 尚未实施 Chunker V2 或真实 pgvector 切换，但已确认解析变化不会破坏当前本地 RAG 消费：

| 冻结查询指标（8 条） | 实测 | 门槛 |
|---|---:|---:|
| Recall@5 | 1.00 | ≥ 1.00 |
| 全查询引用页准确率 | 1.00 | ≥ 1.00 |
| 命中后的引用页准确率 | 1.00 | ≥ 1.00 |
| 查询结果覆盖率 | 1.00 | ≥ 1.00 |
| native 子集 Recall@5 | 1.00 | ≥ 1.00 |

检索探针只使用隔离 run storage、artifact index 和 deterministic hashing embedding，不写共享数据库。

## 7. 阶段 2 审核清单

- [x] 所有当前支持的 PDF 和图片首先调用 MinerU。
- [x] 带文本层 PDF 在 MinerU 不可用时进入 PyMuPDF。
- [x] 扫描 PDF 和图片在 MinerU 不可用时进入 PaddleOCR。
- [x] 混合 PDF 使用全页检测，并按页合并 MinerU/PyMuPDF/OCR 结果。
- [x] parser report 记录每次尝试、范围、耗时、错误类别和最终解析器。
- [x] title、text、table、formula、image 映射由 mapper 单测覆盖，真实 complex 样本包含 title/table/image 证据。
- [x] 页码、bbox、source block、content hash 和图片 Asset 关联可追溯。
- [x] MinerU 结果损坏或服务不可达时安全降级。
- [x] 低质量、部分页失败、超时、迟到结果和 stale generation 均按状态机处理。
- [x] mixed-PDF golden case 的页序、逐页 parser、重复率和缺页均符合预期。
- [x] Mock OCR 占位内容不会成为页面正文或 RAG chunk。
- [x] PDF/图片成功率、有效文本、引用准确率、性能和 GPU 指标均满足阶段 0 冻结阈值。

## 8. 偏差、限制与风险

1. PaddleOCR 采用 CPU 而不是 CUDA。这是有意的资源隔离：本机安装的 PaddlePaddle 3.3.1 不是 CUDA build，MinerU 已使用 RTX 4070 Ti SUPER。此策略仍满足 OCR 和性能门槛。
2. 规范化产物 compare-and-publish 锁目前只保证单 CloudPath 进程。多 API worker 上线前，阶段 4 必须提供数据库事务锁/CAS。
3. MinerU 3.4.4 没有取消接口；CloudPath 超时不能终止已提交的上游 GPU 任务。
4. 保守的 OCR 视觉空格修复只处理有明显双词间隙的全大写 ASCII 行，避免用词典臆造正文；更一般的 OCR 语言纠错不在本阶段范围。
5. 当前 Chunk/RAG 仍是兼容模式。结构化切片、冻结 BGE-M3 tokenizer、质量隔离和原子向量替换属于阶段 3/4。
6. Office 文件尚未开放上传；只在阶段 5 完成格式安全验证后启用。
7. 本阶段没有触发历史课件重解析，因此已有书籍不会自动变更；后续若需批量重建仍须单独批准。

## 9. 回退方式

- 将 `BOOKCOURSE_PARSER_PROVIDER` 显式设为 `pymupdf` 或 `ocr` 可用于定向诊断；完整回退应恢复阶段 1 的 router/pipeline 代码，而不是删除现有书籍。
- 将 `BOOKCOURSE_OCR_PROCESS_ISOLATION=false` 可回到进程内 Paddle 调用，但会失去 Windows 下已验证的 5 秒心跳保证。
- 所有新增 Page/Block/Chunk 字段均保持向后兼容；回退不要求数据库迁移或删除历史 artifacts。
- 如本地 MinerU 暂时离线，默认 MinerU-first 路由会记录失败，并按类型进入 PyMuPDF/PaddleOCR；无需伪造成功状态。

## 10. 下一阶段

用户已明确要求后续阶段无需逐次确认，但每一阶段仍必须先完成独立审核并满足标准。因此阶段 2 通过后自动进入阶段 3，范围严格限于：

- Chunker V2 与冻结 BGE-M3 tokenizer 协议。
- 标题路径、跨页语义合并、token 上限、overlap、短尾规则。
- 表格/公式/图片原子内容处理、质量隔离、稳定 chunk ID。
- 本地 artifact/BM25 消费兼容和阶段 3 golden 验收。

阶段 3 不执行共享/生产 pgvector 迁移、不删除旧索引，也不批量重解析历史课件。
