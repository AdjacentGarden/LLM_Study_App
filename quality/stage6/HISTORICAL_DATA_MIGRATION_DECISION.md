# 历史课程重解析独立决策项

> 当前决策：**不执行批量重解析**  
> 执行状态：`not_executed`  
> 生产/共享存储访问：否  
> 生产/共享数据库写入：否  
> 后续执行要求：必须取得针对历史批量重解析的单独明确批准

## 1. 当前清单

只读盘点工具为 `quality/stage6/inventory_historical_courses.py`。本轮仅检查工作区默认 `backend/data`，结果：

- 本地课程：0。
- 重解析候选：0。
- 生产存储：未提供、未访问，因此本结果不代表生产实际课程数。

机器清单见 `historical_reparse_inventory.json`。不得把空的本地清单推断为生产没有历史数据，也不得编造生产课程 ID。

## 2. 生产只读盘点规则

获得生产快照/只读路径授权后，先运行只读盘点，不启动 parser 或写数据库：

```text
python quality/stage6/inventory_historical_courses.py --storage-root <approved-snapshot-root> --output <inventory-output.json>
```

建议优先级：

- **P0**：缺 parser report、索引非 ready、解析/索引曾失败、数据一致性异常。
- **P1**：非 MinerU/混合主结果、缺少 V2 chunk、OCR pending 或低质量需复核。
- **P2**：内容可用但希望获得 Office 新定位、Assets 或新质量字段。
- **不重解析**：当前 generation 已是合格 MinerU + V2 + ready 索引且无业务需求。

实际清单必须包含 book ID、文件类型、当前 parser、chunk version、index generation/status、预计 GPU 时间、优先级、失败重试次数和回退 generation。

## 3. 影响评估

- MinerU 当前并发上限为 1，批量任务会与实时上传竞争 GPU 队列。
- 图片/扫描 PDF 可能触发 PaddleOCR，时延和 CPU/内存开销高于原生 PDF/Office。
- 每本书会创建新的 parse/index generation；BGE-M3 embedding 与 pgvector 写入增加 GPU、数据库 WAL、索引和存储压力。
- 新旧引用可能因 Chunk V2 和 Office 语义变化而改变，课程内容需要抽样复核。
- 失败重试必须复用/失效正确 generation，不能无限重新 POST MinerU 任务。
- 容量与总工期只能在生产只读清单完成后估算；当前没有足够数据给出真实总量。

## 4. 若未来批准，执行门禁

1. 完成生产只读清单并由业务确认范围。
2. 备份数据库、原始文件、任务状态和当前 ready generations，并验证恢复演练。
3. 先在生产快照上执行 1～5 本代表性课程 canary。
4. 批次内 MinerU 并发保持 1；设置队列、GPU、失败率和业务高峰暂停门槛。
5. 每批完成后核对解析质量、chunk/向量数量、引用语义和旧 generation 隔离，再进入下一批。
6. 任一 P0 条件触发即停止；保留原文件并切回上一 ready generation。

## 5. 独立审批模板

批准请求必须明确：生产环境、课程 ID/筛选条件、批次数量、时间窗口、最大 GPU/数据库负载、备份位置、回退负责人和是否允许删除旧 generation。没有这些字段时保持 `not_executed`。

本阶段只交付盘点工具、空的本地清单和决策建议，没有执行任何历史课程解析、索引替换或删除。
