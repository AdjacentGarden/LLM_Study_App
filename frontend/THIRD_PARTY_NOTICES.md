# 第三方许可与素材来源

本文件说明本次原生迁移保留的第三方组件与素材。完整许可文本在 `LICENSES/`，并复制进应用的 `Content/licenses/`。原有 `Features/Notes/LICENSE.lucide.txt`、`Resources/Content/icons/LICENSE.phosphor-icons.txt`、字体 OFL 与字体目录 README 原样保留。

| 组件/资源 | 固定来源 | 许可与记录 |
| --- | --- | --- |
| ONNX Runtime Swift package / native binary | Microsoft `onnxruntime-swift-package-manager` 1.24.2，revision `b7fb7f7dea8a2469e6335d95a61b8f36d0dc83b2` | MIT；保留 package/LICENSE、上游运行时第三方 notices |
| ZIPFoundation | weichsel/ZIPFoundation 0.9.20，revision `22787ffb59de99e5dc1fbfe80b19c97a904ad48d` | MIT |
| Lucide SVG 路径 | 原 `lucide-react` 1.18.0 安装包 | ISC；原 LICENSE 同时列出由 Feather 派生、适用 MIT 的图标与原版权声明 |
| Phosphor 图标路径 | 原 NavigationIcon / 已保留 notice | MIT，Copyright (c) 2023 Phosphor Icons |
| Noto Sans SC | 原 public/assets/fonts 的 400/700 WOFF | SIL Open Font License 1.1；只转换容器与修正实例名称，保留轮廓 |
| BGE small Chinese v1.5 | BAAI/bge-small-zh-v1.5，经 Xenova ONNX 转换；固定 revision `75c43b069aac4d136ba6bc1122f995fedcfd2781` | 原 model-manifest 标注 MIT；BAAI 模型卡标注 MIT；固定 Xenova 转换仓库没有独立 LICENSE 文件，保留模型来源记录与上游 FlagEmbedding MIT 文本 |

ONNX 与 ZIPFoundation 由 SwiftPM 按 `Package.resolved` 下载。React、Vite、Capacitor、Transformers.js 和浏览器 WASM 不打包到原生应用；其中 TypeScript / React / Transformers.js 只用于显式的维护导出或金标生成，不是应用运行依赖。fonttools 也只用于维护转换，应用不包含 Python 运行时。

模型源：[固定 Xenova revision](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781)、[BAAI 模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)、[FlagEmbedding 许可](https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE)。这些记录说明权重与转换来源，并不把教材语料的版权变为 MIT。

业务源码、界面设计、教材原页/摘录、书封、示例视频和其他插画来自用户指定的原仓库工作区，哈希记录在 `References/`。原仓库没有提供覆盖所有这些内容的统一根 LICENSE。本迁移没有擅自为全部源码和素材添加 MIT，也未补充或推定教材、封面、视频的再分发授权。对外发布、公开仓库或商业使用前，应由内容权利人确认这些内容的许可范围。

更新依赖后，在已解析的 package checkout 上运行 `python3 scripts/refresh-licenses.py`；它仅收集本地现有许可，不联网解析依赖。可传 `--packages PATH` 指定构建缓存，或 `--source PATH` 显式读取原 lucide-react 的 LICENSE。上游模型及 ONNX 第三方 notices 的更新应固定来源 revision 并人工审阅，不能仅替换文件标题。
