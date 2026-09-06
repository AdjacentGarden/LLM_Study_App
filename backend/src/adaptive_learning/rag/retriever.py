from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ..ingestion.models import TextBlock


def _tokens(text: str) -> list[str]:
    cjk = list(re.findall(r"[\u3400-\u9fff]", text))
    words = [value.lower() for value in re.findall(r"[A-Za-z0-9]{2,}", text)]
    bigrams = [cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)]
    return words + bigrams


@dataclass(frozen=True, slots=True)
class SearchHit:
    block: TextBlock
    lexical_score: float
    semantic_score: float
    rerank_score: float


class HybridRetriever:
    """Local lexical baseline with stable interfaces for dense retrieval and reranking."""

    def __init__(self, blocks: list[TextBlock]) -> None:
        self.blocks = blocks
        self.documents = [Counter(_tokens(block.text)) for block in blocks]
        self.document_frequency = Counter(
            token for document in self.documents for token in set(document.keys())
        )

    def search(
        self,
        query: str,
        *,
        chapter_block_ids: set[str] | None = None,
        top_k: int = 6,
    ) -> list[SearchHit]:
        query_tokens = Counter(_tokens(query))
        candidates: list[SearchHit] = []
        for block, document in zip(self.blocks, self.documents, strict=True):
            if chapter_block_ids is not None and block.block_id not in chapter_block_ids:
                continue
            lexical = self._bm25(query_tokens, document)
            phrase_bonus = 0.3 if query.strip() and query.strip() in block.text else 0
            candidates.append(
                SearchHit(
                    block=block,
                    lexical_score=lexical,
                    semantic_score=0,
                    rerank_score=lexical + phrase_bonus,
                )
            )
        return sorted(candidates, key=lambda item: item.rerank_score, reverse=True)[:top_k]

    def _bm25(self, query: Counter[str], document: Counter[str]) -> float:
        if not query or not document:
            return 0
        length = sum(document.values())
        average = sum(sum(value.values()) for value in self.documents) / max(1, len(self.documents))
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
                frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / max(1, average)))
            )
            score += inverse * normalized * min(2, query_count)
        return score
