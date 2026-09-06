"""Private visitor profile; avatar bytes and fields committed together on Save."""

from __future__ import annotations

import base64
import binascii
import io
import unicodedata
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UserProfileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    nickname: str = Field(min_length=1, max_length=32)
    age: int | None = Field(default=None, ge=1, le=120, strict=True)
    bio: str = Field(default="", max_length=160)
    revision: int = Field(ge=0, strict=True)
    avatar_data_url: str | None = Field(default=None, max_length=2_800_000)
    reset_avatar: bool = False

    @field_validator("nickname", "bio")
    @classmethod
    def no_controls(cls, value: str) -> str:
        if any(unicodedata.category(c) == "Cc" for c in value):
            raise ValueError("请勿包含换行或控制字符")
        return value

    @model_validator(mode="after")
    def avatar_choice(self) -> UserProfileInput:
        if self.reset_avatar and self.avatar_data_url:
            raise ValueError("不能同时上传和重置头像")
        return self


def normalize_avatar(value: str) -> bytes:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        header, encoded = value.split(",", 1)
        if header not in {
            "data:image/jpeg;base64",
            "data:image/png;base64",
            "data:image/webp;base64",
        }:
            raise ValueError("unsupported format")
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > 2 * 1024 * 1024:
            raise ValueError("image too large")
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"} or getattr(
                source, "is_animated", False
            ):
                raise ValueError("unsupported image")
            if source.width * source.height > 16_000_000:
                raise ValueError("too many pixels")
            source.load()
            rgba = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGBA"), (256, 256))
            output = Image.new("RGB", (256, 256), "white")
            output.paste(rgba, mask=rgba.getchannel("A"))
            buffer = io.BytesIO()
            output.save(buffer, format="JPEG", quality=88)
            return buffer.getvalue()  # Re-encoding strips EXIF/location and executable payloads.
    except (
        ValueError,
        OSError,
        binascii.Error,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as error:
        raise HTTPException(
            422, "请选择不超过 2 MB、1600 万像素的静态 JPG、PNG 或 WebP 图片"
        ) from error


def public_profile(row: Any) -> dict[str, Any]:
    if row is None:
        return {"nickname": "新同学", "age": None, "bio": "", "revision": 0, "avatar_url": None}
    return {
        "nickname": row["nickname"],
        "age": row["age"],
        "bio": row["bio"],
        "revision": row["revision"],
        "avatar_url": f"/api/user/profile/avatar?v={row['revision']}" if row["avatar"] else None,
    }
