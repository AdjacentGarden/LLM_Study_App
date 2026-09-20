#!/usr/bin/env python3
"""Offline, bounded transcription worker for private study voice notes.

This intentionally runs out-of-process: the application currently uses a newer
Python runtime while the compact CTranslate2 wheel is isolated in Python 3.8.
Only the final JSON object is written to stdout so the caller can validate it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--max-seconds", type=float, default=600)
    args = parser.parse_args()

    audio = Path(args.audio).resolve()
    model_path = Path(args.model).resolve()
    if not audio.is_file() or not model_path.is_dir():
        raise SystemExit("audio or model is missing")

    from faster_whisper import WhisperModel

    started = time.monotonic()
    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=8,
        num_workers=1,
    )
    segments, info = model.transcribe(
        str(audio),
        language=args.language,
        beam_size=5,
        best_of=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=True,
        initial_prompt=(
            "这是一段中文学习笔记，可能包含教材术语、英文缩写、数字和公式。" + args.prompt[:800]
        ),
        word_timestamps=True,
    )
    rows = []
    uncertain = []
    transcript = []
    duration = 0.0
    for segment in segments:
        duration = max(duration, float(segment.end))
        if duration > args.max_seconds + 2:
            raise SystemExit("audio is too long")
        text = segment.text.strip()
        if not text:
            continue
        transcript.append(text)
        probability = math.exp(min(0.0, float(segment.avg_logprob)))
        rows.append(
            {
                "start": round(float(segment.start), 2),
                "end": round(float(segment.end), 2),
                "text": text,
                "confidence": round(probability, 3),
            }
        )
        low_words = [
            word.word.strip()
            for word in (segment.words or [])
            if any(character.isalnum() for character in word.word)
            and float(word.probability) < 0.30
        ]
        if low_words:
            uncertain.append(
                f"{segment.start:.1f}–{segment.end:.1f} 秒可能听错：{'、'.join(low_words[:8])}"
            )
        if probability < 0.55 or float(segment.no_speech_prob) > 0.55:
            uncertain.append(f"{segment.start:.1f}–{segment.end:.1f} 秒：{text}")

    detected_duration = float(getattr(info, "duration", duration) or duration)
    if detected_duration > args.max_seconds + 2:
        raise SystemExit("audio is too long")
    value = {
        "transcript": "".join(transcript).strip(),
        "uncertain": uncertain[:30],
        "segments": rows,
        "duration_seconds": round(detected_duration, 2),
        "language": str(getattr(info, "language", args.language)),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    json.dump(value, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
