from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from urllib import request as urllib_request
import base64
import json
import urllib.error

import fitz

from app.core.config import get_settings
from app.core.errors import AppError


class ImageGenerationAdapter(ABC):
    @abstractmethod
    def generate(self, prompt: str, output_path: Path) -> None:
        raise NotImplementedError


class MockImageGenerationAdapter(ImageGenerationAdapter):
    def generate(self, prompt: str, output_path: Path) -> None:
        doc = fitz.open()
        page = doc.new_page(width=1024, height=1024)
        page.draw_rect(fitz.Rect(64, 64, 960, 960), color=(0.1, 0.45, 0.85), fill=(0.92, 0.97, 1.0), width=4)
        page.insert_text((96, 150), "BookCourse AI", fontsize=40, color=(0.05, 0.2, 0.4), fontname="helv")
        page.insert_textbox(
            fitz.Rect(96, 220, 928, 860),
            prompt[:900],
            fontsize=26,
            color=(0.08, 0.16, 0.24),
            fontname="helv",
            align=fitz.TEXT_ALIGN_LEFT,
        )
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
        pix.save(output_path)
        doc.close()


class OpenAICompatibleImageAdapter(ImageGenerationAdapter):
    def __init__(self, api_url: str, api_key: str) -> None:
        self.api_url = api_url
        self.api_key = api_key

    def generate(self, prompt: str, output_path: Path) -> None:
        payload = json.dumps({"prompt": prompt, "size": "1024x1024", "n": 1}).encode("utf-8")
        request = urllib_request.Request(
            self.api_url,
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise AppError("image_api_failed", "图片生成 API 调用失败", details={"reason": str(exc)}) from exc

        image_data = data.get("data", [{}])[0]
        if image_data.get("b64_json"):
            output_path.write_bytes(base64.b64decode(image_data["b64_json"]))
            return
        if image_data.get("url"):
            with urllib_request.urlopen(image_data["url"], timeout=90) as image_response:
                output_path.write_bytes(image_response.read())
            return
        raise AppError("image_api_invalid_response", "图片生成 API 返回格式不支持")


def get_image_adapter() -> ImageGenerationAdapter:
    settings = get_settings()
    if settings.image_provider == "mock":
        return MockImageGenerationAdapter()
    if settings.image_provider == "openai_compatible":
        if not settings.image_api_url or not settings.image_api_key:
            raise AppError("image_api_not_configured", "图片生成 API 未配置")
        return OpenAICompatibleImageAdapter(settings.image_api_url, settings.image_api_key)
    raise AppError("image_provider_unsupported", "不支持的图片生成 provider", details={"provider": settings.image_provider})
