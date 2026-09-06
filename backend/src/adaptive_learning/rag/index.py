from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

_CJK = re.compile(r"[\u3400-\u9fff]")


class RAGIndexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    chunk_id: str
    page_number: int
    text: str
    granularity: str


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    chunk_id: str
    page_number: int
    text: str
    granularity: str


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    question: str
    score: float
    pages: tuple[int, ...]
    evidence: tuple[RetrievedEvidence, ...]
    lexical_support: bool = False


class QueryEncoder(Protocol):
    @property
    def dimensions(self) -> int: ...

    def encode_query(self, text: str) -> NDArray[np.float32]: ...


class PairReranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


def lexical_tokens(text: str) -> list[str]:
    cjk = _CJK.findall(text)
    bigrams = [cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)]
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9]{2,}", text)]
    return words + bigrams


class BM25:
    def __init__(self, texts: Iterable[str]) -> None:
        self.documents = [Counter(lexical_tokens(text)) for text in texts]
        self.document_frequency = Counter(
            token for document in self.documents for token in document
        )
        self.average_length = sum(sum(document.values()) for document in self.documents) / max(
            1, len(self.documents)
        )

    def scores(self, query: str) -> list[float]:
        tokens = Counter(lexical_tokens(query))
        return [self._score(tokens, document) for document in self.documents]

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
                + (len(self.documents) - documents_with_token + 0.5) / (documents_with_token + 0.5)
            )
            normalized = (
                frequency
                * 2.2
                / (frequency + 1.2 * (0.25 + 0.75 * length / max(1, self.average_length)))
            )
            score += inverse * normalized * min(2, query_count)
        return score


def _ranks(scores: Sequence[float]) -> list[int]:
    return sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)


def _technical_terms(query: str) -> list[str]:
    values = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|\d+(?:\.\d+)?%?", query)
    return list(dict.fromkeys(value.lower() for value in values))


def _exact_term_ranking(query: str, chunks: Sequence[IndexedChunk]) -> list[int]:
    terms = _technical_terms(query)
    if len(terms) < 2:
        return []
    frequencies = {term: sum(term in chunk.text.lower() for chunk in chunks) for term in terms}
    weights = {
        term: math.log(1 + (len(chunks) + 1) / (frequency + 1))
        for term, frequency in frequencies.items()
    }
    scored: list[tuple[int, float]] = []
    for index, chunk in enumerate(chunks):
        lowered = chunk.text.lower()
        score = sum(weight for term, weight in weights.items() if term in lowered)
        if score:
            scored.append((index, score))
    return [index for index, _ in sorted(scored, key=lambda item: item[1], reverse=True)]


def _definition_ranking(query: str, chunks: Sequence[IndexedChunk]) -> list[int]:
    """A glossary/definition route for technical terms, not raw occurrence frequency."""
    if not re.search(r'是什么|什么是|表示什么|含义|区别|定义|\bmeaning\b|\bmean\b|\bdifference\b', query, re.I):
        return []
    terms = [term for term in _technical_terms(query) if len(term) >= 2][:8]
    if not terms:
        return []
    patterns = [re.compile(r'(?<!\w)' + re.escape(term) + r'\s*[:：](?!:)', re.I) for term in terms]
    scored = [(i, sum(bool(p.search(chunk.text)) for p in patterns)) for i, chunk in enumerate(chunks)]
    return [i for i, score in sorted(scored, key=lambda item: item[1], reverse=True) if score]


def _locator_ranking(query: str, chunks: Sequence[IndexedChunk]) -> list[int]:
    """Reserve evidence for explicit exercise IDs and scientific alphanumeric symbols."""
    identifiers = re.findall(r'\b[A-Za-z]+\d+[A-Za-z0-9]*\b', query)[:8]
    examples = re.findall(r'(?:例|example)\s*(\d+)\s*[-—－.]\s*(\d+)', query, re.I)[:8]
    if not identifiers and not examples:
        return []
    scored = []
    for i, chunk in enumerate(chunks):
        # Normalize notation only for matching; returned quotations remain untouched.
        text = re.sub(r'\\(?:mathrm|text)\s*\{([^{}]+)\}', r'\1', chunk.text)
        text = re.sub(r'_\s*\{\s*(\d+)\s*\}', r'\1', text)
        count = sum(bool(re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)', text, re.I)) for term in identifiers)
        count += sum(bool(re.search(r'(?:例|example)\s*'+a+r'\s*[-—－.]\s*'+b+r'(?!\d)', text, re.I))
                     for a, b in examples)
        if count:
            scored.append((i, count))
    return [i for i, _ in sorted(scored, key=lambda item: item[1], reverse=True)]


def _reciprocal_rank_fusion(*rankings: Sequence[int], limit: int = 30) -> list[int]:
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking[:60], start=1):
            fused[index] = fused.get(index, 0.0) + 1 / (60 + rank)
    ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)
    return [index for index, _ in ordered[:limit]]


def _diversify(
    ranking: Sequence[int], chunks: Sequence[IndexedChunk], *, per_page: int = 2, limit: int = 30
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


def _unique_pages(
    ranking: Sequence[int], chunks: Sequence[IndexedChunk], *, limit: int
) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for index in ranking:
        page = chunks[index].page_number
        if page in seen:
            continue
        seen.add(page)
        result.append(index)
        if len(result) >= limit:
            break
    return result


def _protected_pages(
    primary: Sequence[int],
    supplement: Sequence[int],
    chunks: Sequence[IndexedChunk],
    *,
    primary_pages: int,
    limit: int,
) -> list[int]:
    result = _unique_pages(primary, chunks, limit=primary_pages)
    seen = {chunks[index].page_number for index in result}
    for index in supplement:
        page = chunks[index].page_number
        if page in seen:
            continue
        result.append(index)
        seen.add(page)
        if len(result) >= limit:
            break
    return result


def _evidence_indexes(
    ranking: Sequence[int],
    page_ranking: Sequence[int],
    chunks: Sequence[IndexedChunk],
    *,
    per_page: int,
    total: int,
) -> list[int]:
    pages = [chunks[index].page_number for index in page_ranking]
    buckets: dict[int, list[int]] = {page: [] for page in pages}
    for index in ranking:
        page = chunks[index].page_number
        if page not in buckets or len(buckets[page]) >= per_page:
            continue
        text = chunks[index].text
        existing = [chunks[other].text for other in buckets[page]]
        if any(text in value or value in text for value in existing):
            continue
        buckets[page].append(index)

    # Page representatives come first so sibling child chunks cannot consume the budget.
    representatives = [bucket[0] for bucket in buckets.values() if bucket]
    siblings = [index for bucket in buckets.values() for index in bucket[1:]]
    return [*representatives, *siblings][:total]


class PersistentRAGIndex:
    def __init__(
        self,
        *,
        index_dir: Path,
        chunks: list[IndexedChunk],
        embeddings: NDArray[np.float32],
        encoder: QueryEncoder,
        reranker: PairReranker,
    ) -> None:
        self.index_dir = index_dir
        self.chunks = chunks
        self.embeddings = embeddings
        self.encoder = encoder
        self.reranker = reranker
        self.bm25 = BM25(chunk.text for chunk in chunks)
        self._by_id = {chunk.chunk_id: chunk for chunk in chunks}
        self._page_parents: dict[int, list[IndexedChunk]] = {}
        for chunk in chunks:
            if chunk.granularity == 'parent':
                self._page_parents.setdefault(chunk.page_number, []).append(chunk)

    def _expanded_evidence(self, indexes: Sequence[int]) -> tuple[RetrievedEvidence, ...]:
        """Small retrieval children locate passages; bounded parent windows supply intact context."""
        result: list[RetrievedEvidence] = []
        seen: set[str] = set()
        budget = 18000
        for index in indexes:
            hit = self.chunks[index]
            parent_id = re.sub(r'-s\d+$', '', hit.chunk_id)
            parent = self._by_id.get(parent_id, hit)
            if parent.chunk_id in seen:
                continue
            parents = self._page_parents.get(parent.page_number, [])
            window = [parent]
            if parent in parents:
                position = parents.index(parent)
                # Include the following formula/proof block and the preceding introduction.
                for offset in (1, -1):
                    neighbor = position + offset
                    if 0 <= neighbor < len(parents):
                        candidate = parents[neighbor]
                        if sum(len(c.text) for c in window) + len(candidate.text) + 2 <= 2800:
                            if offset == 1:
                                window.append(candidate)
                            else:
                                window.insert(0, candidate)
            text = window[0].text
            for chunk in window[1:]:
                overlap = next((n for n in range(min(len(text), len(chunk.text), 120), 19, -1)
                                if text.endswith(chunk.text[:n])), 0)
                text += chunk.text[overlap:] if overlap else '\n' + chunk.text
            if len(text) > budget:
                continue  # Never truncate a formula to fit a token budget.
            budget -= len(text)
            seen.update(chunk.chunk_id for chunk in window)
            result.append(RetrievedEvidence('+'.join(c.chunk_id for c in window),
                parent.page_number, text, 'parent_window'))
        return tuple(result)

    @classmethod
    def load(
        cls,
        index_dir: Path,
        *,
        encoder: QueryEncoder,
        reranker: PairReranker,
    ) -> PersistentRAGIndex:
        manifest_path = index_dir / "index_manifest.json"
        chunks_path = index_dir / "chunks.json"
        embeddings_path = index_dir / "embeddings.npy"
        missing = [
            path.name
            for path in (manifest_path, chunks_path, embeddings_path)
            if not path.is_file()
        ]
        if missing:
            raise RAGIndexError(f"index is incomplete; missing: {', '.join(missing)}")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            chunks = [
                IndexedChunk(
                    chunk_id=str(item["chunk_id"]),
                    page_number=int(item["page_number"]),
                    text=str(item["text"]),
                    granularity=str(item.get("granularity", "parent")),
                )
                for item in raw_chunks
            ]
            embeddings = np.load(embeddings_path, mmap_mode="r")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RAGIndexError(f"index cannot be loaded: {type(error).__name__}") from error

        expected_count = int(manifest.get("chunk_count", -1))
        expected_dimensions = int(manifest.get("embedding_dimensions", -1))
        if not chunks or expected_count != len(chunks):
            raise RAGIndexError("chunk count does not match index manifest")
        if embeddings.ndim != 2 or embeddings.shape != (len(chunks), expected_dimensions):
            raise RAGIndexError("embedding shape does not match index manifest")
        if encoder.dimensions != expected_dimensions:
            raise RAGIndexError("query encoder dimensions do not match index")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise RAGIndexError("index contains duplicate chunk ids")
        if any(chunk.page_number < 1 or not chunk.text.strip() for chunk in chunks):
            raise RAGIndexError("index contains invalid chunks")
        return cls(
            index_dir=index_dir,
            chunks=chunks,
            embeddings=embeddings,
            encoder=encoder,
            reranker=reranker,
        )

    def search(
        self,
        question: str,
        *,
        top_pages: int = 5,
        max_evidence: int = 10,
        per_page: int = 3,
    ) -> RetrievalResult:
        question = question.strip()
        if not question:
            raise ValueError("question must not be empty")
        if top_pages < 1 or max_evidence < 1 or per_page < 1:
            raise ValueError("retrieval limits must be positive")

        bm25_scores = self.bm25.scores(question)
        query_vector = np.asarray(self.encoder.encode_query(question), dtype=np.float32)
        if query_vector.shape != (self.embeddings.shape[1],):
            raise RAGIndexError("query encoder returned an invalid vector shape")
        norm = float(np.linalg.norm(query_vector))
        if not math.isfinite(norm) or norm <= 0:
            raise RAGIndexError("query encoder returned an invalid vector")
        query_vector = query_vector / norm
        dense_scores = np.asarray(self.embeddings @ query_vector).tolist()
        bm25_ranking = _ranks(bm25_scores)
        dense_ranking = _ranks(dense_scores)
        fused = _diversify(_reciprocal_rank_fusion(bm25_ranking, dense_ranking), self.chunks)
        rerank_scores = self.reranker.score(question, [self.chunks[index].text for index in fused])
        if len(rerank_scores) != len(fused) or any(
            not math.isfinite(float(score)) for score in rerank_scores
        ):
            raise RAGIndexError("reranker returned invalid scores")
        reranked_pairs = sorted(
            zip(fused, rerank_scores, strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
        reranked = [index for index, _ in reranked_pairs]
        pages = _protected_pages(
            fused,
            bm25_ranking,
            self.chunks,
            primary_pages=min(3, top_pages),
            limit=top_pages,
        )
        definitions = _definition_ranking(question, self.chunks)
        locators = _locator_ranking(question, self.chunks)
        if definitions or locators:
            pages = _protected_pages([*locators, *definitions], [*fused, *bm25_ranking], self.chunks,
                                     primary_pages=min(2, top_pages), limit=top_pages)
        evidence_ranking = list(
            dict.fromkeys([*locators, *definitions, *_exact_term_ranking(question, self.chunks), *bm25_ranking, *reranked])
        )
        evidence_indexes = _evidence_indexes(
            evidence_ranking,
            pages,
            self.chunks,
            per_page=per_page,
            total=max_evidence,
        )
        score = float(reranked_pairs[0][1]) if reranked_pairs else float("-inf")
        evidence = self._expanded_evidence(evidence_indexes)
        stopwords = set('a an the in on of to and or is are was were be as by for from with why how what where does do did according textbook describe book'.split())
        anchors = set(re.findall(r'[a-z][a-z0-9_]{2,}', question.lower())) - stopwords
        lexical_support = len(anchors) >= 2 and any(
            len(anchors & set(re.findall(r'[a-z][a-z0-9_]{2,}', item.text.lower()))) / len(anchors) >= 0.75
            for item in evidence
        )
        return RetrievalResult(
            question=question,
            score=score,
            pages=tuple(self.chunks[index].page_number for index in pages),
            evidence=evidence,
            lexical_support=lexical_support,
        )
