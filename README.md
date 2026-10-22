# 云径 CloudPath

SwiftUI iPhone / iPad 客户端与 FastAPI 后端在本仓库统一维护。`frontend/` 已替换为当前原生工程，最低 iOS 17；原 React 前端位于 `legacy/react-frontend/`，不参与当前构建和部署。

当前原生版本保留已有页面、插图和动效，并接入默认账号、六步个性化引导、PDF 上传、多资料课程、诊断、课程生成、教材答疑和学习进度恢复。真实 OCR、向量检索和模型服务需要单独配置；没有配置时会显示所缺服务。现有生物课程可直接导入，无需重新解析或生成。

## 仓库结构

```text
LLM_Study_App/
├── frontend/BookCourse.xcodeproj   # SwiftUI 主客户端
├── frontend/App/                  # App 入口与全局路由
├── frontend/Core/                 # 状态、持久化、网络与数据适配
├── frontend/DesignSystem/         # 共享组件、字体和视觉样式
├── frontend/Features/             # 课程、学习、发现、笔记与个人页
├── frontend/Resources/            # 随 App 发布的课程素材和原生资源
├── frontend/Tests/、UITests/       # XCTest 与原生 UI 测试
├── frontend/scripts/              # 构建、测试和模拟器启动
├── backend/src/adaptive_learning/ # FastAPI、OCR、RAG 与课程设计
├── backend/tests/                 # 后端测试与隔离联调适配器
├── deployment/                    # 后端和模型服务部署工具
├── docs/                          # 整体架构与接口说明
└── legacy/                        # 历史 React 实现与旧版项目归档
```

## 本地启动

客户端需要 macOS、完整 Xcode、可用的 iOS Simulator 和 Python 3。后端需要 Python 3.11+，可运行于本机或 GPU 服务器。克隆本仓库后即可构建，日常开发不需要原 `study_app_swiftui` 仓库或 Node.js。

首次准备后端：

```sh
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,pdf]"
./scripts/run_native_dev.sh
```

开发脚本默认监听 `http://127.0.0.1:8100`，开启限本机回环访问的默认账号，无需邮箱、SMTP 或 `.env`。首次进入 App 显示“称呼 → 学习目标 → 每日时间 → 学习卡点 → 帮助方式 → 计划支持”六步引导。正常学习数据保存于后端持久化目录。

另开终端，在仓库根目录启动客户端：

```sh
cd frontend
python3 scripts/validate-content.py
./scripts/build.sh
./scripts/run-simulator.sh --device SIMULATOR_UDID --no-build
```

也可以用 Xcode 打开 `frontend/BookCourse.xcodeproj`，选择 `BookCourse` scheme。首次构建需下载已锁定的 ONNX Runtime 和 ZIPFoundation。真机运行需配置自己的签名团队。后端地址可在 App 连接页切换，或通过运行环境变量 `BOOKCOURSE_API_BASE_URL` 设置。

默认账号仅适用于本机开发。局域网真机或远程服务应使用正式账号认证或安全转发；正式服务不会开放本地默认账号入口。详情见 [原生接口与账号边界](docs/NATIVE_CLIENT_P0.md)。

## PostgreSQL 与离线恢复

服务端可选择 PostgreSQL 主存储，未配置时保留本机 SQLite；配置失败不会静默回退。43 张权威业务表通过统一连接入口和显式 SQL migrations 管理，OCR/Studio 可作为独立 Worker 运行，运行部署不依赖 Redis。迁移工具只对停写后的 SQLite 快照向空目标导入，不会自动迁移现有用户数据。配置、租约、文件路径和切换流程见 [PostgreSQL 运维手册](docs/P2_POSTGRES_RUNBOOK.md)。

原生端恢复最近验证成功的账号缓存后即可显示已有学习空间；暂时断网允许本机学习和笔记落盘，恢复网络后重试同步，明确会话失效仍要求重新登录。轻量 bootstrap 按版本复用内置正文，个人进度、笔记与正文分开持久化。现有界面和功能保持原有入口；真实服务接入范围见 [能力矩阵](docs/NATIVE_CAPABILITIES.md)。

## 发现中的内置生物课程

《遗传与进化高频考点课》是永久随代码保存的已有课程，包含 15 个课时、90 张闪卡、45 道练习和 125 张已发布原文页图片。原有 AI 补充及待原文核验标记继续保留。

- 服务端内容快照：`backend/src/adaptive_learning/data/native_biology.json`，发布后端包时包含该文件。
- App 内置发现目录：`frontend/Resources/Data/builtin-catalog.json`；封面与学习素材随 App 打包。
- 发现目录独立于个人课程和进度。导入、学习、删除自己的课程都不会移除这条目录；服务端目录暂时缺失时使用内置条目，刷新不会重复显示。
- 在线导入仍通过后端完成身份绑定，个人进度与客观题评分独立保存；主观题评分和教材答疑需要模型服务。

显式维护内容时，在仓库根目录执行：

```sh
backend/.venv/bin/python backend/scripts/import_native_biology.py \
  --sources frontend/Resources/Data/sources.json \
  --community frontend/Resources/Data/community.json
```

该命令同时更新服务端快照、客户端内置目录和与 App 原文一致的答疑页文本，不读取设备个人记录。内置生物课使用独立的 `book_biology_2` 索引，不复用可能属于其他版本的 `biology-required-2` 索引；首次答疑会按已配置的向量模型建立索引，仍需要向量、重排和文本模型服务。

## OCR、模型与课程个性化

真实新教材流程需要 MinerU/OCR、文本模型服务以及 BGE 向量与重排序模型。按 `.env.example` 配置 `backend/.env`，需要 RAG 时安装后端 `.[rag]` 依赖。模型密钥只保存在服务器配置中。

六步偏好已进入后端有效课程配置、教学单元、每日计划及答疑策略。当前 SwiftUI 保留已接入的课程展示；新教学单元的分段界面、服务端每日计划清单、建议确认及撤销界面仍属后续接入范围。详见 [个性化后端与接口](docs/NATIVE_CLIENT_P0.md) 和 [客户端接入边界](frontend/docs/CLOUD_INTEGRATION.md)。

## 测试

```sh
cd backend
.venv/bin/pytest
cd ../frontend
./scripts/test.sh --device SIMULATOR_UDID
# 实际 HTTP / SQLite / Swift 客户端联调，临时服务监听 18101
./scripts/test-cloud.sh --device SIMULATOR_UDID --only BookCourseTests/CloudTests
# 本地默认账号、六步引导、已有课程导入与重启恢复
NATIVE_E2E_LOCAL_LOGIN=1 ./scripts/test-cloud.sh --device SIMULATOR_UDID \
  --only BookCourseUITests/CloudAccountUITests/testLocalDefaultAccountEntersHomeWithoutEmailAndSurvivesRelaunch
```

联调脚本自动定位同仓库 `backend/`，使用临时数据库；只有外部 OCR/模型适配器被替换。普通开发数据不会被清空。已有完整教材测试依赖未随仓库发布的原始 OCR 产物，测试统计与实际边界见 [迁移验收](docs/SWIFTUI_MIGRATION.md)。

## 后端部署

4090 / Linux 服务器部署 FastAPI、OCR 和模型服务；SwiftUI App 在 macOS 上构建与分发。后端不再加载 React `dist` 或提供网页路由回退，`/` 返回 API 信息，`/docs` 为接口文档，`/api/health` 为进程健康检查，RAG 就绪情况见 `/api/rag/health`。

服务管理使用 [run_backend.sh](deployment/server/run_backend.sh)，按实际服务器配置 `APP_ROOT`、`RUNTIME_ROOT` 和 `SECRET_FILE`；部署说明见 [API 服务部署](deployment/server/README.md)。PDF、OCR 文本、索引、模型、账号与学习数据库属于运行数据，不随前端迁移复制。

## 进一步阅读

- [迁移来源、功能边界与回退](docs/SWIFTUI_MIGRATION.md)
- [原生客户端构建与资源说明](frontend/README.md)
- [原生客户端接口、个性化与持久化](docs/NATIVE_CLIENT_P0.md)
- [整体技术架构](docs/TECHNICAL_ARCHITECTURE.md)
- [客户端第三方许可与内容来源](frontend/THIRD_PARTY_NOTICES.md)
- [React 前端归档说明](legacy/react-frontend/README.md)

历史文档与截图仅代表记录时点。仓库没有覆盖所有业务代码与教材内容的统一开源授权；依赖、模型和教材的许可分别以对应来源记录为准。
