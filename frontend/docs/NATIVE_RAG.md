# 原生教材检索与助手

## 固定模型与数据

生物课本使用原仓库已经构建的 `biology-required-2-rag-v1`，包含 171 个检索块、125 个 PDF 页的 page map、BM25 索引与 171×512 的 little-endian Float32 向量。每个块保留书 ID、章节、PDF 页、印刷页与原文。模型为 `Xenova/bge-small-zh-v1.5` 的 `onnx/model_int8.onnx`，固定 revision：

```text
75c43b069aac4d136ba6bc1122f995fedcfd2781
model SHA-256: b9837c19ce154ff0726d398ee77abbc03a7faf0476c6f93016c84e531be7ebb5
```

模型与 tokenizer 完整存于 `Resources/Content/rag/models/`；没有首次启动下载。模型约 23.9 MB。向量、索引、原页与所有模型文件的 SHA-256 由 manifest 与内容校验器核对。源 manifest 的 `provider: wasm` 记录原来的构建环境；iOS 查询使用原生 ONNX Runtime 1.24.2，不载入 WASM 或 JavaScript。

## Tokenizer 与推理

Swift tokenizer 保持源 BERT normalizer 的 **`lowercase: false`**，不能根据中文模型印象擅自改为小写。包括 Unicode 清理、中文字符切分、标点、WordPiece、特殊 tokens 与最大长度，均用原 Transformers.js 3.8.1 AutoTokenizer 生成的固定结果核验。

查询前缀保持“为这个句子生成表示以用于检索相关文章：”。序列加入特殊 tokens 后按原库行为截断到 512；不能自行改为“截断正文但永远保留最终 SEP”。61 个金标包含 50 条评测 query，以及大小写、重音、标点、emoji、空白、特殊 tokens 和超长输入。`scripts/generate-tokenizer-golden.mjs` 使用本地 tokenizer 文件，禁止远端模型，只运行 tokenizer，不启动 ONNX 推理或 Worker。

推理由 `Core/TextbookRetriever.swift` 的 actor 隔离，使用 CLS pooling 后 L2 normalize 为 512 维向量。词法模式继续保留；初始化/推理失败不阻止原文阅读。当前语料是一个固定课本索引，新增个人资料不会冒充已经拥有 BGE 预计算向量。

## 排序、阈值与引用

混合权重保持 semantic 0.75、BM25 0.20、chapter prior 0.05。可靠证据阈值与高置信阈值均为 0.6037，纯词法回退阈值为 58.6248。章节优先只影响合法范围，不能用错误书籍或缺失页面补引用。小数在源 manifest 中已有舍入，不能用评测报告中舍入前的 accepted-count 直接断言当前运行结果。

引用由本地实际命中的可靠 chunk 生成，保留 bookID / chunkID / PDF 页及文本；无可靠依据时不生成教材页码。原资料中缺失第 1 章正文，因此不能对该部分声称有 PDF 原文证据。新导入资料使用自己的文本块执行保守词法匹配，不跨书借用生物页码；无文本层的扫描 PDF 和图片无 OCR 可检索内容。

离线助手是原文摘录与引用式回答，**不是设备上运行的生成式大语言模型**。可选在线助手用个人配置调用 DeepSeek，并通过 `search_textbook` 工具取得本地证据；tool 返回的是资料而非指令，UI 引用仍由实际检索结果产生。没有 key、fixture 模式、测试强制离线时不调用外部服务。在线异常明确报错，不隐式声称已完成云端生成。

## 评测与验收

`Resources/Content/rag/biology-required-2-rag-v1/evaluation-set.json` 固定 50 个 query：34 个教材直接/改写问题，8 个闲聊，8 个不应引用本教材的通用问题。用这组数据同时检查召回、章节或页面命中、接受引用精度与非教材误引用。`rag-golden.json` 另存 7 条 BM25 原始排名与分数，`tokenizer-golden.json` 存 61 条 tokenizer 结果。

运行 `./scripts/test.sh --device SIMULATOR_UDID --only BookCourseTests/RAGTests` 可单独核验实际 Core 实现。测试包括 cased tokenizer 精确金标、BM25 对齐、原生模型真实加载/推理、50 条语义检索评测、离线助手闲聊与引用隔离。模型不可用或非预期回退不应被当成“语义测试通过”。完整检查还应执行其余 importer / storage / UI tests。

原 WASM 预生成向量与原生量化推理存在数值差异，测试允许明确的小范围分数偏差，同时要求召回与引用质量。源 evaluation-report 记录的是源 WASM 校准结果，不应把它改名当作 iOS 测试报告。每次模型、tokenizer、排序、阈值或运行时版本改变，都需在实际 iOS 目标重跑这 50 条；不能只凭模型文件哈希相同认定行为相同。

分数与接受结果只针对该固定教材及这组评测；不代表任意用户资料上的通用检索质量，也不代表离线回答等同在线模型。未来新增向量索引构建、OCR、更多课本或在线模型，需要各自数据授权、范围隔离和独立评测。

本轮最终统一iOS模拟器测试的原生BGE指标为recall@5 1.0、top3 0.970588、接受引用precision 0.954545、非教材falseCitationRate 0；纯BM25为recall@5 0.970588、top3 0.941176、precision 1.0。实际保守阈值下接受召回分别为0.617647与0.058824。原始XCTest文本与JSON、附件来源记录归档于 `References/Validation/`，不把源WASM报告冒充原生结果。
