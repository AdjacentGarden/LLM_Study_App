"""Loopback-only photo-note UI regression server; executes the real job pipeline."""
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", required=True)
parser.add_argument("--port", type=int, default=19286)
args = parser.parse_args()
data_dir = Path(args.data_dir)
data_dir.mkdir(parents=True, exist_ok=True)
os.environ["DATA_DIR"] = str(data_dir)
os.environ["DATABASE_URL"] = ""
os.environ["DEEPSEEK_API_KEY"] = "isolated-ui-test-adapter"

from fastapi import FastAPI, HTTPException
import uvicorn
from adaptive_learning.studio import get_studio
from adaptive_learning.api.studio_routes import studio_router

studio = get_studio(data_dir)
calls = []
class TestClient:
    def structured(self, **kwargs):
        calls.append({"images": len(kwargs.get("images", [])), "stage": "recognize" if "images" in kwargs else "organize"})
        (data_dir / "calls.json").write_text(json.dumps(calls))
        if "images" in kwargs:
            return {"transcript": "减数分裂\nDNA只复制1次。\n细胞连续分裂2次。\n染色体：2n → n。\n不是有丝分裂。", "uncertain": []}
        return {"title": "减数分裂照片笔记", "summary": "整理减数分裂的次数和染色体变化。",
                "polished": "减数分裂\n- DNA只复制1次。\n- 细胞连续分裂2次。\n- 染色体：2n → n。\n- 不是有丝分裂。"}
    def close(self): pass
studio.voice_note_client = TestClient
studio.capabilities = lambda: {"photo_notes": True, "image": False, "video": False, "voice_notes": False, "notes_ai": False}

def owner(): return "photo-ui-isolated-owner"
def owned_book(owner, book):
    if book != "book_biology_2": raise HTTPException(404, "教材不存在")
app = FastAPI()
app.include_router(studio_router(data_dir, owner, owned_book), prefix="/api")
@app.on_event("startup")
def startup(): studio.start()
@app.on_event("shutdown")
def shutdown(): studio.stop()
@app.get("/health")
def health(): return {"ok": True, "isolated_test_adapter": True}
uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
