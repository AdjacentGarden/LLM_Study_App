# Demo 增量移植验收记录（2026-10-02）

状态：冻结快照增量实现、前端构建/单测和实际浏览器验收完成。根代理独立构建与真实HTTP复核也通过。

## 来源与目标基线

用户追加授权移植 Demo 后续 UI。来源提交 `dee6d691d08c857c36de82230ea0a13205a11d9a`，`feat: improve course browsing, study UI and settings`，2026-10-02 20:20:43 +08:00。冻结时间 20:28:03，读取 `tmp/demo_delta_20261002/source-snapshot`，247 个 src 文件。root 只读结束复核 `source-recheck.json` 无变化或新增。本轮不从动态来源仓库继续读取/同步。

相对上一轮开始来源清单，本次 source 为 39 个既有 src 变化、9 个新增，无移除。目标已有真实适配由 `tmp/demo_delta_20261002/target-before` 300 文件备份和 SHA256 清单保留。最终目标 src 增量为 38 个变化、9 个新增（含独立目标入口文件），完整路径在 `target-delta-final.json`，最终校验清单在 `target-final-hashes.json`。两个 E2E 文件另计。

## 增量实现与适配保留

- SVG StickerIcon/artwork 与贴纸样式，另补新桌面侧栏云 PNG。
- 首页课程操作区、创建首门课程、课程卡 resize 居中、目录预览模型与封面可选字段。
- 学习页吸顶区域、动态高度预留、目录项和目录专用展示；移动/平板/桌面保持原新 UI。
- 个人页课程选择布局、引导及各工具贴纸图标，保留 AdditionalTools、AccountControls。
- 新 Settings 原页面布局，App/header/router/types 导航接入，展示当前服务端学习偏好与课程数。
- 设置退出调用真实 AccountGate.logout Promise；等待请求完成，错误留在设置对话框，支持重试。pending 禁用确认/取消，忽略 Escape/native back，同步 inFlight ref 防同刻双提交。文案改为当前账号保存、再次登录恢复，不宣称只保存在本机或清空服务端偏好。
- 同步纯 fixture 的物理课程、目录课程、DemoRepository 和显式测试。生产 App/Home/Study 无 demoRepository 运行时引用，bookcourseApi 仍 HTTP 代理，仅 type import fixture 类；无示例课程注入登录账号。

保留上一轮所有权/版本/真实性实现：四个 HTTP repositories、课程 revision ACK/排队保存/失败重试草稿、owner fencing、账号水合门、真实异步任务、真实批注/录音/用户确认稿、页图和原文件下载、服务端积分 reserve/settle/refund、真实报告与 PDF、作答 event_id/实测答题时长、生成练习/FSRS、AdditionalTools。未照搬来源的本机 logout、固定统计、示例 RAG 或初始化演示课程逻辑。模型 Key 客户端环境读取仍禁用。

source main 的入口/样式变化合入目标 DemoDirectEntry，不覆盖账号入口；source repository 仅新增本机 logout 因真实账号语义不采用。api/types 新 cover_url/content_mode/directory_unit_label 与旧 reservation_id/event_id/confidence/response_seconds/CourseSummary 并存。公共资产 734 项约 148MB 为整体差异，不是本轮新增；仅补一张当前 UI 确实使用的侧栏 PNG，未全量复制教材页/RAG资源、依赖、env、Key或模型。未部署、提交Git、改全局配置或清空真实数据。

## 验证

- `npm run build --prefix frontend`：完整 Demo TypeScript 和 Vite 构建通过。剩余提示仅原页面主包超过 500kB。
- `npm run test:demo --prefix frontend`：55 文件 **360 passed**，包含新增目录/物理 fixture 测试、旧 owner/revision/异步保存与动效契约。
- `npm run test:unit --prefix frontend`：**20 passed**。
- root 独立后端 `backend/tests/test_demo_port.py`：**18 passed**，新目录 `tmp/demo_delta_20261002/root-backend-tests`。本轮设置/UI不需要新后端契约。
- Chromium 更新原 E2E：全新 `original-e2e-data`、8193/5193，**1 passed，12.9s**；两真实 MD/CSV、目录编辑、文字笔记/刷新、实际PDF下载、报告空态、未配置QA失败/积分保持、社交入口、A→B→A隔离全部通过。
- Chromium 增量 E2E：`e2e-final-data`、8192/5192，**1 passed，1.2m**；同一主流程加 SVG贴纸、402/820/1440学习页、设置取消、退出pending取消禁用/Escape保持、模拟后端503显示错误且Cookie同user_id、成功退出/刷新回登录、尊重OTP60s限制后真实再次登录恢复课程。测试身份仅隔离 xiaolin/momo；root独立使用achen。

实际命令在 frontend 工作目录设置 `DIRECT_PORT_E2E=1`、`DIRECT_PORT_URL=http://127.0.0.1:5192`，执行 `npx playwright test e2e/demo-delta-20261002.spec.ts --workers=1 --output ../tmp/demo_delta_20261002/browser-evidence`。原流程独立 5193 执行原 spec；其截图复制到 `tmp/demo_delta_20261002/original-browser-evidence`。最初运行的失败是旧按钮文字定位及重复OTP测试限流，已更新定位和有效会话切换；未修改后端限流。

截图长期保留在 `tmp/demo_delta_20261002/browser-evidence/demo-delta-20261002-delta--d5af1-nd-confirms-settings-logout`：delta-study-402/820/1440、delta-settings、delta-logout-failure、delta-logout-refresh、delta-relogin-preserved、账号隔离及原笔记/下载截图。屏幕检查无横向溢出，reduced-motion；手机学习页和设置页面已视觉检查。

## 外部边界

测试无外部Key，规则provider、AUTH_DEMO_MODE和Windows CJK字体显式进程设置；APP_DATA_DIR仅本轮新tmp目录。未用实际外部模型、视觉、OCR、语音或视频生成提供方验证成功，本轮增量没有改变其服务端契约。模拟503仅证明前端真实失败反馈，成功退出/重新登录用实际后端。公网及4090部署未执行。

## 必要侧栏资产后补冻结

首轮 src 快照未含 public 字节，734 项 public 差异初始仅记录 size。视觉检查发现新侧栏引用的 `assets/brand/sidebar-cloud-icon.png` 缺失。root 于20:44后补单资产到 snapshot/public，19,699 bytes，SHA256 `5d871eb2d8c0f7c19418847d5da40a85f3a4d15809097e40acd756cf5ba1d230`；目标复制后SHA一致，记录 `ui-asset-manifest.json`。该来源PNG被Git忽略，无法声称对应提交blob；只读前后确认稳定、size与20:28清单相同、mtime为2026-09-30早于快照。这里不宣称所有public于20:28已哈希冻结。该单张从后补冻结副本复制，不再读取动态来源。

## 根代理独立最终复核

root 独立最终 build 通过；PNG后补后实施代理再次 build 通过。root实际HTTP复核 **7/7**，证据 `tmp/demo_delta_20261002/root-http-review.json`：初始空账号、真实上传、退出会话、访客独立空状态且不能读原账号资料、真实重新登录原账号、偏好与课程/资料保留。退出后访客兼容接口可返回空课程与 preferences null，此为既有语义，不误断言为全部401。root此前后端18回归已通过。

root 于20:48再次对照冻结清单核验：247个来源src未变化或新增；资料API/transport、课程、笔记、积分、后端模型配置边界与AdditionalTools七处关键实现均与本轮目标备份逐字节相同，作答/积分字段保留，新目录/封面字段齐全；结果 `tmp/demo_delta_20261002/root-final-audit.json`。补齐资源后的手机学习页、桌面学习页和设置页截图已独立视觉检查。最终确认生产构建包含该侧栏PNG且SHA一致，5192/5193/8192/8193测试端口均已关闭。

PNG后补后的实际浏览器使用原5193有效隔离会话只读重拍402/820/1440学习页，`visual-cookie.cjs`检查所有非空src的实际图像 naturalWidth，**3视口全部无损坏图像**。新PNG已显示，最新三张覆盖原路径。xiaolin多次调试OTP触发小时次数限制时停止发码，改用既有有效隔离会话；未修改或绕过后端限制。全部本轮测试服务在验收结束后停止，数据与证据保留。
