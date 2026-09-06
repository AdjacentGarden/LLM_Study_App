from adaptive_learning.ingestion.quality import evaluate_text_quality


def test_clean_chinese_text_scores_better_than_garbled_ocr() -> None:
    clean = "系统由相互作用的要素构成。反馈会改变系统随时间发展的行为。"
    broken = "Gees: OU ot neaaK CADE ee gy ee UVa □□□ _ _ _ A B C D E"

    clean_score = evaluate_text_quality(clean, secondary_candidate=clean).overall_score
    broken_score = evaluate_text_quality(broken, secondary_candidate="unrelated").overall_score

    assert clean_score > broken_score
    assert broken_score < 0.72
