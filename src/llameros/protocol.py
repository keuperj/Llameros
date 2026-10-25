"""Cheap structural admission checks; no schema compilation or media decoding."""

import math

from fastapi import HTTPException


def validate_protocol(body):
    """Check nested message/tool envelopes and finite runtime sampling ranges once."""

    def require(ok, field):
        if not ok:
            raise HTTPException(400, f"Invalid protocol field: {field}")

    ranges = {
        "temperature": (0, 2),
        "top_p": (0, 1),
        "min_p": (0, 1),
        "presence_penalty": (-2, 2),
        "frequency_penalty": (-2, 2),
        "repeat_penalty": (0, 100),
        "top_k": (0, 100000),
        "seed": (-1, 2**63 - 1),
        "num_keep": (-1, 1048576),
        "repeat_last_n": (-1, 1048576),
        "top_logprobs": (0, 20),
    }
    integers = {"top_k", "seed", "num_keep", "repeat_last_n", "top_logprobs"}
    options = body.get("options", {})
    require(isinstance(options, dict), "options")
    for values in (body, options):
        for field, (low, high) in ranges.items():
            if field in values:
                value = values[field]
                require(
                    type(value) in ((int,) if field in integers else (int, float))
                    and low <= value <= high
                    and math.isfinite(value),
                    field,
                )
        for field in ("stop", "stop_sequences"):
            if field in values:
                value = values[field]
                require(
                    isinstance(value, str)
                    or (
                        isinstance(value, list)
                        and len(value) <= 64
                        and all(isinstance(s, str) for s in value)
                    ),
                    field,
                )
    if "stream_options" in body:
        value = body["stream_options"]
        require(isinstance(value, dict), "stream_options")
        require(type(value.get("include_usage", False)) is bool, "include_usage")
    for field in ("messages",):
        if field not in body:
            continue
        messages = body[field]
        require(isinstance(messages, list), field)
        for message in messages:
            require(isinstance(message, dict), "message")
            require(
                message.get("role") in ("system", "developer", "user", "assistant", "tool"), "role"
            )
            content = message.get("content")
            require(
                isinstance(content, (str, list))
                or (content is None and isinstance(message.get("tool_calls"), list)),
                "content",
            )
            if isinstance(content, list):
                for block in content:
                    require(
                        isinstance(block, dict) and isinstance(block.get("type"), str),
                        "content block",
                    )
                    if block["type"] in ("text", "input_text", "output_text"):
                        require(isinstance(block.get("text"), str), "text")
            calls = message.get("tool_calls", [])
            require(isinstance(calls, list), "tool_calls")
            for call in calls:
                require(
                    isinstance(call, dict) and isinstance(call.get("function"), dict), "tool call"
                )
                function = call["function"]
                require(
                    isinstance(function.get("name"), str)
                    and isinstance(function.get("arguments"), (str, dict)),
                    "tool function",
                )
    tools = body.get("tools", [])
    require(isinstance(tools, list), "tools")
    for tool in tools:
        require(isinstance(tool, dict), "tool")
        function = tool.get("function", tool)
        require(
            isinstance(function, dict)
            and isinstance(function.get("name"), str)
            and 1 <= len(function["name"]) <= 128,
            "function name",
        )
        schema = function.get("parameters", function.get("input_schema", {}))
        require(isinstance(schema, dict), "function schema")
    if isinstance(body.get("input"), list):
        for item in body["input"]:
            require(isinstance(item, (str, dict)), "input item")
            if isinstance(item, dict):
                if "content" in item:
                    require(isinstance(item["content"], (str, list)), "input content")
                if "role" in item:
                    require(
                        item["role"] in ("system", "developer", "user", "assistant", "tool"),
                        "input role",
                    )
    if "logit_bias" in body:
        bias = body["logit_bias"]
        require(isinstance(bias, dict), "logit_bias")
        for token, value in bias.items():
            require(
                token.isdecimal() and type(value) in (int, float) and -100 <= value <= 100,
                "logit_bias entry",
            )
    for field in ("response_format", "reasoning", "text", "thinking", "metadata", "output_config"):
        if field in body:
            require(isinstance(body[field], dict), field)
