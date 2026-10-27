# iPhone 按网络选择处理方式

`ProcessingNetworkPolicy` 统一管理 OCR、语音识别和教材检索的入口。稳定 Wi-Fi / 有线网络使用原有流程；蜂窝网络在第一次使用这些功能时询问。选择适用于当前蜂窝会话中的三项操作。取消只取消正在等待的操作，不记录选择。

默认 Wi-Fi 稳定窗口为 5 秒，蜂窝防抖为 2 秒，重复询问冷却为 10 分钟。恢复稳定 Wi-Fi 后自动恢复原流程；冷却期内再次进入蜂窝网络复用最近一次明确选择。网络状态未知先等待 2 秒，离线或仍未知时只使用本机能力。切网不会重启、重放或重新收费执行中的任务。账号变化清除选择。

| 操作 | 稳定 Wi-Fi / 选择原有流程 | 选择本机处理 |
|---|---|---|
| OCR | 上传 PDF，使用现有服务端 OCR、结构化与教材准备 | PDF 文字层优先；扫描页由 iPhone Apple Vision 中英文识别，不上传原文件。保存真实页码的原文片段，可阅读、检索；不假造课程。课程资料提供“在 Wi-Fi 下生成课程”入口 |
| 语音笔记 | 原有上传录音、服务端识别、DeepSeek 整理 | Apple Speech 严格 `requiresOnDeviceRecognition=true`。只发送文字到 DeepSeek，界面只展示整理结果；原始录音与机器转录保留在账号本机缓存 |
| 教材检索 | 原有服务端问答或既有本机教材链路 | 仅搜索当前范围内已缓存原文：Apple 中文句向量优先，其次词向量平均；模型缺失时明确标注本机关键词检索。返回原文和页码引用，无云端问答 |

本机 OCR 文本按账号单独保存。已有服务端教材刷新、退出再打开后仍保留本机文字，既有章节、课程、素材及引用 ID 保持可用。本机缺少原文件时不会自动下载，提示在 Wi-Fi 下准备原书。

端侧能力使用 Vision、Speech、NaturalLanguage 框架，不依赖 Foundation Models。Speech 按设备检查已安装中文模型和权限；不可用时明确提示，不自动下载模型或改为上传录音。语音笔记整理仍需要网络与 DeepSeek 服务。

服务端增加 `PUT /api/studio/notes/{id}/speech-text`（revision、text、source、language），输入与音频互斥、幂等并按账号/教材校验。`voice_text_notes` 就绪状态仅依赖文字整理服务；旧 `voice_notes` 的服务端 ASR 含义不变。原始转录不出现在公开笔记、任务和 edition 响应中。

测试覆盖网络防抖/冷却/并发/取消、真实 Vision 中英文图与 PDF 页码、OCR 缓存隔离、端侧 Speech 权限与超时、本机检索引文、仅文字上传及丢响应恢复。UI 使用 Debug 专用蜂窝网络 fixture 检查询问和复用；模拟器结果不能替代国行真机上语音模型安装情况的验证。

Apple 接口参考：[Vision OCR](https://developer.apple.com/documentation/vision/recognizing-text-in-images)、[端侧 Speech](https://developer.apple.com/documentation/speech/sfspeechrecognitionrequest/requiresondevicerecognition)、[NaturalLanguage 向量](https://developer.apple.com/documentation/naturallanguage/nlembedding)。
