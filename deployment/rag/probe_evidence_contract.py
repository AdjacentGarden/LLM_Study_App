"""Isolated diagnostic: make existing evidence-count constraints explicit; no production edit."""
import json
from pathlib import Path
import time

from adaptive_learning.config import get_settings
from adaptive_learning.ingestion.chaptering import ChapterReconstructor, load_normalized_pages
from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient

ROOT = Path('/data1/zhenghang/adaptive-book-ocr/output/examples-20260905')


class ExplicitContractClient(OpenAICompatibleClient):
    def structured(self, *, system, **kwargs):
        return super().structured(system=system+'\n输出必须满足接口约束：顶层 evidence_ids 含 2–12 个编号；'
                                  '每一个 knowledge_points 项的 evidence_ids 含 1–6 个编号；'
                                  '每项 text 不超过 300 字，知识点为 3–12 项。不要罗列超过上限的引用。',
                                  **kwargs)


def main():
    s = get_settings()
    pages = load_normalized_pages(ROOT/'a1932d43047a/normalized/pages.jsonl')
    results = []
    for model in ['grok-4-fast', 'grok-4.3']:
        client = ExplicitContractClient(LLMConfig(s.text_base_url, s.text_api_key, model,
                                                timeout_seconds=120, max_retries=0))
        builder = ChapterReconstructor(client, validation_retries=0)
        chapters, _ = builder.detector.detect(pages)
        t = time.monotonic()
        row = {'model':model, 'chapter':chapters[0].title, 'diagnostic_only':True,
               'difference':'Same input and validation, explicit existing count/length constraints added to system prompt',
               'production_changed':False}
        try:
            output = builder._summarize(chapters[0], pages)
            row.update(passed=True, output=output.model_dump(mode='json'))
        except Exception as error:
            row.update(passed=False, error=str(error))
            cause = error.__cause__
            if hasattr(cause, 'errors'):
                row['validation_errors'] = cause.errors(include_input=False, include_url=False)
        row['seconds'] = round(time.monotonic()-t, 2)
        row['usage'] = client.usage_totals()
        results.append(row)
        (ROOT/'evidence-contract-diagnostic.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps({k:v for k,v in row.items() if k!='output'}, ensure_ascii=False), flush=True)
        client.close()


if __name__ == '__main__':
    main()
