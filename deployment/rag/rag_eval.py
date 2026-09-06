from __future__ import annotations

import argparse
import html
import json
import math
import re
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_CJK = re.compile(r"[\u3400-\u9fff]")
# Only recognized markup; C++ templates and inequalities are source text, not HTML.
_TAG = re.compile(r"</?(?P<tag>table|thead|tbody|tfoot|tr|td|th|caption|p|br|div|span|b|strong|em|i|sup|sub)(?=[\s/>])[^>]*>", re.I)
_TABLE_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", flags=re.IGNORECASE | re.DOTALL)
_TABLE_CELL = re.compile(
    r"<t([dh])([^>]*)>(.*?)</t\1>", flags=re.IGNORECASE | re.DOTALL
)


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    page_number: int
    block_indexes: tuple[int, ...]
    block_types: tuple[str, ...]
    text: str
    granularity: str = "parent"


def lexical_tokens(text: str) -> list[str]:
    cjk = _CJK.findall(text)
    bigrams = [cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)]
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9]{2,}", text)]
    return words + bigrams


def plain_text(text: str) -> str:
    if text.lstrip().startswith('```'):
        return text.strip()
    paired_tags = {tag.lower() for tag in re.findall(r'</([a-z]+)\s*>', text, re.I)} | {'br'}
    text = re.sub(r"</(?:td|th|tr)>", " | ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(
        lambda match: ' ' if match.group('tag').lower() in paired_tags else match.group(0), text))).strip()


def _span(attributes: str, name: str) -> int:
    match = re.search(rf'{name}\s*=\s*["\']?(\d+)', attributes, flags=re.IGNORECASE)
    return max(1, int(match.group(1))) if match else 1


def table_grid(table_html: str) -> list[list[str]]:
    """Expand a simple HTML table, preserving rowspan and colspan coordinates."""
    matrix: list[dict[int, str]] = []
    for row_index, row_html in enumerate(_TABLE_ROW.findall(table_html)):
        while len(matrix) <= row_index:
            matrix.append({})
        column = 0
        for _, attributes, cell_html in _TABLE_CELL.findall(row_html):
            while column in matrix[row_index]:
                column += 1
            value = plain_text(cell_html)
            rowspan = _span(attributes, "rowspan")
            colspan = _span(attributes, "colspan")
            for target_row in range(row_index, row_index + rowspan):
                while len(matrix) <= target_row:
                    matrix.append({})
                for target_column in range(column, column + colspan):
                    matrix[target_row][target_column] = value
            column += colspan
    width = max((max(row, default=-1) + 1 for row in matrix), default=0)
    return [[row.get(column, "") for column in range(width)] for row in matrix]


def structured_table_texts(table_html: str) -> list[str]:
    flattened = plain_text(table_html)
    grid = table_grid(table_html)
    if len(grid) < 3 or len(grid[1]) < 3:
        return [flattened] if flattened else []

    facts: list[str] = []
    for row in grid[2:]:
        if len(row) != len(grid[1]):
            continue
        left_key, right_key = row[0], row[-1]
        for column_index in range(1, len(row) - 1):
            column_key = grid[1][column_index]
            value = row[column_index]
            selectors = [key for key in (left_key, column_key, right_key) if key]
            if not selectors or not value:
                continue
            composite = "".join(selectors)
            facts.append(f"{composite} → {value}")
    if not facts:
        return [flattened] if flattened else []
    structured = "表格结构化坐标索引（组合键 → 单元格值）：\n" + "；".join(facts)
    return [text for text in (flattened, structured) if text]


def split_long(text: str, *, limit: int = 360, overlap: int = 60) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            boundary = max(text.rfind(mark, start + limit // 2, end) for mark in "。！？；|\n")
            if boundary > start:
                end = boundary + 1
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [piece for piece in pieces if piece]


def build_chunks(
    pages_path: Path, *, limit: int = 900, child_limit: int = 360
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for line in pages_path.read_text(encoding="utf-8").splitlines():
        page = json.loads(line)
        page_number = int(page["page_number"])
        units: list[tuple[int, str, str]] = []
        for block in page.get("blocks", []):
            block_type = str(block.get("type", "text"))
            if block_type in {"footer", "page_number"}:
                continue
            raw_text = str(block.get("text", ""))
            texts = (
                structured_table_texts(raw_text)
                if block_type == "table"
                else [plain_text(raw_text)]
            )
            for text in texts:
                if not text:
                    continue
                for piece in split_long(text, limit=limit):
                    units.append((int(block.get("block_index", -1)), block_type, piece))

        packed: list[tuple[list[int], list[str], str]] = []
        indexes: list[int] = []
        types: list[str] = []
        texts: list[str] = []
        for block_index, block_type, text in units:
            separator = "\n" if texts else ""
            if texts and len(separator.join(texts + [text])) > limit:
                packed.append((indexes, types, "\n".join(texts)))
                indexes, types, texts = [], [], []
            indexes.append(block_index)
            types.append(block_type)
            texts.append(text)
        if texts:
            packed.append((indexes, types, "\n".join(texts)))

        for index, (block_indexes, block_types, text) in enumerate(packed, start=1):
            parent = Chunk(
                chunk_id=f"p{page_number:03d}-c{index:02d}",
                page_number=page_number,
                block_indexes=tuple(block_indexes),
                block_types=tuple(dict.fromkeys(block_types)),
                text=text,
            )
            chunks.append(parent)
            if len(text) > child_limit:
                for child_index, child_text in enumerate(
                    split_long(text, limit=child_limit), start=1
                ):
                    chunks.append(
                        Chunk(
                            chunk_id=f"{parent.chunk_id}-s{child_index:02d}",
                            page_number=page_number,
                            block_indexes=parent.block_indexes,
                            block_types=parent.block_types,
                            text=child_text,
                            granularity="child",
                        )
                    )
    return chunks


class BM25:
    def __init__(self, texts: Iterable[str]) -> None:
        self.documents = [Counter(lexical_tokens(text)) for text in texts]
        self.document_frequency = Counter(
            token for document in self.documents for token in document
        )
        self.average_length = (
            sum(sum(document.values()) for document in self.documents)
            / max(1, len(self.documents))
        )

    def scores(self, query: str) -> list[float]:
        query_tokens = Counter(lexical_tokens(query))
        return [self._score(query_tokens, document) for document in self.documents]

    def _score(self, query: Counter[str], document: Counter[str]) -> float:
        length = sum(document.values())
        score = 0.0
        for token, query_count in query.items():
            frequency = document.get(token, 0)
            if not frequency:
                continue
            documents_with_token = self.document_frequency[token]
            inverse = math.log(
                1
                + (len(self.documents) - documents_with_token + 0.5)
                / (documents_with_token + 0.5)
            )
            normalized = frequency * 2.2 / (
                frequency + 1.2 * (0.25 + 0.75 * length / max(1, self.average_length))
            )
            score += inverse * normalized * min(2, query_count)
        return score


def load_embedding_model(model_path: Path, device: str) -> tuple[Any, Any]:
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModel.from_pretrained(model_path, local_files_only=True).to(device).eval()
    return tokenizer, model


def embed(
    texts: list[str], tokenizer: Any, model: Any, device: str, *, query: bool, batch_size: int = 32
) -> Any:
    import numpy as np
    import torch
    from torch.nn import functional

    prefix = "为这个句子生成表示以用于检索相关文章：" if query else ""
    batches: list[Any] = []
    for start in range(0, len(texts), batch_size):
        values = [prefix + text for text in texts[start : start + batch_size]]
        inputs = tokenizer(
            values, padding=True, truncation=True, max_length=512, return_tensors="pt"
        ).to(device)
        with torch.inference_mode():
            vector = model(**inputs).last_hidden_state[:, 0]
            vector = functional.normalize(vector, p=2, dim=1)
        batches.append(vector.float().cpu().numpy())
    return np.concatenate(batches, axis=0)


def load_reranker(model_path: Path, device: str) -> tuple[Any, Any]:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = (
        AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True)
        .to(device)
        .eval()
    )
    return tokenizer, model


def rerank(
    query: str,
    candidate_indexes: list[int],
    chunks: list[Chunk],
    tokenizer: Any,
    model: Any,
    device: str,
) -> list[tuple[int, float]]:
    import torch

    pairs = [[query, chunks[index].text] for index in candidate_indexes]
    scores: list[float] = []
    for start in range(0, len(pairs), 16):
        inputs = tokenizer(
            pairs[start : start + 16],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        ).to(device)
        with torch.inference_mode():
            logits = model(**inputs, return_dict=True).logits.view(-1).float().cpu().tolist()
        scores.extend(float(value) for value in logits)
    return sorted(zip(candidate_indexes, scores, strict=True), key=lambda item: item[1], reverse=True)


def ranks(scores: list[float]) -> list[int]:
    return sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)


def reciprocal_rank_fusion(*rankings: list[int], limit: int = 30) -> list[int]:
    fused: Counter[int] = Counter()
    for ranking in rankings:
        for rank, index in enumerate(ranking[:60], start=1):
            fused[index] += 1 / (60 + rank)
    return [index for index, _ in fused.most_common(limit)]


def diversify_chunks(
    ranking: list[int],
    chunks: list[Chunk],
    *,
    per_page: int = 2,
    limit: int = 30,
) -> list[int]:
    counts: Counter[int] = Counter()
    result: list[int] = []
    for index in ranking:
        page = chunks[index].page_number
        if counts[page] >= per_page:
            continue
        counts[page] += 1
        result.append(index)
        if len(result) >= limit:
            break
    return result


def page_reciprocal_rank_fusion(
    rankings: Iterable[list[int]], chunks: list[Chunk], *, limit: int = 30
) -> list[int]:
    page_scores: Counter[int] = Counter()
    representative: dict[int, int] = {}
    for ranking in rankings:
        seen_pages: set[int] = set()
        page_rank = 0
        for index in ranking:
            page = chunks[index].page_number
            representative.setdefault(page, index)
            if page in seen_pages:
                continue
            seen_pages.add(page)
            page_rank += 1
            page_scores[page] += 1 / (60 + page_rank)
            if page_rank >= 60:
                break
    ordered_pages = [page for page, _ in page_scores.most_common(limit)]
    return [representative[page] for page in ordered_pages]


def technical_terms(query: str) -> list[str]:
    """Extract exact identifiers that semantic models often underweight."""
    terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|\d+(?:\.\d+)?%?", query)
    return list(dict.fromkeys(term.lower() for term in terms))


def exact_term_ranking(query: str, chunks: list[Chunk]) -> list[int]:
    terms = technical_terms(query)
    if len(terms) < 2:
        return []
    document_frequency = {
        term: sum(term in chunk.text.lower() for chunk in chunks) for term in terms
    }
    weights = {
        term: math.log(1 + (len(chunks) + 1) / (frequency + 1))
        for term, frequency in document_frequency.items()
    }
    scored: list[tuple[int, float]] = []
    for index, chunk in enumerate(chunks):
        lowered = chunk.text.lower()
        score = sum(weight for term, weight in weights.items() if term in lowered)
        if score:
            scored.append((index, score))
    return [index for index, _ in sorted(scored, key=lambda item: item[1], reverse=True)]


def unique_page_ranking(
    ranking: list[int], chunks: list[Chunk], *, limit: int = 5
) -> list[int]:
    result: list[int] = []
    seen_pages: set[int] = set()
    for index in ranking:
        page_number = chunks[index].page_number
        if page_number in seen_pages:
            continue
        result.append(index)
        seen_pages.add(page_number)
        if len(result) >= limit:
            break
    return result


def protect_primary_pages(
    primary: list[int],
    supplement: list[int],
    chunks: list[Chunk],
    *,
    primary_pages: int = 3,
    limit: int = 5,
) -> list[int]:
    result = unique_page_ranking(primary, chunks, limit=primary_pages)
    seen_pages = {chunks[index].page_number for index in result}
    for index in supplement:
        page = chunks[index].page_number
        if page in seen_pages:
            continue
        result.append(index)
        seen_pages.add(page)
        if len(result) >= limit:
            break
    return result


def page_evidence_indexes(
    ranking: list[int],
    page_ranking: list[int],
    chunks: list[Chunk],
    *,
    per_page: int = 3,
    total: int = 12,
) -> list[int]:
    selected_pages = [chunks[index].page_number for index in page_ranking]
    buckets: dict[int, list[int]] = {page: [] for page in selected_pages}
    for index in ranking:
        page = chunks[index].page_number
        if page in buckets and len(buckets[page]) < per_page:
            text = chunks[index].text
            existing_texts = [chunks[existing].text for existing in buckets[page]]
            if any(text in existing or existing in text for existing in existing_texts):
                continue
            buckets[page].append(index)
    result: list[int] = []
    for page in selected_pages:
        for index in buckets[page]:
            if index not in result:
                result.append(index)
            if len(result) >= total:
                return result
    return result


def guard_high_confidence_evidence(
    reranked: list[tuple[int, float]], fused: list[int], *, limit: int = 5
) -> list[int]:
    """Keep reranker precision without allowing it to erase fused top evidence."""
    result: list[int] = []
    for index in [*(item[0] for item in reranked[:2]), *fused[:3], *(item[0] for item in reranked)]:
        if index not in result:
            result.append(index)
        if len(result) >= limit:
            break
    return result


def stage_metrics(results: list[dict[str, Any]], stage: str) -> dict[str, float]:
    positive = [result for result in results if result["expected_pages"]]
    reciprocal: list[float] = []
    hits = {1: 0, 3: 0, 5: 0}
    coverage: list[float] = []
    page_recall: list[float] = []
    ndcg: list[float] = []
    duplicate_rate: list[float] = []
    for result in positive:
        pages = result[f"{stage}_pages"]
        gold = set(result["expected_pages"])
        first = next((index for index, page in enumerate(pages, start=1) if page in gold), None)
        reciprocal.append(0.0 if first is None else 1 / first)
        for cutoff in hits:
            hits[cutoff] += int(bool(set(pages[:cutoff]) & gold))
        evidence = result[f"{stage}_evidence"]
        terms = result["expected_terms"]
        coverage.append(
            1.0 if not terms else sum(term.lower() in evidence.lower() for term in terms) / len(terms)
        )
        page_recall.append(len(set(pages[:5]) & gold) / len(gold))
        discounted_gain = sum(
            1 / math.log2(rank + 1)
            for rank, page in enumerate(pages[:5], start=1)
            if page in gold
        )
        ideal_gain = sum(
            1 / math.log2(rank + 1)
            for rank in range(1, min(5, len(gold)) + 1)
        )
        ndcg.append(discounted_gain / ideal_gain)
        duplicate_rate.append(
            0.0 if not pages else 1 - len(set(pages[:5])) / len(pages[:5])
        )
    count = max(1, len(positive))
    return {
        "mrr": round(sum(reciprocal) / count, 4),
        "hit_at_1": round(hits[1] / count, 4),
        "hit_at_3": round(hits[3] / count, 4),
        "hit_at_5": round(hits[5] / count, 4),
        "gold_page_recall_at_5": round(sum(page_recall) / count, 4),
        "ndcg_at_5": round(sum(ndcg) / count, 4),
        "duplicate_page_rate_at_5": round(sum(duplicate_rate) / count, 4),
        "expected_term_coverage_at_5": round(sum(coverage) / count, 4),
    }


def command_index(args: argparse.Namespace) -> None:
    import numpy as np
    import torch

    started = time.monotonic()
    chunks = build_chunks(
        args.pages, limit=args.chunk_limit, child_limit=args.child_chunk_limit
    )
    device = args.device if torch.cuda.is_available() else "cpu"
    tokenizer, model = load_embedding_model(args.embedding_model, device)
    embeddings = embed([chunk.text for chunk in chunks], tokenizer, model, device, query=False)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "chunks.json").write_text(
        json.dumps([asdict(chunk) for chunk in chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    np.save(args.output / "embeddings.npy", embeddings)
    manifest = {
        "chunk_count": len(chunks),
        "embedding_dimensions": int(embeddings.shape[1]),
        "embedding_model": str(args.embedding_model),
        "chunk_character_limit": args.chunk_limit,
        "child_chunk_character_limit": args.child_chunk_limit,
        "parent_chunk_count": sum(chunk.granularity == "parent" for chunk in chunks),
        "child_chunk_count": sum(chunk.granularity == "child" for chunk in chunks),
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    (args.output / "index_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


def command_evaluate(args: argparse.Namespace) -> None:
    import numpy as np
    import torch

    chunks = [Chunk(**value) for value in json.loads((args.index / "chunks.json").read_text())]
    embeddings = np.load(args.index / "embeddings.npy")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    device = args.device if torch.cuda.is_available() else "cpu"
    embedding_tokenizer, embedding_model = load_embedding_model(args.embedding_model, device)
    rerank_tokenizer, rerank_model = load_reranker(args.reranker_model, device)
    bm25 = BM25(chunk.text for chunk in chunks)
    results: list[dict[str, Any]] = []
    for case in cases:
        query = str(case["question"])
        bm25_scores = bm25.scores(query)
        query_vector = embed(
            [query], embedding_tokenizer, embedding_model, device, query=True, batch_size=1
        )[0]
        dense_scores = (embeddings @ query_vector).tolist()
        bm25_ranking = ranks(bm25_scores)
        dense_ranking = ranks(dense_scores)
        candidates = diversify_chunks(
            reciprocal_rank_fusion(bm25_ranking, dense_ranking), chunks
        )
        reranked = rerank(
            query, candidates, chunks, rerank_tokenizer, rerank_model, device
        )
        guarded = guard_high_confidence_evidence(reranked, candidates)
        reranker_ranking = [index for index, _ in reranked]
        final_evidence_ranking = list(
            dict.fromkeys(
                [*exact_term_ranking(query, chunks), *bm25_ranking, *reranker_ranking]
            )
        )
        final_page_ranking = protect_primary_pages(
            candidates, bm25_ranking, chunks
        )
        chunk_rankings = {
            "bm25": bm25_ranking,
            "dense": dense_ranking,
            "fusion": candidates,
            "hybrid": reranker_ranking,
            "guarded": guarded,
            "final": final_page_ranking,
        }
        evidence_rankings = {"final": final_evidence_ranking}
        result: dict[str, Any] = {**case}
        for stage, ranking in chunk_rankings.items():
            page_indexes = unique_page_ranking(ranking, chunks)
            evidence_indexes = page_evidence_indexes(
                evidence_rankings.get(stage, ranking), page_indexes, chunks
            )
            result[f"{stage}_pages"] = [
                chunks[index].page_number for index in page_indexes
            ]
            result[f"{stage}_chunk_ids"] = [
                chunks[index].chunk_id for index in evidence_indexes
            ]
            result[f"{stage}_evidence"] = "\n".join(
                chunks[index].text for index in evidence_indexes
            )
        result["hits"] = [
            {
                **asdict(chunks[index]),
                "rerank_score": round(score, 5),
            }
            for index, score in reranked[:5]
        ]
        results.append(result)

    metrics = {
        stage: stage_metrics(results, stage)
        for stage in ("bm25", "dense", "fusion", "hybrid", "guarded", "final")
    }
    output = {
        "metrics": metrics,
        "case_count": len(results),
        "positive_case_count": sum(bool(result["expected_pages"]) for result in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate page-aware hybrid RAG on OCR output")
    subparsers = parser.add_subparsers(dest="command", required=True)
    index = subparsers.add_parser("index")
    index.add_argument("--pages", type=Path, required=True)
    index.add_argument("--embedding-model", type=Path, required=True)
    index.add_argument("--output", type=Path, required=True)
    index.add_argument("--chunk-limit", type=int, default=900)
    index.add_argument("--child-chunk-limit", type=int, default=360)
    index.add_argument("--device", default="cuda")
    index.set_defaults(function=command_index)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--index", type=Path, required=True)
    evaluate.add_argument("--cases", type=Path, required=True)
    evaluate.add_argument("--embedding-model", type=Path, required=True)
    evaluate.add_argument("--reranker-model", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--device", default="cuda")
    evaluate.set_defaults(function=command_evaluate)
    return parser.parse_args()


if __name__ == "__main__":
    parsed = parse_args()
    parsed.function(parsed)
