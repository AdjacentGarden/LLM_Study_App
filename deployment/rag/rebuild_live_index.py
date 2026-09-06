"""Build a new immutable live-book index beside the old one, never overwrite it."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
from rag_eval import build_chunks, embed, load_embedding_model

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')


def main():
    source = ROOT/'output/biology-full/normalized/pages.jsonl'
    target = ROOT/'rag-eval/index-repaired-20260905'
    if target.exists():
        raise RuntimeError('Target already exists; preserve prior index')
    chunks = build_chunks(source, limit=900, child_limit=360)
    model_path = ROOT/'rag-eval/models/bge-small-zh-v1.5'
    tokenizer, model = load_embedding_model(model_path, 'cuda')
    vectors = embed([c.text for c in chunks], tokenizer, model, 'cuda', query=False)
    target.mkdir()
    (target/'chunks.json').write_text(json.dumps([asdict(c) for c in chunks], ensure_ascii=False))
    np.save(target/'embeddings.npy', vectors)
    manifest = {'chunk_count':len(chunks), 'embedding_dimensions':int(vectors.shape[1]),
                'embedding_model':str(model_path), 'source_path':str(source),
                'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'chunker_version':'preserve-code-markup-v2'}
    (target/'index_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
