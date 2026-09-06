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

自动质量分用于异常发现，不等同于人工真值 CER。`review` 页面会保留并进入后续清洗/章节模块；`rescue` 或 `missing` 页面会阻止自动进入下一阶段。
