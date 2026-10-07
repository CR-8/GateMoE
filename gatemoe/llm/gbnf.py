"""JSON schema -> compact GBNF grammar for llama.cpp (no layout whitespace).

llama-server's own ``response_format: json_schema`` conversion allows up to two newlines and
20 spaces of indentation between every JSON token, and models use it: pretty-printed JSON costs
generation time on a CPU for characters that are thrown away. This converter emits the same
constraints (types, enums, string/array length bounds, integer ranges, required/optional
properties) with ``","`` and ``":"`` glued to the values.

Only the schema subset used by GateMoE's gateways is supported; anything else raises
``Unsupported`` and the caller falls back to ``response_format``.
"""
from __future__ import annotations

import json
import re

# same character class as llama.cpp's json-schema-to-grammar (no raw control characters)
CHAR = r'char ::= [^"\\\x7F\x00-\x1F] | [\\] (["\\/bfnrt] | "u" [0-9a-fA-F]{4})'
NUMBER = 'number ::= "-"? ([0] | [1-9] [0-9]{0,15}) ("." [0-9]{1,16})? ([eE] [-+]? [0-9]{1,16})?'
INTEGER = 'integer ::= "-"? ([0] | [1-9] [0-9]{0,15})'


class Unsupported(ValueError):
    pass


def _lit(text: str) -> str:
    """A GBNF string literal matching ``text`` exactly."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


class _Builder:
    def __init__(self, sep: str = ""):
        self.sep = sep                     # GBNF fragment allowed after ':' and ',' ("" = nothing)
        self.rules: dict[str, str] = {}
        self.by_body: dict[str, str] = {}
        self.base: set[str] = set()

    def add(self, hint: str, body: str) -> str:
        if body in self.by_body:            # identical rules are shared (many str-1-60 fields)
            return self.by_body[body]
        name = re.sub(r"[^A-Za-z0-9-]", "-", hint)   # GBNF rule names: letters, digits, '-'
        hint = name
        i = 1
        while name in self.rules:
            i += 1
            name = f"{hint}{i}"
        self.rules[name] = body
        self.by_body[body] = name
        return name

    def visit(self, schema: dict, hint: str) -> str:
        if "enum" in schema:
            return self.add(hint, " | ".join(_lit(json.dumps(v, ensure_ascii=False)) for v in schema["enum"]))
        t = schema.get("type")
        if t == "string":
            lo, hi = int(schema.get("minLength", 0)), schema.get("maxLength")
            self.base.add("char")
            rep = f"{{{lo},{int(hi)}}}" if hi is not None else ("*" if lo == 0 else f"{{{lo},}}")
            return self.add(f"str{lo}-{hi}", f'"\\"" char{rep} "\\""')
        if t == "integer":
            lo, hi = schema.get("minimum"), schema.get("maximum")
            if lo is not None and hi is not None and 0 <= hi - lo <= 64:
                return self.add(f"int{lo}-{hi}", " | ".join(_lit(str(v)) for v in range(int(lo), int(hi) + 1)))
            self.base.add("integer")
            return "integer"
        if t == "number":
            self.base.add("number")
            return "number"
        if t == "boolean":
            return self.add("bool", '"true" | "false"')
        if t == "array":
            item = self.visit(schema["items"], hint + "-item")
            lo, hi = int(schema.get("minItems", 0)), schema.get("maxItems")
            if hi is None:
                raise Unsupported("array without maxItems")
            hi = int(hi)
            if hi == 0:
                return self.add(hint, '"[]"')
            more_lo, more_hi = max(0, lo - 1), hi - 1
            tail = f' ("," {self.sep}{item}){{{more_lo},{more_hi}}}' if more_hi > 0 else ""
            inner = f"{item}{tail}"
            body = f'"[" {inner} "]"' if lo >= 1 else f'"[" ({inner})? "]"'
            return self.add(hint, body)
        if t == "object":
            props = schema.get("properties") or {}
            required = [k for k in props if k in set(schema.get("required", []))]
            optional = [k for k in props if k not in required]
            if not required:
                raise Unsupported("object without required properties")
            if schema.get("additionalProperties", False) not in (False, None):
                raise Unsupported("additionalProperties")
            parts = []
            for i, k in enumerate(required):
                v = self.visit(props[k], f"{hint}-{k}")
                parts.append(("" if i == 0 else f'"," {self.sep}') + f'{_lit(json.dumps(k))} ":" {self.sep}{v}')
            for k in optional:
                v = self.visit(props[k], f"{hint}-{k}")
                parts.append(f'("," {self.sep}{_lit(json.dumps(k))} ":" {self.sep}{v})?')
            return self.add(hint, '"{" ' + " ".join(parts) + ' "}"')
        raise Unsupported(f"schema type {t!r}")


def schema_to_gbnf(schema: dict, spaces: bool = False) -> str:
    """``spaces=True`` allows one optional space after ':' and ',' (json.dumps style), still no
    newlines or indentation."""
    b = _Builder('" "? ' if spaces else "")
    root = b.visit(schema, "root-obj")
    lines = [f"root ::= {root}"]
    lines += [f"{name} ::= {body}" for name, body in b.rules.items()]
    lines += [{"char": CHAR, "number": NUMBER, "integer": INTEGER}[x] for x in sorted(b.base)]
    return "\n".join(lines) + "\n"
