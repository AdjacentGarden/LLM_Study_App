# 视觉还原与验收记录

**最新验收范围已调整：用户取消一比一界面还原，改为功能和动效完整可用。** 下文保留此前视觉研究、采集方法与中间证据的追溯记录，不再作为发布门槛。新的像素校准和整套截图采集已停止；部分中间采集不能宣称为最终 53 场景验收通过。功能结果以实际 XCTest / XCUITest 的执行计数、失败记录和交互流程为准。

新增 `CourseFunctionalUITests` 已在独立 iPad Pro 11 / iPadOS 26 模拟器真实执行 **3 项、3 通过、0 失败、0 跳过**（`output/Course-Functional-02.xcresult`）：三步引导完成并显示所填称呼；六步课程问卷、章节编辑未保存关闭/保存重开/移除取消、确认生成及进入学习；社区课程导入、六步问卷、成功及进入学习。测试使用唯一 fixture 会话，不读生产数据，也没有开启静态视觉或 Reduce Motion 标志。首轮两项失败来自自动化中心点击将输入光标放在中文标题中间，修正为从尾端清空并立即断言输入值后，完整三项重跑通过。目录中未提供原文页码的章节显示“原文页码待补充”，不再显示误导性的 `0–0`。

本次视觉真相来自原 React 仓库**当前工作区**，包括未提交的修改。`References/source-status.txt`、`source-working-tree.patch`、`source-manifest.json` 保存来源；不能用旧设计稿或旧 PNG 覆盖当前代码的行为。新应用采用 SwiftUI、最低 iOS 17，直接复用原素材、SVG 图形、数据与 Noto Sans SC 字形。

## 已有证据

`References/VisualBaselines/manifest.json` 和同目录 PNG 是通过只读运行原 Vite 前端、Playwright WebKit 实拍得到的 159 个场景（原四组 106 张，加同原生 iPad 尺寸的 53 张）。包括手机 402×874、平板 834×1194 的全部 28 个路由、6 类弹层、独立 AI 助手、引导后两步、课程画像后五步，以及手机 874×402、平板 1194×834 的 11 个主要横屏路由。另保留相同场景的原生平板视口 834×1210、1210×834，便于逐页同尺寸对照。原前端没有被修改；外部网络与 `/api/` 请求被阻断。manifest 记录每场景实际 route、实际数据选择、区域 bounds、字体、安全区与截图失败信息。

截图 fixture 仅来源于仓库演示数据，不读取设备上的个人课程、笔记、附件或浏览器用户数据。截图时固定称呼、日期、资料、课程画像、选中社区书，并启用 Reduce Motion。应用功能测试使用独立目录；截图不是个人数据库迁移。

**截图覆盖不等于视觉验收通过。** 原生截图、叠图、差异图与像素统计只能作为审阅证据；本报告没有给出未经测量的“误差不超过 2pt”结论。字号轮廓相同也仍存在 CoreText 与 WebKit 的排版、基线和抗锯齿差异。

## 可复现命令

源网页基线需要原前端已安装锁定版本的 npm 依赖并在本地运行。以下维护脚本只读取原工程：

```bash
# 在原 React 仓库启动其 Vite 服务（不要在 SwiftUI 构建中隐式启动它）
# 在本仓库另一个终端运行
BOOKCOURSE_SOURCE=/absolute/path/to/original-frontend WEB_BASE_URL=http://127.0.0.1:4178 node scripts/capture-web-baselines.mjs
# 可限定某个设备，脚本会保留其他设备的 manifest 记录
CAPTURE_DEVICES=ipad-834x1210,ipad-landscape-1210x834 BOOKCOURSE_SOURCE=/absolute/path/to/original-frontend WEB_BASE_URL=http://127.0.0.1:4178 node scripts/capture-web-baselines.mjs
```

原生采集需要 Xcode 已构建出 `BookCourse.app`，以及 Pillow。`scripts/capture-native-baselines.py --plan-only` 可先查看 53 个计划场景（42 竖屏 + 11 横屏），不操作模拟器。默认专用设备为 iPad Pro 11 的 `C4116145-9865-4813-9CDA-33CCE36FA756`；脚本拒绝 `booted` 这种不明确的设备选择，以免干扰其他 UI 测试。

```bash
.tools/bin/python scripts/capture-native-baselines.py --orientation portrait
# 基于已有 manifest 重建比较，不启动应用
.tools/bin/python scripts/capture-native-baselines.py --compare-only
# 手机用独立UDID，默认scale3、安全区top62/bottom34
.tools/bin/python scripts/capture-native-baselines.py --udid YOUR_PHONE_UDID \
  --device-family iphone --orientation portrait
```

首次在新检出中执行对照脚本，可建立独立的维护工具环境（不属于 App 运行依赖）：

```bash
python3 -m venv .tools
.tools/bin/python -m pip install -r scripts/requirements-visual.txt
```

原生视觉采集另外传入 `--visual-baseline`，只在视觉 fixture 中加入与 Web 相同的一条旧版演示笔记；正常应用和功能流测试不设置此标记。引导与问卷中间步使用仅 fixture 生效的 0-based `--fixture-step`，每个场景使用 `--fixture-session <唯一 ID>`，生成独立临时存储；脚本记录已安装 app 可执行文件和 debug dylib 的 SHA-256、模拟器 runtime、运行参数、截图尺寸与哈希。它不重新构建，不复制生产数据库，也不自动将外部网络调用视为必要依赖。

横屏需**真实设备旋转**。iPadOS 的窗口模式可能忽略 `requestGeometryUpdate`，脚本会检查截图宽高，拒绝把竖图旋转成横图来代替实拍。可靠的横屏入口为 XCTest 的 `testLandscapeCorePages`：它先显式回到 portrait，逐个路由启动后再切换 `XCUIDevice.shared.orientation = .landscapeLeft`，使物理方向变化确实触发事件，再等待窗口宽大于高，并为 11 个路由保留原始截图附件。实测本次 iPadOS 26 的 `app.screenshot()` 附件虽然原始宽高为横屏，内容却侧转并带黑边，因此不能作为正常横屏通过证据。维护脚本在同一个真实旋转 XCTest 运行期间读取未经裁切或旋转的 `simctl io <UDID> screenshot` 屏幕帧，并按附件的路由名称和时间戳选取其前 0.1–3 秒的稳定画面；最终仍需人工确认页面内容与方向。异常原附件与对应取帧时间留作溯源，不用图像旋转伪造横屏。

```bash
# 需要事先完成统一构建；test-without-building 不会隐式改变 app
.tools/bin/python scripts/capture-landscape-framebuffer.py \
  --udid C4116145-9865-4813-9CDA-33CCE36FA756 \
  --result output/ipad-landscape.xcresult --output output/iPadLandscapeFramebuffer
.tools/bin/python scripts/capture-native-baselines.py \
  --landscape-xcresult output/ipad-landscape.xcresult \
  --landscape-frame-index output/iPadLandscapeFramebuffer/frames.json
# 手机换成独立UDID，第二条另加 --udid PHONE_UDID --device-family iphone
```

请勿在手机 UI suite 正使用同一个模拟器时运行手机采集，也不要在采集期间覆盖 app 二进制。一次采集应来自同一安装版本。

## 比较方法与尺寸边界

原生 iPad Pro 11 实际截图为 1668×2420 像素，即 **834×1210 逻辑点**；源网页平板参考为 **834×1194 CSS 像素**。竖屏高度相差 16pt；横屏原生将为 1210×834，而网页为 1194×834，宽度相差 16pt。原四组参考与原生视口不同，依赖高度、百分比、滚动和固定底边的元素不能直接按绝对位置宣称通过。新增同尺寸 Web 两组后，脚本优先选择与原生截图逻辑尺寸相同的源图；原 834×1194/1194×834 仍保留，供追溯来源与响应式审阅。

比较脚本将原生图按设备 scale 还原到 1 逻辑点每像素，不拉伸任何一张图。在两者共同矩形上生成：

- 左源右原生对照图，缺少的视口区域以灰色显示。
- 50% 透明叠图。
- RGB 差异热图、共同矩形 RGB 平均绝对差、任意通道差大于 24 的像素比例。

这些指标没有设置通过阈值。它们会受到内容、抗锯齿、系统栏、设备高度与滚动状态影响，不能替代边界测量。生成结果位于 `output/VisualComparisons/`，清单位于 `output/NativeScreenshots/capture-<UDID>.json`。

Web manifest 已记录 `.ios-status-bar` 和 `.home-indicator` 的 bounds：手机状态区 62、高度 34 的底区；平板状态区 24。原生保留真实系统状态栏与安全区，避免额外叠加网页模拟系统栏。脚本只在统计时排除源实测系统区和显式配置的原生 top/bottom，默认 iPad 24/20；配置值及其来源会写入 manifest。页面标题的内容间距则按源样式复现：平板状态区后 42pt、手机后 4pt。源平板标题 y66 是内容布局要求，不能把真实系统状态区再画一次。

每一场景的 fixture 必须使用**实测状态**。Web 顶层 seed 想选 c2s1，但 React 恢复后实际 `fixtureState.chapterID` 为 c1s1；原生脚本逐场读取实际值。首页的下一步按保存位置→首个待完成计划→课时→目录选取，因此 activeChapter=c1s1 时仍可能推荐 c2s1。章节编辑源弹层显式编辑 c2s1，背景仍是 c1s1；原生编辑使用选中章节，比较记录会明确这个背景状态差异。它不是像素误差。

## 实施中已修正的差异

首页恢复源轮播封面比例、重叠、顺序、选中缩放、负 gutter、渐变遮罩；创建按钮避免 20pt 中文文字截断；本章工具使用 194pt 卡高、118pt 预览、76pt footer、8pt gap、原小图标与薄荷/蓝/紫配色。学习安排按源三列竖卡，辅助文字仅为可访问性提示。首页继续学习与工具入口依源的下一步选择规则，而非直接使用任意当前选中章节。书架完成度按真正仓库计划的 done/tasks 计算，排除仅旧学习计划页展示的 `front-end-mock-plan:` 任务；回归覆盖真实已完成任务仍被计入。

上传保持白色 210×210 添加块、选中资料托盘、多选、移除和真实文件名。解析确认与社区详情共用封面跨越薰衣草背景与白色 56pt 圆角内容区的布局；底部主操作、解析指标、社区四列统计与评论按原结构呈现。解析进度复用原八帧 sprite，Reduce Motion 保留第 0 帧；五个阶段卡与 CSS Grid 的空闲行高分配对应。原网页进度标签包含 PostgreSQL、BGE-M3 与 pgvector；原生没有执行这些后端操作，保留五卡几何并显示真实本机处理步骤，因此文案不是逐字相同。章节确认默认折叠根章节并显示文件概览；原书目录和页码编辑保持层级、后代删除、页码上限和冲突校验。

引导后两步也纳入截图：目标卡使用原 64pt sticker 与 150pt 卡高；步骤图标恢复蓝色/桃色；每日时间为 50pt 居中按钮，不额外加入问卷用的选中勾；跳过按钮为 44pt 灰色常规文字。

功能审计补齐了两处真实差异：引导步骤/称呼/目标/每日时间保存草稿，完成后清理；课程建议汇总当前课程全部资源的既有计划任务，按源评分、预算、碎片时间分段、复盘偏好生成建议，不补造教材内容。社区完整问卷后进入 `communityImport` 成功页，而不是误走文件解析完成页。新建课程保存文件时等待本机整理完成再直接进入学习问卷，与源保存入口一致；旧解析路由仍保留。文件解析既可后台继续，也可显式取消；取消保留原件以供重试。

## 功能边界与验收范围

以下边界在原演示前端已存在，不能把移植误写成新增服务能力：语音录制/播放/本机保存可用，但整理文本仍为演示稿；手写使用 PencilKit 保存笔迹，文字识别仍为演示流程；扫描 PDF/图片没有通用 OCR；Office 只提取 XML 文本，不还原完整排版；本地新资料仅形成忠于原文的阅读片段，不伪造测验或闪卡。文件支持与限制详见 `DATA_AND_REPRODUCIBILITY.md`。

保留旧 `export` 路由用于路由覆盖，它与源一样仅有演示完成提示，不能据此宣称生成了 PDF。保留原笔记页的演示入口，仅显示预览与完成提示，不生成可分享的 PDF。AI、积分、教材引用、原页、录音、手写、导入、社区身份与持久化由各自 XCTest/UI suite 另行验证；静态截图不证明点击后行为。

本次人工对照包括全部原生路由/弹层的同尺寸左右图，并针对首页、解析确认、处理阶段、书架、课程分组、个人页和完成页检查大图；仅缩略预览不能确认文本或返回按钮。原生普通系统栏的字形/日期、真实安全区、文字换行与字号应分别检查，不能把系统栏差异混成页面内容差异。此轮审阅发现的封面页、书架卡高、课程分组、问卷按钮、完成页纵向位置等显著差异均已形成明确修正；最终构建后的截图需要覆盖这些修正后再下结论。

当前源完成页的 focused 模式只显示云朵和成功标记，完成说明放在无障碍 live region 中；没有在截图中缺少一组应可见的统计或完成文案。Reduced Motion 图记录其稳定静态构图，不证明正常动画的时长和中断行为。

最终严格样式验收仍需在相同逻辑视口、同数据、同滚动位置逐页测量关键边界；动画需关闭 Reduce Motion 后单独检验触发、时长、方向、打断/返回。截图脚本中的 `geometryToleranceResult` 固定为 `NOT_ASSERTED`，不得用“全部路由已截图”替代此验收。
