# 阶段 5 审核报告：文件类型扩展与端到端兼容

> 状态：通过  
> 执行日期：2026-07-14  
> 真实流水线 run：`run_20260714T111508Z`  
> 机器总审：19/19 通过，失败列表为空  
> 格式矩阵：13/13 真实解析、Chunk V2、索引和 RAG 查询通过  
> 数据操作：未迁移共享/生产数据库，未删除历史数据，未批量重解析历史课程

## 1. 阶段结论

阶段 5 已完成并达到方案规定的全部 19 项审核标准，可以按既有授权自动进入阶段 6。

上传白名单现统一为 PDF、全部目标图片扩展名/别名和 DOCX/PPTX/XLSX；旧版 `.doc/.ppt/.xls` 与未知格式明确拒绝。后端对声明 MIME、真实签名、解码结果、PDF 加密/页数、图片像素/帧数，以及 OOXML 的 ZIP 结构、真实主类型、relationship、XML、活动内容、资源数量和处理时限执行 fail-closed 校验。前端 accept、说明、引用标签和阅读器语义已与后端同步。

Office 来源不再假装成 PDF 页：PPTX 使用幻灯片，XLSX 使用工作表/区域，DOCX 使用标题路径和块序号；缺少页面图片时使用安全转义的文本 SVG fallback，不进入 PDF-only 渲染或错误的 OCR/PyMuPDF 降级路径。

## 2. 最终格式矩阵

以下均为本机真实 MinerU/PaddleOCR 流水线结果，不是 mock parser：

| 格式 | 最终解析器 | 耗时（秒） | V2 chunks | Assets | RAG 引用 | 结果 |
|---|---|---:|---:|---:|---|---|
| PDF | MinerU | 5.054 | 2 | 0 | 第 1 页 | 通过 |
| PNG | PaddleOCR 降级 | 10.833 | 2 | 1 | 第 1 页 | 通过 |
| JPG | PaddleOCR 降级 | 8.212 | 2 | 1 | 第 1 页 | 通过 |
| JPEG | PaddleOCR 降级 | 8.266 | 2 | 1 | 第 1 页 | 通过 |
| JP2 | PaddleOCR 降级 | 8.361 | 2 | 1 | 第 1 页 | 通过 |
| WEBP | PaddleOCR 降级 | 8.225 | 2 | 1 | 第 1 页 | 通过 |
| GIF（单帧） | PaddleOCR 降级 | 8.216 | 2 | 1 | 第 1 页 | 通过 |
| BMP | PaddleOCR 降级 | 8.204 | 2 | 1 | 第 1 页 | 通过 |
| TIF（单页） | PaddleOCR 降级 | 8.251 | 2 | 1 | 第 1 页 | 通过 |
| TIFF（单页） | PaddleOCR 降级 | 8.375 | 2 | 1 | 第 1 页 | 通过 |
| DOCX | MinerU | 3.838 | 3 | 1 | `课程：整本文档导读 > 1. Cell membrane · 块 3` | 通过 |
| PPTX | MinerU | 3.858 | 1 | 1 | `幻灯片 2：ATP production` | 通过 |
| XLSX | MinerU | 3.779 | 2 | 0 | `工作表 Growth Data（A1:D5）` | 通过 |

真实格式成功率为 1.0；全格式 warm pipeline P95 为 9.358 秒；GPU 峰值显存 2304 MiB、峰值利用率 4%、OOM 0 次。

## 3. 上传与 OOXML 安全边界

统一文件类型模块维护扩展名、MIME、真实类型和用户提示。上传验证依次检查声明、落盘字节、签名和完整解码，不能仅凭扩展名或浏览器 MIME 放行。

OOXML 校验包含：

- ZIP 条目数、单项解压大小、总解压大小、压缩比和总时限。
- 规范化成员路径、目录穿越、重复 part、ZIP symlink、加密 flag 和非支持压缩算法。
- `[Content_Types].xml`、主文档 content type、根 relationship、内部 target 存在性与边界。
- 外部 relationship、DOCTYPE/ENTITY、嵌套压缩包、宏、ActiveX、OLE/embedding 和加密容器拒绝。
- DOCX 资源数、PPTX 幻灯片数和 XLSX 工作表数上限。

动画 GIF 和多页 TIFF 按阶段 0 冻结决策拒绝；单帧 GIF、单页 `.tif/.tiff` 放行。JP2/GIF 在进入 PaddleOCR 前规范化为 PNG，避免 Paddle 原生解码不兼容；该转换只发生在已完成像素/帧数安全验证之后。

## 4. 19 项审核标准

| # | 审核标准 | 结果 | 主要证据 |
|---:|---|---|---|
| 1 | 全部目标格式可上传 | 通过 | API 白名单测试；13 格式真实流水线 |
| 2 | 旧 Office 与未知格式拒绝 | 通过 | exact extension contract |
| 3 | 加密 PDF/Office 拒绝 | 通过 | 冻结加密 PDF；compound password container |
| 4 | 前后端白名单一致 | 通过 | shared contract 测试；lint/build |
| 5 | 伪造扩展名拒绝 | 通过 | 图片签名与 OOXML 内部类型测试 |
| 6 | 损坏 OOXML 拒绝 | 通过 | frozen rejection matrix |
| 7 | 路径穿越和高压缩比拒绝 | 通过 | traversal、symlink、ratio fixtures |
| 8 | OOXML 高风险内容隔离 | 通过 | external rel、XXE、nested、active/encrypted tests |
| 9 | 文件字节边界 | 通过 | init 与流式上传 boundary/overflow |
| 10 | 图片像素边界 | 通过 | exact pixel boundary/overflow |
| 11 | PDF/PPTX/XLSX/DOCX 上限 | 通过 | 页面、幻灯片、工作表、资源超限测试 |
| 12 | ZIP 四类资源上限 | 通过 | entry/item/total/ratio boundary tests |
| 13 | MinerU/Office 超时状态 | 通过 | MinerU 全量超时测试；typed Office timeout |
| 14 | GIF/TIFF/TIF 冻结行为 | 通过 | 单帧/单页通过；动画/多页拒绝；真实 E2E |
| 15 | PPTX 幻灯片引用 | 通过 | `幻灯片 2：ATP production` |
| 16 | XLSX 工作表/区域引用 | 通过 | `工作表 Growth Data（A1:D5）` |
| 17 | DOCX 不虚构页码 | 通过 | document 类型 + 标题路径/块序号 |
| 18 | Office 不调用 PDF-only 渲染 | 通过 | preview endpoint、safe SVG、router tests |
| 19 | 每种格式解析并完成 RAG 查询 | 通过 | 13/13 real E2E；deterministic format E2E |

机器可读逐项判定见 `acceptance_evaluation.json`，格式明细见 `format_matrix.json`。

## 5. 自动化测试

| 测试 | 结果 |
|---|---:|
| 后端全量 pytest | 346 passed，0 failed，9 skipped |
| 阶段 5 专项 pytest | 70 passed，0 failed |
| 前端 Vitest | 7 passed |
| 前端 ESLint | passed |
| 前端 TypeScript + Vite build | passed |
| Stage 5 machine acceptance | 19/19 passed |
| 真实 MinerU/PaddleOCR 格式流水线 | 13/13 passed |

默认后端套件的 9 个 skip 均是需要显式隔离 Stage-4 PostgreSQL/pgvector DSN 的数据库集成测试（7 个 pgvector 原子性测试、2 个业务生命周期测试）；阶段 4 已在批准的本机 loopback 隔离库中以 296 passed、0 failed 独立验证，并在测试结束后确认状态表和向量表均为 0 行。阶段 5 没有访问该数据库或任何共享/生产数据库。

## 6. 主要修改范围

后端实现主要涉及：

- `backend/app/services/file_types.py`
- `backend/app/services/upload_validation.py`
- `backend/app/services/ooxml_validation.py`
- `backend/app/services/storage.py`
- `backend/app/api/routes_uploads.py`
- `backend/app/api/routes_books.py`
- `backend/app/document/detector.py`
- `backend/app/document/office_preview.py`
- `backend/app/document/parsers/base.py`
- `backend/app/document/parsers/mineru_parser.py`
- `backend/app/document/parsers/router.py`
- `backend/app/document/page_artifacts.py`
- `backend/app/document/image_preprocessor.py`
- `backend/app/document/ocr_pipeline.py`
- `backend/app/document/chunker_v2.py`
- `backend/app/rag/service.py`
- `backend/app/schemas/books.py`
- `backend/app/core/config.py`

前端实现主要涉及上传页、共享文件类型契约、API 类型、聊天引用、来源阅读器、课程页和章节确认页。配置样例、根 README 与后端 README 已同步更新。

新增的阶段专项覆盖位于：

- `backend/app/tests/test_stage5_upload_security.py`
- `backend/app/tests/test_stage5_office_semantics.py`
- `backend/app/tests/test_stage5_format_e2e.py`
- `backend/app/tests/test_phase5_ocr.py`
- `frontend/src/screens/shared.test.ts`
- `quality/stage5/`

## 7. 独立审计返工记录

首轮真实 13 格式探针没有被当作通过证据：它暴露了 JP2/GIF 可被 Pillow 验证但 PaddleOCR 不能直接接收，以及报告序列化访问错误。现已在 OCR 边界加入受控 PNG 规范化并修复探针，随后使用全新 run `run_20260714T111508Z` 重跑 13 格式全部通过。

最后一轮安全审计还补上了 ZIP 目录项路径检查、symlink 拒绝和 Office SVG 中非法 XML 控制字符清理，并在这些修改后重新运行完整矩阵。成功报告只引用最终通过 run；失败 run 保留在隔离的 `quality/stage5/real_runs` 中用于诊断追踪。

## 8. 回退方式

- 原始上传文件和既有解析产物没有被删除，可按 parse generation 重新构建。
- 若需要回退 MinerU 主路由，可将 parser provider 切回既有 `auto`/`pymupdf` 路由；同时必须关闭 Office 上传入口，因为 Office 明确没有 PyMuPDF/OCR 兼容降级。
- 前后端白名单必须作为同一个发布单元回退，避免前端允许而后端拒绝或相反。
- Chunker V2 和索引 generation 的回退仍使用阶段 3/4 已验证的版本及发布门禁；不得直接混用新旧向量。
- 本阶段没有执行数据库迁移或历史重解析，因此无需数据回滚。

## 9. 剩余风险与阶段 6 门禁

- 阶段 6 仍需汇总冻结查询指标、性能、故障注入、启动顺序、监控和发布回退手册。
- 真实格式探针使用 artifact fallback 完成格式/语义 RAG 验证；真实 pgvector/BGE-M3 的原子发布和检索一致性已在阶段 4 的隔离库与 CUDA 探针中单独通过，阶段 6 必须合并两组证据说明边界。
- Office 当前提供安全的文本型预览 fallback，不承诺像素级还原原始 Word/PowerPoint/Excel 版式。
- 生产配置切换、共享/生产数据库 migration 和历史课程批量重解析均未执行，仍需分别明确授权。

阶段 5 判定为 **通过**，按用户已记录的剩余方案授权继续阶段 6；该授权不包含生产发布、共享数据库变更或历史批量重解析。
