# 课程预览 API

课程预览用于先查看真实教材生成的讲解、闪卡和练习，不需要代替读者填写学习背景或诊断答案。它使用现有教材解析、课程任务队列和完整教学审核流程。

## 部署与调用

更新后重启后端，并安装包含预览入口的原生客户端。沿用当前账号的登录会话，依次调用：

1. `POST /api/native/uploads`：上传 PDF，取得 `book_id`。
2. `POST /api/books/{book_id}/process`，轮询 `GET /api/books/{book_id}/status`，等待 `ocr_ready`。待复核的教材必须先完成正常复核或重试。
3. `POST /api/native/books/{book_id}/prepare`：保存完整目录并建立该教材的检索索引。
4. `POST /api/workspaces` 创建课程，再以 `POST /api/workspaces/{wid}/resources`、`{"book_id":"..."}` 添加教材。
5. `POST /api/workspaces/{wid}/resources/{rid}/preview-session`：取得预览会话 `session_id`。
6. `POST /api/interviews/{session_id}/course-jobs`，请求体为 `{"chapter_id":"真实章节标识","request_id":"本次操作的唯一标识"}`。
7. 轮询 `GET /api/interviews/{session_id}/course-jobs/{job_id}`。`succeeded` 才能展示已审核课程；失败时保留任务并按原接口显式重试。

可以先生成一章，其余章节保留完整目录并显示尚未生成。重新加载 `/api/native/bootstrap` 或对应 resource 后，原生客户端会展示实际生成的讲解、闪卡及练习。

## 状态与记录

- 预览会话返回 `purpose="preview"`、`phase="preview_ready"`，不使用诊断完成的 `complete` 状态。普通诊断会话的用途为 `learning`。
- resource 与 bootstrap 的完整、轻量响应均提供 `session_purpose`；客户端据此显示“课程预览 / 未做诊断”。
- 新预览的作答、自报证据和诊断观察记录均为空，掌握度未知；数值先验不是读者成绩。实际提交练习或闪卡反馈后，才形成相应学习记录。
- 同一 resource 再次请求预览会恢复原预览会话。已有普通诊断或学习会话返回 `409`，不能覆盖；需要单独创建预览课程。
- 预览不会修改账号的学习偏好或其他课程的会话，也不会绕过原文证据检查、教学完整性审核和独立事实审核。

## Apple Vision 提取缓存

提取身份包含 Swift helper 源码 SHA-256、有效 DPI，以及配置自定义 helper 时的二进制 SHA-256。身份保存在原始 metadata、`ocr_capabilities`、质量报告和提取 checkpoint 中。旧版无身份的 Apple Vision 缓存，以及 helper、DPI 或自定义二进制发生变化的缓存，均不能被重试复用；原提取 attempt 保留。相同身份的正常复核恢复仍可复用已完成的提取。

## 已完成教材的显式修正

- 原上传账号可调用 `POST /api/books/{book_id}/process/reprocess`，重新处理已完成 OCR 的教材。正在排队、运行或等待重试的任务返回 `409`；普通 `/process/retry` 规则保持不变。旧原文与提取 attempt 保留，相同提取身份可复用 OCR 并重新复核。此入口没有请求幂等标识：一个任务完成后再次主动调用，会启动新的处理操作。
- 原上传账号可调用 `POST /api/books/{book_id}/structure?refresh=true` 显式重建目录；默认调用仍复用已有目录。其他账号即使取得教材，也不能刷新原上传教材。
- 刷新共享原文目录不会重写个人章节快照。对仅用于预览、尚未实际学习的课程，应通过现有 resource `chapters` 接口确认新目录，原预览会话保留历史、新目录重新建立预览。普通诊断与原有学习记录不应由运营脚本覆盖。
