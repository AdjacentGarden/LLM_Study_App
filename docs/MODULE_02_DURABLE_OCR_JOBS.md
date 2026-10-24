# 模块 2：可恢复 OCR 任务系统

## 目标

把“上传后只显示 queued”的占位逻辑升级为可在生产环境长期运行的 GPU OCR 队列。任务状态、尝试次数、租约、质量结果和错误诊断全部落入数据盘 SQLite；API 或机器重启后不会丢失。

## 状态机

`uploaded -> queued -> running -> ocr_ready | ocr_review_required`

失败路径为 `running -> retry_wait -> running`。达到尝试上限后进入 `failed`，用户可以显式调用重试接口。部署造成的协作式停止会把任务重新放回 `queued`，且不消耗尝试次数。

## 关键实现

- SQLite WAL 持久化书籍与 OCR 任务。
- `BEGIN IMMEDIATE` 原子抢占，避免多个 Worker 重复处理同一本书。
- Worker 租约、心跳、超时回收、可配置失败等待和最大尝试次数。
- 每次尝试使用独立输出目录，失败日志不会被下一次覆盖。
- MinerU 使用数据盘本地模型；生产环境禁止因模型缺失而静默联网下载。
- 停止 Worker 时递归终止完整子进程树，包括 MinerU 自建的新进程组，避免残留 GPU 进程。
- 成功后发布稳定的 `ocr/normalized` 目录，并重建可独立校验的 SHA-256 清单。
- API 提供幂等处理、状态查询和受状态机保护的人工重试。

## API

- `POST /api/books`：上传 PDF 并持久化书籍记录。
- `POST /api/books/{book_id}/process`：幂等入队。
- `GET /api/books/{book_id}/status`：返回状态、进度、尝试次数、页数、质量分和是否可重试。
- `POST /api/books/{book_id}/process/retry`：只允许对 `failed` 或 `ocr_review_required` 任务人工重试。

## 验证门禁

- 59 项后端测试全部通过。
- 12 个并发 Worker 抢占同一任务时，只有 1 个获得租约。
- 覆盖重复入队、心跳单调性、错误所有者、租约过期、崩溃恢复、尝试耗尽、失败后成功、人工重试、服务重启恢复。
- 使用真实的独立进程组验证完整子进程树终止。
- Ruff 与 MyPy strict 全部通过；前端 TypeScript 与 Vite 构建通过。
- 4090 数据盘真实上传 3 页扫描版生物教材并完成 VLM OCR：142.1 秒，3/3 页，1,125 字符，33 个结构块，0 个异常块，平均启发式质量分 0.9441；2 页 accepted、1 页 review、0 页 rescue/missing。
- 尝试目录和规范化目录的 SHA-256 校验全部通过。
- 部署后 RAG 交叉回归通过：回答状态 `supported`，5 条有原文引用的结论，检索 6,011 ms、生成 6,665 ms。

## 已知边界

自动质量分用于异常发现，不等同于人工真值 CER。启用自动视觉复核后，`review`、`rescue` 和 `missing` 页面先对照原 PDF 图像复核；这些规则标签本身不再阻止进入下一阶段。

## 自动视觉复核与跳过页

默认开启 `DOCUMENT_VISION_RESCUE_ENABLED=true`。规则通过的页面直接保留，其余页面由视觉模型决定保留原识别、修正、确认空白或跳过。修正结果必须再对照原图核验，通过后才发布；图像无法可靠辨认或修正未通过核验时记为 `skipped`。

跳过页保留原 PDF 页码，发布的文字、结构块和旧版备用文字字段全部清空。原始 OCR 保存在独立提取目录供审计。确认空白使用 `blank_confirmed`，不计入无法识别的缺页范围。重新发布 OCR 会使旧章节结构失效，避免继续使用旧引文。

章节摘要、讲解、题目和问答接收内部 `source_coverage`，其中 `skipped_page_ranges` 使用从 1 开始的 PDF 页码闭区间，例如 `[[4, 6], [12, 12]]`。跳过页不进入索引、引文和教学证据。整个章节没有可读内容时不生成该章节；其他章节仍可用。正常学习输出不主动提及缺页、OCR 或证据不足。问答可转为普通知识解释，此时不附教材引文。

视觉调用参数：

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `OCR_REVIEW_MODEL` | `PUCODING_VISION_MODEL` | 具备图像输入能力的复核模型 |
| `OCR_REVIEW_TIMEOUT_SECONDS` | `90` | 每次视觉调用总时限 |
| `OCR_REVIEW_MAX_CALLS_PER_BOOK` | `1024` | 每本书的持久化调用上限，包含核验和失败尝试 |

服务复用 `PUCODING_BASE_URL`、`PUCODING_API_KEY` 与已有代理设置。密钥缺失、服务拒绝、断网、无效结构化返回均不能把页面记为已跳过。任务保留进度并按队列策略重试；耗尽尝试后显示失败，可从已完成页之后继续。

`ocr/extraction-checkpoint.json` 记录与源文件哈希和 OCR 后端匹配的提取产物；`ocr/review/` 记录与原 OCR、源文件、模型、地址及复核版本绑定的逐页决策和调用预算。成功的页面不会重复付费调用。若请求已提交但无法确认结果，记录 `submitted` 或 `uncertain` 并停止自动重放，需管理员核实供应商请求结果后恢复该条记录。不要批量清空复核目录来重试。

只有持有有效租约的 Worker 可以发布完整复核产物。Windows 使用临时目录复制后替换并保留旧目录，POSIX 使用原子符号链接切换。中断、取消或失去租约不会发布半成品。
