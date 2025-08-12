# ─── tools.py ──────────────────────────────────────────────────────────────
import inspect, typing, functools, sys

REGISTERED_TOOLS: list[dict] = []
TOOL_FUNCS: dict[str, typing.Callable] = {}

# Simple Python → JSON-Schema type mapping -----------------------------
_SIMPLE_TYPES = {
    str:  "string",
    int:  "integer",
    float:"number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

def _schema_from_signature(sig: inspect.Signature) -> dict:
    """Build the {type:'object', properties:…, required:[…]} block."""
    props, required = {}, []

    for name, p in sig.parameters.items():
        if name in {"self", "cls"}:          # ignore typical non-user args
            continue

        t = _SIMPLE_TYPES.get(p.annotation, "string")   # fallback to string
        props[name] = {"type": t}
        if p.default is p.empty:
            required.append(name)

    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def tool(fn: typing.Callable) -> typing.Callable:
    """Decorator that registers an async function as an OpenAI-style tool."""
    sig = inspect.signature(fn)

    raw_doc = inspect.getdoc(fn) or ""
    doc_lines = [ln for ln in raw_doc.splitlines() if ln.strip()]
    description = doc_lines[0] if doc_lines else fn.__name__

    REGISTERED_TOOLS.append(
        {
            "name": fn.__name__,
            "description": description,
            "inputSchema": _schema_from_signature(sig),
        }
    )
    TOOL_FUNCS[fn.__name__] = fn
    return fn