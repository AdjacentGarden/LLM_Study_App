# 云径 App 产品质量检查基线

更新日期：2026-09-27

## 采用的标准

- Apple Human Interface Guidelines：布局、反馈、无障碍、文案与设计原则
- WCAG 2.2：文本对比度、焦点可见性、可预测交互与文字缩放
- iOS 常见交互基线：主要触控目标不小于 44 × 44 pt，状态反馈靠近对应任务，底部导航保持稳定

参考：

- https://developer.apple.com/design/human-interface-guidelines/layout
- https://developer.apple.com/design/human-interface-guidelines/feedback
- https://developer.apple.com/design/human-interface-guidelines/accessibility
- https://developer.apple.com/design/human-interface-guidelines/writing
- https://www.w3.org/WAI/WCAG22/quickref/

## 本轮检查结果

| 检查项 | 原状态 | 本轮处理 | 验收方式 |
| --- | --- | --- | --- |
| 信息层级 | 详情抽屉存在重复栏目提示 | 删除重复眉题，只保留唯一页面标题 | 目视检查 + 组件测试 |
| 正文可读性 | 次要文字灰度偏浅 | 次要文字改为 `#646b78` | 与白色背景对比度计算 |
| 触控区域 | 个别页签、筛选和次按钮不足 44pt | 统一交互控件最小高度 44px | 浏览器尺寸检查 |
| 键盘访问 | 焦点环颜色偏淡 | 统一为高对比蓝色 3px 焦点环 | Tab 键遍历 |
| 字体缩放 | 仅支持 90%–120% | 调整为 100%–200%，大字号时自动改为单列 | 200% 布局回归 |
| 产品文案 | 存在实现方式说明 | 改为面向用户的结果型短文案 | 全局文案抽查 |
| 动效 | 已有减弱动态设置 | 保留并验证 `prefers-reduced-motion` | 浏览器媒体查询检查 |
| 导航语义 | 当前页已有视觉选中 | 保留 `aria-current="page"` | DOM 语义检查 |

## 后续发布门槛

每次发布至少完成：手机端关键路径回归、200% 字号检查、键盘焦点检查、文本对比度检查、无横向溢出检查，以及主要异步任务的成功/失败/恢复状态检查。此基线用于持续改进，不代表未经第三方审核的完整 WCAG 合规声明。
