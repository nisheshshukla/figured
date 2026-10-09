"""Read a finished agent run from common transcript formats into a sequence of events."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

_INLINE_CALLS = re.compile(r"<function_calls>.*?</function_calls>", re.DOTALL)

Event = tuple[str, dict[str, Any]]


def events(messages: list[dict[str, Any]], system: str | None = None) -> Iterator[Event]:
    """Yield ("system"|"user"|"assistant"|"call"|"result", payload) in order.

    Accepts OpenAI chat messages (tool_calls on assistant messages, role "tool" results) and
    Anthropic messages (content blocks of type text, tool_use, and tool_result), including mixtures.
    """
    if system:
        yield "system", {"text": system}
    names: dict[str, str] = {}
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            yield "system", {"text": _text(content)}
        elif role == "tool":
            cid = m.get("tool_call_id")
            yield (
                "result",
                {"name": m.get("name") or names.get(cid or "", "tool"), "output": _text(content), "id": cid},
            )
        elif role == "user":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        cid = b.get("tool_use_id")
                        yield (
                            "result",
                            {
                                "name": names.get(cid or "", "tool"),
                                "output": _text(b.get("content")),
                                "id": cid,
                            },
                        )
                text = _text(
                    [b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_result")]
                )
                if text:
                    yield "user", {"text": text}
            else:
                yield "user", {"text": _text(content)}
        elif role == "assistant":
            calls: list[dict[str, Any]] = []
            if isinstance(content, list):
                text = _text([b for b in content if isinstance(b, dict) and b.get("type") == "text"])
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        calls.append({"name": b.get("name"), "args": b.get("input") or {}, "id": b.get("id")})
            else:
                text = _text(content)
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", tc)
                args = fn.get("arguments", fn.get("input", {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except ValueError:
                        args = {"_raw": args}
                calls.append({"name": fn.get("name"), "args": args, "id": tc.get("id")})
            text = _INLINE_CALLS.sub("", text).strip()
            if text:
                yield "assistant", {"text": text}
            for c in calls:
                if c["id"]:
                    names[str(c["id"])] = str(c["name"])
                yield "call", c


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                if "text" in b:
                    parts.append(str(b["text"]))
                elif b.get("type") == "tool_result":
                    parts.append(_text(b.get("content")))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, default=str)
    return str(content)
