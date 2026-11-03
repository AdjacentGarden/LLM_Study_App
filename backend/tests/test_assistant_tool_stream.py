from __future__ import annotations

import json

import pytest

from adaptive_learning.llm.tool_stream import (
    MAX_ARGUMENT_CHARS,
    MAX_TOOL_CALLS,
    AssembledTurn,
    ToolCall,
    ToolStreamError,
    ToolStreamIncomplete,
    ToolStreamRefused,
    assemble_chat_stream,
    iter_sse_data,
    validate_arguments,
)

LOOKUP_SCHEMA = {
    "type": "object",
    "properties": {
        "q": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": ["q"],
}

TOOLS = {"lookup": LOOKUP_SCHEMA}


def _delta_chunk(
    delta: dict,
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> dict:
    chunk: dict = {
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish_reason}
        ]
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def _finish_chunk(reason: str, usage: dict | None = None) -> dict:
    return _delta_chunk({}, finish_reason=reason, usage=usage)


def _fragment(
    index: int,
    *,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
    call_type: str | None = None,
) -> dict:
    fragment: dict = {"index": index}
    if call_type is not None:
        fragment["type"] = call_type
    if call_id is not None:
        fragment["id"] = call_id
    function: dict = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    if function:
        fragment["function"] = function
    return fragment


def _single_call_chunks(
    arguments: str, name: str = "lookup", finish_reason: str = "tool_calls"
) -> list[dict]:
    return [
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(
                        0,
                        call_id="call_1",
                        name=name,
                        arguments=arguments,
                        call_type="function",
                    )
                ]
            }
        ),
        _finish_chunk(finish_reason),
    ]


def test_content_only_turn_with_usage() -> None:
    chunks = [
        _delta_chunk({"role": "assistant", "content": ""}),
        _delta_chunk({"content": "Hel"}),
        _delta_chunk({"content": "lo"}),
        _finish_chunk("stop"),
        {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 2}},
    ]

    turn = assemble_chat_stream(chunks, TOOLS)

    assert isinstance(turn, AssembledTurn)
    assert turn.content == "Hello"
    assert turn.tool_calls == ()
    assert turn.finish_reason == "stop"
    assert turn.usage == {"input_tokens": 11, "output_tokens": 2, "cached_input_tokens": 0}
    assert turn.reasoning_chars == 0


def test_usage_last_non_null_wins_and_defaults_to_zero() -> None:
    chunks = [
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        _delta_chunk({"content": "x"}),
        {"choices": [], "usage": None},
        _finish_chunk("stop"),
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
    ]

    turn = assemble_chat_stream(chunks, TOOLS)
    assert turn.usage == {"input_tokens": 7, "output_tokens": 3, "cached_input_tokens": 0}

    no_usage = assemble_chat_stream(
        [_delta_chunk({"content": "x"}), _finish_chunk("stop")], TOOLS
    )
    assert no_usage.usage == {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}


def test_fragmented_tool_arguments_accumulate_in_order() -> None:
    chunks = [
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(
                        0,
                        call_id="call_abc",
                        name="lookup",
                        arguments='{"q":',
                        call_type="function",
                    )
                ]
            }
        ),
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(
                        0,
                        call_id="call_abc",
                        name="lookup",
                        arguments='"pyth',
                    )
                ]
            }
        ),
        _delta_chunk(
            {"tool_calls": [_fragment(0, arguments='on", "limit": ')]}
        ),
        _delta_chunk({"tool_calls": [_fragment(0, arguments="3}")]}),
        _finish_chunk("tool_calls"),
    ]

    turn = assemble_chat_stream(chunks, TOOLS)

    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert isinstance(call, ToolCall)
    assert call.id == "call_abc"
    assert call.name == "lookup"
    assert call.arguments == {"q": "python", "limit": 3}
    assert call.raw_arguments == '{"q":"python", "limit": 3}'


def test_parallel_tool_calls_interleaved_by_index() -> None:
    tools = {
        "lookup": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "limit": {"type": "integer"},
            },
        }
    }
    chunks = [
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(
                        0,
                        call_id="call_a",
                        name="lookup",
                        arguments='{"q":',
                        call_type="function",
                    ),
                    _fragment(
                        1,
                        call_id="call_b",
                        name="lookup",
                        arguments='{"limit":',
                        call_type="function",
                    ),
                ]
            }
        ),
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(1, arguments="5}"),
                    _fragment(0, arguments='"x"}'),
                ]
            }
        ),
        _finish_chunk("tool_calls"),
    ]

    turn = assemble_chat_stream(chunks, tools)

    assert [call.id for call in turn.tool_calls] == ["call_a", "call_b"]
    assert turn.tool_calls[0].arguments == {"q": "x"}
    assert turn.tool_calls[1].arguments == {"limit": 5}


def test_tool_calls_accepted_with_stop_finish_reason() -> None:
    turn = assemble_chat_stream(
        _single_call_chunks('{"q": "x"}', finish_reason="stop"), TOOLS
    )
    assert turn.finish_reason == "stop"
    assert turn.tool_calls[0].arguments == {"q": "x"}


def test_empty_arguments_treated_as_empty_object() -> None:
    tools = {"ping": {"type": "object", "properties": {}}}
    chunks = [
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(
                        0,
                        call_id="call_ping",
                        name="ping",
                        call_type="function",
                    )
                ]
            }
        ),
        _finish_chunk("tool_calls"),
    ]

    turn = assemble_chat_stream(chunks, tools)

    assert turn.tool_calls[0].arguments == {}
    assert turn.tool_calls[0].raw_arguments == ""


def test_reasoning_content_discarded_but_counted() -> None:
    chunks = [
        _delta_chunk({"reasoning_content": "think"}),
        _delta_chunk({"reasoning_content": "ing", "content": "Answer"}),
        _finish_chunk("stop"),
    ]

    turn = assemble_chat_stream(chunks, TOOLS)

    assert turn.content == "Answer"
    assert turn.reasoning_chars == len("thinking")


def test_missing_finish_reason_is_incomplete() -> None:
    with pytest.raises(ToolStreamIncomplete):
        assemble_chat_stream([_delta_chunk({"content": "hi"})], TOOLS)


def test_length_finish_reason_is_incomplete() -> None:
    chunks = [_delta_chunk({"content": "hi"}), _finish_chunk("length")]

    with pytest.raises(ToolStreamIncomplete):
        assemble_chat_stream(chunks, TOOLS)


def test_content_filter_finish_reason_is_refused() -> None:
    chunks = [_delta_chunk({"content": "hi"}), _finish_chunk("content_filter")]

    with pytest.raises(ToolStreamRefused):
        assemble_chat_stream(chunks, TOOLS)


def test_refusal_delta_is_refused() -> None:
    chunks = [_delta_chunk({"refusal": "I cannot help with that"})]

    with pytest.raises(ToolStreamRefused):
        assemble_chat_stream(chunks, TOOLS)


def test_unknown_tool_is_rejected() -> None:
    chunks = _single_call_chunks('{"q": "x"}', name="search")

    with pytest.raises(ToolStreamError, match="unknown tool"):
        assemble_chat_stream(chunks, TOOLS)


def test_invalid_json_arguments_are_rejected() -> None:
    chunks = _single_call_chunks('{"q": ')

    with pytest.raises(ToolStreamError, match="not valid JSON"):
        assemble_chat_stream(chunks, TOOLS)


def test_non_object_arguments_are_rejected() -> None:
    chunks = _single_call_chunks('["q"]')

    with pytest.raises(ToolStreamError, match="JSON object"):
        assemble_chat_stream(chunks, TOOLS)


@pytest.mark.parametrize(
    ("schema", "arguments", "message"),
    [
        (
            {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            {},
            "required property",
        ),
        (
            {"type": "object", "properties": {"q": {"type": "string"}}},
            {"q": "x", "extra": 1},
            "additional property",
        ),
        (
            {
                "type": "object",
                "properties": {"q": {"type": "string", "maxLength": 2}},
            },
            {"q": "abc"},
            "maxLength",
        ),
        (
            {"type": "object", "properties": {"n": {"type": "integer"}}},
            {"n": "1"},
            "expected integer",
        ),
        (
            {"type": "object", "properties": {"n": {"type": "integer"}}},
            {"n": True},
            "expected integer",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "xs": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "maxItems": 2,
                    }
                },
            },
            {"xs": [1, 2, 3]},
            "maxItems",
        ),
    ],
)
def test_argument_schema_violations(
    schema: dict, arguments: dict, message: str
) -> None:
    chunks = _single_call_chunks(json.dumps(arguments))

    with pytest.raises(ToolStreamError, match=message):
        assemble_chat_stream(chunks, {"lookup": schema})


def test_schema_error_message_contains_path() -> None:
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }

    with pytest.raises(ToolStreamError) as excinfo:
        validate_arguments(schema, {})

    assert "$.q" in str(excinfo.value)


def test_conflicting_id_across_fragments_is_rejected() -> None:
    chunks = [
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(
                        0,
                        call_id="call_a",
                        name="lookup",
                        arguments='{"q":',
                        call_type="function",
                    )
                ]
            }
        ),
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(0, call_id="call_b", arguments='"x"}')
                ]
            }
        ),
        _finish_chunk("tool_calls"),
    ]

    with pytest.raises(ToolStreamError, match="conflicting tool call id"):
        assemble_chat_stream(chunks, TOOLS)


def test_non_contiguous_tool_call_indexes_are_rejected() -> None:
    chunks = [
        _delta_chunk(
            {
                "tool_calls": [
                    _fragment(
                        0,
                        call_id="call_a",
                        name="lookup",
                        arguments='{"q": "a"}',
                        call_type="function",
                    ),
                    _fragment(
                        2,
                        call_id="call_c",
                        name="lookup",
                        arguments='{"q": "c"}',
                        call_type="function",
                    ),
                ]
            }
        ),
        _finish_chunk("tool_calls"),
    ]

    with pytest.raises(ToolStreamError, match="contiguous"):
        assemble_chat_stream(chunks, TOOLS)


def test_too_many_tool_calls_are_rejected() -> None:
    tools = {"noop": {"type": "object", "properties": {}}}
    fragments = [
        _fragment(
            index,
            call_id=f"call_{index}",
            name="noop",
            arguments="{}",
            call_type="function",
        )
        for index in range(MAX_TOOL_CALLS + 1)
    ]

    with pytest.raises(ToolStreamError, match="too many tool calls"):
        assemble_chat_stream(
            [
                _delta_chunk({"tool_calls": fragments}),
                _finish_chunk("tool_calls"),
            ],
            tools,
        )


def test_duplicate_tool_call_ids_are_rejected() -> None:
    fragments = [
        _fragment(
            0,
            call_id="call_same",
            name="lookup",
            arguments='{"q": "a"}',
            call_type="function",
        ),
        _fragment(
            1,
            call_id="call_same",
            name="lookup",
            arguments='{"q": "b"}',
            call_type="function",
        ),
    ]

    with pytest.raises(ToolStreamError, match="duplicate tool call id"):
        assemble_chat_stream(
            [
                _delta_chunk({"tool_calls": fragments}),
                _finish_chunk("tool_calls"),
            ],
            TOOLS,
        )


def test_invalid_tool_call_id_is_rejected() -> None:
    chunks = _single_call_chunks('{"q": "x"}')
    chunks[0]["choices"][0]["delta"]["tool_calls"][0]["id"] = "call with space"

    with pytest.raises(ToolStreamError, match="invalid id"):
        assemble_chat_stream(chunks, TOOLS)


def test_overlong_raw_arguments_are_rejected() -> None:
    arguments = '{"q": "' + ("a" * MAX_ARGUMENT_CHARS) + '"}'

    with pytest.raises(ToolStreamError, match="exceed"):
        assemble_chat_stream(_single_call_chunks(arguments), TOOLS)


def test_empty_turn_is_rejected() -> None:
    chunks = [_delta_chunk({"content": "   "}), _finish_chunk("stop")]

    with pytest.raises(ToolStreamError, match="empty assistant turn"):
        assemble_chat_stream(chunks, TOOLS)


def test_iter_sse_data_handles_bytes_comments_and_control_lines() -> None:
    lines = [
        b": keep-alive\n",
        b"\n",
        b"event: message\n",
        b"id: 42\n",
        b"retry: 1000\n",
        'data: {"choices": []}\n',
        b'data:{"choices": [{"index": 0}]}\r\n',
        "data: [DONE]\n",
        'data: {"ignored": true}\n',
    ]

    assert list(iter_sse_data(lines)) == [
        {"choices": []},
        {"choices": [{"index": 0}]},
    ]


@pytest.mark.parametrize(
    "line",
    [
        "data: not-json\n",
        b"data: [1, 2]\n",
        b"data: null\n",
    ],
)
def test_iter_sse_data_rejects_invalid_payloads(line: str | bytes) -> None:
    with pytest.raises(ToolStreamError):
        list(iter_sse_data([line]))


def test_unsupported_schema_keyword_is_rejected() -> None:
    with pytest.raises(ToolStreamError, match="unsupported schema keyword"):
        validate_arguments({"type": "string", "pattern": "^a"}, "abc")


def test_supported_schema_keywords_validate_values() -> None:
    schema = {
        "type": "object",
        "title": "demo",
        "description": "supported metadata is ignored",
        "properties": {
            "name": {"type": "string", "minLength": 2, "enum": ["ab", "cd"]},
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
            "flag": {"type": "boolean"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
            },
        },
        "required": ["name", "score", "flag", "tags"],
        "additionalProperties": False,
    }
    value = {"name": "ab", "score": 5, "flag": False, "tags": ["x"]}

    validate_arguments(schema, value)

    for broken, expected in [
        ({**value, "name": "zz"}, "allowed values"),
        ({**value, "score": 0}, "minimum"),
        ({**value, "flag": 1}, "expected boolean"),
        ({**value, "tags": ["x", "x"]}, "uniqueItems"),
    ]:
        with pytest.raises(ToolStreamError, match=expected):
            validate_arguments(schema, broken)


def test_usage_reports_deepseek_prompt_cache_hits() -> None:
    turn = assemble_chat_stream(
        [
            _delta_chunk({"content": "ok"}),
            _finish_chunk("stop"),
            {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 4,
                                      "prompt_cache_hit_tokens": 768,
                                      "prompt_cache_miss_tokens": 132}},
        ],
        {},
    )
    assert turn.usage == {"input_tokens": 900, "output_tokens": 4, "cached_input_tokens": 768}
