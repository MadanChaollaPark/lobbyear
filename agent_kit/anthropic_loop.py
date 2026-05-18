from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


ToolHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]
EmitHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class AgentTraceEvent:
    kind: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolLoopResult:
    finished: bool
    finish_reason: str
    messages: list[dict[str, Any]]
    trace: list[AgentTraceEvent]


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {key: jsonable(item) for key, item in vars(value).items() if not key.startswith("_")}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def compact_json(value: Any, limit: int = 12_000) -> str:
    text = json.dumps(jsonable(value), ensure_ascii=True, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _block_to_dict(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json")
    if isinstance(block, dict):
        return block
    data = {"type": getattr(block, "type", None)}
    for key in ("id", "name", "input", "text"):
        if hasattr(block, key):
            data[key] = getattr(block, key)
    return data


async def run_tool_loop(
    *,
    system_prompt: str,
    initial_user: str,
    tools: list[AgentTool],
    finish_tool_names: set[str],
    model: str | None = None,
    max_turns: int = 10,
    max_tokens: int = 1800,
    temperature: float = 0.3,
    emit: EmitHandler | None = None,
) -> ToolLoopResult:
    """Run an Anthropic tool-use loop with system prompt caching enabled."""

    try:
        from anthropic import Anthropic
    except Exception as exc:  # pragma: no cover - depends on local env
        raise RuntimeError("Install the official Anthropic SDK: pip install anthropic") from exc

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required for the agent loop")

    client = Anthropic()
    selected_model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    tool_map = {tool.name: tool for tool in tools}
    anthropic_tools = [tool.anthropic_schema() for tool in tools]
    messages: list[dict[str, Any]] = [{"role": "user", "content": initial_user}]
    trace: list[AgentTraceEvent] = []
    finished = False
    finish_reason = "max_turns"

    for turn in range(max_turns):
        response = await asyncio.to_thread(
            client.messages.create,
            model=selected_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=anthropic_tools,
            messages=messages,
        )
        assistant_blocks = [_block_to_dict(block) for block in response.content]
        messages.append({"role": "assistant", "content": assistant_blocks})

        tool_results: list[dict[str, Any]] = []
        saw_tool = False
        for block in assistant_blocks:
            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    event = AgentTraceEvent("text", "reasoning", {"turn": turn + 1, "text": text})
                    trace.append(event)
                    if emit:
                        await maybe_await(emit({"kind": "text", "name": "reasoning", "payload": event.payload}))
                continue
            if block_type != "tool_use":
                continue

            saw_tool = True
            tool_name = str(block.get("name"))
            tool_input = block.get("input") or {}
            event = AgentTraceEvent("tool_use", tool_name, {"turn": turn + 1, "input": tool_input})
            trace.append(event)
            if emit:
                await maybe_await(emit({"kind": "tool_use", "name": tool_name, "payload": event.payload}))

            tool = tool_map.get(tool_name)
            if tool is None:
                result: dict[str, Any] = {"error": f"Unknown tool: {tool_name}"}
                is_error = True
            else:
                try:
                    if inspect.iscoroutinefunction(tool.handler):
                        handler_result = await tool.handler(dict(tool_input))
                    else:
                        handler_result = await asyncio.to_thread(tool.handler, dict(tool_input))
                    result = jsonable(await maybe_await(handler_result))
                    is_error = False
                except Exception as exc:  # noqa: BLE001 - tool errors must return to model
                    result = {"error": str(exc)}
                    is_error = True

            result_event = AgentTraceEvent(
                "tool_result",
                tool_name,
                {"turn": turn + 1, "result": result, "is_error": is_error},
            )
            trace.append(result_event)
            if emit:
                await maybe_await(
                    emit({"kind": "tool_result", "name": tool_name, "payload": result_event.payload})
                )

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.get("id"),
                    "content": compact_json(result),
                    "is_error": is_error,
                }
            )

            if tool_name in finish_tool_names and not is_error:
                finished = True
                finish_reason = tool_name

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        if finished:
            break
        if not saw_tool:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Continue the agent loop. Use tools for the next concrete action; "
                        "call a finish/finalize tool only when the goal condition is met."
                    ),
                }
            )

    return ToolLoopResult(finished=finished, finish_reason=finish_reason, messages=messages, trace=trace)
