# 知我 - Adaptive Book Learning

这是一个与旧 `LLM_Study_App` 完全独立的新项目。它把任意一本书重建成可追溯的高质量文本，再通过主动访谈和自适应诊断建立学习者画像，最后按章节为不同用户生成不同深度、重点和练习方式的课程。

## 当前可运行内容

- 手机端模拟页：书籍状态、主动访谈、实时画像、自适应题目和信心输入。
- FastAPI：上传、处理队列、书籍状态、访谈状态机和诊断回答接口。
- 扫描 PDF 自动判型、MinerU 输出归一化、OCR 质量门控和 LLM 二次清洗合同。
- IRT/CAT + Beta 后验的选题、停止和画像更新内核。
- 个性化策略、章节课程数据模型、带原文引用的生成合同。
- 页码/文本块级混合检索基线。
- DeepSeek 教材可核验答疑：证据覆盖规划、原子化 claims、逐字引用校验和教材外确定性拒答。

详细设计见 [技术架构](docs/TECHNICAL_ARCHITECTURE.md) 和 [样例扫描书诊断](docs/SAMPLE_PDF_DIAGNOSTIC.md)。
扫描生物教材的最新 RAG 与回答生成结果见 `artifacts/rag/biology-20260829/grounded_qa_release_report.md`。
持久化 RAG/API 的完整验收见 [模块 1 报告](docs/MODULE_01_PERSISTENT_RAG_QA.md)。
可恢复 OCR 队列的完整验收见 [模块 2 报告](docs/MODULE_02_DURABLE_OCR_JOBS.md)。
通用章节结构与证据化摘要的完整验收见 [模块 3 报告](docs/MODULE_03_VERIFIED_CHAPTER_STRUCTURE.md)。
主动式学习者诊断与可恢复画像的完整验收见 [模块 4 报告](docs/MODULE_04_ACTIVE_LEARNER_DIAGNOSIS.md)。
证据化个人课程与持续掌握闭环的完整验收见 [模块 5 报告](docs/MODULE_05_PERSONALIZED_COURSE_LOOP.md)。
移动端前端与真实全栈交互的完整验收见 [模块 6 报告](docs/MODULE_06_MOBILE_FRONTEND.md)。
4090 生产部署、并发与恢复验收见 [模块 7 报告](docs/MODULE_07_PRODUCTION_RELEASE.md)。
知识追踪、稳定 CAT 与 FSRS 融合验收见 [模块 8 报告](docs/MODULE_08_ADAPTIVE_FUSION.md)。

## 本地启动

后端：

```bash
cd backend
cp ../.env.example .env
uv sync --extra dev --extra pdf
uv run uvicorn adaptive_learning.api.app:app --host 0.0.0.0 --port 8100 --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5174`。开发服务器会把 `/api` 代理到 `8100`。

## 密钥安全

PUCODING 或 DeepSeek 密钥只放在部署 Secret 或本地 `.env`，不得提交到 Git。模型返回的事实也不能直接进入课程：必须通过 JSON 合同、来源引用和质量门控。

## 验证

```bash
cd backend
uv run pytest
uv run ruff check .
uv run mypy

cd ../frontend
npm run build
```
