# BookCourse AI RAG Eval Report

Date: 2026-07-04

Fixture book: `book_phase4`

## Retrieval Stack

- Sparse retrieval: BM25
- Dense retrieval: local hashing embedder
- Fusion: RRF
- Answer adapter: grounded template adapter
- Evidence fields: `chunk_id`, `page`, `chapter_id`, `score`, `retrieval_method`, `source_metadata`, `related_assets`

## Fixed Evaluation Questions

```json
[
  {
    "id": "Q1",
    "question": "同源染色体什么时候分离",
    "scope": {"book_id": "book_phase4", "chapter_id": "c2s1"},
    "top_k_chunks": [
      {
        "chunk_id": "chunk_meiosis_001",
        "page": 30,
        "chapter_id": "c2s1",
        "retrieval_method": "rrf",
        "score": ">0",
        "source_metadata": {"bm25_score": ">0", "dense_score": ">0"}
      }
    ],
    "generated_answer": "仅基于已检索到的教材片段，提示学生对照来源检查分离对象。",
    "citations": [{"chunk_id": "chunk_meiosis_001", "page": 30, "chapter_id": "c2s1"}],
    "related_assets": [{"asset_id": "fig_phase4_001", "source_type": "extracted", "source_chunk_ids": ["chunk_meiosis_001"]}],
    "manual_judgement": "pass"
  },
  {
    "id": "Q2",
    "question": "姐妹染色单体什么时候分离",
    "scope": {"book_id": "book_phase4", "chapter_id": "c2s1"},
    "top_k_chunks": [{"chunk_id": "chunk_meiosis_001", "page": 30, "chapter_id": "c2s1", "score": ">0"}],
    "generated_answer": "基于同一教材片段回答，并返回 page/chunk citation。",
    "citations": [{"chunk_id": "chunk_meiosis_001", "page": 30}],
    "related_assets": [{"asset_id": "fig_phase4_001", "source_type": "extracted"}],
    "manual_judgement": "pass"
  },
  {
    "id": "Q3",
    "question": "请解释减数分裂",
    "scope": {"book_id": "book_phase4"},
    "top_k_chunks": [{"chunk_id": "chunk_meiosis_001", "page": 30, "chapter_id": "c2s1", "score": ">0"}],
    "generated_answer": "基于检索片段给出概念解释，不扩写整书内容。",
    "citations": [{"chunk_id": "chunk_meiosis_001", "page": 30}],
    "related_assets": [{"asset_id": "fig_phase4_001", "source_type": "extracted"}],
    "manual_judgement": "pass"
  },
  {
    "id": "Q4",
    "question": "减数分裂和有丝分裂如何对比",
    "scope": {"book_id": "book_phase4"},
    "top_k_chunks": [
      {"chunk_id": "chunk_meiosis_001", "page": 30, "chapter_id": "c2s1", "score": ">0"},
      {"chunk_id": "chunk_mitosis_001", "page": 38, "chapter_id": "c2s2", "score": ">0"}
    ],
    "generated_answer": "基于两个 chunk 做对比，citation 覆盖 meiosis 与 mitosis 来源。",
    "citations": [{"chunk_id": "chunk_meiosis_001"}, {"chunk_id": "chunk_mitosis_001"}],
    "related_assets": [{"asset_id": "fig_phase4_001", "source_type": "extracted"}],
    "manual_judgement": "pass"
  },
  {
    "id": "Q5",
    "question": "同源染色体什么时候分离",
    "scope": {"book_id": "book_phase4", "chapter_id": "missing_chapter"},
    "top_k_chunks": [],
    "generated_answer": "原书中未找到明确说明。",
    "citations": [],
    "related_assets": [],
    "manual_judgement": "pass_low_confidence"
  },
  {
    "id": "Q6",
    "question": "Newton law outside this biology chapter",
    "scope": {"book_id": "book_phase4"},
    "top_k_chunks": [],
    "generated_answer": "原书中未找到明确说明。",
    "citations": [],
    "related_assets": [],
    "manual_judgement": "pass_out_of_book_refusal"
  }
]
```

## AI-Generated Figure Evidence

```json
{
  "asset_id_pattern": "ai_fig_{book_id}_{job_id}",
  "source_type": "ai_generated",
  "source_chunk_ids_required": true,
  "generation_provider": "mock or openai_compatible",
  "generation_prompt_summary": "Original educational diagram prompt built from source chunk text; does not request copying the textbook figure.",
  "review_status": "pending"
}
```

## Automated Checks

```powershell
cd BookCourseAI_backend
python -m pytest app/tests/test_phase4_rag_assignment_plan.py
```

The automated test asserts:

- 6 fixed RAG questions are executed.
- In-book questions return `confidence=medium`.
- Missing-chapter and out-of-book questions return `confidence=low`, no citations, and no related assets.
- Citations include `score`, `retrieval_method`, and `source_metadata`.
- Related source figure asset `fig_phase4_001` remains tied to `chunk_meiosis_001`.
