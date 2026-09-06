# CloudPath 前端动效系统实施与审查计划

## 1. 目标与基线

### 1.1 工作基线

- 仓库：`AdjacentGarden/study_app`
- 分支：`frontend-update`
- 动效工作统一基线：`e2f88f4 feat(frontend): add responsive iPhone and iPad layouts`
- 技术栈：React、TypeScript、Vite、自定义 CSS
- 当前基线检查：
  - `npm run lint` 通过
  - `npm run test`：14/14
  - `npm run build` 通过
  - `npm run test:responsive`：117 通过、3 个按项目条件跳过
  - `npm run test:e2e`：173 通过、3 个按项目条件跳过
  - 现有 40 张 WebKit 响应式视觉基线保持稳定

### 1.2 实施目标

- 建立统一、可复用、可审计的 Motion Tokens 和动效状态接口。
- 为页面切换、主导航、浮层、AI 助手、上传解析、课程生成和学习工具增加有目的的动效反馈。
- 手机与 iPad 使用不同强度的空间运动，保持原有响应式布局和安全区行为。
- 所有业务状态在关闭动效后仍然完整、可理解、可操作。
- 完整支持 `prefers-reduced-motion: reduce`。
- 动效不得改变后端 API、轮询频率、业务数据类型、导航语义或 mock 边界。
- 保持现有玻璃拟态和云朵品牌视觉，不增加持续干扰学习的装饰动画。

### 1.3 明确不做

- 不引入 Tailwind、shadcn、GSAP、Motion/Framer Motion 或其他动画依赖。
- 不使用 User-Agent、设备名称或物理像素判断动效。
- 不使用持续视差、粒子背景、空闲呼吸、吉祥物循环漂浮或强弹簧过冲。
- 不动画整页 `filter`、`backdrop-filter`、大面积 `box-shadow`、渐变背景位置。
- 不通过动效制造虚假上传、解析、生成或学习进度。
- 不以固定延时伪造业务状态、同步动画或同步 E2E；保留既有 Toast `3200ms` 产品展示时长。
- 不修改后端、生产 API、请求结构、轮询间隔或公共业务类型。

## 2. 设备与动效合同

### 2.1 目标尺寸

| 设备 | 方向/状态 | CSS viewport |
| --- | --- | ---: |
| iPhone 17 Pro | 动态高度较短竖屏 | `402×681` |
| iPhone 17 Pro | 标准竖屏 | `402×874` |
| iPhone 17 Pro | 短横屏 | `756×352` |
| iPhone 17 Pro | 标准横屏 | `874×402` |
| iPad Pro 11 | 竖屏 | `834×1194` |
| iPad Pro 11 | 竖屏扩展高度 | `834×1210` |
| iPad Pro 11 | 横屏 | `1194×834` |
| iPad Pro 11 | 宽横屏 | `1210×834` |

### 2.2 设备差异

- 设备类别严格复用现有响应式合同，不按设备名称、User-Agent 或单独宽度判断。
- 默认规则为手机。
- 只有 `(min-width: 768px) and (min-height: 600px)` 才启用 iPad 动效。
- `(max-height: 599px) and (orientation: landscape)` 启用短横屏覆盖；该规则写在 iPad 规则之后，优先级最高。
- `874×402` 和 `756×352` 必须按手机短横屏处理，不能因为宽度超过 `768px` 使用 iPad 动效。
- 手机页面前进：新页面从右侧 `12px` 淡入。
- 手机页面返回：新页面从左侧 `12px` 淡入。
- iPad 页面切换：使用 `translateY(6px) + opacity`，不做整画布横向滑动。
- 短横屏页面位移压缩至 `6px`，普通 surface 动画重映射为至多 `180ms`。
- 短横屏 Flashcard 不使用 3D 翻转，改为 `180ms` 交叉淡入。
- 短横屏进度仍使用 `320ms`，因为只动画 `scaleX` 且不阻塞操作；这是 surface 上限的唯一时长例外。
- 屏幕旋转、`visualViewport` 改变或软件键盘出现时，浮层立即重新约束到可见区域；不得等待长动画完成。
- 设备尺寸变化时可以取消正在进行的非关键动画并直接落到最终状态。

## 3. Motion Tokens 与技术决策

### 3.1 固定 Tokens

在 `frontend/src/styles/tokens.css` 增加：

```css
--motion-duration-press: 90ms;
--motion-duration-fast: 140ms;
--motion-duration-base: 180ms;
--motion-duration-surface: 240ms;
--motion-duration-progress: 320ms;
--motion-duration-flip: 340ms;
--motion-duration-loading: 1200ms;

--motion-ease-standard: cubic-bezier(.2, .8, .2, 1);
--motion-ease-enter: cubic-bezier(.16, 1, .3, 1);
--motion-ease-exit: cubic-bezier(.4, 0, 1, 1);

--motion-distance-small: 6px;
--motion-distance-medium: 12px;
--motion-distance-panel: 20px;
```

约束：

- 普通一次性动画最长不超过 `340ms`。
- 正常模式下循环动画只允许真实加载指示器，且必须配有可读文字状态。
- 按钮按压只允许 `transform`，不得改变布局尺寸。
- 进度条使用 `scaleX()`，不动画容器宽度。

### 3.2 组件级动效映射

下表是唯一允许的产品动效参数来源。实现中不得出现表外硬编码时长、缓动、位移或缩放值。

| 目标 | 正常模式 from → to | 时长 / 缓动 | iPad | 短横屏 | 退出 | Reduced Motion |
| --- | --- | --- | --- | --- | --- | --- |
| 基础 Button 按下 | `scale(1)` → `scale(.98)` | Press / Standard | 相同 | 相同 | 释放用 Fast / Standard 回到 `1` | 无缩放 |
| IconButton 按下 | `scale(1)` → `scale(.96)` | Press / Standard | 相同 | 相同 | Fast / Standard 回到 `1` | 无缩放 |
| Focus Visible | 保留现有静态 outline | 静态更新，不动画 | 相同 | 相同 | 不适用 | 相同 |
| 普通导航 active 图标 | active 时一次 `scale(.94)` → `scale(1)`；最终保持当前 `transform:none` | Fast / Standard | Rail 相同 | Bottom Nav 相同 | 非 active 静态回到 `1`；按下 `scale(.96)` | 无动画，最终 `1` |
| Upload 导航 active 图标 | active 时一次 `scale(.98)` → `scale(1)`；最终保持当前 `transform:none` | Fast / Standard | Rail 相同 | Bottom Nav 相同 | 非 active 静态回到 `1`；按下 `scale(.96)` | 无动画，最终 `1` |
| 导航颜色/背景 | 当前值 → 现有 active 计算值 | Fast / Standard | 相同 | 相同 | Fast / Standard | 直接最终值 |
| 页面 forward | `opacity:0; translateX(12px)` → `1; 0` | Surface / Enter | 改为 `translateY(6px)` | `translateX(6px)`，Base / Enter | 无页面退出动画 | 直接最终状态 |
| 页面 back | `opacity:0; translateX(-12px)` → `1; 0` | Surface / Enter | 改为 `translateY(6px)` | `translateX(-6px)`，Base / Enter | 无页面退出动画 | 直接最终状态 |
| 页面 replace | `opacity:0; translateY(6px)` → `1; 0` | Base / Enter | 相同 | `translateY(4px)`，Fast / Enter | 无页面退出动画 | 直接最终状态 |
| Scrim | `opacity:0` → `1` | Fast / Standard | 相同 | 相同 | `1` → `0`，Fast / Exit | 直接最终状态 |
| 手机底部 Sheet | `opacity:0; translateY(18px)` → `1; 0` | Surface / Enter | 不适用 | `translateY(6px)`，Base / Enter | `1; 0` → `0; translateY(12px)`，Base / Exit；短横屏 `6px` / Fast | 直接进入/卸载 |
| iPad Chat / AI 面板 | `opacity:0; translateX(20px)` → `1; 0` | Surface / Enter | 适用 | 不适用 | `1; 0` → `0; translateX(16px)`，Base / Exit | 直接进入/卸载 |
| 手机 AI Dialog | `opacity:0; translateY(14px)` → `1; 0` | Surface / Enter | 不适用 | `translateY(6px)`，Base / Enter | `1; 0` → `0; translateY(10px)`，Base / Exit；短横屏 `6px` / Fast | 直接进入/卸载 |
| iPad Source Dialog | `opacity:0; translateY(6px)` → `1; 0` | Base / Enter | 适用 | 不适用 | 反向，Fast / Exit | 直接进入/卸载 |
| iPad Note/EditChapter | `opacity:0; scale(.98)` → `1; scale(1)` | Base / Enter | 适用 | 不适用 | `1; scale(1)` → `0; scale(.98)`，Fast / Exit | 无缩放，直接进入/卸载 |
| Toast | `opacity:0; translateY(8px)` → `1; 0` | Base / Enter | 相同 | Fast / Enter | `1; 0` → `0; translateY(-4px)`，Fast / Exit | 直接进入/卸载 |
| AI Orb pointerup settle | FLIP inverse transform → `translate3d(0,0,0)` | Base / Standard | 相同 | 相同 | 不适用 | 直接落位 |
| 进度条 | 当前 `scaleX` → 新 `scaleX` | Progress / Standard | 相同 | 保留 Progress | 不适用 | 直接新值 |
| 轮询百分比/数字文本 | 静态替换文本，不做滚动、缩放或淡入 | 静态更新 | 相同 | 相同 | 不适用 | 相同 |
| 阶段完成 Check | `opacity:0; scale(.85)` → `1; scale(1)` | Base / Enter | 相同 | Fast / Enter | 不重播 | 直接最终状态 |
| 成功标记 | `opacity:0; scale(.96)` → `1; scale(1)` | Surface / Enter | 相同 | Base / Enter | 不重播 | 直接最终状态 |
| 图片载入 | `opacity:0` → `1` | Base / Standard | 相同 | Fast / Standard | 不适用 | 直接显示 |
| SourceReader 翻页内容 | 新页 `opacity:0` → `1`，旧页立即替换 | Base / Standard | 相同 | Fast / Standard | 无旧页退出 | 直接显示 |
| 有限列表新增项 | `opacity:0; translateY(6px)` → `1; 0` | Base / Enter；延迟 `index × 30ms`，最多 6 项 | 相同 | 无 stagger，Fast | 删除节点不做退出 | 直接显示 |
| 通用局部内容/空态 | 显式 `[data-motion-item]`：`opacity:0; translateY(6px)` → `1; 0` | Base / Enter，无 stagger | iPad 详情可使用映射表最后一行 | `translateY(4px)` / Fast | 旧内容立即替换 | 直接显示 |
| Flashcard 问答区 | front `rotateY(0)` / back `rotateY(180deg)` 互换 | Flip / Standard | 相同 | 禁止 3D，Base 交叉淡入 | 反向相同 | `1ms` 交叉替换，无 3D |
| Flashcard 下一张 | `opacity:0; translateX(8px)` → `1; 0` | Base / Enter | 改为 `translateY(6px)` | `translateX(4px)` / Fast | 当前内容立即替换 | 直接显示 |
| 选中态 Check / Pill | `opacity:0; scale(.9)` → `1; scale(1)` | Fast / Standard | 相同 | 相同 | 反向 Fast | 直接最终状态 |
| Diagnosis/掌握度条 | 当前 `scaleX` → 新 `scaleX` | Progress / Standard | 相同 | 保留 Progress | 不适用 | 直接新值 |
| iPad 局部详情替换 | `opacity:0; translateX(6px)` → `1; 0` | Base / Enter | 适用 | 不适用 | 旧详情立即替换 | 直接显示 |

### 3.3 文件组织与测试基础设施

新增：

- `frontend/src/styles/motion.css`
- `frontend/src/motion/useReducedMotion.ts`
- `frontend/src/motion/useMotionPresence.ts`
- `frontend/src/motion/presenceMachine.ts`
- `frontend/src/motion/presenceMachine.test.ts`
- `frontend/src/motion/ScreenTransition.tsx`
- `frontend/src/motion/MotionHistoryContext.tsx`
- `frontend/src/motion/index.ts`
- `frontend/e2e/motion.spec.ts`
- `frontend/e2e/fixtures/visual-viewport.ts`

修改：

- `frontend/src/main.tsx`
- `frontend/src/styles/tokens.css`
- `frontend/src/App.tsx`
- `frontend/src/components/ui.tsx`
- 与当前阶段直接相关的 screen 文件
- `frontend/package.json`
- `frontend/e2e/responsive.spec.ts`（阶段 2 仅机械提取 visualViewport shim）

文件规则：

- `motion.css` 在 `responsive.css` 之后导入。
- `motion.css` 只负责动画、过渡、动效状态和 reduced-motion，不得覆盖布局宽高、断点、安全区或导航结构。
- 阶段 1 就创建 `e2e/motion.spec.ts` 和 `npm run test:motion`；后续阶段只追加相应 `Stage N` 分组。
- 不增加 jsdom、happy-dom 或 React Testing Library。
- Presence 状态机是纯函数，通过现有 Node Vitest 测试。
- hooks 的浏览器监听、StrictMode、console warning 和实际卸载通过真实 App 的 Playwright 路径验证。
- 组件使用统一的 `data-motion-state`：
  - `entering`
  - `idle`
  - `closing`
- 页面使用统一的 `data-motion-direction`：
  - `forward`
  - `back`
  - `replace`
- 测试依赖 `data-motion-state="idle"` 或动画/过渡事件完成，不使用固定等待。

### 3.4 Presence 状态机与所有权

- 页面采用“新页面单独入场”，旧页面立即卸载。
- 不同时保留两个可交互页面，不产生重复焦点树。
- 页面快速连续切换时，以最后一个 `screen` 状态为准，不锁死导航。
- `useMotionPresence<T>` 的固定输入：
  - `requested: T | null`
  - `getKey: (value: T) => string`
  - `reducedMotion: boolean`
  - `motionNames: string[]`
  - `maxMotionMs: 320`
- 固定输出：
  - `rendered: T | null`
  - `state: entering | idle | closing`
  - `presenceId: number`
  - `onAnimationEnd`
  - `onAnimationCancel`
- `maxMotionMs` 同时覆盖丢失事件的 `entering → idle` 和 `closing → unmounted`，不是只覆盖退出。
- 事件处理只接受 `event.target === event.currentTarget` 且 `animationName` 位于 `motionNames` 的事件，忽略子节点冒泡。
- handler 闭包携带当前 `presenceId`；过期 generation 的 end/cancel/后备清理不得改变新 surface。
- `animationcancel`：
  - `closing` 时立即卸载。
  - `entering` 时立即进入 `idle`。
- `maxMotionMs: 320` 只作为 WebKit 丢失 end/cancel 事件时的收敛后备；正常 E2E 不等待该时间。
- reduced-motion 初始为 reduce，或运行中切换为 reduce 时：
  - `closing` 同步卸载。
  - `entering` 同步进入 `idle`。
  - 清除事件监听和后备计时器。
- `null → A`：渲染 A，进入 `entering`。
- `A → null`：冻结最后一个 A 快照，进入 `closing`。
- `A closing → A open`：取消旧清理和旧焦点恢复，使用新的 `presenceId` 重新进入。
- `A → B` 或 `A closing → B`：取消 A 清理，只保留 B；B 进入 `entering`，页面中始终最多一个 surface。
- 同 key 的内容更新：`idle` 时更新；进入 `closing` 后冻结最后一次内容快照。

Presence surface 必须使用具名 CSS keyframe，完成 handler 绑定在实际发生 animation 的 panel 节点，而不是不动画的 overlay 外层：

| Surface | Enter animation-name | Exit animation-name | Handler 节点 |
| --- | --- | --- | --- |
| 手机 Sheet | `motion-sheet-phone-in` / 短横屏 `motion-sheet-short-in` | `motion-sheet-phone-out` / `motion-sheet-short-out` | `.sheet` |
| iPad Chat | `motion-panel-tablet-in` | `motion-panel-tablet-out` | `.sheet[data-sheet-type="chat"]` |
| iPad Source | `motion-dialog-source-in` | `motion-dialog-source-out` | `.sheet[data-sheet-type="source"]` |
| iPad Note/EditChapter | `motion-dialog-center-in` | `motion-dialog-center-out` | 对应 `.sheet` |
| 手机 AI | `motion-dialog-ai-phone-in` / 短横屏 `motion-dialog-ai-short-in` | 对应 `*-out` | `.ai-overlay` |
| iPad AI | `motion-panel-tablet-in` | `motion-panel-tablet-out` | `.ai-overlay` |
| Toast | `motion-toast-in` | `motion-toast-out` | `.toast` |

- Scrim 使用 `motion-scrim-in/out`，但不负责 Presence 完成；Surface panel 的完成事件是唯一卸载信号。

`ScreenTransition` 固定接口：

```ts
type ScreenTransitionProps = {
  screenKey: Screen;
  direction: "forward" | "back" | "replace";
  nonce: number;
  initial: boolean;
  reducedMotion: boolean;
  children: ReactNode;
};
```

- 初始挂载：`state="idle"`，不动画。
- 同 screen 导航：nonce 不增加，保持 `idle`，不重播。
- 新 nonce：只渲染新 children，进入 `entering`。
- 使用具名 animation：
  - `motion-screen-phone-forward-in`
  - `motion-screen-phone-back-in`
  - `motion-screen-replace-in`
  - `motion-screen-tablet-in`
  - `motion-screen-short-forward-in`
  - `motion-screen-short-back-in`
- handler 绑定到 ScreenTransition 动画根，过滤 `target/currentTarget`、允许的 animation-name、nonce generation。
- `animationend` 或 `animationcancel` 后进入 `idle`。
- 丢失 end/cancel 时使用同一 `maxMotionMs: 320` 后备进入 `idle`。
- runtime 切换 reduced-motion 时同步进入 `idle` 并清理后备。
- 快速新 nonce 替换旧 nonce 时清理旧事件/后备，过期 generation 不得覆盖新状态。

所有权固定为：

- Sheet：App 层创建包含 `key`、`SheetState`、已解析内容 props 和 ReactNode 的 `sheetView`，Presence 持有最后一个 `sheetView`；EditChapter 删除后 closing 使用冻结的章节快照，不能重新查找已删除章节。
- AI Dialog：`GlobalAIAssistant` 常驻并拥有 Dialog Presence。
- Toast：`Toast` 常驻并拥有 Toast Presence；新 Toast 在 closing 中到达时取消旧卸载并替换内容。
- ScreenTransition：不使用 Presence，不保留旧页面。

closing 期间的业务激活安全合同：

- Surface 保留 Dialog 语义和焦点锁定，不设置 `inert`，不提前设置 `aria-hidden`。
- Surface 根设置 `aria-busy="true"`。
- 在实际 panel 根的 capture 阶段统一拦截并 `preventDefault + stopPropagation`：
  - `pointerdown`
  - `pointerup`
  - `click`
  - `submit`
  - `keydown` 的 `Enter` 和 `Space`
- `Tab`、`Shift+Tab` 和 `Escape` 不拦截，继续由焦点锁定和关闭逻辑处理。
- Overlay/Scrim 在 closing 期间继续覆盖背景，不能使用会让指针穿透到页面的 `pointer-events:none`。
- capture 拦截只在 `state="closing"` 生效，不修改冻结内容节点内的业务 handler。
- 测试必须证明 closing 中指针、Enter、Space 和 form submit 均不会增加 API/本地提交次数。

### 3.5 焦点与导航方向合同

- `AppShell` 的 `main` 增加 `tabIndex="-1"` 和稳定 ref。
- 初始挂载不强制移动焦点。
- screen 实际变化后，立即把焦点移动到新 `main`，使用 `preventScroll: true`，不等待页面动画。
- 同 screen 的 `go` 不移动焦点、不播放新页面动画，并保持现有 history 行为不变。
- 普通 Sheet 关闭：触发器仍连接时恢复触发器；否则回退到当前 `main`。
- 导航同时关闭 Sheet：标记 `restoreFocus: false`，由新页面 `main` 接管焦点，旧 Sheet 清理不得再次抢焦点。
- AI Dialog closing 期间 Orb 保持隐藏、不可聚焦；`aria-expanded` 反映 Presence 实际存在，而不是只反映 requested open。
- AI closing 中重新打开时取消旧焦点恢复；只有当前 `presenceId` 可以执行恢复。
- 任意回退焦点不得落到 `body`。

导航方向固定为：

| 路径 | Direction | 是否入场 | History |
| --- | --- | --- | --- |
| 初始 `home` | `replace` | 否 | 不变 |
| `go(next)` 且 next 不同 | `forward` | 是 | 保持现有 push |
| `go(current)` | `replace` | 否 | 保持现有 push 行为 |
| `back()` 且 history 非空 | `back` | 是 | 保持现有 pop |
| `back()` history 为空且 screen 不是 home | `back` | 是，进入 home | 保持现有清空 |
| `back()` history 为空且已是 home | `replace` | 否 | 不变 |
| `openSourcePage()` | `forward` | 是 | 保持现有 push |
| 后台解析完成直接进入 ChapterConfirm | `replace` | 是 | 不增加 history |
| 其他业务自动替换 screen | `replace` | 是 | 不增加 history |

- screen、direction 和单调递增的 transition nonce 必须在同一导航事务中更新；快速连续导航最终以最后一次事务为准。
- 不改变现有 history 数组内容或返回结果。

### 3.6 AI Orb 的 transform/FLIP 合同

- 移除 `top/left/right` 的 transition；这些属性只用于无过渡提交最终落点。
- pointerdown 记录基础 rect。
- pointermove 通过 `requestAnimationFrame` 更新 `translate3d(dx, dy, 0)` CSS variables，不在每个 pointermove 写 React top/left state。
- pointerup：
  1. 读取释放前 rect。
  2. 计算安全区内最终 top/left。
  3. 无过渡提交最终 top/left。
  4. 计算 inverse delta。
  5. 只动画 transform 从 inverse delta 到 `translate3d(0,0,0)`，Base / Standard。
- Orb 的按压/active scale 使用独立 CSS variable 与 translate transform 合成。
- pointercancel、resize、orientationchange、visualViewport resize/scroll 和 reduced-motion 切换：取消 rAF/settle，清除拖动 transform，无动画直接约束。
- `transition-property` 不得包含 `top`、`left` 或 `right`。

### 3.7 Reduced Motion 合同

`prefers-reduced-motion: reduce` 下：

- Press/Fast/Base/Surface/Progress/Flip Tokens 变为 `1ms`。
- Loading Token 可以保持定义值，但 Spinner 必须 `animation: none`，绝不能出现 `1ms infinite`。
- 位移距离变为 `0px`。
- 禁止 3D 翻转、自动滑动、视差、循环脉冲和内容 stagger。
- Spinner 显示静态图标并依赖现有文字状态。
- `motion.css` 以末尾特定选择器覆盖现有 `base.css` 的 `0.01ms !important`；Surface 计算样式继续满足 `animation-name: none` 和 `transition-duration: 0s`。
- reduced-motion 下强制 `scroll-behavior: auto`。
- 页面、Sheet 和 Dialog 直接进入最终位置。
- Presence 由 JS 同步完成，不等待 `1ms` animation event。
- 焦点、ARIA live region、关闭、提交和导航逻辑不得依赖动画时长。

### 3.8 “首次播放”生命周期

新增 App 会话级 `MotionHistoryContext`，只保存动效 key 的 `Set<string>`，页面刷新后清空，不写 localStorage，不修改业务数据类型。

| 动效 | 唯一 key / 触发 | 重渲染 | 离开再返回 | 新实体 |
| --- | --- | --- | --- | --- |
| Processing 阶段完成 | `parse:{jobId}:stage:{index}`，仅未完成→完成 | 不重播 | 不重播 | 新 job 播放 |
| CourseReady 成功 | `course-ready:{bookId}:{lessonBuildJobId ?? "current"}` | 不重播 | 不重播 | 新 course/job 播放 |
| Diagnosis 条形结果 | `diagnosis:{submissionId}` | 不重播 | 不重播 | 新 submission 播放 |
| Home/Library 课程卡 | `course-card:{bookId}`，仅新节点 | 不重播 | 不重播 | 新 book 播放，最多 6 项 stagger |
| ChapterConfirm 阶段勾选 | `chapter-confirm:{bookId}:{chapterId}:{status}` | 同状态不重播 | 不重播 | 新状态播放 |
| 图片淡入 | 每个 `<img>` 的一次成功 load | 不重播 | DOM 重建可重播 | 新 URL 播放 |
| Flashcard 翻面/下一张 | 每次明确用户操作 | 按操作播放 | 按操作播放 | 按操作播放 |
| Plan/Assignment/筛选选中 | 每次明确用户操作 | 按状态变化播放 | 按状态变化播放 | 按状态变化播放 |

- MotionHistory 只决定是否播放，不改变实体状态和 API 数据。
- 同值轮询、普通 rerender 和 React StrictMode 重挂检查不得消费两次 key。

### 3.9 阶段所有权矩阵

| 阶段 | 可以实现 | 禁止提前实现 |
| --- | --- | --- |
| 1 | Tokens、`motion.css`、纯 Presence machine、hooks、ScreenTransition primitive、MotionHistory primitive、Button/IconButton、Spinner、motion 测试骨架 | `.nav-item`、页面接入、任何 Sheet/AI/Toast、业务 screen 动效、ProgressBar |
| 2 | 页面根、PrimaryNav、ActionSheet、Toast、AI Dialog/Orb、Scrim、焦点与导航方向 | 上传/解析/课程状态、Flashcard、页面局部内容 stagger |
| 3 | Upload/ParseReady/Processing/ChapterConfirm/CourseReady/Library 业务状态与 ProgressBar | Flashcard、Plan、Assignment、Diagnosis、其他页面局部内容 |
| 4 | 各目标 screen 的显式局部 `[data-motion-item]`、Flashcard、图片、列表/详情 | 再次动画页面根、修改 Stage 2 Surface 参数、全局选择器 stagger |
| 5 | 测试、压力、性能审计、重复 Token 清理和审核要求的修复 | 新增未在映射表定义的产品动效 |

- 同一 DOM 节点不能同时承担页面级和局部内容入场。
- 页面级动画只作用于 `ScreenTransition` 根。
- 局部动画只作用于显式 `data-motion-item`，不得给通用 `.screen-stack` 再加动画。

### 3.10 测试基础设施与稳定性合同

- 阶段 1 创建 `npm run test:motion`，固定为 `playwright test e2e/motion.spec.ts`。
- 每个阶段把新测试放入 `test.describe("Stage N ...")`，阶段自测运行完整 `npm run test:motion`，确保累积覆盖。
- `motion.spec.ts` 复用现有 `bookcourseApi` fixture、外网阻断、未处理 API、console error、pageerror 和 React warning guard。
- Playwright 继续使用现有 `workers: 4`，不得按测试结果动态调整。
- 软件键盘只复用现有 deterministic `visualViewport` shim 和 viewport-height resize，不声称模拟真实 iOS 硬件键盘。
- 测试同步只使用：
  - `data-motion-state`
  - computed style
  - DOM 卸载
  - 产品状态/ARIA 状态
- 不断言窄毫秒墙钟区间；只断言 Token 计算值、动画属性和最终状态。
- 不使用 `waitForTimeout`。
- 阶段 5 固定稳定性命令：

```powershell
npm run test:motion -- --repeat-each=3 --workers=4
```

- 三轮必须全部通过，无 retry、无顺序依赖。
- 动画运行中必须覆盖 viewport resize、orientation 模拟、visualViewport shim 更新、`animationcancel` 和 reduced-motion 运行时切换。

### 3.11 固定延时边界

- 禁止固定延时伪造业务状态、同步动画或同步 E2E。
- 保留现有 Toast `3200ms` dwell，这是既有产品展示时长，不属于动画同步。
- dwell 结束后才把 Toast requested value 设为 null 并开始退出。
- 新 Toast 替换旧 Toast 时取消旧 closing 和旧清理。
- Presence 的 `320ms` 后备只处理丢失 end/cancel 事件，不作为正常路径。

## 4. 统一审核协议

### 4.1 代理职责

#### `motion_author`

- 模型：`gpt-5.6-terra`
- `reasoning_effort: max`
- 长期复用同一代理。
- 每次只实施主代理明确授权的当前阶段。
- 不修改审查记录。
- 不进入后续阶段。
- 不暂存、不提交、不 push。

每阶段报告必须包含：

- 修改文件与实现内容。
- 运行的命令和精确结果。
- 动效行为和 reduced-motion 行为。
- 已知限制。
- 当前 `git diff --stat`。

#### `motion_reviewer`

- 模型：`gpt-5.6-sol`
- `reasoning_effort: max`
- 长期复用同一代理。
- 始终只读，不修改文件、不暂存、不提交。
- 每轮审核当前阶段和全部累积回归。
- 不只检查返修行。

统一结论：

- `APPROVED`：所有阶段要求和必需检查通过，没有未解决的可执行问题。
- `CHANGES_REQUESTED`：逐条提供严重级别、文件/行、问题、期望修复和验证方式。
- 非阻塞建议必须明确标记为 `NOTE`。

### 4.2 阶段门禁

每阶段严格执行：

1. 主代理授权当前阶段。
2. Terra 实施并自测。
3. 主代理写入“待审查”记录。
4. Sol 只读审核完整累积差异。
5. 若 `CHANGES_REQUESTED`，主代理记录问题并把全部意见交回 Terra。
6. Terra 完整返修并自测。
7. Sol 重新审核完整累积差异。
8. 只有收到 `APPROVED`，主代理才更新记录并授权下一阶段。

### 4.3 通用代码审查门槛

Sol 只有在以下条件全部满足时才能批准：

- 当前阶段完整实现，没有越过后续阶段或引入无关改动。
- `npm run lint`、`npm run test`、`npm run build` 通过。
- 阶段 1 起 `npm run test:motion` 通过；当前阶段 `Stage N` 分组和此前累积动效测试全部通过。
- 已实现阶段的累积测试通过。
- 现有 `test:responsive` 和 40 张视觉基线没有非预期退化。
- 不修改后端、生产 API、请求结构、轮询频率和公共业务类型。
- 不引入动画库或 UI 框架。
- 动画不会改变布局尺寸、安全区、44px 触控目标或导航合同。
- 没有重复可聚焦页面、焦点丢失、焦点锁定破坏或退出后焦点恢复错误。
- `prefers-reduced-motion` 行为与正常模式功能等价。
- 动画只以 `transform` 和 `opacity` 为主；非必要布局属性动画视为阻塞问题。
- 不存在持续干扰、闪烁、强震动、强弹跳或超过约定时长的装饰动画。
- 没有未处理的 console error、React warning、TypeScript error 或不稳定测试。
- E2E 不使用固定延时同步动画，不依赖执行顺序或外部网络；既有 Toast `3200ms` dwell 保持不变。
- `git diff --check` 通过，暂存区为空。

阶段命令固定为：

| 阶段 | 必跑命令 |
| --- | --- |
| 0 | 文档 trailing-whitespace、UTF-8、EOF、直接未跟踪文件检查 |
| 1 | `npm run lint`、`npm run test`、`npm run build`、`npm run test:motion`、`npm run test:responsive`、`npm run test:e2e`、`git diff --check`、未跟踪文本检查 |
| 2 | 与阶段 1 相同；`test:motion` 必须包含 Stage 1–2 |
| 3 | 与阶段 1 相同；`test:motion` 必须包含 Stage 1–3 |
| 4 | 与阶段 1 相同；`test:motion` 必须包含 Stage 1–4 |
| 5 | 最终必跑命令，加 `npm run test:motion -- --repeat-each=3 --workers=4` |

## 5. Implementation Stages

### 阶段 0：执行文档

实现：

- 创建本文档。
- 锁定 Motion Tokens、设备差异、文件组织、阶段范围、审查门槛和最终提交协议。
- 建立审查记录。

代码审查标准：

- 文档与当前 React、自定义 CSS、AppShell、ActionSheet、AI 和 Playwright 结构一致。
- 所有时长、缓动、位移、动效状态和 reduced-motion 决策已锁定。
- 明确默认手机、`min-width:768px && min-height:600px` iPad 和 `max-height:599px` 横屏覆盖的判定顺序。
- 每个 surface、状态反馈、Flashcard、进度和 Orb 都有唯一参数映射。
- 明确不引入 Tailwind、shadcn、GSAP 或 Motion。
- 明确页面只做新页面入场，不保留两个交互页面。
- 明确 Presence 所有权、公开 API、状态机、end/cancel 过滤、运行时 reduced-motion、焦点恢复和防御性清理策略。
- 明确导航方向、自动替换、初始/同页/空 history 和页面焦点合同。
- 明确 Orb 使用 transform/FLIP，布局落点属性不做 transition。
- 明确“首次播放”由 App 会话 MotionHistory key 控制。
- 明确阶段 1 建立 motion 测试基础设施，每个阶段都有可运行的定向门禁。
- 明确不修改业务 API、轮询和 mock 边界。
- 明确每阶段必须经 Sol `APPROVED` 才可继续。
- 明确最终只创建一个本地提交，不 push。

### 阶段 1：Motion Tokens 与基础能力

实现：

- 在 `tokens.css` 增加固定 Motion Tokens。
- 新增末尾导入的 `motion.css`。
- 新增 `useReducedMotion`。
- 新增 `useMotionPresence`。
- 新增 `ScreenTransition`。
- 新增纯 `presenceMachine` 与 Node Vitest。
- 新增 `MotionHistoryContext` primitive。
- 建立 `data-motion-state` 和 `data-motion-direction` 接口。
- App 根接入 `useReducedMotion`，只暴露可测试的 `data-motion-reduced`，不启用页面/浮层动效。
- 创建 `e2e/motion.spec.ts`、`npm run test:motion` 和 Stage 1 测试。
- 只为基础 Button、IconButton 实现映射表中的按压过渡；Focus Visible 保持现有静态 outline，不动画。
- 保留现有 Spinner，但纳入统一 duration 和 reduced-motion 规则。

测试：

- Tokens 计算值与约定一致。
- 正常模式和 reduced-motion 模式均能进入最终状态。
- Button/IconButton 按压不改变元素边界。
- Presence 纯状态机覆盖 null/open/closing/reopen/replace/end/cancel、entering/closing fallback、过期 generation 和 reduced 分支。
- `useReducedMotion` 对运行时媒体查询变化更新根属性，监听器清理经代码审查和真实 App reload/StrictMode console guard 验证。
- StrictMode 下不出现重复计时器或状态更新警告。

代码审查标准：

- 新基础能力没有实际启用后续阶段的页面/业务动效。
- `motion.css` 不包含布局宽高、断点或安全区覆盖。
- `useMotionPresence` 清理动画事件、后备计时器和 media query 监听器。
- reduced-motion 不依赖仅 CSS 隐藏逻辑，JS 延迟卸载也同步缩短。
- Stage 1 不得修改 `.nav-item`、ActionSheet、Toast、AI、ProgressBar 或任何业务 screen。
- 不使用全局 `transition: all`。
- 不永久设置大面积 `will-change`。
- 现有视觉基线最终状态不变。

### 阶段 2：页面、导航与浮层动效

实现：

- 在 App 页面内容区接入 `ScreenTransition`。
- 按导航方向表接入 `go`、`back`、`openSourcePage`、空 history、自动解析完成和同页导航。
- 新 screen 挂载后按焦点合同聚焦 `main`；初始挂载和同页导航不移动焦点。
- 手机/iPad/短横屏使用约定的不同入场位移。
- 手机底部导航和 iPad Navigation Rail 增加统一活跃态与按压反馈。
- App 层建立冻结内容的 `sheetView` Presence。
- ActionSheet：
  - 手机底部 Sheet 进入/退出。
  - iPad Chat 右侧进入/退出。
  - Source 宽对话框淡入/退出。
  - Note/EditChapter 居中缩放淡入/退出。
- Toast 增加退出动画。
- `Toast` 常驻并拥有 Presence，保留 `3200ms` dwell。
- `GlobalAIAssistant` 拥有 Dialog Presence；closing 期间 Orb 保持隐藏且不可聚焦。
- AI 对话框增加进入/退出；Orb 按 transform/FLIP 合同重构拖动与落点。
- Scrim 使用独立 opacity 动画。
- 阶段 2 把 `responsive.spec.ts` 内现有 deterministic visualViewport shim 机械提取到 `e2e/fixtures/visual-viewport.ts`，`responsive.spec.ts` 与 `motion.spec.ts` 共同导入；仓库只保留一份实现且既有行为/测试数量不变。

测试：

- 导航方向表全部路径、最终 screen 和 history 结果正确。
- 八个目标 viewport 断言实际 animation-name、duration、easing、位移方向和设备分类，重点覆盖 `874×402` 与 `834×1194`。
- 页面始终只有一个可交互内容树。
- 快速导航不会破坏 history 或卡住 motion state。
- Dialog Tab 焦点锁定、Esc 关闭和关闭后焦点恢复正确。
- Sheet 快速关开、closing 中换类型、EditChapter 删除后退出只有一个最终节点。
- Presence/ScreenTransition 的 entering 中 cancel、丢失事件 fallback、runtime reduce 和快速 nonce 替换最终均收敛到 `idle` 或正确卸载。
- Sheet 关闭并同时导航、触发器卸载和 AI 快速关开时焦点不落到 body、不被旧清理抢走。
- closing 中用指针、Enter、Space 和 form submit 激活冻结内容均不触发业务 handler；Tab/Shift+Tab/Escape 仍正常。
- 退出过程中不能重复提交或穿透点击背景。
- animationcancel、旋转、deterministic visualViewport shim 和 viewport-height resize 后浮层仍可见。
- Orb 拖动、pointerup settle、pointercancel、resize 和 reduced-motion 切换符合 FLIP 合同。
- reduced-motion 下所有表面直接落到最终位置。

代码审查标准：

- 页面入场不重新挂载 AppProvider 或清空业务状态。
- 导航视觉反馈不改变现有 44px 触控尺寸和 active/go props。
- 普通导航与 Upload 导航最终 transform 均保持当前 `scale(1)`；Upload 不使用 `1.3` 放大，既有视觉基线不应因静态比例改变。
- ActionSheet 调用方不增加必填动效参数。
- 退出动画完成前 Dialog 语义和焦点管理仍有效。
- Scrim、Sheet、Dialog 和 Toast 不动画 blur、width、height、top 或 left。
- Orb 的 `transition-property` 只包含 transform；pointermove 不逐帧写 React top/left。
- 旧 Presence generation 不能卸载或恢复新 surface 的焦点。
- 仓库仅有一份 `visual-viewport.ts` shim，responsive 与 motion spec 共用，原 responsive 场景与计数不变。
- iPhone/iPad 竖横屏无水平溢出、遮挡或点击延迟。

### 阶段 3：上传、解析与业务状态动效

覆盖：

- Upload
- ParseReady
- Processing
- ChapterConfirm
- CourseReady
- Library 中解析/失败/完成状态

实现：

- 文件选择、上传中、上传成功和上传失败按“通用局部内容/空态”“成功标记”映射反馈；不新增 drag/drop handler 或拖放业务能力。
- 进度条使用 `scaleX` 从当前值过渡到新值。
- Processing、CourseReady 和 ChapterConfirm 按“首次播放”生命周期表使用 MotionHistory。
- 后端轮询更新只动画映射表中的进度和阶段；百分比/数字文本静态替换，不做表外数字动画，也不重播整页。
- ChapterConfirm 选择章节、保存、删除和 Sticky Action Bar 状态提供局部反馈。
- 课程生成成功状态播放一次性成功反馈。
- 错误和重试状态使用颜色、图标和清晰文案，不使用摇晃。
- 图片载入使用稳定尺寸内的 opacity 过渡。

测试：

- 0、1、50、99、100% 进度连续更新正确。
- 进度不会在每次轮询时从 0 重播。
- 阶段勾选覆盖同值轮询、rerender、离开返回、新 job 和新状态 key。
- 上传、失败、重试、后台运行和成功流程保持可操作。
- 快速状态变化不会遗留 `entering/closing` 状态。
- reduced-motion 下状态内容和 ARIA live 更新保持一致。

代码审查标准：

- 不引入本地假进度或修改真实后端轮询。
- 不因动画增加 API 请求或重复提交。
- 进度动画不使用容器宽度变化造成布局抖动。
- 错误状态没有仅靠颜色或动画表达。
- ChapterConfirm 本地草稿、保存和删除行为保持不变。
- 长文件名、长章节名和错误消息不会因动画裁切。
- 页面离开后所有动画监听和后备计时器被清理。
- MotionHistory 只保存动效 key，不进入 API payload、localStorage 或业务类型。

### 阶段 4：学习工具与内容动效

覆盖：

- Home
- Library
- BookCourse
- Lesson
- SourceReader
- StudyPlan
- Flashcard
- Assignment
- Diagnosis
- MistakeBook
- Notes
- LessonReport
- Profile
- Community
- Export

实现：

- 首页和课程库只对新增或首次出现的卡片做有限 stagger，最多 6 项、每项 `30ms`。
- 图片加载在稳定 aspect-ratio 容器内淡入。
- SourceReader 翻页严格使用“SourceReader 翻页内容”映射，只替换页面内容并淡入，不滑动整份文档。
- StudyPlan 日期选择使用“选中态 Check/Pill”，任务完成使用“阶段完成 Check”，空态切换使用“通用局部内容/空态”。
- Flashcard：
  - 只翻转核心问答内容区，不复制来源按钮或外层操作。
  - 正反面约 `340ms`。
  - 动画期间禁止重复切换。
  - 不可见面 `aria-hidden`。
  - reduced-motion 下改为直接交叉淡入。
  - 下一张卡使用短距离淡入，掌握度与 Toast 行为不变。
- Assignment 选择/提交使用边框、背景和 Check 图标反馈。
- Diagnosis 结果和知识点条首次出现时增长，重复渲染不从 0 重播。
- MistakeBook、Notes 和 iPad 列表/详情只动画发生变化的详情区。
- Community、Report、Profile、Export 只对显式节点使用“通用局部内容/空态”，不增加表外动效或持续装饰动画。

测试：

- Flashcard 翻面、连续快速点击、换卡、查看原文和返回路径正确。
- reduced-motion 下 Flashcard 没有 3D transform。
- 计划任务更新、作业提交、诊断、错题筛选和笔记行为不变。
- 新增/删除列表项只影响相关节点。
- 图片成功、失败和跨页恢复无布局跳变。
- iPad 主从视图更新不会整页重播。
- 每个局部节点必须显式使用 `data-motion-item`，不得给页面根或 `.screen-stack` 叠加第二个入场动画。

代码审查标准：

- 同时 stagger 的元素不超过 6 个。
- 不对长列表每一项设置持续 animation-delay。
- Flashcard 不产生重复可聚焦内容或读屏重复。
- 动画锁只阻止重复动效触发，不阻止 Esc、返回或必要导航。
- Hover 只在 `(hover: hover) and (pointer: fine)` 下启用。
- 课程、计划、作业、诊断和 mock 行为没有业务扩展。
- 没有持续漂浮、视差、粒子或无意义脉冲。

### 阶段 5：动效测试、性能与最终验收

实现：

- 扩展阶段 1 已创建的 `e2e/motion.spec.ts`，完成 Stage 5 压力、性能和终验分组。
- 保留现有视觉截图测试的 `animations: "disabled"`，除非最终静态样式确有预期变化。
- 增加正常模式和 reduced-motion 模式对照测试。
- 增加快速连续导航、连续开关浮层、连续翻卡、轮询更新和旋转中断测试。
- 检查所有 motion state 最终回到 `idle` 或节点正确卸载。
- 审计并移除被新 Motion Tokens 取代的重复 duration/easing，但不做无关 CSS 重构。
- 完成最终差异、临时产物、空白和 Git 范围检查。
- 使用阶段 2 已提取的 `e2e/fixtures/visual-viewport.ts` 和 viewport resize 代理软件键盘，不声称为真机键盘测试。

动效专项必须覆盖：

- 页面 forward/back/replace。
- 手机和 iPad 页面差异。
- Bottom Nav 与 Navigation Rail。
- 四类 ActionSheet、Toast、AI Dialog 和 Orb。
- 上传、解析、进度、成功、失败和重试。
- Flashcard 翻面、换卡和重复点击。
- StudyPlan、Assignment、Diagnosis 和列表/详情。
- 旋转、visualViewport、软件键盘和短横屏。
- reduced-motion。
- 焦点锁定、焦点恢复和键盘导航。
- console、React warning 和未处理网络请求。

性能标准：

- 主要动画属性为 `transform` 和 `opacity`。
- 不存在全局 `transition: all`。
- 不永久保留大面积 `will-change`。
- API 轮询更新不触发整个页面重新动画。
- 四个 WebKit 项目重复运行稳定。
- 固定使用 `workers: 4`，`test:motion --repeat-each=3` 三轮全部通过且无 retry。
- 没有动效造成的水平溢出、触控目标变化或导航遮挡。

最终必跑：

```powershell
npm run lint
npm run test
npm run build
npm run test:motion
npm run test:motion -- --repeat-each=3 --workers=4
npm run test:responsive
npm run test:e2e
git diff --check
```

代码审查标准：

- 动效专项、响应式和完整 E2E 全部通过。
- 现有 40 张视觉基线无非预期差异。
- 正常模式和 reduced-motion 功能完全等价。
- 所有页面和浮层最终 motion state 可观察且稳定。
- 没有固定等待、外部网络或顺序依赖测试。
- 没有 trace、报告、截图差异文件、调试日志或临时文件进入 Git 清单。
- Git 差异只包含本文档、动效实现、必要测试与配置。
- Sol 第一次完整终审 `APPROVED` 后，主代理只更新阶段 5 审查记录。
- Sol 再执行一次封印审核；封印 `APPROVED` 后不得修改任何文件。

## 6. 最终提交协议

- 所有阶段不创建中间提交。
- 当前 `HEAD e2f88f4` 是统一动效基线。
- 阶段 5 第一次完整终审通过后，只更新阶段记录。
- Sol 封印审核通过后，不再修改任何源码、文档、测试或快照。
- 显式暂存本文档、动效源码、相关前端实现和测试文件。
- 必须核对：
  - `git diff --cached --name-only`
  - `git diff --cached --check`
  - `git diff --check`
  - 未暂存和未跟踪文件清单
- 从 `e2f88f4` 到交付只创建一个本地提交：

```text
feat(frontend): add accessible motion system
```

- 提交后验证工作区干净。
- 不 push、不创建 PR。
- 若出现用户无关改动，不修改、不暂存、不清理；无法安全分离时停止提交并报告。

## 7. 审查记录

| 阶段 | 状态 | Terra 自测 | Sol 审查轮数 | Sol 结论 | 备注 |
| --- | --- | --- | ---: | --- | --- |
| 0 执行文档 | 已完成 | 直接空白与 EOF 检查通过 | 3 | `APPROVED` | 三轮文档审查完成；测试顺序、组件参数、Presence/焦点/导航/Orb/reduced-motion/生命周期和阶段所有权全部锁定 |
| 1 Tokens 与基础能力 | 已完成 | lint、20 项 Vitest、build、16 项 motion、117 responsive 与 189 全量 E2E 通过；各 3 项条件跳过；`git diff --check` 通过 | 2 | `APPROVED` | 第 1 轮两项意见已返修；generation fallback 上界稳定，四项目真实 `:active`、精确缩放、时长、布局与 reduced-motion 均获复审批准 |
| 2 页面、导航与浮层 | 已完成 | lint、26 项 Vitest、build、108 项 motion、117 responsive 与 281 全量 E2E 通过；各 3 项条件跳过；`git diff --check` 通过 | 3 | `APPROVED` | 两轮返修后获批：页面根几何与完整测试矩阵稳定，Toast surface 按 `presenceId` remount 并隔离旧同名事件 |
| 3 上传、解析与业务状态 | 已完成 | lint、26 项 Vitest、build、184 项 motion、117 responsive 与 357 全量 E2E 通过；responsive/E2E 各 3 项条件跳过；reduced→normal 定向 4/4、ChapterConfirm 20/20、`git diff --check` 通过 | 3 | `APPROVED` | 前两轮意见全部返修；第 3 轮独立复核 motion 184/184、responsive 117+3 skip、Stage7 56/56，并实证 reduced 保存恢复、Processing、ParseReady ARIA 与 SourceReader 真实 3200ms 路径后批准进入阶段 4 |
| 4 学习工具与内容 | 已完成 | lint、26 项 Vitest、build、268 项 motion、117 responsive 与 441 全量 E2E 通过；responsive/E2E 各 3 项条件跳过；Mistake 定向 4/4、Stage4C 28/28、`git diff --check` 通过 | 2 | `APPROVED` | 第 1 轮 2 项 P1 已返修；第 2 轮独立实证初始详情静态、记录不筛、后续选择唯一入场，并重跑全量门禁后批准进入阶段 5 |
| 5 测试、性能与最终验收 | 已完成 | lint、26 项 Vitest、build、284 项 motion、三轮无 retry 852/852、117 responsive 与 457 全量 E2E 通过；responsive/E2E 各 3 项条件跳过；Stage5 定向 16/16、`git diff --check` 通过 | 1 | `APPROVED` | Sol 第一次完整终审独立复跑全部必跑门禁并完成 Git、40 张视觉基线、产物与进程封口；仅更新本审查记录，等待第二次封印审核 |
