# CloudPath API 服务部署

当前 `frontend/` 是原生 SwiftUI iOS / iPadOS 客户端，在 macOS / Xcode 中构建。GPU / Linux 服务器运行 `backend/`、OCR 和模型服务，不执行 npm 构建，不加载 `frontend/dist`。

`run_backend.sh` 继续提供 `start / stop / restart / status / serve`。调用前按目标机器设置 `APP_ROOT`、`RUNTIME_ROOT`、`SECRET_FILE`；Secret、教材、OCR 产物、检索索引、模型和账号数据库保存在原运行目录。脚本不会迁移或重置学习数据。

```sh
APP_ROOT=/path/to/LLM_Study_App \
RUNTIME_ROOT=/path/to/runtime \
SECRET_FILE=/path/to/secrets/backend.env \
bash deployment/server/run_backend.sh start
```

Secret 中的模型、已发布教材清单和 Python 环境仍需按已有服务配置。脚本包含原部署的模型和数据目录默认值，不能不检查便套用到其他机器。当前后端忽略旧 `FRONTEND_DIST_DIR`，可从 Secret 中移除该变量。

若后端无法直连 SMTP 465，可使用[保留端到端 TLS 验证的 SMTP TCP 隧道](README-smtp-tunnel.md)；SMTP 授权码仍只保存在后端私密配置中。

接口入口：`/` 返回服务信息，`/docs` 提供 OpenAPI 文档，`/api/health` 检查进程，`/api/rag/health` 检查检索服务。普通网页路径与旧 JS 静态资源返回 404；受保护的 PDF / 教材媒体接口继续使用原权限校验。

App 通过可配置的 API 地址连接服务。正式服务使用邮箱账号与有效会话；开发默认账号仅允许本机回环访问，在 production 中不可用。建议通过既有 HTTPS 代理或本机 SSH 转发连接远程服务，具体地址按部署环境配置。

服务端生物课程快照随 Python 包发布，App 的课程目录、封面与原文页图片随原生包分发。课程目录独立于学习数据库；删除个人课程不会从发现页撤下内置课程。

## macOS 本机 OCR

在 Mac 上部署后端时，可使用系统 Apple Vision 识别扫描 PDF 中的简体中文和英文，无需 OCR API 密钥或 CUDA。识别在 **Mac 后端进程**中执行，iPhone App 上传 PDF 后通过现有 OCR 队列取得结果；这不代表已在 iPhone 客户端接入离线 OCR 或 Apple Intelligence。

将以下设置写入本机私密环境配置后，重启后端：

```dotenv
OCR_WORKER_ENABLED=true
OCR_BACKEND=apple-vision
APPLE_VISION_OCR_DPI=200
DOCUMENT_VISION_RESCUE_ENABLED=false
```

后端 Python 环境需要安装 PDF 依赖（`uv pip install --python /path/to/python './backend[pdf]'`），Mac 需要可用的 Xcode Command Line Tools。首次识别时会用 `xcrun swiftc` 编译随 Python 包发布的 Swift helper，缓存在 `APP_DATA_DIR/runtime/apple-vision/`；后续识别直接复用。可在后端启动前运行 `python -c 'from adaptive_learning.ingestion.apple_vision import native_helper; print(native_helper())'` 预编译并检查环境。`APPLE_VISION_OCR_BINARY` 可指定已经编译好的可执行文件绝对路径；留空时使用自动编译。

识别结果保留每行置信度、位置、真实页码、引擎版本和完整页覆盖记录，继续使用原有质量阈值。空白或未识别页进入待确认状态，损坏 PDF 处理失败。`quality_report.json` 的启发式质量分用于筛查异常，不能作为准确率。Vision 文本行模式不执行表格结构还原、专用公式识别或跨栏版面分类；相关能力在报告中明确标为未提供或未验证。

上述配置关闭付费视觉救援，使本机 OCR 无需调用外部模型。需要视觉复核时，可另行配置已验证的视觉模型并开启 `DOCUMENT_VISION_RESCUE_ENABLED`，OCR 初次提取仍由本机 Vision 执行。

## 本地语音笔记

语音转写可安装 `./backend[voice]` 依赖并准备本地 Faster Whisper 模型目录。设置 `VOICE_ASR_PYTHON` 为该环境的 Python、`VOICE_ASR_MODEL` 为模型目录、`VOICE_ASR_SCRIPT` 为 `deployment/server/transcribe_voice_note.py` 的绝对路径；若设置 `VOICE_ASR_PYTHONPATH`，应指向实际存在的依赖目录。Mac 使用 `VOICE_ASR_DEVICE=cpu` 和 `VOICE_ASR_COMPUTE_TYPE=int8`。启动前应以一段真实音频运行该脚本，确认模型能够转写；目录存在仅代表配置齐全。

当前 voice 依赖限制 `av<19`，因为 Faster Whisper 1.2.1 的音频解码调用尚不兼容 PyAV 19。语音在本地转写；随后点击整理笔记会使用配置的文本模型。

## 学习媒体

生图与视频可设置 `STUDIO_MEDIA_REVIEW_MODE=off`：跳过应用自己的教学适用性、方案复核和画面质量审核，服务商返回的有效成品直接展示，并标注未做 AI 质量复核。仍保留服务商错误处理、文件验证、账号权限、预算和超时不重投；笔记审核不变。`strict` 保留原审核，未设置或配置无效时也使用 `strict`。每个任务保存提交时的模式，相同请求编号继续返回原任务；切换模式不会重新提交旧任务。

旧的一次性 React 发布脚本已移至 `legacy/react-frontend/deployment/`。当前仓库迁移不代表已更新远程服务器或完成 App 签名、分发；实际发布需要独立执行对应环境的流程。
