# Stage 0 审核报告：基线、样本与安全准备

> 状态：待用户审核  
> 执行日期：2026-07-13  
> 实施范围：仅阶段 0  
> 生产行为变更：无  
> 当前停止点：阶段 0 已完成，未获批准不得进入阶段 1

## 1. 阶段目标与结论

阶段 0 已完成以下目标：

- 冻结 CloudPath 当前解析、切片、检索和测试基线。
- 建立包含全部目标格式、异常文件和安全样本的非敏感合成语料。
- 验证本地 MinerU HTTP API、任务生命周期、CUDA PyTorch 和目标格式实际行为。
- 冻结 Chunker V2、解析质量、检索质量、性能和格式语义的计算协议及数值门槛。
- 建立 155 个生产源文件和关键配置文件的 SHA-256 快照。

阶段 0 没有修改 CloudPath 解析路由、默认 parser、OCR provider、上传白名单、RAG chunker 或数据库行为。

## 2. 工作区与环境基线

工作区 `D:/code/CloudPath_app` 不包含 `.git` 元数据，因此无法通过 Git 判断批准前已经存在的用户改动。为避免后续阶段误判，已生成 `source_inventory.json`：

- 文件数量：155
- 内容：`backend/app`、`frontend/src`、依赖清单、TypeScript 配置和关键根文档
- 每个文件记录相对路径、字节数和 SHA-256

环境摘要：

| 项目 | 当前值 |
|---|---|
| 操作系统 | Windows 10 Pro 10.0.19045 x64 |
| 系统 Python | 3.14.0；未安装 pytest |
| 隔离后端测试 Python | 3.12.11 |
| Node / npm | 24.11.1 / 11.6.2 |
| MinerU | 3.4.4，protocol 2 |
| MinerU endpoint | `http://127.0.0.1:8001` |
| MinerU 并发 | 1 |
| MinerU task TTL | 86400 秒 |
| PyTorch | 2.13.0+cu130 |
| CUDA | 可用，13.0 |
| GPU | RTX 4070 Ti SUPER，16376 MiB |
| NVIDIA driver | 610.47 |

MinerU 主 Python 进程 PID 14100 出现在 NVIDIA compute-app 列表中，并持有约 2.2 GiB GPU 上下文；Windows WDDM 模式下 `nvidia-smi` 不返回逐进程显存数值，但 PyTorch 明确返回 `cuda_available=true` 和正确 GPU 名称。

详细机器可读快照见 `environment_snapshot.json`。

## 3. 当前配置与解析路由快照

当前代码默认值：

| 配置 | 当前默认 |
|---|---|
| Parser | `auto` |
| MinerU endpoint | 未配置 |
| OCR | `mock` |
| Layout | `opencv` |
| Embedding | `hashing` |
| RAG index | `pgvector`，无数据库时回退 Artifact |
| Reranker | `heuristic` |
| RAG LLM | `template` |

当前实际路由：

```text
带文本层 PDF：MinerU（不可用）→ Marker（不可用）→ PyMuPDF
扫描 PDF / 图片：Mock OCR
```

当前 `rag_indexing` 只生成并写入 `chunks.jsonl`，没有实际调用 pgvector `upsert_chunks()`。

## 4. 自动化测试基线

### 4.1 首次环境测试

后端首次执行被依赖声明阻断：

- 系统 Python 3.14 没有 pytest。
- 按 `backend[test]` 创建隔离环境后，FastAPI/Starlette TestClient 仍要求 `httpx2`，但 `pyproject.toml` 的 test extras 没有声明该包。

前端首次执行被缺少 `node_modules` 阻断。

这些首次失败作为环境基线保留。本阶段只在 `%TEMP%/cloudpath-stage0-venv` 安装后端测试依赖，并执行 `npm ci`；没有修改项目依赖声明。

### 4.2 补齐隔离环境后的结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端 | `python -m pytest -q` | 87 passed，28 failed，2 skipped |
| 前端测试 | `npm test -- --reporter=verbose` | 1 file / 4 tests passed |
| 前端 lint | `npm run lint` | passed |
| 前端 build | `npm run build` | passed，1777 modules transformed |

后端 28 个失败属于同一既有原因：测试仍硬编码旧路径 `BookCourseAI_frontend_project`，而当前前端目录为 `frontend`。本阶段没有修复测试。后续阶段不得增加无法说明的新失败；该路径债务应在开始修改相关测试前单独处理并报告。

## 5. 合成测试语料

`fixtures` 目录由 `generate_fixtures.py` 生成，不包含用户文件或个人信息。`fixture_manifest.json` 记录每个文件的 SHA-256、大小、类别和测试用途。

覆盖范围：

- PDF：原生文本、复杂多栏/表格/公式、纯扫描、混合页面、加密、损坏。
- 图片：PNG、JPG、JPEG、JP2、WEBP、BMP、GIF、TIFF、TIF alias、损坏图片。
- Office：DOCX、PPTX、XLSX。
- 安全：扩展名伪造、OOXML 路径穿越、高压缩比包。

异常和安全样本不会提交到生产 parser，仅用于后续获批阶段的上传校验测试。

## 6. MinerU HTTP 与格式探针

使用实际异步协议执行：

```text
GET /health
POST /tasks
GET /tasks/{task_id}
GET /tasks/{task_id}/result
```

11 个合法/决策样本均获得 HTTP 202 并完成，结果包含 `middle_json` 和 `content_list`。

| 文件 | MinerU 页数 | 主要类型 | 结论 |
|---|---:|---|---|
| native_text.pdf | 2 | text | 成功，保留标题级别 |
| multicolumn_table_formula.pdf | 1 | text、table | 成功，识别表格；ASCII 公式仍作为 text |
| scanned.pdf | 1 | chart | 完成，但 chart content 为空，必须触发质量降级 |
| mixed.pdf | 2 | text、chart | 页序正确；扫描页无语义内容，必须页面级降级 |
| source_diagram.png | 1 | chart | 完成但无语义内容，必须降级 OCR |
| sample.docx | 1 | text、table、image | 成功，标题级别可用 |
| sample.pptx | 2 | text、image | 成功，source page 对应幻灯片 |
| sample.xlsx | 2 | text、table | 成功，source page 对应工作表 |
| animated.gif | 1 | chart | 两帧只返回一页，冻结为拒绝动画 GIF |
| multipage.tiff | 1 | chart | 两页只返回一页，冻结为拒绝多页 TIFF |
| sample.tif | 1 | chart | 本地 MinerU 实际接受，阶段 5 安全校验通过后开放 alias |

机器可读的请求状态、task ID、耗时、GPU 采样、结果结构和原始结果位于 `mineru_probe`。

## 7. 当前 CloudPath 解析与 RAG 基线

使用现有默认行为在隔离 storage 中解析五个场景：

| 文件 | 实际 parser | chunks | 主要结果 |
|---|---|---:|---|
| native_text.pdf | PyMuPDF | 6 | 5 text + 1 figure |
| multicolumn_table_formula.pdf | PyMuPDF | 9 | 8 text + 1 figure；表格语义被拆散 |
| scanned.pdf | Mock OCR | 3 | 1 ocr_pending + 2 figure |
| mixed.pdf | PyMuPDF | 4 | 扫描页变成 ocr_pending |
| source_diagram.png | Mock OCR | 1 | 仅 ocr_pending |

冻结查询集共 8 个问题：

| 指标 | 当前基线 | 最终门槛 |
|---|---:|---:|
| Recall@5 | 0.625 | 1.000 |
| 所有查询引用页准确率 | 0.625 | 1.000 |
| 命中后的引用页准确率 | 1.000 | 1.000 |
| 查询结果覆盖率 | 0.625 | 1.000 |

三个未命中问题都来自扫描页或图片。详细 chunk、query、分数和耗时见 `cloudpath_baseline/summary.json`。

## 8. 冻结的计算协议与验收门槛

机器可读门槛位于 `acceptance_thresholds.json`。关键决定：

- BGE-M3 tokenizer/model revision：`5617a9f61b028005a4858fdac845db406aefb181`。
- BGE reranker revision：`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`。
- Transformers/tokenizers 协议版本：4.57.6 / 0.22.2。
- Chunk：目标 450、最大 700、最小 120、overlap 80 tokens。
- 原子内容硬上限：900 tokens。
- 冻结 fixture 精确重复率：0；overlap 重复 token 比例不超过 0.20。
- 有效 fixture 的 parser task、source page、usable semantic page 覆盖率均为 1.0。
- `ocr_pending` 比例为 0。
- Recall@5、所有查询引用准确率和查询覆盖率均为 1.0。
- 当前硬件 warm MinerU fixture P95 不超过 10 秒；冷启动不超过 180 秒；GPU OOM 为 0。

质量分 `quality-v1` 使用以下权重：有效字符 0.25、内容覆盖 0.30、OCR 置信度 0.15、映射完整度 0.20、去重 0.10。只有图片路径、但没有 caption/OCR/analysis 的 chart/image 不算可用语义内容。

模型信息来源：

- https://huggingface.co/BAAI/bge-m3
- https://huggingface.co/BAAI/bge-reranker-v2-m3

## 9. 格式语义冻结

- `.tif`：阶段 5 安全校验实现并通过后，作为单页 TIFF alias 开放。
- GIF：只接受单帧；动画 GIF 拒绝，避免静默丢帧。
- TIFF/TIF：只接受单页；多页文件拒绝，避免静默丢页。
- DOCX：默认引用“标题路径 + 段落/块序号”；只有存在稳定渲染页时才显示页码。
- PPTX：source page 表示幻灯片序号。
- XLSX：source page 表示工作表，引用同时保存 sheet name/区域。
- MinerU task TTL：86400 秒；清理间隔 300 秒。

## 10. 已知风险与后续处理

1. 工作区没有 Git 元数据，后续只能依据 source inventory 和逐阶段修改清单保护既有内容。
2. 后端测试依赖声明不完整；阶段 1 引入 HTTP 客户端时应同时确定并锁定 TestClient 依赖策略。
3. 28 个后端测试仍引用旧前端路径；在后续阶段修改测试前需要先修复该路径债务并单独展示差异。
4. MinerU pipeline 对合成扫描图将页面识别为无内容 chart；阶段 2 的质量门禁必须触发 PaddleOCR 页面级降级。
5. MinerU API 当前没有在探针中验证取消能力；阶段 1 必须按方案处理超时后的迟到结果和幂等性。

## 11. 阶段 0 审核清单

- [x] 工作区无 Git 元数据的限制已记录，并建立 155 文件 SHA-256 快照。
- [x] 后端和前端基线测试结果可重复。
- [x] 每类目标文件至少有一个合法合成样本。
- [x] 包含损坏文件、伪造扩展名、路径穿越和高压缩比样本。
- [x] MinerU `/health` 通过，版本、协议、并发和 TTL 已记录。
- [x] MinerU PyTorch CUDA 可用，GPU 型号和进程上下文已确认。
- [x] 当前解析耗时、有效内容、chunk 和检索结果已记录。
- [x] 成功率、Recall@5、引用准确率、性能和 OOM 门槛已冻结。
- [x] tokenizer、token 范围、重复率、quality score、短尾和原子内容规则已冻结。
- [x] `.tif`、动画 GIF、多页 TIFF、DOCX 定位和 MinerU TTL 已明确。
- [x] 未改变生产解析行为。

## 12. 修改文件与产物

本阶段新增：

```text
quality/stage0/README.md
quality/stage0/generate_fixtures.py
quality/stage0/probe_mineru.py
quality/stage0/measure_cloudpath_baseline.py
quality/stage0/snapshot_inventory.py
quality/stage0/fixture_manifest.json
quality/stage0/source_inventory.json
quality/stage0/environment_snapshot.json
quality/stage0/acceptance_thresholds.json
quality/stage0/fixtures/*
quality/stage0/mineru_probe/*
quality/stage0/cloudpath_baseline/*
quality/stage0/STAGE0_AUDIT_REPORT.md
```

项目根目录已有的实施方案将在阶段 0 审核时标记为“阶段 0 待审核”。

## 13. 回退方式

阶段 0 没有改变生产代码或配置。如不保留基线产物，可以删除 `quality/stage0`；前端 `node_modules` 和 `dist` 均属于 `.gitignore` 中的本地依赖/构建输出。隔离 Python 环境位于 `%TEMP%/cloudpath-stage0-venv`。

## 14. 强制停止声明

阶段 0 已完成，当前实施已停止。未收到用户明确回复“批准阶段 0，进入阶段 1”前，不实施 MinerU HTTP 客户端，不修改默认 parser 路由，也不进入任何后续阶段。
