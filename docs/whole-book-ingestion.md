# 整书解析与部署

整书处理包括 OCR、检索索引、章节与全书摘要、知识点、诊断题和语义审核。默认仍使用 MinerU；快速模式需要显式设置 `OCR_BACKEND=paddle-gpu`。

## 安装与运行目录

应用环境使用 Python 3.11+：

```sh
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,pdf]'
cp ../.env.example .env
```

`pdf` 包含 PyMuPDF、pypdf 与 pypdfium2；不会给普通 macOS 应用环境安装 CUDA。需要向量检索时另装 `.[rag]` 并填写索引与嵌入、重排模型路径。只配置部分 RAG 路径会在索引阶段报错；全部留空时可整理章节内容，但不建立教材答疑索引。

GPU OCR 可使用独立 Python 环境。按 [MinerU 官方文档](https://opendatalab.github.io/MinerU/) 安装适合驱动的 MinerU/PyTorch CUDA、OpenCV 和模型权重；快速服务依赖 `mineru.model.ocr.pytorch_paddle.PytorchPaddleOCR`、`torch`、`numpy`、`cv2`、`pypdfium2`。应用环境与 OCR 环境的 Python 版本、依赖和模型位置由部署者选择。两个 GPU 模型必须实际位于 CUDA，快速服务不回退到 CPU 或其他识别器。

[启动脚本](../deployment/server/run_backend.sh) 从自身位置定位仓库，默认数据位于 `APP_ROOT/data/ocr-runtime`。支持：

| 配置 | 默认与用途 |
| --- | --- |
| `APP_ROOT` | 当前仓库根目录 |
| `RUNTIME_ROOT` | `APP_ROOT/data/ocr-runtime`；运行数据、日志、PID |
| `ENV_FILE` | `APP_ROOT/backend/.env`；兼容旧 `SECRET_FILE`，无本机文件时可读取 `RUNTIME_ROOT/secrets/adaptive-book.env` |
| `APP_PYTHON` | `APP_ROOT/backend/.venv/bin/python`；运行应用 |
| `OCR_PYTHON` | 与 `APP_PYTHON` 相同；可指定独立 OCR 解释器 |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8100` |
| `BACKEND_STARTUP_TIMEOUT_SECONDS` | `300`；包含常驻模型加载的启动等待上限 |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1`；受信任反向代理地址 |
| `GPU_PROFILE_FILE` | 可选 `RUNTIME_ROOT/config/gpu.env` |
| `INGESTION_PROFILE_FILE` | 可选 `RUNTIME_ROOT/config/ingestion-performance.env` |

环境文件与可选 profile 是受信任的 shell 配置，不是上传内容。脚本在启动父进程中加载配置，后台子进程继承相同的端口与环境。`PYTHONPATH` 保留调用者配置并加入 `backend/src`，不依赖其他机器的 runtime overlay 或固定 GPU 编号。CUDA 设备、模型缓存与 MinerU 配置可通过 profile 显式导出。

```sh
# 在仓库根目录执行；先按实际环境编辑 backend/.env。
./deployment/server/run_backend.sh start
./deployment/server/run_backend.sh status
./deployment/server/run_backend.sh stop
```

前端构建后可由 FastAPI 同源提供；开发模式见 [README](../README.md)。健康检查为 `/api/health`；RAG 就绪情况另查 `/api/rag/health`。停止应用后 OCR 常驻服务仍可复用，必要时使用对应管理脚本的 `stop --port <端口>` 单独停止。

## 两种 OCR 与常驻服务

| 模式 | 能力与适用范围 |
| --- | --- |
| 默认 MinerU (`vlm-auto-engine`) | 沿用 MinerU 文档重建流程。具体版面、表格、公式能力取决于安装后端与模型。 |
| 快速 `paddle-gpu` | PP-OCRv5 文本检测和识别；200 DPI 渲染、识别批量 32、FP16 autocast，渲染线程与 GPU 推理交叠。保留文字行、框、置信度与原始来源；不执行 VLM 版面分析、表格结构还原或专用公式识别。跨栏阅读顺序未经验证。 |

快速模式的章标题提示仅依据真实 OCR 大字几何和目录原文匹配生成，标明推断来源并保留原识别行。它不能取代复杂排版教材的完整解析。报告携带实际 `ocr_capabilities`、`paddle_gpu` 方法、完整页号和空页清单；未识别页进入 `missing` 质量门控，不能用较快耗时掩盖漏页。自动文本质量分属于异常信号，不是人工真值 CER。

默认不启动 GPU 常驻服务。启动脚本仅在 `OCR_WORKER_ENABLED=true` 且显式选择 `paddle-gpu` 时管理快速服务；`FAST_OCR_MANAGED=false` 可复用已启动的 loopback 服务。常驻服务启动失败或请求失败直接报错，不隐式加载第二个本地模型。

```sh
# backend/.env 或显式 profile 中的示例配置
OCR_WORKER_ENABLED=true
OCR_BACKEND=paddle-gpu
FAST_OCR_API_URL=http://127.0.0.1:18201
# 允许收窄为原文支持的诊断答案，仍强制语义审核。
DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE=true
# OCR_PYTHON=/path/to/gpu-environment/bin/python
```

可直接运行 `deployment/server/fast_ocr_service.py start --port 18202` 建立隔离测试实例。PID、锁和日志按端口区分，不影响其他实例。API URL 设置端口时，脚本按该端口管理服务；同时设置不一致的 `FAST_OCR_PORT` 会报错。托管服务的 URL 使用 `127.0.0.1` 或 `localhost`；自行管理的服务仍须符合客户端 loopback HTTP 限制。

服务仅监听 loopback，独立启动时默认只从 `RUNTIME_ROOT/input`、`data`、`benchmarks` 读取 PDF，向 `data`、`benchmarks` 的全新目录写入结果。应用启动脚本会把其中的 `data` 配置为实际 `APP_DATA_DIR`，支持环境文件中的自定义数据位置。自定义 `FAST_OCR_SOURCE_ROOTS` / `FAST_OCR_OUTPUT_ROOTS` 可配置绝对目录列表，在 Linux/macOS 用 `:` 分隔；解析后的实际路径必须位于配置目录内，符号链接或 `..` 不能逃逸。不要把根目录或整个个人目录当成白名单。

保留 MinerU 并复用其常驻模型时设置：

```sh
OCR_BACKEND=vlm-auto-engine
OCR_WORKER_ENABLED=true
MINERU_RESIDENT_ENABLED=true
MINERU_API_URL=http://127.0.0.1:18200
# 可选且有数值校验；不接受任意 CLI 参数。
MINERU_ENGINE_ARGS_JSON='["--gpu-memory-utilization",0.5,"--session-len",8192]'
```

只填写 `MINERU_API_URL` 而不启用托管开关时，应用连接已启动的 MinerU API。GPU 资源有限时分别使用两种模式，避免同时加载不必要的常驻模型。

## 后台准备与恢复

1. 上传 PDF 后，通过 `/api/books/{book_id}/process` 入 OCR 持久化队列，查询 `/api/books/{book_id}/status`。OCR 工作者使用 SQLite 租约与重试；崩溃后的过期任务可恢复，`ocr_review_required` 需检查质量报告后再重试。
2. OCR 达到 `ocr_ready` 后，持有上传归属的用户调用 `POST /api/books/{book_id}/prepare`。接口立即返回排队状态，不把整书模型调用放在浏览器 HTTP 请求里等待。
3. `GET /api/books/{book_id}/prepare` 返回 `status`、`stage`、`next_step`。阶段依次为 `indexing`、`structuring`、`diagnostics`；成功为 `status=succeeded`、`stage=ready`。
4. 阶段进度存于 `APP_DATA_DIR/state/preparation.sqlite3`。服务重启会恢复中断任务，已完成阶段通过检查点复用；当前未完成阶段可能重新执行。失败后同一上传者可调用 `POST /api/books/{book_id}/prepare?retry=true`，从失败阶段继续。

准备状态和写入接口检查上传归属。保留原 PDF、OCR 规范化输出、索引和 SQLite 文件，才能在重启后继续使用。当前部署使用一个 Uvicorn 工作者；准备队列有跨进程锁，启动脚本固定 `--workers 1`。

## 章节并发与模型配置

`BOOK_LLM_CONCURRENCY=6` 控制进程共享的章节模型线程池，允许 `1..8`；修改后重启。章节摘要、诊断出题和语义审核共享总并发限制，输出仍按章节排序。嵌套调用在当前工作线程中运行，避免线程池互相等待；排队时间计入模型期限，失败不会返回部分整书结果。

`TEXT_LLM_THINKING_ENABLED` 留空时沿用服务商默认；设 `false` 时对代码支持的模型显式关闭思考。`TEXT_LLM_REASONING_EFFORT` 可选。模型供应商、模型名及密钥由环境配置，未知模型不会被强行套用其他厂商的参数。并发数、模型响应时间及限流共同影响整书耗时。`DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE` 默认 `false`，保持既有答案固定；显式启用时可把答案收窄为原文支持的表述，仍必须通过语义审题。

## 测试与计时

```sh
cd backend
.venv/bin/pytest tests/test_ocr_job.py tests/test_ocr_jobs.py tests/test_fast_ocr_service.py \
  tests/test_server_launcher.py tests/test_book_parallel.py tests/test_preparation.py
```

小 PDF 集成测试使用真实渲染和 HTTP 通信，但识别器是明确的测试替身；它验证队列、页面覆盖、原文保留、失败行为和质量报告，不证明 GPU 识别准确率。真实 GPU 验收应使用实际模型与授权 PDF，从新输出目录重新执行，避免复用 OCR 或索引缓存。

隔离 OCR 样例：先配置 `RUNTIME_ROOT`、模型和 CUDA 环境，再启动常驻实例，执行：

```sh
# 在仓库根目录执行；输入 PDF 自行放入 runtime/input。
export FAST_OCR_API_URL=http://127.0.0.1:18202
export PYTHONPATH="$PWD/backend/src${PYTHONPATH:+:$PYTHONPATH}"
"${OCR_PYTHON:-backend/.venv/bin/python}" deployment/server/fast_ocr_service.py start --port 18202
backend/.venv/bin/python -m adaptive_learning.ingestion.ocr_job \
  --source "$RUNTIME_ROOT/input/book.pdf" \
  --output "$RUNTIME_ROOT/benchmarks/ocr-$(date +%s)" --backend paddle-gpu
```

记录常驻服务 PID、模型与参数、源页数、空页/缺页、质量分布、全新输出位置和每阶段耗时。完整流程还应验证新建 RAG 索引、所有章节与全书摘要、知识点、诊断题、语义审核及落盘，不能只以 OCR 结束计时。

预热后的整书处理时间不包含上传、首次下载、模型载入和服务冷启动。冷启动时间单独记录；不同教材的页数、文字密度、复杂排版及模型服务延迟不同，历史样本不能作为固定一分钟内完成的承诺。
