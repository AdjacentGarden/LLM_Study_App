# 云径 CloudPath

云径 CloudPath 是一款把用户上传的教材、PDF、图片或文档转成 AI 互动课程的学习应用。项目采用前后端分离结构：

```text
CloudPath_app/
  backend/   FastAPI 后端：上传、解析、OCR、RAG、课程生成、问答、作业诊断
  frontend/  React + Vite 前端：移动端学习界面、上传流程、课程页、问答与诊断页
```

当前上传格式为 PDF、PNG、JPG/JPEG、JP2、WEBP、单帧 GIF、BMP、单页
TIF/TIFF、DOCX、PPTX 和 XLSX。旧版 `.doc`、`.ppt`、`.xls` 不受支持。
Office 文件会在进入 MinerU 前执行 OOXML 结构、真实类型、关系、XML 与
ZIP Bomb 安全校验；PPTX/XLSX 引用分别使用幻灯片和工作表语义，DOCX
使用标题路径与块序号，不虚构 PDF 页码。

## 后端启动

```powershell
cd backend
python -m pip install -e ".[test]"
$env:BOOKCOURSE_AUTH_MODE="optional"
$env:BOOKCOURSE_PARSER_PROVIDER="pymupdf"
$env:BOOKCOURSE_RAG_INDEX_PROVIDER="artifact"
$env:BOOKCOURSE_USE_WORKER="false"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

以上是无需外部服务的本地单用户模式，适合开发和体验 PDF 文本层解析。生产或多人环境请使用严格鉴权，并由可信网关或服务端会话附加密钥：

扫描教材、复杂表格和公式建议启用高精度文档解析配置：

```powershell
python -m pip install -e ".[test,ocr,rag]"
$env:BOOKCOURSE_PARSER_PROVIDER="mineru"
$env:BOOKCOURSE_MINERU_BACKEND="hybrid"
$env:BOOKCOURSE_MINERU_EFFORT="high"
$env:BOOKCOURSE_OCR_PROVIDER="paddleocr-vl"
$env:BOOKCOURSE_OCR_MODEL="PaddleOCR-VL-1.6"
$env:BOOKCOURSE_OCR_QUALITY_PROFILE="quality"
$env:BOOKCOURSE_OCR_TEXT_FALLBACK_ENABLED="true"
$env:BOOKCOURSE_OCR_CACHE_ENABLED="true"
$env:BOOKCOURSE_EMBEDDING_PROVIDER="bge_m3"
$env:BOOKCOURSE_RERANKER_PROVIDER="bge"
```

该配置优先保留 PDF 原生文本，再对扫描页使用完整的布局分析与 VLM
识别。没有 GPU 时也可使用 CPU，但复杂页面延迟会明显增加；吞吐优先时可将
MinerU effort 调为 `medium`，并配置 PaddleOCR-VL 推理服务。

```powershell
# 后端终端
$env:BOOKCOURSE_AUTH_MODE="strict"
$env:BOOKCOURSE_API_KEY="<local-development-key>"

```

不要把后端 API 密钥写入 `VITE_*` 环境变量；这类变量会被打包进浏览器代码。

可选 DeepSeek 配置：

```powershell
$env:BOOKCOURSE_LLM_PROVIDER="deepseek"
$env:BOOKCOURSE_RAG_LLM_PROVIDER="deepseek"
$env:BOOKCOURSE_DEEPSEEK_API_KEY="<your DeepSeek API key>"
$env:BOOKCOURSE_DEEPSEEK_LESSON_MODEL="deepseek-v4-pro"
$env:BOOKCOURSE_DEEPSEEK_RAG_MODEL="deepseek-v4-flash"
```

不要把真实 API key 写进仓库或打包文件。

## 前端启动

```powershell
cd frontend
npm install
$env:VITE_BOOKCOURSE_API_BASE_URL="http://127.0.0.1:8000"
$env:VITE_BOOKCOURSE_USER_ID="local_user"
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173/
```

## 验证命令

后端：

```powershell
cd backend
python -m pytest
```

前端：

```powershell
cd frontend
npm run lint
npm run test
npm run build
```

## 打包说明

当前整理目录已经排除以下内容：`node_modules`、`dist`、`backend/data`、运行日志、Python 缓存、虚拟环境和本地密钥文件。交给他人时，可以直接压缩 `CloudPath_app` 文件夹；对方需要自行安装依赖，并配置自己的 LLM API key。
