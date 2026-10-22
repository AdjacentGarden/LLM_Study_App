# React 前端历史归档

该目录保存迁移前 `LLM_Study_App/frontend/` 的 React / TypeScript / Vite 实现。当前主客户端已位于根目录 `frontend/`，采用 SwiftUI；启动、构建和验收以根 README 为准。

归档基线为 `8017c4b449b677512cb85b0578b5500134d22df7`。本目录不参与当前 App 或后端的构建，也不提供默认网页入口。`PROJECT_README.md` 是迁移前项目文档快照，其中的路径、界面截图、测试数量和部署命令均为历史记录。

`deployment/publish_refinement_20260905.sh` 是原有的一次性 React 发布脚本，包含历史服务器和 staging 路径，仅保留用于追溯，不应用于当前 SwiftUI / API 部署。

生物课程的当前内容快照保存在 `backend/src/adaptive_learning/data/native_biology.json`，App 的永久发现目录位于 `frontend/Resources/Data/builtin-catalog.json`。它们不从本归档目录动态加载，也不依赖浏览器或个人账号的历史数据。
