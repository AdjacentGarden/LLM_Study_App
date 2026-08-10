from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import posixpath
import re
import time
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from app.core.config import get_settings
from app.core.errors import AppError


_MAIN_PARTS = {
    ".docx": (
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    ),
    ".pptx": (
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    ),
    ".xlsx": (
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    ),
}
_ARCHIVE_EXTENSIONS = {
    ".zip",
    ".7z",
    ".rar",
    ".jar",
    ".docx",
    ".pptx",
    ".xlsx",
    ".docm",
    ".pptm",
    ".xlsm",
}
_FORBIDDEN_PART_MARKERS = (
    "vbaproject",
    "/activex/",
    "/embeddings/",
    "oleobject",
    "encryptedpackage",
    "encryptioninfo",
)
_FORBIDDEN_CONTENT_TYPE_MARKERS = (
    "macroenabled",
    "vbaproject",
    "oleobject",
    "activex",
    "encryptedpackage",
)
_XML_DECLARATION_MARKERS = (b"<!doctype", b"<!entity")
_ALLOWED_COMPRESSIONS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_NATURAL_NUMBER = re.compile(r"(\d+)")


@dataclass(frozen=True, slots=True)
class OfficeSourceUnit:
    index: int
    label: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OOXMLInspection:
    extension: str
    unit_type: str
    units: tuple[OfficeSourceUnit, ...]
    entry_count: int
    total_uncompressed_bytes: int
    relationship_count: int
    resource_count: int

    def source_locations(self) -> list[dict[str, object]]:
        return [
            {"index": unit.index, "label": unit.label, **unit.metadata}
            for unit in self.units
        ]


class _Deadline:
    def __init__(self, timeout_seconds: float) -> None:
        self._expires_at = time.monotonic() + timeout_seconds

    def check(self) -> None:
        if time.monotonic() > self._expires_at:
            raise AppError(
                "office_validation_timeout",
                "Office 文件安全校验超时",
                details={"stage": "ooxml_validation"},
            )


def _invalid(code: str, message: str, **details: object) -> AppError:
    return AppError(code, message, details=dict(details))


def _safe_member_name(raw_name: str) -> str:
    if not raw_name or "\x00" in raw_name or "\\" in raw_name:
        raise _invalid("ooxml_unsafe_path", "Office 压缩包包含不安全路径")
    decoded = unquote(raw_name)
    if decoded != raw_name and ("\\" in decoded or "\x00" in decoded):
        raise _invalid("ooxml_unsafe_path", "Office 压缩包包含不安全路径")
    path = PurePosixPath(decoded)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _invalid("ooxml_unsafe_path", "Office 压缩包包含路径穿越")
    if path.parts and ":" in path.parts[0]:
        raise _invalid("ooxml_unsafe_path", "Office 压缩包包含绝对路径")
    normalized = posixpath.normpath(decoded).lstrip("/")
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise _invalid("ooxml_unsafe_path", "Office 压缩包包含路径穿越")
    return normalized


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, deadline: _Deadline) -> bytes:
    deadline.check()
    try:
        payload = archive.read(info)
    except RuntimeError as exc:
        raise _invalid("encrypted_office_unsupported", "暂不支持带密码的 Office 文件") from exc
    except (OSError, NotImplementedError, zipfile.BadZipFile) as exc:
        raise _invalid("invalid_ooxml", "Office 文件内容损坏或无法解压") from exc
    deadline.check()
    return payload


def _parse_xml(payload: bytes, *, part_name: str) -> ET.Element:
    lowered = payload.lower()
    if any(marker in lowered for marker in _XML_DECLARATION_MARKERS):
        raise _invalid("ooxml_unsafe_xml", "Office 文件包含不安全 XML 声明", part=part_name)
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise _invalid("invalid_ooxml_xml", "Office 文件包含损坏的 XML", part=part_name) from exc


def _relationship_source(rel_name: str) -> str:
    if rel_name == "_rels/.rels":
        return ""
    marker = "/_rels/"
    if marker not in rel_name or not rel_name.endswith(".rels"):
        return ""
    prefix, leaf = rel_name.split(marker, 1)
    return f"{prefix}/{leaf[:-5]}"


def _resolve_relationship_target(source_part: str, target: str) -> str:
    decoded = unquote(target.strip())
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or decoded.startswith("//") or "\\" in decoded:
        raise _invalid("ooxml_external_relationship", "Office 文件包含外部资源引用")
    path = parsed.path
    if not path:
        raise _invalid("invalid_ooxml_relationship", "Office 文件包含空的关系目标")
    if path.startswith("/"):
        resolved = posixpath.normpath(path.lstrip("/"))
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), path))
    if resolved in {"", ".", ".."} or resolved.startswith("../"):
        raise _invalid("ooxml_unsafe_relationship", "Office 文件关系目标超出文档包")
    return resolved


def _relationship_map(
    root: ET.Element,
    *,
    rel_name: str,
    member_names: set[str],
) -> tuple[dict[str, str], int]:
    source_part = _relationship_source(rel_name)
    relationships: dict[str, str] = {}
    count = 0
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "Relationship":
            continue
        count += 1
        if str(item.attrib.get("TargetMode", "")).strip().lower() == "external":
            raise _invalid("ooxml_external_relationship", "Office 文件包含外部资源引用", part=rel_name)
        relationship_id = str(item.attrib.get("Id", "")).strip()
        target = str(item.attrib.get("Target", "")).strip()
        resolved = _resolve_relationship_target(source_part, target)
        if resolved not in member_names:
            raise _invalid(
                "invalid_ooxml_relationship",
                "Office 文件关系目标不存在",
                part=rel_name,
            )
        if relationship_id:
            relationships[relationship_id] = resolved
    return relationships, count


def _attribute_by_local_name(element: ET.Element, local_name: str) -> str | None:
    for name, value in element.attrib.items():
        if name.rsplit("}", 1)[-1] == local_name:
            return str(value)
    return None


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.lower() for part in _NATURAL_NUMBER.split(value))


def _slide_units(
    xml_roots: dict[str, ET.Element],
    relationships: dict[str, dict[str, str]],
    member_names: set[str],
) -> tuple[OfficeSourceUnit, ...]:
    presentation = xml_roots["ppt/presentation.xml"]
    presentation_rels = relationships.get("ppt/_rels/presentation.xml.rels", {})
    ordered_parts: list[str] = []
    for item in presentation.iter():
        if item.tag.rsplit("}", 1)[-1] != "sldId":
            continue
        relationship_id = _attribute_by_local_name(item, "id")
        if relationship_id and relationship_id in presentation_rels:
            ordered_parts.append(presentation_rels[relationship_id])
    if not ordered_parts:
        ordered_parts = sorted(
            (name for name in member_names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=_natural_key,
        )
    units: list[OfficeSourceUnit] = []
    for index, part in enumerate(ordered_parts, start=1):
        root = xml_roots.get(part)
        texts: list[str] = []
        if root is not None:
            texts = [
                (item.text or "").strip()
                for item in root.iter()
                if item.tag.rsplit("}", 1)[-1] == "t" and (item.text or "").strip()
            ]
        title = texts[0][:160] if texts else ""
        label = f"幻灯片 {index}" + (f"：{title}" if title else "")
        units.append(
            OfficeSourceUnit(
                index=index,
                label=label,
                metadata={"slide_number": index, "slide_title": title, "source_format": "pptx"},
            )
        )
    return tuple(units)


def _sheet_units(
    xml_roots: dict[str, ET.Element],
    relationships: dict[str, dict[str, str]],
) -> tuple[OfficeSourceUnit, ...]:
    workbook = xml_roots["xl/workbook.xml"]
    workbook_rels = relationships.get("xl/_rels/workbook.xml.rels", {})
    units: list[OfficeSourceUnit] = []
    for item in workbook.iter():
        if item.tag.rsplit("}", 1)[-1] != "sheet":
            continue
        index = len(units) + 1
        name = str(item.attrib.get("name") or f"Sheet{index}").strip()[:160]
        relationship_id = _attribute_by_local_name(item, "id")
        worksheet_part = workbook_rels.get(relationship_id or "")
        cell_range = ""
        if worksheet_part and worksheet_part in xml_roots:
            for child in xml_roots[worksheet_part].iter():
                if child.tag.rsplit("}", 1)[-1] == "dimension":
                    cell_range = str(child.attrib.get("ref") or "").strip()[:80]
                    break
        label = f"工作表 {name}" + (f"（{cell_range}）" if cell_range else "")
        units.append(
            OfficeSourceUnit(
                index=index,
                label=label,
                metadata={
                    "sheet_name": name,
                    "cell_range": cell_range or None,
                    "source_format": "xlsx",
                },
            )
        )
    return tuple(units)


def _document_units(xml_roots: dict[str, ET.Element]) -> tuple[OfficeSourceUnit, ...]:
    document = xml_roots["word/document.xml"]
    body = next((item for item in document.iter() if item.tag.rsplit("}", 1)[-1] == "body"), None)
    block_count = 0
    if body is not None:
        block_count = sum(1 for item in list(body) if item.tag.rsplit("}", 1)[-1] in {"p", "tbl"})
    return (
        OfficeSourceUnit(
            index=1,
            label="文档结构（标题路径 + 块序号）",
            metadata={
                "source_format": "docx",
                "has_stable_page": False,
                "document_block_count": block_count,
            },
        ),
    )


def inspect_ooxml(path: Path, extension: str | None = None) -> OOXMLInspection:
    ext = (extension or path.suffix).lower()
    if ext not in _MAIN_PARTS:
        raise _invalid("unsupported_office_type", "不支持的 Office 文件类型", extension=ext)
    settings = get_settings()
    deadline = _Deadline(settings.office_validation_timeout_seconds)
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise _invalid("invalid_ooxml", "Office 文件不是有效的 OOXML 压缩包") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > settings.max_zip_entries:
            raise _invalid(
                "zip_entry_limit_exceeded",
                "Office 压缩包条目数超过限制",
                entry_count=len(infos),
                max_zip_entries=settings.max_zip_entries,
            )

        info_by_name: dict[str, zipfile.ZipInfo] = {}
        seen_casefold: set[str] = set()
        total_uncompressed = 0
        for info in infos:
            deadline.check()
            name = _safe_member_name(info.filename)
            casefolded = name.casefold()
            if casefolded in seen_casefold:
                raise _invalid("ooxml_duplicate_part", "Office 文件包含重复部件")
            seen_casefold.add(casefolded)
            unix_mode = (info.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise _invalid("ooxml_unsafe_link", "Office 压缩包包含符号链接")
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise _invalid("encrypted_office_unsupported", "暂不支持带密码的 Office 文件")
            if info.compress_type not in _ALLOWED_COMPRESSIONS:
                raise _invalid("ooxml_unsupported_compression", "Office 文件使用了不支持的压缩算法")
            if info.file_size > settings.max_zip_entry_uncompressed_bytes:
                raise _invalid(
                    "zip_entry_size_limit_exceeded",
                    "Office 压缩包单项解压大小超过限制",
                    max_bytes=settings.max_zip_entry_uncompressed_bytes,
                )
            total_uncompressed += info.file_size
            if total_uncompressed > settings.max_zip_total_uncompressed_bytes:
                raise _invalid(
                    "zip_total_size_limit_exceeded",
                    "Office 压缩包总解压大小超过限制",
                    max_bytes=settings.max_zip_total_uncompressed_bytes,
                )
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > settings.max_zip_compression_ratio:
                raise _invalid(
                    "zip_compression_ratio_exceeded",
                    "Office 压缩包压缩比超过安全限制",
                    max_ratio=settings.max_zip_compression_ratio,
                )
            lowered_name = f"/{name.lower()}"
            if Path(name).suffix.lower() in _ARCHIVE_EXTENSIONS or any(
                marker in lowered_name for marker in _FORBIDDEN_PART_MARKERS
            ):
                raise _invalid("ooxml_nested_or_active_content", "Office 文件包含嵌套压缩包或活动内容")
            info_by_name[name] = info

        member_names = set(info_by_name)
        required_main, required_content_type = _MAIN_PARTS[ext]
        required = {"[Content_Types].xml", "_rels/.rels"}
        missing = sorted(required.difference(member_names))
        if missing:
            raise _invalid("invalid_ooxml_structure", "Office 文件缺少必要的 OOXML 部件", missing=missing)

        xml_roots: dict[str, ET.Element] = {}
        for name, info in info_by_name.items():
            deadline.check()
            is_xml = name.lower().endswith((".xml", ".rels")) or name == "[Content_Types].xml"
            if is_xml:
                if info.file_size > settings.max_office_xml_bytes:
                    raise _invalid(
                        "office_xml_size_limit_exceeded",
                        "Office XML 部件超过大小限制",
                        max_bytes=settings.max_office_xml_bytes,
                    )
                xml_roots[name] = _parse_xml(_read_member(archive, info, deadline), part_name=name)
            else:
                prefix = b""
                try:
                    with archive.open(info, "r") as handle:
                        prefix = handle.read(8)
                except RuntimeError as exc:
                    raise _invalid("encrypted_office_unsupported", "暂不支持带密码的 Office 文件") from exc
                if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
                    raise _invalid("ooxml_nested_archive", "Office 文件包含嵌套压缩包")

        content_types = xml_roots["[Content_Types].xml"]
        overrides: dict[str, str] = {}
        for item in content_types.iter():
            content_type = str(item.attrib.get("ContentType") or "").strip()
            if any(marker in content_type.lower() for marker in _FORBIDDEN_CONTENT_TYPE_MARKERS):
                raise _invalid("ooxml_active_content", "Office 文件包含宏、嵌入对象或活动内容")
            part_name = str(item.attrib.get("PartName") or "").lstrip("/")
            if part_name:
                overrides[part_name] = content_type
        if overrides.get(required_main) != required_content_type:
            raise _invalid(
                "ooxml_type_mismatch",
                "Office 文件内部类型与扩展名不匹配",
                extension=ext,
            )
        if required_main not in member_names:
            raise _invalid(
                "invalid_ooxml_structure",
                "Office 文件缺少必要的 OOXML 主文档部件",
                missing=[required_main],
            )

        relationships: dict[str, dict[str, str]] = {}
        relationship_count = 0
        for name, root in xml_roots.items():
            if not name.lower().endswith(".rels"):
                continue
            mapped, count = _relationship_map(root, rel_name=name, member_names=member_names)
            relationships[name] = mapped
            relationship_count += count
            if relationship_count > settings.max_ooxml_relationships:
                raise _invalid(
                    "ooxml_relationship_limit_exceeded",
                    "Office 文件关系数量超过限制",
                    max_relationships=settings.max_ooxml_relationships,
                )

        if required_main not in relationships.get("_rels/.rels", {}).values():
            raise _invalid("invalid_ooxml_root_relationship", "Office 文件根关系未指向正确的主文档")

        resource_count = sum(
            1
            for name in member_names
            if name.startswith(("word/media/", "ppt/media/", "xl/media/"))
        )
        if ext == ".docx" and resource_count > settings.max_docx_resources:
            raise _invalid(
                "docx_resource_limit_exceeded",
                "DOCX 资源数量超过限制",
                resource_count=resource_count,
                max_docx_resources=settings.max_docx_resources,
            )

        if ext == ".pptx":
            units = _slide_units(xml_roots, relationships, member_names)
            unit_type = "slide"
            if not units:
                raise _invalid("invalid_pptx", "PPTX 不包含可解析幻灯片")
            if len(units) > settings.max_pptx_slides:
                raise _invalid(
                    "pptx_slide_limit_exceeded",
                    "PPTX 幻灯片数量超过限制",
                    slide_count=len(units),
                    max_pptx_slides=settings.max_pptx_slides,
                )
        elif ext == ".xlsx":
            units = _sheet_units(xml_roots, relationships)
            unit_type = "sheet"
            if not units:
                raise _invalid("invalid_xlsx", "XLSX 不包含可解析工作表")
            if len(units) > settings.max_xlsx_sheets:
                raise _invalid(
                    "xlsx_sheet_limit_exceeded",
                    "XLSX 工作表数量超过限制",
                    sheet_count=len(units),
                    max_xlsx_sheets=settings.max_xlsx_sheets,
                )
        else:
            units = _document_units(xml_roots)
            unit_type = "document"

        deadline.check()
        return OOXMLInspection(
            extension=ext,
            unit_type=unit_type,
            units=units,
            entry_count=len(infos),
            total_uncompressed_bytes=total_uncompressed,
            relationship_count=relationship_count,
            resource_count=resource_count,
        )


__all__ = ["OOXMLInspection", "OfficeSourceUnit", "inspect_ooxml"]
