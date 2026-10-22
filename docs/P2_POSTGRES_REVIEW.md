# P2 与 PostgreSQL 实施独立审核记录

日期：2026-10-22。实施由用户指定的 GPT-6.1-sol / high 子代理执行；本文由主代理记录独立验证结果。保留上一轮未提交的 P1 修复。未提交 Git、未部署生产、未切换用户数据。

## 验收范围

对应 [实施计划](P2_POSTGRES_IMPLEMENTATION_PLAN.md) 的 P2-7～10 与 PostgreSQL 阶段 1～4。数据库切换步骤见 [运行手册](P2_POSTGRES_RUNBOOK.md)。Redis 已从活动部署中移除，不作为后续接入项。

主代理使用独立 PostgreSQL 17 临时实例，实施测试与审核测试使用不同数据库。临时 PostgreSQL 实例在验收后已停止，未配置常驻服务。没有把 mock 数据库结果当成 PostgreSQL 验收。模型调用与 OCR 内容生成仍使用既有测试适配器；未验收真实 GPU、外部模型质量或生产负载。

## 独立审核发现并修复的问题

- SQL migration 按分号拆分会误拆注释，导致真实 PostgreSQL 首次建库失败；改为正确执行整份无参数 SQL。
- SQLite 的字符串查询、聊天已读位置冲突更新与 PostgreSQL 方言不兼容；改为可验证的 SQL，并复用原 API 测试覆盖。
- 数据库连接归还池后仍可访问原始连接/游标；增加生命周期保护及回归。
- Studio 的异常处理可能二次失败退出 Worker，旧租约可能覆盖媒体文件；增加外层恢复、数据库结果和文件发布的租约检查。
- 完整 API 同时启动暴露出遗漏的 `studio_note_editions` 运行时建表；补入版本迁移和导入清单，并增加 schema inventory 测试。
- 旧 URL 兼容逻辑可能把非默认端口与默认端口的身份凭证混用；限制候选地址为同一服务来源，增加端口隔离回归。
- OCR 的通用质量评分与实际页面启发式评分使用不同尺度；分别配置，保留原有默认判定行为。

- 独立 Worker 拆分后，健康接口只探测 API 内线程会阻断原生 OCR 入口；增加跨进程 15 秒过期心跳，保持原客户端字段兼容，验证上线、退出和过期状态。

## 性能与持久化实测

主代理使用隔离本地 API，向一个账户导入相同内置课程，记录未压缩 HTTP 响应字节和 SELECT 次数（包含认证）。这些是功能基准，不是生产压测。

| 工作区数 | 完整 bootstrap | 元数据 bootstrap | SELECT 次数 |
|---:|---:|---:|---:|
| 1 | 698,958 bytes | 2,658 bytes | 11 |
| 5 | 3,488,616 bytes | 7,012 bytes | 11 |
| 20 | 13,949,849 bytes | 23,355 bytes | 11 |

首次打开还需要下载一份约 0.7 MB 的共享正文；元数据大小不是完整冷启动总流量。相同版本正文可在后续资源中复用。非内置个性化课程正文按资源/版本缓存。

客户端回归验证 20 份课程共享一份正文；单资源翻页不重写正文，只改变对应资源记录，实际写入小于 4 KB。测试仍验证本地磁盘写入失败向笔记编辑器传播。

## 测试记录

- 实施前后端基线：448 passed、5 skipped、1 failed；失败为外部 OCR `pages.jsonl` 缺失。
- 主代理最终全量后端（启用真实 PG 专测）：465 passed、6 skipped、0 failed。跳过项为可选外部 OCR/全书评估数据缺失。
- 原有账户、验证码、社交、社区、Native、Studio、学习设计、Assessment、OCR 合同在 PostgreSQL 上复用：200 passed。仓库已有 `tests/pg_contracts.py` 和 CI 命令可复现。
- 主代理额外迁移回归：导入中途外键失败整体回滚；未确认停写时不创建备份；损坏 JSON 拒绝导入且回滚；归还连接后的游标拒绝读取。4 项通过，见 `backend/tests/test_postgres_review.py`。
- 双真实 API 进程：A 登录并创建工作区，B 使用同一 Cookie 立即读取成功；独立 `worker --queue all` 启动与 SIGTERM 正常退出通过。两个 API 均能感知外部 Worker 心跳，OCR 可用状态随 Worker 启停变化；无 Worker 时不会伪报可用。
- Python wheel 构建通过，已检查包含 001～005 SQL migrations、内置课程与问答 JSON。
- `uv lock --check --offline` 通过；锁文件仅新增 PostgreSQL 驱动/连接池及 tzdata 包，没有升级或删除已有包版本。实测安装的 psycopg 3.3.6 / psycopg-pool 3.3.3 与锁文件一致。
- Ruff `src tests` 全部通过；strict mypy 63 个源文件全部通过；`git diff --check` 通过。
- 最新 Swift 单元/集成：84 项执行，83 passed、1 skipped、0 failed；所跳过的本地默认账户用例在独立开启 `NATIVE_E2E_LOCAL_LOGIN=1` 的服务上补测通过，合计 84 个用例均有通过记录。新增非默认端口身份/缓存隔离回归通过。
- iOS 全套 UI：32 项执行，31 passed、1 skipped、0 failed；所跳过的本地默认账户 UI 在独立配置服务上补测通过，合计 32 个 UI 用例均有通过记录。覆盖课程、学习、复习、笔记、账号登录和重启恢复。

补测说明：新增端口隔离测试第一次因测试域名含大写 UUID、预期值未规范化而失败，已改为小写测试域名；跨端口身份隔离断言本身通过。本地默认账户 unit 与 UI 必须分两次 `test-cloud.sh` 启动独立 fixture 服务：共用服务时 unit 会完成公共账户的新手引导，导致 UI 的初始状态预期不成立。

本地复现命令及 PG 隔离 schema 规则见运行手册。GitHub Actions 配置已添加，但没有把本地执行结果称为远程 CI 通过。

## 上线边界

- 阶段 4 交付迁移工具、失败回滚验证与隔离演练，不代表生产迁移完成。迁移前必须停止所有写入并备份文件与签名材料。
- PG 接收新写入后不能直接回切旧 SQLite；工具不提供自动反向增量迁移。
- 多步骤业务事务暂用保守的事务 advisory lock 保持既有一致性；这是已知写入热点，不能据此宣称无限水平扩展。
- 多机部署仍需要一致的文件路径/共享产物、签名密钥和经过配置的 OCR GPU 镜像。
- 本机没有 Docker，未执行 Compose 容器部署；真实 GPU OCR、外部模型效果、真机麦克风/Pencil 与生产性能不在本轮实测范围。
