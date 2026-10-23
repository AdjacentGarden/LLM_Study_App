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

接口入口：`/` 返回服务信息，`/docs` 提供 OpenAPI 文档，`/api/health` 检查进程，`/api/rag/health` 检查检索服务。普通网页路径与旧 JS 静态资源返回 404；受保护的 PDF / 教材媒体接口继续使用原权限校验。

App 通过可配置的 API 地址连接服务。正式服务使用邮箱账号与有效会话；开发默认账号仅允许本机回环访问，在 production 中不可用。建议通过既有 HTTPS 代理或本机 SSH 转发连接远程服务，具体地址按部署环境配置。

服务端生物课程快照随 Python 包发布，App 的课程目录、封面与原文页图片随原生包分发。课程目录独立于学习数据库；删除个人课程不会从发现页撤下内置课程。

生图与视频可设置 `STUDIO_MEDIA_REVIEW_MODE=off`：跳过应用自己的教学适用性、方案复核和画面质量审核，服务商返回的有效成品直接展示，并标注未做 AI 质量复核。仍保留服务商错误处理、文件验证、账号权限、预算和超时不重投；笔记审核不变。`strict` 保留原审核，未设置或配置无效时也使用 `strict`。每个任务保存提交时的模式，相同请求编号继续返回原任务；切换模式不会重新提交旧任务。

旧的一次性 React 发布脚本已移至 `legacy/react-frontend/deployment/`。当前仓库迁移不代表已更新远程服务器或完成 App 签名、分发；实际发布需要独立执行对应环境的流程。
