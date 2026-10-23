# 单用户原生客户端优化状态（2026-10-23）

此提交同步此前冻结的优化候选源码；上传 GitHub 不等于生产发布。

## 已实现

- DeepSeek 主生成和快速常驻 GPU OCR 保留。
- 模型429/503冷却、共享8个实际请求槽、半开恢复与在途请求生命周期保护。
- 课程入队及消耗尝试前检查冷却，拒绝不创建别名、不消耗尝试次数。
- Web和原生客户端准确展示失败/次数耗尽；恢复仅查询；显式更新保留幂等与持久意图。
- 笔记强制逐片段完整审核及自身引用检查；允许一次格式复核，受原5次总调用预算约束，保留已有否定结论。
- 原生 SwiftUI、URLSession、PDFKit、AVFoundation 工程及 Mac host 验证脚本。

## 验证与边界

冻结候选后端1398通过、6个可选历史fixture跳过，198个文件前后哈希不变；Web103项及另26项笔记SSR通过；原生61组Mac host检查通过。原始冻结校验摘要随本次上传清单保存，不能自动外推到后续合并改动。

最新固定合成笔记隔离测试11.076秒、5次HTTP200，格式复核与正文修订已走通；自动终审仍false，未发布。独立全文未见该最终样本的阻断性错误，最后拒绝主要混同gene位置与主要载体范围。没有人工覆盖false，没有消耗最后原生测试额度。

当前仍有课程事实适用范围、练习量规充分性、审核误判、全任务时限及未知提交策略等工作。整书到全部合格教学内容少于60秒未达标。复杂版面选择性OCR回退尚未实现。

没有完成iPhoneOS编译、签名、IPA安装或真机验收；Mac host检查不等于iPhone验证。生产服务尚未发布。

## 研究依据

- [FActScore](https://aclanthology.org/2023.emnlp-main.741/)：细粒度事实核查；本实现的标点片段目录不保证语义原子事实完备。
- [LiteLLM cooldown](https://docs.litellm.ai/docs/routing#cooldowns)：故障冷却与限流隔离。
- [Microsoft异步请求模式](https://learn.microsoft.com/en-us/azure/architecture/patterns/asynchronous-request-reply)：任务状态、轮询和幂等。
- [Pydantic AI输出验证](https://pydantic.dev/docs/ai/core-concepts/output/)：有界输出修正；本项目未引入该依赖。

## 交付范围

此仓库同步源码、必要测试夹具及配置模板。API密钥、Cookie、用户数据库、教材PDF、录音、模型权重和本机运行缓存均不提交。

## 分支来源

本分支保存基于6d01e7f的冻结候选。2026-10-23检查时origin/main为c67bfeb，包含另一批SwiftUI/PostgreSQL改动；本次没有覆盖或合并主分支。
