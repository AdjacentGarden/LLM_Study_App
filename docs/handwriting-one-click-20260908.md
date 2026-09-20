# 手写笔记一键整理（2026-09-08）

## 本次变化

正常路径只需点击“完成并整理”：先保存矢量笔画，再提交一个 `complete` 后台任务，依次进行视觉转写、对照原图复核转写、教材 RAG 检索、整理补全、独立调用模型复核。正常共四次模型调用；复核不通过最多修订一次并重新复核（另两次），仍不通过不展示为成功。未调用 MiniMax 图片或视频生成。

识别复核只检查笔迹与转写是否一致，不用教材替用户“改写”错误观点；教材校对在后续独立阶段进行。复核提示明确检查并行/先后、因果、否定、条件、范围、数量和例外，但这不等于保证大模型判断完全正确。

任务持久化在原 studio_jobs 表，无数据库迁移。前端显示阶段进度，刷新可通过任务列表恢复结果；原始笔迹不被替换。整理结果随任务自动保存，“保留整理版”仍是可选的采纳动作，不是生成必经步骤。

视觉复核仍返回不确定文字、待确认标记或空白时，任务停在 `needs_confirmation`，不继续生成推测性教材补全；用户仅在这个异常分支编辑文字并点“确认并继续整理”。旧 recognize/improve 接口与历史结果兼容。

重复提交按原请求指纹去重；整理开始与结束都检查笔记版本；更新后的笔记不能采纳旧任务结果。

## 已执行验证

- 后端完整 pytest：347 项通过（含 studio 39 项）。
- Ruff 与所改后端模块 mypy：通过。
- 前端单元测试：26 项通过；TypeScript/Vite 生产构建通过。
- WebKit 浏览器组件测试 `frontend/e2e/studio-complete.spec.ts`：3 项通过，覆盖一次点击成功、无法辨认后的确认续跑、保存失败禁止 AI 请求。使用真实组件和浏览器操作、模拟 API，不是线上大模型测试；临时前端测试服务已关闭，未启动本地后端。
- 实时 4090 浏览器测试 `frontend/e2e/learning-studio.spec.ts`：5 项通过，其中一项实际调用 PUCODING 完成一键手写整理，其余涵盖保存、撤销重做、刷新恢复、隔离、窄屏、保存失败和模拟媒体失败。

## 部署与真实模型联测（用户要求重试后完成）

2026-09-08 初次连接被拒绝；用户要求重试后，`10.249.186.201` SSH 已恢复。确认没有进行中的 studio 任务后，完成增量部署与后端重启，并恢复 18100 SSH 预览通道。仅部署 studio.py、studio_models.py、api/studio_routes.py 和 frontend/dist，未提交 Git，未改 API Key 或模型配置。

- 应用目录：`/data1/zhenghang/adaptive-book-ocr/app`。
- 部署前代码及前端备份：`/data1/zhenghang/adaptive-book-ocr/release-backups/handwriting-one-click-20260908-ftprR3`。
- 最终后端 PID：336740；健康检查通过；远端三个后端文件 SHA256 与本地一致。
- 前端资源：`index-Dc0JwI4y.js`，`index-DIip5-vd.css`。
- 识别、复核和文字整理均使用 `https://pucoding.com/v1`、`grok-4.3`。

实测还发现并修复了中转接口兼容问题：Responses JSON 模式要求用户输入中显式包含 JSON，只有系统提示包含仍可能被 HTTP 400 拒绝。现于 studio 的用户提示末尾统一追加 JSON 输出要求，并增加回归测试。前两轮失败保留在独立测试账号的任务历史；没有掩盖失败或把它们计为通过。

最终执行：

```sh
STUDIO_LIVE_E2E=1 SOCIAL_E2E_BASE_URL=http://127.0.0.1:18100 npx playwright test e2e/learning-studio.spec.ts --output=test-results-handwriting-one-click-live-fixed
```

最终 5 项通过（约 1.5 分钟）。真实任务 `8c530e301bdb476e96ada04f895d7981`，笔记 `studio_1788830570050_yrv27aolze`：四笔 DNA，转写为 DNA，一键后台完成耗时 72.2 秒，原笔画仍为四条，采纳并刷新后整理结果与原笔画一致。结果列出教材第 49、53 页依据，并明确“仅记录关键词，不足以判断掌握情况”。已检查本次整理内容：复制特点正确写为“边解旋边复制”，不再出现旧样例的先后误写；这是一例通过，不代表复杂中文手写、公式、图示及全部知识点已获得准确性保证。

没有调用 MiniMax 生图或视频。最终活动 studio 任务为零。浏览器截图：`frontend/test-results-handwriting-one-click-live-fixed/learning-studio-real-handw-8ed0a-rounded-improvement-on-4090/ink-reviewed-tablet.png`。

预览：`http://127.0.0.1:18100/?device=iphone-16&release=handwriting-one-click-20260908`。
