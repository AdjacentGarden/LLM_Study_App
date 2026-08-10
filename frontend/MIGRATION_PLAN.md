# Study App Demo 迁移与动画实施计划

状态：**第 1–5 项已实现，sol high 审核通过**  
目标仓库：`D:\code\study_app_demo`  
源仓库：`D:\code\study_app`  
计划日期：2026-08-02

## 本轮已完成（计划第 1–5 项）

1. 已记录源仓库 `codex/frontend-changes` 的分支、工作树和关键文件 SHA-256，源仓库未被修改。
2. 已在目标目录建立独立 Vite + React + TypeScript 前端并固定依赖。
3. 已迁移 P0 页面、视觉 token、响应式布局和动效基础。
4. 已将页面 API 适配层替换为本地、确定性的 `DemoRepository`，运行时不依赖真实后端或网络。
5. 已使用指定 125 页 PDF 运行 MinerU `magic_pdf.tools.cli` 的中文 OCR 流程；P0 课程 chunks 与引用直接由 `content_list.json` / `middle.json` 重建，人工策划的闪卡、题目和诊断从独立 `src/data/seed/curated-content.json` 读取并由 MinerU 证据刷新页码与 source metadata，且 `npm run demo:validate` 会逐条验证原始 OCR 文本、PDF 页、教材印刷页和条目哈希。

本阶段的审核重点是：P0 流程覆盖、离线/无网络约束、MinerU provenance、fixture 与本次 PDF 的对应关系，以及源仓库隔离。

## 1. 目标与边界

创建一个独立、纯前端、可离线运行的 iOS 风格 Demo，复刻现有 Study App 最有宣传价值的核心流程，并使用本地生物教材预生成固定课程内容。Demo 同时提供确定性的场景控制接口，供后续自动录制宣传动画。

本阶段不迁移真实后端，不在录制时执行 OCR、调用大模型或访问网络，也不追求覆盖正式产品的全部功能。

### 目标结果

1. `npm ci && npm run dev` 后可独立运行，无需启动 `study_app` 后端。
2. 以 iPhone 竖屏视口展示完整的“教材导入 → 课程生成 → 学习 → 诊断反馈 → 学习成果”故事。
3. 所有数据均来自本地固定 fixtures，每次打开和录制结果一致。
4. 每个宣传镜头可以通过 URL 或场景控制器直接定位并自动播放。
5. 原教材 PDF 不进入 Git；Demo 可在没有原 PDF 的机器上使用已批准的派生数据运行。

### 明确不做

- 不迁移 Python/FastAPI 后端、数据库、用户系统和真实上传存储。
- 不在 Demo 运行时执行真实 OCR、RAG、图片生成或 AI 对话。
- 不复制与宣传故事无关的社区、复杂导出、PWA 安装和后台同步能力，除非后续单独批准。
- 不直接修改 `D:\code\study_app` 中的文件。

## 2. 已完成的源项目盘点

### 前端基线

- React + TypeScript + Vite。
- 现有依赖较轻，主要为 React、Lucide、Vitest 和 Playwright。
- 已有设计 token、响应式样式、动效状态机、页面转场和 iOS/PWA 相关布局。
- 已有约 21 个页面状态，包括上传、解析、章节确认、课程、学习、作业、诊断、闪卡、报告等。
- API 当前通过 `frontend/src/api/bookcourseApi.ts` 连接本地后端；Demo 将以本地 repository 替换这一层。
- `frontend/src/data/mockBook.ts` 已包含与指定教材对应的课程、章节、减数分裂课程、题目、诊断、闪卡及 AI 回复，可作为内容迁移基线。
- `frontend/public/assets/textbook` 已有封面、目录、章节和减数分裂页面素材；`frontend/public/assets/brand` 已有品牌插图。

### 源仓库状态风险

源仓库当前存在未提交修改和新增文件。因此实施开始时必须：

1. 记录迁移时的源分支、工作树状态和文件校验值。
2. 明确以“批准时的当前工作树”作为视觉基线，而不是静默读取 Git HEAD。
3. 只复制计划清单中的文件，避免覆盖或整理用户在源仓库中的改动。

### PDF 结论

- 文件存在，共 125 页，约 38.8 MB，未加密。
- 抽样页面没有可提取文字层，属于扫描型 PDF。
- 内容生成管线必须包含 OCR；无需在 Demo 浏览器运行时执行 OCR。
- 现有教材素材和 mock 内容已经覆盖“减数分裂和受精作用”等适合宣传的主题，可先复用，再用 OCR 结果校对页码、原文引用和章节标题。

## 3. 推荐的宣传故事范围

默认制作一条约 35–45 秒、9:16 竖屏的中文 Demo，围绕同一本教材和一条连续学习路径展开。

| 段落 | 推荐画面 | 用户价值 | 预计时长 |
| --- | --- | --- | --- |
| 1 | 从首页选择本地教材 PDF | 任意教材快速导入 | 4–5 秒 |
| 2 | AI 分析教材、识别章节并生成课程 | 把静态书变成结构化课程 | 6–8 秒 |
| 3 | 课程总览和 14 天学习计划 | 自动规划学习路径 | 5–6 秒 |
| 4 | “减数分裂和受精作用”互动课程 | 原文、图示与 AI 导学结合 | 8–10 秒 |
| 5 | 回答一道题并得到错因诊断 | 发现知识卡点并即时反馈 | 7–8 秒 |
| 6 | 自动加入闪卡并展示进度报告 | 形成可持续复习闭环 | 5–6 秒 |

P0 页面建议只迁移：Home、Upload、Processing、ChapterConfirm、CourseReady、BookCourse、Lesson、Assignment、Diagnosis、Flashcard、LessonReport，以及这些页面使用的 Sheet/Toast/Navigation 组件。

## 4. 实施阶段

### 阶段 A：冻结迁移清单并搭建骨架

1. 从现有 `package-lock.json` 获取实际解析版本，在新仓库中固定版本，避免继续使用 `latest`。
2. 创建 Vite + React + TypeScript 工程、ESLint、Vitest 和 Playwright 配置。
3. 建立 `src/app`、`src/screens`、`src/components`、`src/motion`、`src/data`、`src/demo`、`scripts` 和 `public/assets`。
4. 增加 `.env.example`，使用 `DEMO_PDF_PATH` 作为本地内容生成输入，不写死个人路径。

验收：空壳工程可安装、构建、测试并在局域网关闭时启动。

### 阶段 B：迁移视觉系统与 P0 页面

优先复制并适配下列源内容：

- `frontend/src/styles/tokens.css`
- `frontend/src/styles/base.css`
- `frontend/src/styles/responsive.css`
- `frontend/src/styles/motion.css`
- P0 页面需要的 `frontend/src/components/ui.tsx`
- `frontend/src/motion` 中实际使用的转场、presence、reduced-motion 和反馈动效
- P0 页面及其直接依赖
- 已批准的品牌图、教材封面和少量教材页图

迁移时删除 API、登录、PWA 和浏览器存储的隐式依赖；保留 iOS safe-area、底部导航、弹层、触控反馈和页面转场。视觉目标以 `390 × 844` CSS 像素为主视口，同时验证 `393 × 852`。

验收：P0 页面可用静态占位数据串联，截图与源前端主要视觉保持一致，且不改动源仓库。

### 阶段 C：构建 PDF 离线内容管线

1. 通过本地环境变量读取教材 PDF。
2. 采用现有后端 OCR 能力或本机 MinerU 做一次性 OCR，输出中间结果到被 Git 忽略的缓存目录。
3. 检测目录页和章节边界，重点校对第 2 章相关内容。
4. 生成经过人工可审阅的固定数据：
   - 书籍元数据与章节目录
   - 一条 14 天学习计划
   - “减数分裂和受精作用”课程正文、学习目标和关键概念
   - 6–12 张闪卡
   - 3–5 道题及答案解析
   - 1 条错误答案 → 错因诊断 → 复习建议链路
   - 3–4 条固定 AI 问答和页码引用
5. 从教材中只派生宣传镜头需要的封面、目录及少量页面 WebP；压缩并记录来源页码。
6. 用 schema 校验生成的 JSON，运行时只读取通过校验的 fixtures。

推荐输出：

```text
src/data/generated/book.json
src/data/generated/chapters.json
src/data/generated/lessons.json
src/data/generated/quiz.json
src/data/generated/flashcards.json
src/data/generated/ai-responses.json
src/data/generated/demo-state.json
public/assets/textbook/*
```

版权控制：原 PDF 始终留在本机，不复制进新仓库；若仓库或宣传片会公开发布，教材封面、页面截图和长段原文必须先确认授权范围。未经确认时，只保留最少必要的引用与内部演示资产。

验收：删除或断开 PDF 路径后，已生成 Demo 仍可离线运行；重新执行生成命令可得到结构一致的结果。

### 阶段 D：替换后端并建立确定性 Demo 状态

1. 定义 `DemoRepository`，保持与页面所需数据相近的异步接口，但只读取本地 fixtures。
2. 所有模拟等待使用可配置时钟，录制模式下按固定时长推进。
3. 固定日期、用户名、进度、随机种子、答案和 AI 回复。
4. 增加一键重置，刷新或重播不会残留上次状态。
5. 提供 URL 场景入口，例如：

```text
/demo?scene=upload
/demo?scene=processing&autoplay=1
/demo?scene=lesson&step=3
/demo?scene=diagnosis
/demo?scene=report
```

6. 增加仅开发环境可见的场景面板，用于跳转、暂停、单步和重播。

验收：浏览器阻断全部网络请求后，每个场景仍能直接打开、自动播放并恢复到完全一致的结束状态。

### 阶段 E：宣传动画时间轴与录制接口

1. 把 6 段故事实现为独立 scene，每段有明确的开始态、动作、字幕安全区和结束态。
2. 统一暴露 `play()`、`pause()`、`seek()`、`reset()` 和 `ready` 状态，避免依赖人工点击。
3. 动画采用固定时间轴；关闭光标、滚动条、开发面板和系统选择高亮。
4. 优先输出无设备边框的 App 画面，再在视频合成阶段加入 iPhone 外框、灵动岛、背景、标题和音乐。
5. 默认交付规格建议：1080 × 1920、60 FPS、H.264 MP4；另保留透明/无边框素材或高分辨率截图序列。

验收：同一场景连续录制三次，关键帧布局、时长和文案完全一致，无接口请求、加载抖动或人工操作误差。

### 阶段 F：质量验证与交付

必须通过：

- `npm run lint`
- `npm run test`
- `npm run build`
- Playwright P0 流程测试
- 390 × 844 与 393 × 852 的视觉回归截图
- `prefers-reduced-motion` 基础可访问性测试
- 完全离线运行测试
- 全流程重置与重复播放测试
- 中文字体、乱码、页码引用和教材知识点人工抽查

最终交付物：

1. 独立 Demo 前端仓库。
2. 可复现的本地课程 fixtures 和已批准素材。
3. PDF 内容生成脚本与使用说明。
4. 场景控制器、自动播放脚本和录制说明。
5. 宣传动画分镜及最终视频（若本轮批准包含视频制作）。
6. 源文件、派生资产、许可证和版权风险清单。

## 5. 建议目录结构

```text
study_app_demo/
├── docs/
│   ├── STORYBOARD.md
│   ├── CONTENT_PROVENANCE.md
│   └── RECORDING.md
├── scripts/
│   ├── ingest-pdf.ts
│   ├── build-demo-content.ts
│   └── validate-fixtures.ts
├── src/
│   ├── app/
│   ├── components/
│   ├── data/generated/
│   ├── demo/
│   │   ├── DemoController.ts
│   │   ├── scenes.ts
│   │   └── timeline.ts
│   ├── motion/
│   ├── screens/
│   ├── services/DemoRepository.ts
│   └── styles/
├── public/assets/
├── e2e/
└── package.json
```

## 6. 需要批准的默认决策

若用户回复“同意，按计划实施”，默认视为批准以下范围：

1. 新仓库保持纯前端，运行时无真实后端。
2. 以当前工作树中的现有前端视觉为迁移基线。
3. 只实现 P0 页面，不迁移社区和完整 PWA 能力。
4. 以教材第 2 章的“减数分裂和受精作用”为核心课程内容。
5. PDF 只作为本地 OCR 输入，不提交原文件。
6. 默认宣传片为中文、35–45 秒、9:16、1080 × 1920、60 FPS。
7. 先完成可录制 Demo，再完成视频包装，避免两者同时变化。

如需改变页面范围、核心章节、视频时长、横竖屏、语言或是否公开发布，应在批准时一并说明。

## 7. 实施交接说明

收到用户批准后，先把本计划转换为逐项可验收的实施任务，再开始迁移。交接内容必须包含：

- 源/目标绝对路径和禁止改动源仓库的约束。
- 当前源工作树存在用户未提交修改的提醒。
- P0 文件白名单、Demo 数据 schema、场景时序与验收命令。
- PDF 必须本地读取、原文件不得提交、扫描版需要 OCR 的事实。
- 每完成一个阶段即构建和测试，不等待最后统一验证。

“Luna Max”目前不是本会话可直接选择的模型名称。若它是用户侧可调用的外部代理，可在批准后生成完整交接任务供其执行；若用户只是希望使用名为 `luna_max` 的协作子代理，可以创建该任务名，但其底层仍只能使用当前环境支持的模型。交接时不得把两者混同。
