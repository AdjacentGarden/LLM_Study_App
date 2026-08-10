from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import log

from app.document.chunk_protocol import is_chunk_indexable
from app.rag.embedding import render_chunk_embedding_text
from app.rag.tokenizer import tokenize, unique_tokens
from app.schemas.books import Chunk


@dataclass(frozen=True)
class BM25Result:
    chunk: Chunk
    score: float
    rank: int


class BM25Index:
    def __init__(self, chunks: list[Chunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = [chunk for chunk in chunks if is_chunk_indexable(chunk)]
        self.k1 = k1
        self.b = b
        self._doc_tokens: dict[str, list[str]] = {
            chunk.chunk_id: tokenize(render_chunk_embedding_text(chunk), extra_terms=chunk.key_concepts)
            + [term.lower() for term in chunk.key_concepts]
            for chunk in self.chunks
        }
        self._doc_lengths = {chunk_id: len(tokens) for chunk_id, tokens in self._doc_tokens.items()}
        self._avg_doc_len = sum(self._doc_lengths.values()) / max(len(self._doc_lengths), 1)
        self._doc_freq: dict[str, int] = {}
        for tokens in self._doc_tokens.values():
            for token in set(tokens):
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1

    def search(self, query: str, *, chapter_id: str | None = None, top_k: int = 80) -> list[BM25Result]:
        query_tokens = unique_tokens(query)
        if not query_tokens or not self.chunks:
            return []
        results: list[BM25Result] = []
        total_docs = len(self.chunks)
        for chunk in self.chunks:
            if chapter_id and chunk.chapter_id != chapter_id:
                continue
            tokens = self._doc_tokens.get(chunk.chunk_id, [])
            if not tokens:
                continue
            counts = Counter(tokens)
            score = 0.0
            doc_len = self._doc_lengths.get(chunk.chunk_id, 0)
            for token in query_tokens:
                freq = counts.get(token, 0)
                if freq == 0:
                    continue
                doc_freq = self._doc_freq.get(token, 0)
                idf = log(1 + (total_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                denominator = freq + self.k1 * (1 - self.b + self.b * doc_len / max(self._avg_doc_len, 1))
                score += idf * (freq * (self.k1 + 1)) / max(denominator, 1e-9)
            if score > 0:
                results.append(BM25Result(chunk=chunk, score=score, rank=0))
        results.sort(key=lambda item: item.score, reverse=True)
        return [BM25Result(chunk=item.chunk, score=item.score, rank=index + 1) for index, item in enumerate(results[:top_k])]
