# 数据与工程复现

## 独立构建

原生构建只读取本仓库的 Swift、工程文件、SwiftPM 锁文件与 `Resources/`。`scripts/generate-project.py` 依据本仓库文件生成稳定的 Xcode object ID、shared scheme 与 Info.plist，最低 iOS 17；图标只使用已提交的原生 AppIcon，或本仓库的品牌图。没有原仓库绝对路径回退。

`build.sh`、`test.sh`、`run-simulator.sh` 不调用数据导出。首次 SwiftPM 下载后可复用 `.build/packages/`。不要在构建阶段临时从旧前端同步 fixtures，否则无法审阅数据变更与保证离线复现。

## 已迁移内容

`Resources/Data/sources.json` 包含 6 个原有示例资料：生物与数学，以及物理、化学、英语、高数目录示例。生物 fixtures 有 40 个目录项、15 个课时和 290 个 asset 记录。其普通课程 `chunks` 用于示例课时；独立 RAG corpus 有 171 个检索块，二者用途不同。数学与其他目录资料应按现有空内容边界展示，不能借用生物内容补齐。

原仓库 `src/data/generated/demo-state.json`、相关 TypeScript 演示数据、书架、预览、社区和一次性笔记种子被显式导出为 JSON。示例计划保留前端 `mergeFrontEndMockStudyTasks` 的可见任务，确保初始完成情况与界面一致。`published-pages.json` 是原仓库 published-citation-source-page-assets 清单的完整副本，指向现有的 125 张原页图片并保留 SHA-256。

`References/source-manifest.json` 记录原源码与 public 文件的路径、字节数和哈希，以及来源 commit；`source-working-tree.patch`、`source-status.txt` 记录被参考的未提交工作区。清单中的原绝对路径只是来源记录，应用不会使用它。`References/resource-copy-manifest.json` 单独记录原生包实际复制的公开资源及原始缺失素材。

原课本 PDF 共 125 页；目录后直接进入第 2 章，源资料中没有第 1 章正文。目录示例、AI 补充和 PDF 原文页应维持来源区分。不要从互联网找另一个版本补页。

## 静态素材与字体

公开目录采用白名单导入：`public/assets`、`public/icons`、`public/rag`。旧浏览器 WASM runtime 被明确排除，由原生 ONNX Runtime 替代；模型、tokenizer、向量与索引保持原始字节。公开 web app 图标保存为 `Resources/Content/public-icons/`；原生系统图标来自原 iOS 项目的 AppIcon。不会读取 `.env`、设备容器、个人附件、浏览器数据库或 `node_modules` 中的用户数据。

导航与贴纸 SVG 使用原 React 图标组件静态渲染；Lucide SVG 使用原依赖的几何路径。导航的 `selected-white` 是同一个 selected SVG 将 `#7c3aed` 替换为 `#ffffff`，没有重新绘制形状。

原 Noto Sans SC 400/700 WOFF 无损解包为 TTF，保留轮廓，修正 Regular/Bold 独立的 family/style/PostScript name 和字重位，供 CoreText 稳定区分。转换器固定 `recalcTimestamp=False`，工具固定 `fonttools==4.60.2`；源字体 SHA 与转换结果记录在 `References/font-conversion.json`。普通构建使用已提交 TTF，不需要安装 fonttools。

已知的 267 个 `assets/textbook/figures/` 图片引用在原 public 目录即不存在。原网页图片加载失败后不显示；原生按相同可用性隐藏，不留空白占位。原有 23 个 AI 图仍按源文件保留。资源校验允许**清单中明确列出的原始缺失**，新增缺失、原页断档、模型哈希变化会失败。

## 显式更新 fixtures

只有需要更新演示资料时才执行以下维护入口。原仓库须已按自己的锁文件安装 npm 依赖；本脚本只读旧源码和 public。Node 版本需满足原 `package.json`（22.13+ 或 24+），源 TypeScript / React / Transformers.js 版本由原锁文件决定。

```bash
python3 -m venv .tools
.tools/bin/python -m pip install -r scripts/requirements-fonts.txt
PATH="$PWD/.tools/bin:$PATH" ./scripts/import-source.sh /absolute/path/to/original-frontend
git diff --stat
python3 scripts/validate-content.py
```

步骤依次为：导出 TS fixtures 与图标 → 白名单复制公开素材/原页清单并记录缺失 → 转换字体 → 离线生成 tokenizer 金标 → 校验完整性。更新仅写入本仓库。脚本不自动提交、删除文件或更新 Xcode 工程。审阅 `Resources/` 与 `References/` diff 后再提交；源数据发生结构变动时，修正导出适配器与测试，不能静默猜测格式。

若只需复核当前复制内容与原仓库一致，可以运行 `sync-source-resources.py --source PATH --audit-only`，不会覆写业务资源，仅刷新资源 provenance 清单。重新导出不等同于再训练或重新标定 RAG；后者必须重新审阅评测与阈值。

## 本地用户数据与格式支持

正常应用的 SQLite 数据库与附件位于自己的 Application Support/BookCourse 目录。课程、资料身份、笔记与引用锚点都使用独立 ID；原文件复制到附件目录，界面不依赖系统选择器返回的临时 URL。导入身份以文件 SHA-256 派生，原文、课时与引用不应跨书串联。写入失败应反馈给用户；视频与助手积分通过持久化 reservation/complete/refund 事务处理。

`Core/FeatureStates.swift` 将 Observable 状态拆为 CourseState、LearningSessionState、ReaderState、StudyNoteState、ImportTaskState 和 AssistantConversationState。AppStore 负责跨域动作、路由、仓库与异步任务协调。章节修改/删除写入对应 bookID 的 `chapterDrafts`，正式 source chapters 仅在 `confirmChapters` 提交；草稿可持久化以继续编辑，但不应误作已确认目录。`cancelImport` 同时取消解析 task、更新 generation token 并返回待解析页；异步结果必须核对 token，防止取消后的迟到回调改变页面或数据。

| 输入 | 当前原生行为 | 能力边界 |
| --- | --- | --- |
| PDF | PDFKit 阅读与提取既有文字层；按 PDF 页定位 | 加密/损坏文件报错；扫描件仍可读原件但没有 OCR |
| PNG/JPEG/WebP/GIF/HEIC/HEIF | ImageIO 完整解码校验并保存原图 | 无文字识别或视觉语义模型 |
| DOCX | ZIP + `word/document.xml` 取文本 | 不保留 Word 精确版式、公式与批注 |
| PPTX | 按 slide XML 顺序提取文本 | 不渲染原幻灯片与动画 |
| XLSX | worksheet XML + sharedStrings 提取文本/单元格 | 不执行公式或还原完整表格样式 |
| TXT/MD/CSV | UTF-8 文本分段 | 不支持 GBK 自动猜测或 Markdown WebView 渲染 |
| 旧 DOC/PPT/XLS 等 | 不执行解析；已选择原件保留并说明 | 需用户先转换为新版格式 |

单文件最多 100 MB；Office 单个解压内容还有限额。解析可取消，失败后保留原件用于查看/重试；新增资料只生成忠于原文的阅读片段与保守词法检索，不伪装成大模型自动生成课程。

## 复现检查

`python3 scripts/validate-content.py` 只使用 Python 标准库，检查 fixtures JSON / ID、目录关联、公开资源哈希、125 页完整性、171×512 Float32 向量尺寸、全部索引与模型 SHA、cased tokenizer、SVG 白色变体、字体、依赖锁定与潜在外部符号链接。它不运行 iOS，也不证明 UI 像素一致；这些由 XCTest、UI 场景与人工视觉对照补足。

测试输入可以用 `python3 scripts/generate-import-fixtures.py` 重建，生成器使用固定 ZIP 时间戳与确定性内容。源 WebKit 参考图需要单独启动原前端并运行 `capture-web-baselines.mjs`；该维护工具是唯一需要浏览器的视觉基线采集步骤，应用本身没有浏览器依赖。
