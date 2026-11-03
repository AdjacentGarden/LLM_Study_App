# 按钮尺寸规范

适用范围：`frontend/` 的 SwiftUI iPhone / iPad 主应用。尺寸单位为 iOS 逻辑点（pt），手机和平板采用同一套尺寸。

## 两档尺寸

| 类型 | 高度 | 宽度 | 使用场景 |
| --- | --- | --- | --- |
| 主按钮 `.primary` | 54pt | 至少 44pt；通常撑满可用宽度 | 登录、提交、保存、创建、开始学习等主要操作 |
| 常规按钮 `.regular` | 44pt | 至少 44pt；按内容或容器确定 | 次级操作、取消、筛选、菜单入口、页头操作、分页、快捷追问 |
| 图标按钮 | 44pt | 固定 44pt | 返回、关闭、删除、附件移除、发送、麦克风、录音与播放控制 |

按钮尺寸由 `DS.ButtonSize` 统一提供，不在页面中重新写数字高度。图标按钮即使承担录音等主要功能，也使用 44×44pt；图标字形和装饰光晕的尺寸不等于按钮尺寸。

主按钮文字默认使用 `.button`（14pt Semibold）；常规按钮使用 `.button` 或 `.smallButton`（12pt Semibold），根据现有信息层级选择。主按钮推荐水平内边距 14pt，常规按钮推荐 12pt，分别由 `horizontalPadding` 提供。保留页面现有颜色、圆角和材质。

## 代码接口

```swift
// 默认主样式为 54pt；次级、文字和危险样式默认为 44pt。
BCButton("保存", icon: "Save") { save() }
BCButton("取消", style: .secondary) { cancel() }

// 外观与尺寸可以独立指定，例如主要的删除确认操作。
BCButton("确认删除", style: .danger, size: .primary) { remove() }

Button(action: open) {
  Text("查看原文").font(DS.font(.smallButton))
    .padding(.horizontal, DS.ButtonSize.regular.horizontalPadding)
    .buttonFrame()
    .background(DS.primarySoft, in: Capsule())
}

Button(action: close) {
  Image(systemName: "xmark").font(.system(size: 18))
    .iconButtonFrame()
    .background(DS.surface, in: RoundedRectangle(cornerRadius: 12))
}
```

`buttonFrame(_:fillsWidth:)` 设置按钮高度、最小宽度及矩形点击区域；`iconButtonFrame()` 设置 44×44pt 布局及点击区域。优先把尺寸修饰符放在按钮 label 内、背景之前，使可见按钮表面与尺寸一致。不要在尺寸修饰符之后增加垂直内边距或连续缩放按钮来制造新尺寸。胶囊或圆形控件可在随后指定对应 `contentShape`。

## 页面应用与边界

- 首页、课程创建、登录、学习目录、阅读、练习、错题、闪卡、计划、笔记、原文查看、发现、设置和 AI 助手采用以上两档。
- 语音笔记中的开始、停止、播放、暂停、重录，以及 AI 语音输入中心和侧边图标均为 44×44pt。录音光晕仅作装饰，不扩大交互区域。
- 附件菜单单项、分类和错因筛选、课程及页码菜单入口、阅读快捷操作均使用常规 44pt。
- 整块课程卡片、教材预览、带多行描述的选择卡、闪卡翻面、任务与章节内容行属于内容容器，尺寸由内容决定。卡片里的独立按钮仍遵循本规范。初始资料上传区域也按内容容器处理，已有文件旁的添加与移除图标为 44×44pt。
- 搜索框、文本输入框、徽标、静态图标、工具栏外壳及导航栏外壳不作为按钮改变高度。系统原生标签栏、菜单弹出的选项、警告框及系统工具栏遵循系统布局。

## 验证

UI 测试检查实际渲染后的按钮边界，包括资料上传主按钮、页头返回、发现分享、笔记页码菜单，以及 AI 文字和语音控件。按手机和平板分别运行，并检查现有附件、键盘、录音切换、课程导航和笔记操作回归。

### 2026-11-02 验证记录

- 最终构建在 iPhone 17 Pro 和 iPad Air 11-inch（M2）的 iOS 26.0 模拟器上各执行 6 项 UI 测试，共 12 次执行全部通过，0 失败、0 跳过。覆盖跨页面按钮实际尺寸、日历 44pt 单元、AI 文字输入、语音回填、动画切换，以及 54pt 阅读完成按钮和阅读位置恢复。
- 最终结果包：`output/button-sizing-final.xcresult`；机器可读摘要：`output/button-sizing-summary.json`；导出截图：`output/button-sizing-review/final/`。
- 页面巡检另外覆盖 28 个页面和 7 类全局弹层；手机笔记批注、原文圈选和嵌入语音控件交互通过。对应记录保留在 `output/button-sizing-phone.xcresult` 与 `output/button-sizing-phone-final.xcresult`，后者同时保留了一次 AI 动画入口的失败，不能视为该批次全部通过。最终批次在点击前等待入口可交互，并验证上述两台设备全部通过。
- 1882 项内置内容校验通过；`git diff --check` 通过。截图检查包括手机和平板的发现、笔记保存、日历和 AI 语音界面。
