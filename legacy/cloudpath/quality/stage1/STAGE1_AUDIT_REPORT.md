# 阶段 1 审核报告：MinerU HTTP 客户端与任务生命周期

> 状态：待用户审核  
> 执行日期：2026-07-13  
> 实施范围：仅阶段 1  
> 生产解析路由变更：无  
> 当前停止点：阶段 1 已完成；未获批准不得进入阶段 2

## 1. 阶段目标与结论

阶段 1 已完成独立、同步、可测试的 MinerU protocol-v2 HTTP 客户端，但没有接入 CloudPath parser router 或文档 pipeline。

已交付：

- `GET /health`、`POST /tasks`、状态轮询和结果获取。
- MinerU 配置、请求、任务、健康状态和结果显式模型。
- 总 deadline、单请求 timeout、安全 GET 有限重试、429/排队退避和响应大小上限。
- 本地幂等键、parse generation、一次性提交 claim、MinerU task ID 持久化和重启续查。
- `submission_uncertain`、过期、失败、超时、迟到结果、重复结果和损坏持久化状态的安全处理。
- 29 个 HTTP/故障注入测试、15 个状态持久化测试。
- 一次开启 `return_images=true` 的真实本地 CUDA MinerU 调用记录。

当前实际默认值仍为：

```text
BOOKCOURSE_PARSER_PROVIDER=auto
BOOKCOURSE_OCR_PROVIDER=mock
BOOKCOURSE_MINERU_ENDPOINT=<未配置>
```

生产 parser、上传白名单、Chunker、RAG 索引和前端行为均未改变。

## 2. 关键设计决定

### 2.1 HTTP 与协议

- 使用当前项目解析栈对应的 `httpx2>=2.5,<3`，采用可注入 `MockTransport` 的同步客户端。
- 显式提交 `backend=pipeline`、`effort=medium`、结构化结果和图片相关字段，不依赖 MinerU 自身的 `hybrid-engine` 默认值。
- `lang_list` 使用重复 multipart part；文件字段使用重复数组字段名 `files`，一次 CloudPath 调用只上传一个文件。
- 服务返回的 `status_url` 和 `result_url` 不可信；客户端始终用配置 endpoint 和经过校验的 task ID 构造路径。
- endpoint 仅允许显式 loopback origin，客户端设置 `trust_env=false`、`follow_redirects=false`，避免代理或重定向转发课件。
- ZIP、关闭 `middle_json`、关闭 `content_list` 和 client-side output generation 与本阶段 JSON 客户端不兼容，配置模型会直接拒绝。
- 结果必须只有一个文档，同时包含已解码的 `middle_json` object 和 `content_list` list；任务文件标识与结果 key 必须一致。
- 解压后的 result JSON 默认上限为 256 MiB；gzip 只解码一次。结果 digest 直接基于已缓冲响应计算，避免复制一份完整 base64 JSON。

### 2.2 重试与超时

- 安全 GET 在初次请求之外最多重试 `BOOKCOURSE_MINERU_MAX_RETRIES` 次。
- 4xx 不做无意义重试；429 尊重 `Retry-After` 或指数退避。
- `POST /tasks` 的 transport、5xx 或已受理但缺少有效 task ID 的情况都视为 `submission_uncertain`，绝不自动重放。
- 只有明确返回 429、即明确拒绝上传时，才允许按上限重新 POST；此时不会产生已受理任务的副本。
- 所有 health、submit、poll 和 result 操作共用 monotonic 总 deadline；轮询或下一次退避无法在 deadline 内完成时立即停止。

### 2.3 持久化、幂等与 generation

正常状态流：

```text
created → submitting → submitted → pending → processing
                                      └────→ remote_completed → completed
```

安全分支：

```text
submitting + 响应不确定 → submission_uncertain（禁止自动重提）
known task + 404        → expired
known task + deadline   → timed_out（必须显式 invalidate 后才能重提）
旧 generation 迟到      → discarded_stale / CAS 拒绝
```

- 一次性 `created → submitting` claim 防止同一进程内两个 caller 同时提交。
- 已取得 task ID 后，GET 故障保留可恢复状态；后续调用只查询原 task ID，不再次上传。
- 相同 result digest 可重复接受；不同 digest 视为协议冲突。
- JSON 状态文件损坏时 fail closed，不把损坏状态当作空状态后重新提交。
- 当前 JSON compare-and-swap 只保证一个共享 `MinerUTaskStore` 实例、单 CloudPath 进程内的原子性。多 worker 进程必须在后续阶段改用数据库锁/CAS。

### 2.4 与方案的偏差

- 方案中的“5xx 有限重试”只用于安全 GET。MinerU 没有上游 idempotency key、任务列表或按幂等键反查能力，因此 POST 5xx 不重试，这是避免重复 GPU 任务所必需的安全收紧。
- 额外增加了 loopback endpoint 限制、gzip 回归保护、解压后响应大小上限和损坏状态 fail-closed。
- 超时且已有 task ID 的相同请求默认禁止自动重提，必须由后续显式操作先 invalidate；这比原方案更保守。
- 真实 smoke 使用阶段 0 的合成 PPTX，以便同时验证结构化内容和 image data URI。该格式仍未加入 CloudPath 上传入口。
- 按阶段 0 报告的约定，修复了两个旧测试文件中的前端目录硬编码；该修复只影响测试定位，不改变应用行为。

## 3. 修改文件清单

### 3.1 生产代码与依赖

```text
backend/pyproject.toml
backend/app/core/config.py
backend/app/services/persistence.py
backend/app/services/kv_store.py
backend/app/document/mineru/__init__.py
backend/app/document/mineru/client.py
backend/app/document/mineru/exceptions.py
backend/app/document/mineru/models.py
backend/app/document/mineru/task_store.py
```

### 3.2 配置与说明

```text
backend/.env.example
backend/README.md
MINERU_RAG_IMPROVEMENT_PLAN.md
```

### 3.3 测试与阶段产物

```text
backend/app/tests/test_mineru_http_client.py
backend/app/tests/test_mineru_task_store.py
backend/app/tests/test_audit_p0p1.py
backend/app/tests/test_audit_p2.py
quality/stage1/probe_client.py
quality/stage1/real_client_probe.json
quality/stage1/STAGE1_AUDIT_REPORT.md
```

## 4. 自动化测试结果

使用阶段 0 的隔离 Python 3.12.11 环境。

| 检查 | 命令 | 结果 |
|---|---|---|
| 依赖安装 | `python -m pip install -e ".[test]"` | 成功；解析到 `httpx2 2.5.0` |
| MinerU HTTP | `python -m pytest app/tests/test_mineru_http_client.py -q` | 29 passed |
| 任务状态存储 | `python -m pytest app/tests/test_mineru_task_store.py -q` | 15 passed |
| 后端全量 | `python -m pytest -q` | 161 passed，0 failed，0 skipped |
| Python 编译 | `python -m py_compile ...` | passed |
| 前端测试 | `npm test -- --reporter=verbose` | 1 file / 4 tests passed |
| 前端 lint | `npm run lint` | passed |
| 前端 build | `npm run build` | passed；1777 modules transformed |

阶段 0 后端基线为 87 passed、28 failed、2 skipped。28 个失败和 2 个跳过均来自旧目录名 `BookCourseAI_frontend_project`。将测试定位修正为实际 `frontend` 后，原有 117 个测试全部通过；本阶段新增 44 个 MinerU 测试，因此当前总数为 161。

主要故障注入覆盖：

- health 成功、协议不兼容、连接/读取超时、4xx、5xx 和总 deadline。
- 完整 multipart 字段、重复语言、POST 429、4xx、5xx、transport failure 和 HTTP 202 损坏响应。
- pending、processing、completed、failed、queued_ahead、429 Retry-After 和轮询超时。
- result 202、200、404、409、非法 JSON、空/缺损结果、gzip、响应过大和嵌套 JSON 损坏。
- 一次性提交 claim、并发竞争、迟到 generation、重复/冲突 digest、重启恢复、过期、超时阻断重提和损坏 state fail-closed。
- 恶意 task ID、恶意返回 URL、课件正文 sentinel、带空格本地路径和持久化内容泄漏检查。

## 5. 真实本地 MinerU 验证

执行：

```powershell
python ..\quality\stage1\probe_client.py `
  ..\quality\stage0\fixtures\sample.pptx `
  --endpoint http://127.0.0.1:8001 `
  --output ..\quality\stage1\real_client_probe.json `
  --timeout 600
```

脱敏结果：

| 项目 | 结果 |
|---|---|
| MinerU | 3.4.4 / protocol 2 / healthy |
| backend | pipeline |
| 输入 | 阶段 0 合成 `sample.pptx`，36,487 bytes |
| task ID | `f98ace17-e27b-4e3f-8555-aea4eeb49be2` |
| 最终状态 | completed |
| 用时 | 0.625 秒 |
| 结构 | 2 pages / 6 content items / 5 text + 1 image |
| 图片 | 1 个 data URI，共 47,995 characters |
| GPU 采样 | 3 次；峰值 2168 MiB、利用率 9% |

记录只保存 hash、数量、类型、长度、task ID、耗时和 GPU 汇总；没有保存正文或图片 data URI。

另外确认：

```text
parser_provider=auto
ocr_provider=mock
mineru_endpoint=None
parser router / pipeline / API 中没有 MinerUClient 引用
```

## 6. 阶段 1 审核清单

- [x] `/health` 成功、失败和超时均有测试。
- [x] `/tasks` multipart 请求字段与本地 MinerU API 一致。
- [x] `pending`、`processing`、`completed`、`failed` 状态均有测试。
- [x] 安全 GET 的 5xx 重试次数有上限，4xx 不重试；不安全 POST 不盲目重放。
- [x] 总超时后不会无限占用 CloudPath worker。
- [x] 已取得 task ID 后的轮询故障不会重复提交文件。
- [x] 429、排队退避、迟到结果、重复结果、重启恢复和任务过期 404 均有测试。
- [x] 明确记录 MinerU 没有取消接口，CloudPath 超时后上游 GPU 任务可能继续。
- [x] 错误、日志和持久化状态不包含完整课件正文或敏感本地路径。
- [x] HTTP 客户端可以独立运行，但未改变默认 parser 路由。
- [x] 现有后端和前端测试全部通过。

## 7. 已知限制与风险

1. MinerU 3.4.4 没有 cancel、任务列表、幂等键查询或 callback。`submission_uncertain` 无法自动确认，只能等待人工决策或降级。
2. CloudPath deadline 只停止本地等待，不能停止上游 GPU。相同 timed-out 任务已被安全阻断重提，但显式 invalidate 后仍可能与尚未结束的上游任务并存。
3. MinerU task registry 只在服务进程内存中；服务重启、热重载或 TTL 清理后查询会返回 404。completed/failed TTL 为 86400 秒，清理轮询为 300 秒。
4. 当前任务状态 CAS 仅支持单 CloudPath 进程和共享 store 实例；启用多进程 worker 前必须迁移到数据库锁/CAS。
5. `return_images=true` 会在 JSON 中携带 base64 图片。本阶段增加 256 MiB 解压后上限，但超大课件仍会被明确拒绝，不能保证所有 100 MiB 上传均能返回图片 JSON。
6. 阶段 1 只持久化任务身份、状态和 result digest，不持久化 MinerU 原始结果工件；结果落地与原子提交属于阶段 2 mapper/pipeline 工作。
7. 因阶段 1 未接入 parse API，generation 目前由 standalone orchestration 分配。阶段 2 接入时必须在解析请求入队前 reserve generation，不能按 worker 执行顺序分配。

## 8. 回退方式

本阶段没有数据库迁移或生产路由切换。回退时可：

1. 删除 `backend/app/document/mineru/`、对应两份 MinerU 测试和 `quality/stage1/`。
2. 从 `pyproject.toml` 移除 `httpx2`，回退 `config.py`、`.env.example` 和 README 的 MinerU client 配置。
3. 回退 `persistence.py`/`kv_store.py` 的 load-failed 可见性和 fail-closed 支持。
4. 测试前端目录修正建议保留；它与生产功能无关，并清除了阶段 0 已知测试债务。

默认 parser 始终是 `auto`，所以回退不需要重解析课程、删除数据库数据或迁移索引。真实 probe 的任务状态保存在 `%TEMP%` 隔离目录，MinerU 终态任务由其 TTL 自动清理。

## 9. 下一阶段拟实施内容

只有阶段 1 获得明确批准后，阶段 2 才会实施：

- MinerU `middle_json` / `content_list` / images 到 CloudPath Page、Block、Asset 的 mapper。
- 质量门禁、页面级诊断和 parser report。
- 对当前已开放 PDF/图片接入 MinerU 主路由。
- PyMuPDF/PaddleOCR 降级状态机和 mixed-PDF 页面级合并。
- 在 parse API 入队前 reserve generation，并把 mapper 产物按 generation 原子提交。

阶段 2 不包含 Chunker V2、向量索引切换或 Office 上传入口开放。

## 10. 强制停止声明

阶段 1 已完成，当前实施已停止。未收到用户明确回复“批准阶段 1，进入阶段 2”前，不会接入默认 MinerU parser，不会修改降级策略，也不会实施任何阶段 2 及后续内容。
