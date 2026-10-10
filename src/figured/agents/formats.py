"""Read a finished agent run from common transcript formats into one sequence of events.

Supported: OpenAI Chat Completions (tool_calls, role "tool", legacy function_call and role
"function", role "developer"), OpenAI Responses API items (message, function_call,
function_call_output), Anthropic Messages (text, tool_use, and tool_result blocks), Gemini
(parts with functionCall and functionResponse), Amazon Bedrock Converse (toolUse and toolResult
blocks), LangChain messages (objects, dicts with type human/ai/system/tool, and the serialized
{"type", "data"} and {"lc": 1, "kwargs"} forms), and the shapes used by
the tau-bench, tau2-bench, and AgentDojo transcripts. Anything else raises UnrecognizedMessage by
default, so a transcript is never silently reported as clean because nothing in it was read.
"""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Iterator
from typing import Any, Literal

_INLINE_CALLS = re.compile(r"<function_calls>.*?</function_calls>", re.DOTALL)
OnUnknown = Literal["raise", "warn", "ignore"]
Event = tuple[str, dict[str, Any]]
_ROLES = {
    "system": "system",
    "developer": "system",
    "user": "user",
    "human": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "model": "assistant",
    "tool": "tool",
    "function": "tool",
}


class UnrecognizedMessage(ValueError):
    """A message whose shape figured does not know how to read."""


def events(
    messages: list[Any], system: str | None = None, on_unknown: OnUnknown = "raise"
) -> Iterator[Event]:
    """Yield ("system"|"user"|"assistant"|"call"|"result", payload) in order."""
    if system:
        yield "system", {"text": system}
    names: dict[str, str] = {}
    seen = 0
    for idx, raw in enumerate(messages):
        m = _unwrap(_as_dict(raw))
        produced = list(_one(m, names))
        known = bool(produced) or _is_empty(m)
        if not known:
            _unknown(idx, m, on_unknown)
        seen += known
        yield from (e for e in produced if e[0] != "skip")
    if messages and not seen:
        _unknown(-1, {"messages": len(messages)}, on_unknown, "no message in the transcript was recognized")


_LC_CLASSES = {
    "HumanMessage": "human",
    "AIMessage": "ai",
    "AIMessageChunk": "ai",
    "SystemMessage": "system",
    "ToolMessage": "tool",
    "FunctionMessage": "function",
}


def _unwrap(m: dict[str, Any]) -> dict[str, Any]:
    """LangChain's serialized messages: messages_to_dict gives {"type", "data"}, dumpd gives
    {"lc": 1, "type": "constructor", "id": [..., "HumanMessage"], "kwargs": {...}}."""
    if m.get("lc") and m.get("type") == "constructor" and isinstance(m.get("kwargs"), dict):
        ids = m.get("id") or []
        mtype = _LC_CLASSES.get(str(ids[-1]) if ids else "")
        return {**m["kwargs"], "type": mtype} if mtype else m
    if "role" not in m and isinstance(m.get("data"), dict) and m.get("type") in _ROLES:
        return {**m["data"], "type": m["type"]}
    return m


def _one(m: dict[str, Any], names: dict[str, str]) -> Iterator[Event]:
    mtype = m.get("type")
    if mtype == "function_call" and "role" not in m:
        cid = str(m.get("call_id") or m.get("id") or "")
        names[cid] = str(m.get("name") or "")
        yield "call", {"name": m.get("name"), "args": _args(m.get("arguments")), "id": cid or None}
        return
    if mtype == "function_call_output" and "role" not in m:
        cid = str(m.get("call_id") or "")
        yield "result", {"name": names.get(cid, "tool"), "output": _text(m.get("output")), "id": cid or None}
        return
    if mtype == "message" and "role" in m:
        m = {**m, "type": None}
    role = _ROLES.get(str(m.get("role") or mtype or ""))
    if role is None:
        return
    if (m.get("requestor") == "user" and role in ("tool", "assistant")) or (
        role == "user" and m.get("tool_calls")
    ):
        if role == "user" and m.get("content"):
            yield "user", {"text": _text(m.get("content"))}
        else:
            yield "skip", {}
        return
    content = m.get("content")
    if "parts" in m and content is None:
        content = m["parts"]
    if role == "system":
        text = _text(content)
        if text:
            yield "system", {"text": text}
    elif role == "tool":
        results = [_block_result(b, names) for b in content] if isinstance(content, list) else []
        if results and all(r is not None for r in results):
            for r in results:
                if r is not None:
                    yield "result", r
            return
        result_id = m.get("tool_call_id") or m.get("toolUseId") or m.get("id")
        name = str(m.get("name") or names.get(str(result_id or ""), "tool"))
        yield "result", {"name": name, "output": _text(content), "id": result_id}
    elif role == "user":
        user_blocks: list[Any] | None = content if isinstance(content, list) else None
        if user_blocks is not None:
            for b in user_blocks:
                res = _block_result(b, names)
                if res is not None:
                    yield "result", res
        kept = (
            [b for b in user_blocks if _block_result(b, {}) is None] if user_blocks is not None else content
        )
        text = _text(kept)
        if text:
            yield "user", {"text": text}
    else:
        calls: list[dict[str, Any]] = []
        ai_blocks: list[Any] | None = content if isinstance(content, list) else None
        if ai_blocks is not None:
            text = _text([b for b in ai_blocks if _block_call(b) is None and _block_result(b, {}) is None])
            for b in ai_blocks:
                call = _block_call(b)
                if call is not None:
                    calls.append(call)
        else:
            text = _text(content)
        for tc in m.get("tool_calls") or []:
            calls.append(_tool_call(tc))
        if isinstance(m.get("function_call"), dict):
            fc = m["function_call"]
            calls.append({"name": fc.get("name"), "args": _args(fc.get("arguments")), "id": None})
        text = _INLINE_CALLS.sub("", text).strip()
        if text:
            yield "assistant", {"text": text}
        for c in calls:
            if c["id"]:
                names[str(c["id"])] = str(c["name"])
            yield "call", c
        if not text and not calls and content not in (None, "", []):
            yield "assistant", {"text": ""}


def _tool_call(tc: Any) -> dict[str, Any]:
    tc = _as_dict(tc)
    fn = tc.get("function")
    if isinstance(fn, str):
        return {"name": fn, "args": _args(tc.get("args", tc.get("arguments"))), "id": tc.get("id")}
    if fn is not None and not isinstance(fn, dict):
        return {
            "name": getattr(fn, "name", None),
            "args": _args(getattr(fn, "arguments", None)),
            "id": tc.get("id"),
        }
    fn = fn if isinstance(fn, dict) else tc
    args = fn.get("arguments", fn.get("args", fn.get("input", {})))
    return {"name": fn.get("name"), "args": _args(args), "id": tc.get("id")}


def _block_call(b: Any) -> dict[str, Any] | None:
    if not isinstance(b, dict):
        return None
    if b.get("type") == "tool_use":
        return {"name": b.get("name"), "args": b.get("input") or {}, "id": b.get("id")}
    fc = b.get("functionCall") or b.get("function_call")
    if isinstance(fc, dict):
        return {"name": fc.get("name"), "args": _args(fc.get("args")), "id": fc.get("id")}
    tu = b.get("toolUse")
    if isinstance(tu, dict):
        return {"name": tu.get("name"), "args": tu.get("input") or {}, "id": tu.get("toolUseId")}
    return None


def _block_result(b: Any, names: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(b, dict):
        return None
    if b.get("type") == "tool_result":
        cid = b.get("tool_use_id")
        return {"name": names.get(str(cid or ""), "tool"), "output": _text(b.get("content")), "id": cid}
    fr = b.get("functionResponse") or b.get("function_response")
    if isinstance(fr, dict):
        return {"name": fr.get("name") or "tool", "output": _text(fr.get("response")), "id": fr.get("id")}
    tr = b.get("toolResult")
    if isinstance(tr, dict):
        cid = tr.get("toolUseId")
        return {"name": names.get(str(cid or ""), "tool"), "output": _text(tr.get("content")), "id": cid}
    return None


def _args(args: Any) -> Any:
    if isinstance(args, str):
        if not args.strip():
            return {}
        try:
            return json.loads(args)
        except ValueError:
            return {"_raw": args}
    return {} if args is None else args


def _as_dict(m: Any) -> dict[str, Any]:
    if isinstance(m, dict):
        return m
    out: dict[str, Any] = {}
    for key in ("role", "type", "content", "tool_calls", "tool_call_id", "name", "parts"):
        v = getattr(m, key, None)
        if v is not None:
            out[key] = v
    if "tool_calls" in out:
        out["tool_calls"] = [tc if isinstance(tc, dict) else vars(tc) for tc in out["tool_calls"]]
    return out


def _is_empty(m: dict[str, Any]) -> bool:
    return str(m.get("role") or m.get("type") or "") in _ROLES and not m.get("content") and not m.get("parts")


def _unknown(idx: int, m: dict[str, Any], on_unknown: OnUnknown, why: str = "") -> None:
    msg = why or f"message {idx} has an unrecognized shape (keys: {sorted(m)[:8]})"
    if on_unknown == "raise":
        raise UnrecognizedMessage(msg)
    if on_unknown == "warn":
        warnings.warn(msg, stacklevel=3)


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
                elif b.get("type") == "text" and isinstance(b.get("content"), str):
                    parts.append(b["content"])
                elif "json" in b:
                    parts.append(json.dumps(b["json"], default=str))
                elif b.get("type") == "tool_result":
                    parts.append(_text(b.get("content")))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, default=str)
    return str(content)
