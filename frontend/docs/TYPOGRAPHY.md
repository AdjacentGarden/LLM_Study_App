# 前端字体与排版规范

适用范围：`frontend/` 的 SwiftUI iPhone / iPad 主应用。界面统一通过 `DS.Typography` 选择语义样式，默认单位为 iOS 逻辑点（pt）。中文使用苹方简体，英文与数字使用 Helvetica。

## 字体选择与字重

`DesignSystem/Components.swift` 中的 `DS.uiFont` 使用 Helvetica 作为拉丁字符和数字字体，并通过字体描述符的 `cascadeList` 显式指定中文回退到苹方简体。`DS.font` 将同一字体描述符用于 SwiftUI，阅读正文与原生标签栏直接使用 `DS.uiFont`。

| 代码字重 | 中文实际字体 | 英文与数字实际字体 |
| --- | --- | --- |
| `.light` | PingFangSC-Light，Light（约 300） | Helvetica-Light，Light（约 300） |
| `.regular`，默认 | PingFangSC-Regular，Regular（约 400） | Helvetica，Regular（约 400） |
| `.medium` | PingFangSC-Medium，Medium（约 500） | Helvetica，Regular（约 400） |
| `.semibold` | PingFangSC-Semibold，Semibold（约 600） | Helvetica-Bold，Bold（约 700） |
| `.bold` | PingFangSC-Semibold，Semibold（约 600） | Helvetica-Bold，Bold（约 700） |

字重数值用于说明视觉层级，实际使用上表中的字体实例。当前 Helvetica 字体实例没有单独的 Medium / Semibold，苹方没有单独的 Bold；按上表映射到可用实例，不合成额外字重。`.ultraLight` / `.thin` 分别选择苹方 Ultralight / Thin 和 Helvetica-Light；`.heavy` / `.black` 与 `.bold` 相同。常用页面以 Regular 和强调字重为主。

## 统一字号层级

字号、默认字重与动态字体缩放基准集中定义在 `DesignSystem/Components.swift` 的 `DS.Typography`。页面不再传入独立的数字字号。

| 层级 / 样式 | 基准字号 | 默认代码字重 | 应用场景 |
| --- | --- | --- | --- |
| 页面标题 `.pageTitle` | 22 | Bold | 顶部主标题、登录 / 注册标题、引导问题、课程设置问题 |
| 分区标题 `.sectionTitle` | 18 | Semibold | 课程与计划分区、弹层标题、题目标题 |
| 卡片标题 `.cardTitle` | 16 | Regular | 课程卡片、列表标题、表单分组、笔记标题 |
| 阅读正文 `.reading` | 16 | Regular | 课时长文、闪卡内容、练习选项、解释、展开的笔记和摘录 |
| 界面正文 `.body` | 14 | Regular | 常规描述、表单输入、AI 消息、列表摘要 |
| 辅助说明 `.supporting` | 12 | Regular | 来源、提示、状态、说明与次要信息 |
| 标注 `.caption` | 10 | Regular | 标签、时间、字数、附件大小、缩略预览 |
| 主按钮 `.button` | 14 | Semibold | 通用主按钮、提交、保存、登录与注册 |
| 小按钮 `.smallButton` | 12 | Semibold | 页头操作、查看原文、行内操作、阅读页底部操作 |
| 导航 `.navigation` | 12 | Medium | 自绘导航 / 平板导航；选中时可使用 Semibold |
| 原生底部标签 `.tabLabel` | 10 | Regular | iPhone 底部标签栏；选中时使用 Semibold |
| 大数字 `.metric` | 22 | Bold | 进度百分比等突出指标 |

同一角色在各页面使用同一字号。强调内容可以显式指定 `.medium` 或 `.semibold`；页面标题使用 Bold，分区标题和按钮使用 Semibold，卡片标题使用 Regular。上表中的字重最终按中英文字体各自的可用实例映射，详见前表。

旧的 27.2 / 20.8 / 19.2 等字号已移除。标注统一为 10pt，辅助说明统一为 12pt，常规正文统一为 14pt。缩略卡片不再按宽度连续计算字号；必要时在 `.caption` 和 `.supporting` 之间切换，并通过换行或截断处理内容。

## 页面应用约定

- 学习、发现、个人页、课程创建与管理、课时、练习、诊断、错题、闪卡、计划、笔记、原文查看、AI 助手、附件菜单和顶部提示均使用同一套语义样式。
- 通用页头使用 `.pageTitle`；课程与学习分区使用 `.sectionTitle`；卡片与列表标题使用 `.cardTitle`。
- 通用按钮和笔记按钮使用 `.button`，小型文字操作使用 `.smallButton`。
- 按钮尺寸遵循 [按钮尺寸规范](BUTTONS.md)：主按钮 54pt，常规按钮和图标按钮 44pt；字体样式不单独决定按钮高度。
- 课时正文与完整笔记使用 `.reading`；AI 对话、表单和界面描述使用 `.body`；摘要与次要信息按 `.supporting` / `.caption` 区分。
- 较长标题允许换行，不通过连续缩小字体满足容器宽度。全局 AI 助手自适应标题按 **22 → 18 → 16pt** 选择；练习页自适应标题按 **18 → 16 → 14pt** 选择，最后一档可截断。

## 代码使用

```swift
Text("章节学习").font(DS.font(.pageTitle))
Text("今日学习计划").font(DS.font(.sectionTitle))
Text("教材正文").font(DS.font(.reading))
Text("来自教材第 12 页").font(DS.font(.supporting))
Text("保存").font(DS.font(.button))
Text(message.text).appFont(.body)
```

`DS.font`、`DS.uiFont` 和 `DS.lineHeight` 只接收 `DS.Typography` 样式，避免页面重新引入任意字号。只有设计系统内部的动态字体实现接收缩放后的尺寸。

## 动态文字、行距与颜色

- `DS.font(style, weight:)` 使用固定的基准字号；`View.appFont(style, weight:)` 通过 `@ScaledMetric` 按该样式的缩放基准适配系统文字大小。AI 对话、输入框、历史列表、附件菜单与顶部提示等原有动态区域继续支持缩放。
- 课时阅读使用 `.reading` 的 **16pt 字号**，行高由 `DS.readingLineHeight` 统一为 **24pt**（1.5 倍）。原有平衡换行继续使用同一字体测量。
- 其他多行文字保留页面所需的额外行间距，常见为 **4–7pt**；额外行间距不等于完整行高。字距使用字体默认值。
- 主要文字颜色为 **#20263A**，次级文字为 **#4F5E75**，弱化文字为 **#596780**；强调色为 **#7C3AED**，主按钮文字为白色。

## 输出边界与验证

SF Symbols 的尺寸用于图标，不受正文字号层级限制。系统键盘、文件 / 相机 / 照片选择器等原生系统界面遵循 iOS 自身排版。教材原 PDF 和原页图片中的字形来自原始资料。

学习内容的 PDF 导出继续使用内嵌 Noto Sans SC，并保留独立的打印字号（正文 12pt、分区标题 15pt、文档标题 21pt），确保中文可检索与可复制。

`Tests/LearningTests.swift` 核验 CoreText 实际混排字体、中文行高，以及手机 / 平板宽度下的阅读换行；`LearningExportTests` 覆盖 PDF 的可读性。UI 回归与模拟器截图用于检查各页面和弹层的可用性与文字布局。

本次验收使用用户指定的最终字号：页面标题 22pt Bold、分区标题 18pt Semibold、卡片标题 16pt Regular、正文 14pt Regular、长文阅读 16pt Regular、辅助说明 12pt Regular、标注 10pt Regular。已移除界面中直接传入数字的字体调用及按容器宽度连续计算字号的逻辑。

验证结果：iOS 26 iPhone 17 Pro 上 10 项 LearningTests、2 项 LearningExportTests、3 项 UI 测试全部通过；UI 测试覆盖 28 个页面路由、7 类全局弹层、AI 输入与键盘避让、顶部 HUD。iPad Pro 13-inch 的学习页、课程设置、阅读页、练习页已截图检查。1848 项内容资源校验通过；本次未执行 iOS 17 运行测试。截图保存在 `output/typography-scale/`，测试结果包保存在 `DerivedData/Logs/Test/Test-BookCourse-2026.11.01_18-54-18--0800.xcresult`。
