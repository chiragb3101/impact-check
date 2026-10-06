"""Extracts definitions (symbols) and call sites (refs) from source files using tree-sitter.

This is syntax-level, name-based analysis: fast and language-agnostic, but it does not resolve
types. Dependency injection or dynamic dispatch can hide callers, and common names can produce
false matches. facts.py reports ambiguity so the agent knows when to verify by reading code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath

from tree_sitter_language_pack import get_parser

EXT_LANG = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
}

FAMILY = {"python": "python", "java": "java", "javascript": "js", "typescript": "js", "tsx": "js"}

DEF_TYPES = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "java": {
        "method_declaration": "method", "constructor_declaration": "constructor",
        "class_declaration": "class", "interface_declaration": "interface",
        "enum_declaration": "enum", "record_declaration": "record",
    },
    "js": {
        "function_declaration": "function", "generator_function_declaration": "function",
        "method_definition": "method", "class_declaration": "class",
        "abstract_class_declaration": "class", "interface_declaration": "interface",
    },
}

JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function"}


@dataclass
class Symbol:
    name: str
    qualname: str
    kind: str
    start_line: int
    end_line: int
    header: str
    is_test: bool = False


@dataclass
class Ref:
    callee: str
    line: int
    caller: str  # qualname of enclosing symbol, or "<module>"


@dataclass
class ParseResult:
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    line_count: int = 0


def language_for(path: str) -> str | None:
    return EXT_LANG.get(PurePosixPath(path).suffix.lower())


def family_for(path: str) -> str | None:
    lang = language_for(path)
    return FAMILY.get(lang) if lang else None


def is_test_file(path: str) -> bool:
    p = "/" + path.replace("\\", "/")
    name = PurePosixPath(path).name
    fam = family_for(path)
    if fam == "python":
        return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in p or "/test/" in p
    if fam == "java":
        return "/src/test/" in p or bool(re.search(r"(Test|Tests|IT)\.java$", name))
    if fam == "js":
        return bool(re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", name)) or "/__tests__/" in p
    return False


@lru_cache(maxsize=None)
def _parser(lang: str):
    return get_parser(lang)


def _text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _last_identifier(text: str) -> str:
    text = re.sub(r"<.*>", "", text)          # drop generics
    parts = re.split(r"[.:]+", text.strip())
    return parts[-1] if parts else text


def _header(src: bytes, node) -> str:
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    raw = src[node.start_byte:end].decode("utf-8", errors="replace")
    return re.sub(r"\s+", " ", raw).strip()[:400]


def _callee(fam: str, node, src: bytes) -> str | None:
    t = node.type
    if fam == "python" and t == "call":
        fn = node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "attribute":
            attr = fn.child_by_field_name("attribute")
            return _text(src, attr) if attr is not None else None
        if fn.type == "identifier":
            return _text(src, fn)
        return None
    if fam == "java":
        if t == "method_invocation":
            n = node.child_by_field_name("name")
            return _text(src, n) if n is not None else None
        if t == "object_creation_expression":
            ty = node.child_by_field_name("type")
            return _last_identifier(_text(src, ty)) if ty is not None else None
        if t == "method_reference":
            kids = [c for c in node.children if c.type == "identifier"]
            return _text(src, kids[-1]) if kids else None
        return None
    if fam == "js":
        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is None:
                return None
            if fn.type == "member_expression":
                prop = fn.child_by_field_name("property")
                return _text(src, prop) if prop is not None else None
            if fn.type == "identifier":
                return _text(src, fn)
            return None
        if t == "new_expression":
            ctor = node.child_by_field_name("constructor")
            return _last_identifier(_text(src, ctor)) if ctor is not None else None
    return None


def _definition(fam: str, node, src: bytes) -> tuple[str, str] | None:
    """Returns (name, kind) if node defines a named symbol."""
    kinds = DEF_TYPES[fam]
    if node.type in kinds:
        n = node.child_by_field_name("name")
        if n is None:
            return None
        return _text(src, n), kinds[node.type]
    if fam == "js" and node.type == "variable_declarator":
        val = node.child_by_field_name("value")
        n = node.child_by_field_name("name")
        if val is not None and n is not None and val.type in JS_FUNCTION_VALUES and n.type == "identifier":
            return _text(src, n), "function"
    return None


def _is_test_symbol(fam: str, node, name: str, kind: str, src: bytes) -> bool:
    if fam == "python":
        return kind == "function" and name.startswith("test")
    if fam == "java":
        if kind != "method":
            return False
        for c in node.children:
            if c.type == "modifiers" and re.search(r"@(Test|ParameterizedTest|RepeatedTest)\b", _text(src, c)):
                return True
    return False


def parse_source(path: str, source: str) -> ParseResult | None:
    lang = language_for(path)
    if lang is None:
        return None
    fam = FAMILY[lang]
    src = source.encode("utf-8")
    tree = _parser(lang).parse(src)
    result = ParseResult(language=lang, line_count=source.count("\n") + 1)
    test_file = is_test_file(path)

    # iterative walk: (node, enclosing qualname)
    stack = [(tree.root_node, "<module>")]
    while stack:
        node, scope = stack.pop()
        child_scope = scope
        d = _definition(fam, node, src)
        if d is not None:
            name, kind = d
            qual = name if scope == "<module>" else f"{scope}.{name}"
            # for JS variable_declarator the body lives in the value node
            target = node.child_by_field_name("value") if node.type == "variable_declarator" else node
            result.symbols.append(Symbol(
                name=name, qualname=qual, kind=kind,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                header=_header(src, target),
                is_test=test_file and _is_test_symbol(fam, node, name, kind, src),
            ))
            child_scope = qual
        else:
            callee = _callee(fam, node, src)
            if callee:
                result.refs.append(Ref(callee=callee, line=node.start_point[0] + 1, caller=scope))
        for c in reversed(node.children):
            stack.append((c, child_scope))
    return result


def innermost_symbol(symbols: list[Symbol], line: int) -> Symbol | None:
    best = None
    for s in symbols:
        if s.start_line <= line <= s.end_line:
            if best is None or (s.end_line - s.start_line) < (best.end_line - best.start_line):
                best = s
    return best
