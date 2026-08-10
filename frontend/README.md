# BookCourse AI 前端

这里是与 `../backend` FastAPI 服务配套的正式 React + TypeScript 前端。界面来源于最新的 `littleri/study_app_demo`，正式运行默认使用真实后端；内置 `DemoRepository` 仅用于隔离的 UI 自动测试。

## 本地运行

先启动后端 `http://127.0.0.1:8000`，然后执行：

```powershell
npm install
$env:VITE_BOOKCOURSE_API_BASE_URL="http://127.0.0.1:8000"
$env:VITE_BOOKCOURSE_USER_ID="local_user"
npm run dev -- --host 127.0.0.1 --port 5173
```

打开 `http://127.0.0.1:5173/` 会进入带 iPhone/iPad 金属框的设备预览工作台。直接打开应用本体可使用：

```text
http://127.0.0.1:5173/?embedded=device-preview
```

## 数据模式

- 默认：真实 FastAPI，包括上传、解析、OCR、章节、课程、原文页、RAG、作业诊断、错题和学习计划。
- 演示/测试：设置 `VITE_BOOKCOURSE_USE_DEMO_REPOSITORY=true` 后使用固定本地数据，不发送 API 请求。
- 正式运行不要开启演示数据开关。

## 验证

```powershell
npm run lint
npm test -- --run
npm run build
npx playwright test
```

生产构建对各学习页面按需拆包；大型演示教材数据位于独立异步 chunk，不会进入真实后端模式的首屏加载路径。
