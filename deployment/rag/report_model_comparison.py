"""Summarize saved evaluation artifacts without making any model requests."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median


def read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def content_label(result: dict) -> str:
    if not result.get('completed'):
        return '未完成'
    if result.get('flashcards', {}).get('success'):
        return f"流程通过（{result['flashcards']['count']} 张卡）"
    if 'FlashcardQualityError' in result.get('generation_error', ''):
        return '闪卡审校失败'
    return '章节生成失败'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    root = args.root
    inventory = json.loads((root/'inventory.json').read_text())
    results = []
    usage = Counter()
    latency = {'grok-4-fast': [], 'grok-4.3': []}
    statuses = {'grok-4-fast': Counter(), 'grok-4.3': Counter()}
    for book in inventory:
        target = root/book['book_id']
        old = read(target/'content-evaluation.json')
        new = read(target/'model-evaluations/grok-4_3/content-evaluation.json')
        answers = {}
        for model in statuses:
            tag = model.replace('.', '_')
            answer = read(target/f'answer-evaluation-{tag}.json')
            queries = answer.get('queries', [])
            counts = Counter(q.get('answer', {}).get('status', 'error') for q in queries)
            statuses[model].update(counts)
            answers[model] = {'statuses': dict(counts), 'questions': len(queries)}
            for query in queries:
                a = query.get('answer', {})
                if a.get('generation_duration_ms', 0) > 0:
                    latency[model].append(a['generation_duration_ms']/1000)
            if model == 'grok-4.3':
                # Each report stores the same runner's cumulative usage. Take maxima, not sums.
                for key, value in answer.get('runner_cumulative_token_usage', {}).items():
                    usage['rag_'+key] = max(usage['rag_'+key], value)
        for key, value in new.get('new_token_usage', {}).items():
            usage['content_'+key] += value
        results.append({**book, 'old_content_model': old.get('model'),
                        'old_content': content_label(old), 'new_content': content_label(new),
                        'new_error': new.get('generation_error'),
                        'new_validation_errors': new.get('validation_errors', []),
                        'answers': answers, 'completed': new.get('completed', False)})
    done = all(r['completed'] and r['answers']['grok-4.3']['questions'] == 2 for r in results)
    input_tokens = usage['rag_input_tokens']+usage['content_input_tokens']
    output_tokens = usage['rag_output_tokens']+usage['content_output_tokens']
    cached = usage['rag_cached_input_tokens']+usage['content_cached_input_tokens']
    cost = ((input_tokens-cached)*1.25+cached*.3125+output_tokens*2.5)/1_000_000
    report = {'completed': done, 'books': results, 'status_counts': statuses,
              'generation_seconds_median': {m: median(v) if v else None for m,v in latency.items()},
              'recorded_usage': usage, 'estimated_grok43_usd': round(cost, 4)}
    (root/'grok43-comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    comparable = [r for r in results if r['old_content_model'] == 'grok-4-fast']
    old_pass = sum(r['old_content'].startswith('流程通过') for r in comparable)
    new_pass = sum(r['new_content'].startswith('流程通过') for r in comparable)
    lines = ['# PUCODING Grok 4.3 换模复测', '',
             '日期：2026-09-05。执行地点：4090 服务器数据盘。', '',
             f"测试状态：{'本轮均已结束（不等于全部通过）' if done else '仍有测试未完成'}。", '',
             f'同模型可比的 {len(comparable)} 本书：内容生成程序通过数 {old_pass} → {new_pass}。该数字不代表整书教学质量合格。', '',
             '线上文字和视觉配置均已改为 Grok 4.3；健康检查通过，真实 DNA 半保留复制问答通过证据审查，生成用时 12.824 秒。',
             '后端回归 178 项通过，其中模型协议专项 13 项通过。未改前端、正式书架或用户学习数据，未提交 GitHub。', '',
             '## 测试边界', '',
             '- 复用 10 本书、3,368 页的同一份全文 OCR 和检索索引，不重新扫描。',
             '- 对比相同的 20 道源页参考问题；章节摘要、课程和闪卡使用相同代码、提示词、校验条件。',
             '- Grok 4.3 使用低推理档；新增结果独立保存，不覆盖旧模型结果，不发布到用户书架。',
             '- 本轮未重复 30 页视觉校对；仅另行做了图片识别连通性验证。不能据此宣称全文清洗已通过。',
             '- 旧版 C++ 内容生成结果使用 Haiku，其他 9 本使用 Grok 4 Fast。两种基线不可混算。',
             '- “流程通过”表示摘要与闪卡通过现有程序校验，不代表目录边界、教学质量已人工全面验收。', '',
             '## 逐本结果', '',
             '| 书籍 | 页数 | 旧模型内容流程 | Grok 4.3 内容流程 | 旧/新有依据回答数 |',
             '|---|---:|---|---|---|']
    for row in results:
        a = row['answers']
        count = lambda model: a[model]['statuses'].get('supported', 0)
        old_label = row['old_content'] + ('〔Haiku〕' if row['old_content_model'] != 'grok-4-fast' else '')
        lines.append(f"| {row['filename']} | {row['pages']} | {old_label} | {row['new_content']} | {count('grok-4-fast')}/2 → {count('grok-4.3')}/2 |")
    lines += ['', '## 问答指标的正确解释', '']
    for model, counts in statuses.items():
        times = latency[model]
        lines.append(f"- {model}：{dict(counts)}；发生模型生成的请求耗时中位数 {median(times):.2f} 秒。" if times else f'- {model}：暂无耗时记录。')
    lines += ['', '上述 supported 是程序判定的证据支持状态，不是人工正确率；20 题是小样本，不代表整本书覆盖率。',
              '人工复核新增拒答：复数乘法推导题的证据缺失一般推导的后续步骤。旧模型只给了部分推导和数值例子，',
              '新模型明确指出截断并拒答；不能仅按回答数量认定模型退步，但用户仍无法获得完整推导。',
              '旧版目标参考页 Top-5 命中为 15/20，但其他页也可能包含有效证据，不能把未命中指定页直接等同答错。', '',
              '## 独立约束诊断（不混入上表）', '',
              '物理章节两次调用（含一次重试）后仍返回单个知识点 8 条引用，超过接口上限 6 条；提示词原先没有明确这个上限。',
              '另用同一物理首章做小样本诊断：仅补充已有引用数量/长度约束，不放宽校验，两档模型均单次通过。',
              'Grok 4 Fast 用时 23.14 秒，Grok 4.3 用时 25.06 秒。这只是单章单次结果，不能推定所有章节已修复。',
              '该诊断的提示词变更仅用于隔离测试，未部署到生产，详情见 `evidence-contract-diagnostic.json`。', '',
              '## 已知不由换模自动解决的问题', '',
              '- C++ 目录识别只保留了第 29 章；英语 UNIT 被降级为全书一章；有机化学把目录中的第 20 章当成首章。',
              '- 部分教材章节名称仅剩“第九章”等编号。上述结构问题即使内容生成通过，也不算产品合格。',
              '- 抽看生物、化学闪卡，实验名和条件较完整，但个别卡片一次询问多个问题，仍需要教学粒度优化。',
              '- 原 RAG 清洗会误把代码或数学不等式中的尖括号内容当 HTML 删除。',
              '- 英语 Queens 题可检索到参考页，但中文重排模型与固定拒答阈值仍可能提前拦截。',
              '- C++ 的 5 次内容生成调用累计返回 626,099 输入 Token；目录错误与证据体积应先治理，换更贵模型会放大开销。',
              '- 有机化学错误前提题要求模型纠正“C60 不呈现芳香性”；参考页实际写有一定芳香性。拒答虽然避免附和，也不等于完成纠错。', '',
              '## 本轮失败诊断', '']
    for row in results:
        if row['new_error']:
            lines.append(f"- {row['filename']}：{row['new_error']}")
            for error in row['new_validation_errors']:
                lines.append(f"  - {error.get('loc')}：{error.get('msg')}")
    lines += ['', '## 消耗与留存', '',
              f'本轮已记录 Grok 4.3 输入 {input_tokens:,} Token、输出 {output_tokens:,} Token，其中输入缓存 {cached:,} Token。',
              f'按 PUCODING 公示输入 $1.25、输出 $2.50、缓存输入 $0.3125 / 百万 Token 估算约 **${cost:.4f}**。',
              '这不是账单：不含独立连通性/线上验证/约束诊断请求、旧模型费用、无 usage 返回的失败调用及账户分组倍率或特殊计价。',
              '价格来源：[PUCODING 公示模型价格](https://pucoding.com/api/v1/status/models)。', '',
              '原始逐题证据、回答和新生成内容均位于各书 ID 目录；新内容位于 `model-evaluations/grok-4_3/`。',
              '服务器保存于 `/data1/zhenghang/adaptive-book-ocr/output/examples-20260905`。密钥只留在服务器私有配置，不进入报告。', '']
    (root/'grok43-comparison.md').write_text('\n'.join(lines))
    print(json.dumps({'completed':done, 'statuses':statuses, 'estimated_usd':round(cost,4)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
