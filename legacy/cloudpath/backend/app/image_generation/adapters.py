from __future__ import annotations

from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
from urllib import request as urllib_request
import base64
import binascii
import json
import os
import tempfile
import urllib.error

import fitz
from PIL import Image, UnidentifiedImageError

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
        settings = get_settings()
        payload = json.dumps(
            {"prompt": prompt, "size": "1024x1024", "n": 1, "response_format": "b64_json"}
        ).encode("utf-8")
        request = urllib_request.Request(
            self.api_url,
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            # Do not follow provider redirects: even a trusted provider could
            # otherwise redirect this server into a private/metadata network.
            opener = urllib_request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=90) as response:
                raw = response.read(settings.image_provider_max_response_bytes + 1)
            if len(raw) > settings.image_provider_max_response_bytes:
                raise AppError("image_api_response_too_large", "图片生成 API 响应超过安全限制")
            data = json.loads(raw.decode("utf-8"))
        except AppError:
            raise
        except (urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("image_api_failed", "图片生成 API 调用失败", details={"reason": str(exc)}) from exc

        image_data = data.get("data", [{}])[0]
        encoded = image_data.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            # URL responses are deliberately rejected to eliminate provider-
            # response SSRF. Production providers must honor response_format.
            raise AppError("image_api_invalid_response", "图片生成 API 必须返回 b64_json")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AppError("image_api_invalid_response", "图片生成 API 返回了无效图片编码") from exc
        if len(decoded) > settings.image_provider_max_response_bytes:
            raise AppError("image_api_response_too_large", "生成图片超过安全限制")

        try:
            with Image.open(BytesIO(decoded)) as image:
                width, height = image.size
                if width * height > settings.max_image_pixels:
                    raise AppError(
                        "image_pixel_limit_exceeded",
                        "生成图片像素超过安全限制",
                        details={"width": width, "height": height},
                    )
                if str(image.format or "").upper() not in {"PNG", "JPEG", "WEBP"}:
                    raise AppError("image_api_invalid_response", "生成图片格式不受支持")
                image.load()
                normalized = image.convert("RGB")
                output_path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary_name = tempfile.mkstemp(
                    prefix=f".{output_path.name}.",
                    suffix=".tmp",
                    dir=output_path.parent,
                )
                os.close(fd)
                temporary = Path(temporary_name)
                try:
                    normalized.save(temporary, format="PNG", optimize=True)
                    os.replace(temporary, output_path)
                finally:
                    temporary.unlink(missing_ok=True)
        except AppError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise AppError("image_api_invalid_response", "生成图片无法安全解码") from exc


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, "redirects disabled", headers, fp)


def get_image_adapter() -> ImageGenerationAdapter:
    settings = get_settings()
    if settings.image_provider == "mock":
        return MockImageGenerationAdapter()
    if settings.image_provider == "openai_compatible":
        if not settings.image_api_url or not settings.image_api_key:
            raise AppError("image_api_not_configured", "图片生成 API 未配置")
        return OpenAICompatibleImageAdapter(settings.image_api_url, settings.image_api_key)
    raise AppError("image_provider_unsupported", "不支持的图片生成 provider", details={"provider": settings.image_provider})
