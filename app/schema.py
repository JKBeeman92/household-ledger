"""A small JSON Schema validator for the published rules/household schemas (app/schemas/*.schema.json).

Supports only what those schemas use: type, properties, required, additionalProperties (false or a schema), items, prefixItems,
minItems/maxItems, minLength, minimum, pattern, enum and format "regex" (checked by compiling). Errors name the key, e.g. `employers[0].match`."""
from __future__ import annotations
import difflib, json, re
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
NAMES = ("rules", "household")
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def load(name: str) -> dict:
    if name not in NAMES:
        raise KeyError(name)
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _is(value, t: str) -> bool:
    if t in ("integer", "number"):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and (t == "number" or isinstance(value, int))
    return isinstance(value, _TYPES[t])


def _kind(value) -> str:
    return {dict: "an object", list: "a list", str: "a string", bool: "true/false", type(None): "null"}.get(type(value), "a number")


def _join(path: str, key) -> str:
    return f"{path}[{key}]" if isinstance(key, int) else (f"{path}.{key}" if path else str(key))


def validate(value, schema: dict, path: str = "") -> list[str]:
    """Return every problem as 'path: message' (an empty list means valid). A root-level problem has no path prefix."""
    at = f"{path}: " if path else ""
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_is(value, t) for t in types):
            return [f"{at}must be {' or '.join(types)}, not {_kind(value)}"]
    errs: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{at}must be one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        errs.append(f"{at}must be at least {schema['minimum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{at}must not be empty")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{at}{value!r} does not match the expected format ({schema.get('pattern')})")
        if schema.get("format") == "regex":
            try:
                re.compile(value)
            except re.error as e:
                errs.append(f"{at}not a valid regular expression ({e})")
    elif isinstance(value, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{_join(path, k)}: required")
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], _join(path, k))
            elif extra is False:
                near = difflib.get_close_matches(k, props, n=1)
                errs.append(f"{_join(path, k)}: unknown key" + (f" (did you mean \"{near[0]}\"?)" if near else "") + "; the pipeline would ignore it")
            elif isinstance(extra, dict):
                errs += validate(v, extra, _join(path, k))
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            lo, hi = schema.get("minItems", 0), schema.get("maxItems")
            errs.append(f"{at}must have {lo if lo == hi else f'at least {lo}' if hi is None else f'{lo} to {hi}'} item(s), has {len(value)}")
        pre = schema.get("prefixItems", [])
        for i, v in enumerate(value):
            sub = pre[i] if i < len(pre) else schema.get("items")
            if sub:
                errs += validate(v, sub, _join(path, i))
    return errs


def help_fields(name: str) -> list[dict]:
    """Top-level keys of a schema as {key, type, required, description}, for the help text beside an editor."""
    s = load(name)
    out = []
    for k, p in s.get("properties", {}).items():
        t = p.get("type", "any")
        out.append({"key": k, "type": " or ".join(t) if isinstance(t, list) else t, "required": k in s.get("required", []), "description": p.get("description", "")})
    return out
