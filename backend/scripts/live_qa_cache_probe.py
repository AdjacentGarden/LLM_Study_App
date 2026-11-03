#!/usr/bin/env python3
"""Measure DeepSeek prompt-cache hits for the grounded QA generator.

Run against either checkout of ``adaptive_learning`` by pointing PYTHONPATH at
that checkout's ``backend/src`` directory::

    PYTHONPATH=<src> python scripts/live_qa_cache_probe.py --variant new --out result.json
    PYTHONPATH=<src> python scripts/live_qa_cache_probe.py --variant base --out result.json

The same script is used for both variants; ``--variant`` only tags evidence
source ids (``<variant>-evN``) so the two runs cannot share a cached evidence
prefix. The API key is read from ``DEEPSEEK_API_KEY`` and is never printed or
written to the output file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient
from adaptive_learning.rag.grounded_qa import EvidenceChunk, GroundedAnswerGenerator

BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"
TIMEOUT_SECONDS = 90
USAGE_KEYS = ("responses", "input_tokens", "output_tokens", "cached_input_tokens")

QUESTIONS = [
    "减数分裂与有丝分裂的区别是什么？",
    "同源染色体联会发生在什么时期？",
    "为什么生殖细胞染色体数目减半？",
    "交叉互换有什么意义？",
]

# (page_number, text): six realistic Chinese high-school biology textbook
# passages about meiosis, each 250-400 Chinese characters.
EVIDENCE_TEXTS: list[tuple[int, str]] = [
    (
        20,
        "减数分裂是进行有性生殖的生物在形成成熟生殖细胞过程中所进行的一种特殊的细胞分裂方式。"
        "它与有丝分裂的根本区别在于，减数分裂过程中细胞连续分裂两次，而染色体只复制一次，"
        "结果是一个原始的生殖细胞最终形成四个子细胞，并且每个子细胞中的染色体数目比原始生殖细胞减少一半。"
        "减数分裂发生在动物的精巢和卵巢中，也发生在植物的花药和胚珠中。"
        "通过减数分裂产生的配子，其染色体数目为体细胞的一半，受精作用又使受精卵中的染色体数目恢复到体细胞水平，"
        "从而保证了有性生殖过程中亲子代之间染色体数目的恒定。"
        "减数分裂还是基因重组的重要来源，为生物的变异和进化提供了丰富的原材料。",
    ),
    (
        21,
        "在减数第一次分裂的前期，细胞核内的染色体逐渐凝缩变粗，每条染色体都包含两条姐妹染色单体。"
        "此时，形状和大小一般相同、一条来自父方一条来自母方的两条染色体两两配对，这样的两条染色体叫做同源染色体。"
        "同源染色体两两配对的现象称为联会。联会后的每对同源染色体含有四条染色单体，叫做一个四分体。"
        "四分体中的非姐妹染色单体之间常常发生缠绕，并交换相应的片段，这种现象叫做交叉互换。"
        "交叉互换发生在同源染色体的非姐妹染色单体之间，是基因重组的重要方式之一。"
        "联会时，同源染色体之间形成联会复合体，使配对保持稳定，保证减数第一次分裂后期同源染色体能够准确分离并移向细胞两极。",
    ),
    (
        22,
        "减数第一次分裂前期，同源染色体联会形成四分体，核仁逐渐解体，核膜逐渐消失，纺锤体形成。"
        "中期，各对同源染色体排列在赤道板两侧，着丝粒分别与来自两极的纺锤丝相连。"
        "后期，同源染色体在纺锤丝的牵引下彼此分离，分别移向细胞两极，而着丝粒并不分裂，姐妹染色单体仍连在同一个着丝粒上。"
        "末期，细胞质分裂，形成两个子细胞，每个子细胞中的染色体数目是亲代细胞的一半。"
        "由于同源染色体分离时，非同源染色体自由组合，因此减数第一次分裂产生的两个次级性母细胞中，染色体的组合类型可能不同。"
        "这一过程既保证了染色体数目的减半，又为配子的多样性奠定了基础。"
        "减数第一次分裂是减数分裂过程中染色体数目减半的关键阶段。",
    ),
    (
        23,
        "减数第二次分裂与有丝分裂的过程十分相似，但细胞中不存在同源染色体。"
        "前期，染色体再次凝缩，核膜和核仁消失，纺锤体形成。"
        "中期，每条染色体的着丝粒排列在赤道板上，纺锤丝与着丝粒相连。"
        "后期，着丝粒分裂，姐妹染色单体分开成为两条子染色体，在纺锤丝的牵引下分别移向细胞两极。"
        "末期，细胞质分裂，形成两个子细胞。因此，减数第一次分裂形成的两个次级性母细胞，经过减数第二次分裂后共形成四个子细胞。"
        "在雄性动物体内，四个子细胞均发育为精细胞；在雌性动物体内，通常只有一个子细胞发育为卵细胞，其余三个成为极体并逐渐退化。"
        "减数第二次分裂前后，细胞中染色体数目不再减半，但DNA分子数目会随姐妹染色单体的分开而平均分配。",
    ),
    (
        24,
        "以人的精子形成为例，精原细胞中的染色体数目为46条，共23对同源染色体。"
        "精原细胞经过染色体复制成为初级精母细胞，此时DNA分子数目加倍，但染色体数目仍为46条。"
        "减数第一次分裂结束后，同源染色体分离，形成两个次级精母细胞，每个细胞中染色体数目为23条，且没有同源染色体。"
        "减数第二次分裂时，着丝粒分裂，姐妹染色单体分开，最终形成四个精细胞，每个精细胞的染色体数目仍为23条。"
        "精细胞经过变形后成为精子。卵细胞的形成过程与精子形成相似，但由于细胞质不均等分裂，最终只形成一个卵细胞和三个极体。"
        "通过减数分裂和受精作用，生物体前后代体细胞中的染色体数目得以保持相对稳定。"
        "这一数目变化过程体现了减数分裂的连续性和完整性。",
    ),
    (
        25,
        "交叉互换是指减数第一次分裂前期，同源染色体的非姐妹染色单体之间交换相应片段的现象。"
        "交叉互换的结果是，同一条染色体上原来连锁的基因组合被打破，产生了新的基因组合。"
        "对于生物个体而言，交叉互换增加了配子中染色体组合的多样性；"
        "对于整个种群而言，它为自然选择提供了更加丰富的变异材料，有利于生物适应不断变化的环境。"
        "需要注意的是，交叉互换发生在同源染色体的非姐妹染色单体之间，而基因突变发生在DNA分子复制过程中，"
        "两者都能产生新的基因型，但交叉互换不产生新的基因，只是把原有的基因重新组合。"
        "此外，非同源染色体之间的自由组合与交叉互换共同构成了基因重组的主要来源，是生物变异的重要原因之一。",
    ),
]


def build_evidence(variant: str) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            source_id=f"{variant}-ev{index}",
            page_number=page_number,
            text=text,
        )
        for index, (page_number, text) in enumerate(EVIDENCE_TEXTS, start=1)
    ]


def usage_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    delta: dict[str, int] = {}
    for key in USAGE_KEYS:
        delta[key] = int(after.get(key, 0)) - int(before.get(key, 0))
    return delta


def ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def print_table(variant: str, model: str, calls: list[dict[str, Any]]) -> None:
    print(f"variant={variant} model={model}")
    header = (
        f"{'#':>2}  {'question':<30}  {'input':>7}  {'cached':>7}  "
        f"{'output':>7}  {'resp':>4}  {'hit':>7}  {'sec':>7}  error"
    )
    print(header)
    print("-" * len(header))
    for index, call in enumerate(calls, start=1):
        question = call["question"]
        display_question = question if len(question) <= 28 else question[:27] + "…"
        print(
            f"{index:>2}  {display_question:<30}  {call['input_tokens']:>7}  "
            f"{call['cached_input_tokens']:>7}  {call['output_tokens']:>7}  "
            f"{call['responses']:>4}  {call['hit_ratio']:>7.4f}  "
            f"{call['elapsed_seconds']:>7.3f}  {call['error'] or '-'}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure DeepSeek prompt-cache hits for grounded QA."
    )
    parser.add_argument("--variant", required=True, choices=("new", "base"))
    parser.add_argument("--out", required=True, help="Path for the JSON result.")
    args = parser.parse_args(argv)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print(
            "error: DEEPSEEK_API_KEY is not set; export it before running this script.",
            file=sys.stderr,
        )
        return 2

    model = os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    config = LLMConfig(
        base_url=BASE_URL,
        api_key=api_key,
        model=model,
        timeout_seconds=TIMEOUT_SECONDS,
        max_retries=0,
    )
    evidence = build_evidence(args.variant)

    calls: list[dict[str, Any]] = []
    client = OpenAICompatibleClient(config)
    try:
        generator = GroundedAnswerGenerator(client)
        for question in QUESTIONS:
            before = client.usage_totals()
            started = time.monotonic()
            error: str | None = None
            try:
                generator.answer(question=question, evidence=evidence, retrieval_score=1.0)
            except Exception as exc:  # noqa: BLE001 - record the failure type only
                error = type(exc).__name__
            elapsed_seconds = round(time.monotonic() - started, 3)
            after = client.usage_totals()
            delta = usage_delta(before, after)

            input_tokens = delta["input_tokens"]
            cached_input_tokens = delta["cached_input_tokens"]
            calls.append(
                {
                    "question": question,
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": delta["output_tokens"],
                    "responses": delta["responses"],
                    "hit_ratio": ratio(cached_input_tokens, input_tokens),
                    "elapsed_seconds": elapsed_seconds,
                    "error": error,
                }
            )
    finally:
        client.close()

    total_input = sum(call["input_tokens"] for call in calls)
    total_cached = sum(call["cached_input_tokens"] for call in calls)
    tail = calls[1:]
    tail_input = sum(call["input_tokens"] for call in tail)
    tail_cached = sum(call["cached_input_tokens"] for call in tail)

    result = {
        "variant": args.variant,
        "model": model,
        "calls": calls,
        "totals": {
            "input": total_input,
            "cached": total_cached,
            "hit_ratio_all": ratio(total_cached, total_input),
            "hit_ratio_after_first": ratio(tail_cached, tail_input),
        },
    }

    print_table(args.variant, model, calls)
    print(
        f"totals: input={total_input} cached={total_cached} "
        f"hit_ratio_all={result['totals']['hit_ratio_all']:.4f} "
        f"hit_ratio_after_first={result['totals']['hit_ratio_after_first']:.4f}"
    )

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
