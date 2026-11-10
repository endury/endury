"""In-house tool registry: a ``@coach_tool`` decorator and dispatch helpers.

Replaces LangChain's ``@tool``. Each decorated function becomes a
:class:`CoachTool` carrying its name, description, and an OpenAI-compatible
JSON parameter schema derived from the signature's type hints. Tools are
plain sync callables returning a string.

The description is the function docstring as the model needs it on every
round: without the ``Returns:`` section, without the entries of
:data:`HIDDEN_PARAMETERS`, and without the ``Reply contract`` block — that
block rides on the tool's result instead (:func:`with_reply_contract`), so
it is read when the tool ran and not before.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin, get_type_hints

from endury.coach.tools._errors import tool_error_response

logger = logging.getLogger(__name__)

_PRIMITIVE_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}

#: Parameters the app fills in, never the model: ``confirm`` is set only when
#: the athlete applies a proposal card (:mod:`endury.coach.proposals`), and
#: ``expected_input_hash`` comes from the preview that card was built from.
#: Both stay in the signature and out of the schema the model sees.
HIDDEN_PARAMETERS = frozenset({"confirm", "expected_input_hash"})

#: The docstring line that opens a tool's reply contract.
_CONTRACT_HEADING = "Reply contract"
#: The key a tool's reply contract rides under in its result.
REPLY_CONTRACT_KEY = "reply_contract"
#: One ``Args:`` entry's first line, e.g. ``    confirm: Filled in by …``.
_ARG_ENTRY = re.compile(r" {4}(\w+):")


@dataclass(frozen=True)
class CoachTool:
    """A callable coach tool with an OpenAI-compatible parameter schema.

    Attributes:
        name: The function name.
        description: What the model reads in the tool list.
        parameters: The JSON parameter schema.
        fn: The tool itself.
        reply_contract: How to relay the tool's result, one rule per entry.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    reply_contract: tuple[str, ...] = ()

    @property
    def is_async(self) -> bool:
        """Whether the tool awaits something (the analysis sandbox)."""
        return inspect.iscoroutinefunction(self.fn)

    def invoke(self, args: dict[str, Any] | None = None) -> str:
        """Call the tool with keyword arguments parsed from *args*."""
        result: str = self.fn(**(args or {}))
        return result


def _resolve_type(annotation: Any) -> tuple[str, bool]:
    """Map a type annotation to a (json_type, is_optional) pair.

    Optional means the annotation is ``X | None`` / ``Optional[X]``. Unknown
    types fall back to ``"string"``.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        members = get_args(annotation)
        non_none = [arg for arg in members if arg is not type(None)]
        is_optional = len(non_none) < len(members)
        base = non_none[0] if non_none else str
        return _PRIMITIVE_JSON_TYPES.get(base, "string"), is_optional
    return _PRIMITIVE_JSON_TYPES.get(annotation, "string"), False


def _build_parameters(fn: Callable[..., str]) -> dict[str, Any]:
    """Build the OpenAI ``parameters`` JSON schema from *fn*'s signature.

    :data:`HIDDEN_PARAMETERS` are left out: the model never passes them.
    """
    signature = inspect.signature(fn)
    hints = get_type_hints(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in signature.parameters.items():
        if name in HIDDEN_PARAMETERS:
            continue
        json_type, is_optional = _resolve_type(hints.get(name, str))
        properties[name] = {"type": json_type}
        if param.default is inspect.Parameter.empty and not is_optional:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _split_docstring(doc: str) -> tuple[str, tuple[str, ...]]:
    """Split a tool docstring into the model's description and reply contract.

    Args:
        doc: The dedented Google-style docstring.

    Returns:
        The description — body and ``Args:`` minus :data:`HIDDEN_PARAMETERS`,
        nothing from ``Returns:`` on — and the ``Reply contract`` bullets,
        each joined onto one line.
    """
    body: list[str] = []
    contract: list[str] = []
    args: list[str] = []
    section = body
    hidden = False
    for line in doc.splitlines():
        if line == "Returns:":
            break
        if line == "Args:":
            section = args
        elif line.startswith(_CONTRACT_HEADING):
            section = contract
        elif section is contract:
            if line.startswith("* "):
                contract.append(line[2:].strip())
            elif line.strip():
                contract[-1] += " " + line.strip()
        elif section is args:
            entry = _ARG_ENTRY.match(line)
            if entry:
                hidden = entry.group(1) in HIDDEN_PARAMETERS
            if not hidden:
                args.append(line)
        else:
            body.append(line)
    description = "\n".join(body).strip()
    if any(line.strip() for line in args):
        description += "\n\nArgs:\n" + "\n".join(args).rstrip()
    return description, tuple(contract)


def coach_tool(fn: Callable[..., Any]) -> CoachTool:
    """Decorate a function as a :class:`CoachTool`.

    The tool name is the function name; the description and the reply
    contract come from its docstring.

    Args:
        fn: A callable returning a JSON string, or a coroutine function for a
            tool that awaits (:func:`execute_tool_async`). Its signature's
            type hints define the parameter schema.

    Returns:
        A :class:`CoachTool` wrapping *fn*.
    """
    description, reply_contract = _split_docstring(inspect.getdoc(fn) or "")
    return CoachTool(
        name=fn.__name__,
        description=description,
        parameters=_build_parameters(fn),
        fn=fn,
        reply_contract=reply_contract,
    )


def with_reply_contract(tool: CoachTool | None, result: str) -> str:
    """Attach *tool*'s reply contract to the result the model reads.

    Args:
        tool: The tool that ran, or None for an unknown one.
        result: Its raw result string.

    Returns:
        The result with the contract under :data:`REPLY_CONTRACT_KEY`; the
        result unchanged when the tool has no contract or the result is not
        a JSON object.
    """
    if tool is None or not tool.reply_contract:
        return result
    try:
        parsed = json.loads(result)
    except ValueError:
        return result
    if not isinstance(parsed, dict):
        return result
    parsed[REPLY_CONTRACT_KEY] = list(tool.reply_contract)
    return json.dumps(parsed, ensure_ascii=False)


def to_openai_tools(tools: list[CoachTool]) -> list[dict[str, Any]]:
    """Render coach tools into the OpenAI ``tools`` wire schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def _invalid_arguments(name: str, args: dict[str, Any], exc: TypeError) -> str:
    """The error a tool call with arguments the tool cannot take gets."""
    # Keys only: argument *values* carry athlete content (memory rewrites,
    # session notes) that has no business in a log line.
    logger.warning("Invalid arguments for tool %s %s: %s", name, sorted(args), exc)
    return json.dumps(
        {
            "error": f"Invalid arguments for tool '{name}'.",
            "message": (
                f"The arguments were rejected: {exc}. "
                "Re-read this tool's parameter schema and call it again "
                "with corrected arguments."
            ),
        }
    )


def execute_tool(tools: list[CoachTool], name: str, args: dict[str, Any]) -> str:
    """Dispatch a tool call by name, returning its string result.

    Unknown tools yield an error JSON string. A ``TypeError`` means the model
    sent arguments the tool cannot take, so the response names the arguments as
    the fault and quotes the error — the model can only self-correct if it is
    told what was wrong. Every other exception (an intervals.icu outage, a
    database error) keeps the athlete-legible message from
    :func:`tool_error_response`.

    Args:
        tools: The available tools.
        name: The tool name requested by the model.
        args: Keyword arguments parsed from the model's tool call.

    Returns:
        The tool's JSON string result, or an error JSON string.
    """
    tool = next((candidate for candidate in tools if candidate.name == name), None)
    if tool is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        return tool.invoke(args)
    except TypeError as exc:
        return _invalid_arguments(name, args, exc)
    except Exception as exc:
        return tool_error_response(f"run tool {name}", exc)


async def execute_tool_async(
    tools: list[CoachTool], name: str, args: dict[str, Any]
) -> str:
    """Dispatch a call to an async tool, with :func:`execute_tool`'s errors.

    Args:
        tools: The available tools.
        name: The tool name requested by the model.
        args: Keyword arguments parsed from the model's tool call.

    Returns:
        The tool's JSON string result, or an error JSON string.
    """
    tool = next((candidate for candidate in tools if candidate.name == name), None)
    if tool is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        return str(await tool.fn(**(args or {})))
    except TypeError as exc:
        return _invalid_arguments(name, args, exc)
    except Exception as exc:
        return tool_error_response(f"run tool {name}", exc)


__all__ = [
    "HIDDEN_PARAMETERS",
    "REPLY_CONTRACT_KEY",
    "CoachTool",
    "coach_tool",
    "execute_tool",
    "execute_tool_async",
    "to_openai_tools",
    "with_reply_contract",
]
