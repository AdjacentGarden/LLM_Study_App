"""Assemble OpenAI-compatible streaming Chat Completions chunks.

The module is deliberately pure: it performs no I/O, keeps no global mutable
state, and only depends on the standard library.  A stream is consumed from an
iterable of decoded JSON chunks (or raw SSE lines) and validated into a single
assistant turn.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from .prompt_cache import cached_input_tokens


class ToolStreamError(ValueError):
    """The stream is malformed or carries invalid tool call arguments."""


class ToolStreamIncomplete(ToolStreamError):
    """The stream ended without a usable finish_reason (or was truncated)."""


class ToolStreamOutputLimit(ToolStreamIncomplete):
    """The provider stopped because the output token budget ran out."""


class ToolStreamRefused(ToolStreamError):
    """The provider refused or content-filtered the response."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass(frozen=True, slots=True)
class AssembledTurn:
    content: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    usage: dict[str, int]
    reasoning_chars: int


MAX_TOOL_CALLS = 4
MAX_ARGUMENT_CHARS = 32000

_TOOL_CALL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "description",
        "title",
        "properties",
        "required",
        "additionalProperties",
        "minLength",
        "maxLength",
        "enum",
        "minimum",
        "maximum",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)

_TYPE_KEYWORDS: dict[str, frozenset[str]] = {
    "object": frozenset({"properties", "required", "additionalProperties"}),
    "string": frozenset({"minLength", "maxLength", "enum"}),
    "integer": frozenset({"minimum", "maximum"}),
    "boolean": frozenset(),
    "array": frozenset({"items", "minItems", "maxItems", "uniqueItems"}),
}


@dataclass(slots=True)
class _ToolFragment:
    id: str | None = None
    name: str | None = None
    arguments: list[str] = field(default_factory=list)


def iter_sse_data(lines: Iterable[str | bytes]) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from Server-Sent Events ``data:`` lines.

    Blank lines, comments (``:``), and ``event:``/``id:``/``retry:`` fields are
    skipped.  ``data: [DONE]`` terminates iteration; any subsequent input is
    ignored.  A ``data:`` line that does not decode to a JSON object raises
    :class:`ToolStreamError` rather than being skipped silently.
    """

    for raw in lines:
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ToolStreamError("SSE line is not valid UTF-8") from exc
        elif isinstance(raw, str):
            text = raw
        else:
            raise ToolStreamError(
                f"SSE line must be str or bytes, got {type(raw).__name__}"
            )

        for line in text.split("\n"):
            line = line.rstrip("\r")
            if not line or line.startswith(":"):
                continue
            if line.startswith(("event:", "id:", "retry:")):
                continue
            if not line.startswith("data:"):
                # Unknown SSE fields are ignored per the SSE specification.
                continue

            payload = line[len("data:") :]
            if payload.startswith(" "):
                payload = payload[1:]
            if payload.strip() == "[DONE]":
                return

            try:
                decoded = json.loads(payload)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ToolStreamError(
                    f"invalid JSON in SSE data line: {payload!r}"
                ) from exc
            if not isinstance(decoded, dict):
                raise ToolStreamError("SSE data payload must decode to a JSON object")
            yield decoded


def assemble_chat_stream(
    chunks: Iterable[Mapping[str, Any]],
    tools: Mapping[str, Mapping[str, Any]],
) -> AssembledTurn:
    """Assemble and validate one assistant turn from streaming chunks."""

    content_parts: list[str] = []
    reasoning_chars = 0
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    fragments: dict[int, _ToolFragment] = {}

    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise ToolStreamError("stream chunk must be a mapping")

        raw_usage = chunk.get("usage")
        if raw_usage is not None:
            usage = _parse_usage(raw_usage)

        choices = chunk.get("choices")
        if choices is None:
            choices = []
        if not isinstance(choices, list):
            raise ToolStreamError("chunk 'choices' must be a list")

        for choice in choices:
            if not isinstance(choice, Mapping):
                raise ToolStreamError("choice must be a mapping")

            choice_index = choice.get("index", 0)
            if not isinstance(choice_index, int) or isinstance(choice_index, bool):
                raise ToolStreamError("choice index must be an integer")
            if choice_index != 0:
                raise ToolStreamError(
                    f"unexpected choice index {choice_index}; only index 0 is supported"
                )

            delta = choice.get("delta")
            if delta is None:
                delta = {}
            if not isinstance(delta, Mapping):
                raise ToolStreamError("choice delta must be a mapping")

            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ToolStreamError("delta.content must be a string")
                content_parts.append(content)

            reasoning = delta.get("reasoning_content")
            if reasoning is not None:
                if not isinstance(reasoning, str):
                    raise ToolStreamError("delta.reasoning_content must be a string")
                reasoning_chars += len(reasoning)

            refusal = delta.get("refusal")
            if refusal is not None:
                if not isinstance(refusal, str):
                    raise ToolStreamError("delta.refusal must be a string")
                if refusal:
                    raise ToolStreamRefused("assistant refused the request")

            raw_calls = delta.get("tool_calls")
            if raw_calls is not None:
                if not isinstance(raw_calls, list):
                    raise ToolStreamError("delta.tool_calls must be a list")
                for raw_call in raw_calls:
                    _accumulate_tool_call(raw_call, fragments)

            raw_finish = choice.get("finish_reason")
            if raw_finish is not None:
                if not isinstance(raw_finish, str):
                    raise ToolStreamError("finish_reason must be a string")
                finish_reason = raw_finish

    if finish_reason is None:
        raise ToolStreamIncomplete("stream ended without finish_reason")
    if finish_reason == "length":
        raise ToolStreamOutputLimit("stream truncated: finish_reason is 'length'")
    if finish_reason == "content_filter":
        raise ToolStreamRefused("stream refused: finish_reason is 'content_filter'")
    if finish_reason not in {"stop", "tool_calls"}:
        raise ToolStreamError(f"unsupported finish_reason {finish_reason!r}")

    indexes = sorted(fragments)
    if len(indexes) > MAX_TOOL_CALLS:
        raise ToolStreamError(
            f"too many tool calls: {len(indexes)} exceeds {MAX_TOOL_CALLS}"
        )
    if indexes != list(range(len(indexes))):
        raise ToolStreamError(
            "tool call indexes must be contiguous starting at 0"
        )

    tool_calls: list[ToolCall] = []
    seen_ids: set[str] = set()
    for index in indexes:
        fragment = fragments[index]

        call_id = fragment.id
        if not call_id:
            raise ToolStreamError(f"tool call {index} is missing an id")
        if _TOOL_CALL_ID.fullmatch(call_id) is None:
            raise ToolStreamError(f"tool call {index} has an invalid id {call_id!r}")
        if call_id in seen_ids:
            raise ToolStreamError(f"duplicate tool call id {call_id!r}")
        seen_ids.add(call_id)

        name = fragment.name
        if not name:
            raise ToolStreamError(f"tool call {index} is missing a function name")
        if name not in tools:
            raise ToolStreamError(f"unknown tool {name!r}")

        raw_arguments = "".join(fragment.arguments)
        if len(raw_arguments) > MAX_ARGUMENT_CHARS:
            raise ToolStreamError(
                f"tool call {index} arguments exceed {MAX_ARGUMENT_CHARS} characters"
            )

        payload = raw_arguments if raw_arguments != "" else "{}"
        try:
            parsed = json.loads(payload, strict=False)
        except json.JSONDecodeError as exc:
            raise ToolStreamError(
                f"tool call {index} arguments are not valid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise ToolStreamError(
                f"tool call {index} arguments must decode to a JSON object"
            )

        normalized = normalize_model_arguments(tools[name], parsed)
        validate_arguments(tools[name], normalized)
        try:
            strict_value = json.loads(payload)
        except json.JSONDecodeError:
            strict_value = None
        if normalized != parsed or strict_value is None:
            raw_arguments = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        parsed = normalized
        tool_calls.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=parsed,
                raw_arguments=raw_arguments,
            )
        )

    content = "".join(content_parts)
    if not tool_calls and not content.strip():
        raise ToolStreamError("empty assistant turn")

    return AssembledTurn(
        content=content,
        tool_calls=tuple(tool_calls),
        finish_reason=finish_reason,
        usage=usage if usage is not None else {
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0},
        reasoning_chars=reasoning_chars,
    )


def _parse_usage(raw_usage: Any) -> dict[str, int]:
    if not isinstance(raw_usage, Mapping):
        raise ToolStreamError("chunk 'usage' must be a mapping")
    return {
        "input_tokens": _usage_value(raw_usage, "prompt_tokens"),
        "output_tokens": _usage_value(raw_usage, "completion_tokens"),
        "cached_input_tokens": cached_input_tokens(raw_usage),
    }


def _usage_value(raw_usage: Mapping[str, Any], key: str) -> int:
    value = raw_usage.get(key)
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolStreamError(f"usage.{key} must be an integer")
    return value


def _accumulate_tool_call(
    raw_call: Any, fragments: dict[int, _ToolFragment]
) -> None:
    if not isinstance(raw_call, Mapping):
        raise ToolStreamError("tool call fragment must be a mapping")

    index = raw_call.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise ToolStreamError("tool call index must be an integer")
    if index < 0:
        raise ToolStreamError("tool call index must be >= 0")

    call_type = raw_call.get("type")
    if call_type is not None and call_type != "function":
        raise ToolStreamError(f"unsupported tool call type {call_type!r}")

    fragment = fragments.setdefault(index, _ToolFragment())

    call_id = raw_call.get("id")
    if call_id is not None:
        if not isinstance(call_id, str):
            raise ToolStreamError("tool call id must be a string")
        if not call_id:
            pass  # Some providers repeat empty metadata between argument deltas.
        elif fragment.id is None:
            fragment.id = call_id
        elif fragment.id != call_id:
            raise ToolStreamError(f"conflicting tool call id at index {index}")

    function = raw_call.get("function")
    if function is None:
        return
    if not isinstance(function, Mapping):
        raise ToolStreamError("tool call function must be a mapping")

    name = function.get("name")
    if name is not None:
        if not isinstance(name, str):
            raise ToolStreamError("tool call function name must be a string")
        if not name:
            pass
        elif fragment.name is None:
            fragment.name = name
        elif fragment.name != name:
            fragment.name = name if name.startswith(fragment.name) else fragment.name + name
        if fragment.name is not None and len(fragment.name) > 128:
            raise ToolStreamError("tool call function name is too long")

    arguments = function.get("arguments")
    if arguments is not None:
        if not isinstance(arguments, str):
            raise ToolStreamError("tool call arguments fragment must be a string")
        fragment.arguments.append(arguments)


def normalize_model_arguments(schema: Mapping[str, Any], value: Any) -> Any:
    """Recover harmless model wire variations before the unchanged validator.

    Unknown fields have no tool behavior. Optional nulls mean omission, and an
    integer spelled as a numeric string has the same value. Required fields,
    IDs, allowed tools, ranges and actual execution ownership still validate.
    """
    kind = schema.get("type")
    if kind == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return value
        required = schema.get("required", [])
        return {key: normalize_model_arguments(properties[key], item)
                for key, item in value.items() if key in properties
                and not (item is None and key not in required)}
    if kind == "array" and isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        return [normalize_model_arguments(schema["items"], item) for item in value]
    if kind == "integer" and isinstance(value, str) and len(value) <= 32 and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value)
    return value


def validate_arguments(
    schema: Mapping[str, Any], value: Any, path: str = "$"
) -> None:
    """Validate ``value`` against the supported strict JSON-schema subset."""

    if not isinstance(schema, Mapping):
        raise ToolStreamError(f"{path}: schema must be a mapping")

    for keyword in schema:
        if keyword not in _SCHEMA_KEYWORDS:
            raise ToolStreamError("unsupported schema keyword")

    if "type" not in schema:
        raise ToolStreamError(f"{path}: schema is missing 'type'")
    schema_type = schema["type"]
    if not isinstance(schema_type, str) or schema_type not in _TYPE_KEYWORDS:
        raise ToolStreamError(f"{path}: unsupported schema type {schema_type!r}")

    allowed = _TYPE_KEYWORDS[schema_type]
    for keyword in schema:
        if keyword in {"type", "description", "title"}:
            continue
        if keyword not in allowed:
            raise ToolStreamError(
                f"{path}: schema keyword {keyword!r} is not supported "
                f"for type {schema_type!r}"
            )

    if schema_type == "object":
        _validate_object(schema, value, path)
    elif schema_type == "string":
        _validate_string(schema, value, path)
    elif schema_type == "integer":
        _validate_integer(schema, value, path)
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise ToolStreamError(f"{path}: expected boolean")
    else:
        _validate_array(schema, value, path)


def _validate_object(
    schema: Mapping[str, Any], value: Any, path: str
) -> None:
    if not isinstance(value, dict):
        raise ToolStreamError(f"{path}: expected object")

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ToolStreamError(f"{path}: 'properties' must be a mapping")
    for key, subschema in properties.items():
        if not isinstance(key, str):
            raise ToolStreamError(f"{path}: property names must be strings")
        if not isinstance(subschema, Mapping):
            raise ToolStreamError(f"{path}.{key}: property schema must be a mapping")

    additional = schema.get("additionalProperties", False)
    if additional is not False:
        raise ToolStreamError(f"{path}: unsupported 'additionalProperties' value")

    for key in value:
        if key not in properties:
            raise ToolStreamError(f"{path}.{key}: additional property is not allowed")

    required = schema.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(item, str) for item in required
    ):
        raise ToolStreamError(f"{path}: 'required' must be a list of strings")
    for name in required:
        if name not in value:
            raise ToolStreamError(f"{path}.{name}: required property is missing")

    for key, subschema in properties.items():
        if key in value:
            validate_arguments(subschema, value[key], f"{path}.{key}")


def _validate_string(
    schema: Mapping[str, Any], value: Any, path: str
) -> None:
    if not isinstance(value, str):
        raise ToolStreamError(f"{path}: expected string")

    if "minLength" in schema:
        minimum = _schema_int(schema["minLength"], path, "minLength")
        if len(value) < minimum:
            raise ToolStreamError(
                f"{path}: string is shorter than minLength {minimum}"
            )

    if "maxLength" in schema:
        maximum = _schema_int(schema["maxLength"], path, "maxLength")
        if len(value) > maximum:
            raise ToolStreamError(
                f"{path}: string is longer than maxLength {maximum}"
            )

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list):
            raise ToolStreamError(f"{path}: 'enum' must be a list")
        if value not in enum:
            raise ToolStreamError(f"{path}: value is not one of the allowed values")


def _validate_integer(
    schema: Mapping[str, Any], value: Any, path: str
) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolStreamError(f"{path}: expected integer")

    if "minimum" in schema:
        minimum = _schema_number(schema["minimum"], path, "minimum")
        if value < minimum:
            raise ToolStreamError(f"{path}: value is less than minimum {minimum}")

    if "maximum" in schema:
        maximum = _schema_number(schema["maximum"], path, "maximum")
        if value > maximum:
            raise ToolStreamError(f"{path}: value is greater than maximum {maximum}")


def _validate_array(
    schema: Mapping[str, Any], value: Any, path: str
) -> None:
    if not isinstance(value, list):
        raise ToolStreamError(f"{path}: expected array")

    if "items" in schema:
        items = schema["items"]
        if not isinstance(items, Mapping):
            raise ToolStreamError(f"{path}: 'items' must be a schema mapping")
        for position, item in enumerate(value):
            validate_arguments(items, item, f"{path}[{position}]")

    if "minItems" in schema:
        minimum = _schema_int(schema["minItems"], path, "minItems")
        if len(value) < minimum:
            raise ToolStreamError(f"{path}: array has fewer than minItems {minimum}")

    if "maxItems" in schema:
        maximum = _schema_int(schema["maxItems"], path, "maxItems")
        if len(value) > maximum:
            raise ToolStreamError(f"{path}: array has more than maxItems {maximum}")

    unique = schema.get("uniqueItems", False)
    if not isinstance(unique, bool):
        raise ToolStreamError(f"{path}: 'uniqueItems' must be a boolean")
    if unique:
        for left in range(len(value)):
            for right in range(left + 1, len(value)):
                if value[left] == value[right]:
                    raise ToolStreamError(
                        f"{path}[{right}]: duplicate item violates uniqueItems"
                    )


def _schema_int(raw: Any, path: str, keyword: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ToolStreamError(
            f"{path}: schema keyword {keyword!r} must be an integer"
        )
    return raw


def _schema_number(raw: Any, path: str, keyword: str) -> int | float:
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise ToolStreamError(
            f"{path}: schema keyword {keyword!r} must be a number"
        )
    return raw
