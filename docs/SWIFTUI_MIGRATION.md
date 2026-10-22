# SwiftUI 主前端迁移

## 来源与目录

迁入来源：`study_app_swiftui` 的 `6c6bcf80eab8ca35705f893cc71875fac3c22daf`；后端及原 React 基线：`LLM_Study_App` 的 `8017c4b449b677512cb85b0578b5500134d22df7`。实施分支：`codex/swiftui-frontend-replacement`。

SwiftUI 的 713 个受版本管理的文件完整迁入 `frontend/`。原 React 的 99 个文件迁入 `legacy/react-frontend/`，旧的一次性网页发布脚本归档到该目录的 `deployment/`。此前已存在的 `legacy/cloudpath/` 继续作为历史项目保存。

当前主前端由本仓库维护，`study_app_swiftui` 仅作为此次迁入的来源记录。Xcode 工程、Swift 源码、Resources、锁定的 Swift Package 配置、测试和许可文件均包含在仓库内，不需要旁边存在原 SwiftUI 仓库。正常构建需要联网解析或使用本机已下载的锁定第三方依赖。

原页面、动效、字体、Bundle ID `com.littleri.bookcourseai.swiftui`、后端地址、账号隔离、本地持久化格式及后端数据位置沿用现有实现。迁移中的源码改动限于发现页内置目录和同仓库路径适配；不清空设备数据，不导入浏览器个人资料。

## 主入口与后端

在 `backend/` 启动 `scripts/run_native_dev.sh`，在 `frontend/` 构建并运行 `BookCourse.xcodeproj`。根 README 提供完整命令。联调脚本默认定位 `../backend`，临时测试服务监听回环端口 18101。

FastAPI 移除 React 静态挂载和 SPA 回退；根路径返回 API 信息。服务器启动脚本不再设置 `FRONTEND_DIST_DIR`。既有 PDF、媒体、账号、OCR、课程、答疑和学习记录接口仍保留。Docker 构建目标继续是后端。

## 永久生物课程

`native_biology.json` 是服务端已有生物课程快照，保留来源哈希、15 个课时、90 张闪卡、45 道练习和原有核验状态。客户端随包保留素材与 125 张原文页图片；缺失的教材配图继续记录为缺失，不从其他教材补造。

新增 `Resources/Data/builtin-catalog.json`，内容由后端生物快照的公共目录条目生成。账号模式在联网前加载内置目录，再按稳定课程 ID 合并服务器条目。空目录响应不会清空内置课程，刷新不会重复创建条目，网络异常仍保留已加载的课程入口。离线目录展示不冒充在线导入成功：导入工作区和保存个人进度仍需后端。

内置目录与个人工作区分开。导入、练习、删除个人课程和重启服务都不修改内置快照。重新导入创建或恢复自己的课程资料，演示学习状态不会当作用户进度。

显式更新快照时，运行 `backend/scripts/import_native_biology.py`，输入同仓库 `frontend/Resources/Data/sources.json` 与 `community.json`。脚本同时生成服务端内容与 App 内置目录；内容校验拒绝缺失目录、封面、原文页或已知资源哈希变化。

## 验收记录

本次验证从迁移后的目录执行，未引用原 SwiftUI 仓库源码或其构建缓存：

- 迁入来源的 713 个文件无遗漏；页面与动效源码、图像、字体和既有课程素材保持来源哈希，差异仅为路径、目录兜底、测试及文档。
- 内容校验 1835 项通过，包含 125 张原文页哈希、模型和检索资源、课程数量、前后端目录一致性及源内容哈希。
- Git 忽略规则显式允许 App 的 `Resources/Data/*.json`，防止运行数据的 `data/` 规则排除原生内置内容；已从暂存区导出干净目录，再次通过全部 1835 项内容校验，并确认暂存源码与已测试工作区一致。
- 锁定的 Swift 依赖在新目录解析成功；`xcodebuild test` 从新 `frontend/` 编译 App 和测试目标。
- iPhone 原生单元 / 集成测试共 74 项，73 通过、1 项本地默认认证模式专项跳过，0 失败；包含 PDF → 诊断 → 课程 → 答疑 → 进度恢复的实际 HTTP 联调，以及生物课程导入与偏好恢复。
- 后端回归 443 项通过、5 项可选完整教材测试跳过、1 项缺少原始 OCR 产物的既有测试排除。新增回归覆盖 API 入口及未知网页路由、生物课程删除后目录保留和数据库重开恢复。
- Ruff 与 shell 语法检查通过。
- 后端 wheel 构建成功，并在临时目录独立安装后读取生物课程，确认 15 个课时、90 张闪卡和 45 道练习随分发包存在。原有打包规则曾忽略该 JSON，已通过显式 `force-include` 修正。

- iPhone 17 Pro（iOS 26）与 iPad Pro 11-inch M5（iOS 26）各通过 1 项完整 UI 流程：默认登录 → 六步个性化（含草稿恢复）→ 生物课程导入 → 学习 → 重启 → 发现页仍显示内置课程。截图保存在测试结果附件中。

测试命令及输出存放于本机忽略的 `output/migration-*`，没有进入 Git 提交。真实设备签名、iOS 17 runtime、麦克风和 Pencil 硬件未在本轮新增验收。

真实 OCR / GPU / 邮箱 / 模型服务仍需外部环境。隔离联调仅替换这些外部适配器，使用实际 HTTP、SQLite、诊断状态机、课程编排和 Swift 客户端。既有测试 `test_real_biology_artifact_has_six_verified_body_chapters` 依赖未随仓库发布的 `artifacts/ocr/biology-20260829/pages.jsonl`，会单独标注。

服务端教学单元、每日计划和调整建议接口已实现；SwiftUI 新单元分段呈现、服务端任务清单、建议确认和撤销界面仍待后续接入。本次迁移不将其标记为已完成。

## 回退

在独立回退分支上使用 Git revert 撤销本次迁移提交，可恢复旧前端目录和入口；不要用强制推送或清空运行数据回退。原 React 完整代码也保留在 `legacy/react-frontend/`，迁移前根 README 位于其中的 `PROJECT_README.md`。

本次交付限代码迁移、配置、验证和分支推送。远程 4090 部署、主分支合并、App 签名及正式分发需要对应环境的发布操作。
