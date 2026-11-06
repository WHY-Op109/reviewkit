r"""rk_schema.py: validate data and agent records against the kit's JSON Schemas (data format v0, K3).

    python3 -I rk_schema.py check [--schemas DIR] [--expect FORMAT] FILE...
    python3 -I rk_schema.py validate --schema SCHEMA [--def NAME] FILE...
    python3 -I rk_schema.py lint [DIR]

check     selects each FILE's schema by its `format` value: `reviewkit-<name>/<version>` is
          validated against DIR/reviewkit-<name>-<version>.schema.json (default DIR: the
          `schemas/` folder beside this script's folder). FILE is JSON (`.json`) or TOML
          (`.toml`, validated as the JSON value it decodes to; a TOML date or time matches no
          JSON type). With --expect, every FILE must carry exactly that `format` (so a file
          relabelled with another format cannot pass against a looser schema).
validate  validates every FILE against SCHEMA, or against SCHEMA's `$defs/NAME` (for example
          `--def output`, the model-facing part of an agent record).
lint      checks every `*.schema.json` in DIR (default as for check): it parses, uses only the
          supported keywords, resolves every `$ref`, its `$id` and its `format` constant match
          its file name, and its `$defs/output` (if any) stays in the subset both model CLIs
          accept.

`pattern` and `patternProperties` are ECMA-262 regular expressions, as JSON Schema defines them:
each is translated to Python `re` syntax before use (`$` matches only at the very end, so a
trailing newline does not slip through; `.` excludes line terminators; `\d` and `\w` are ASCII),
and lint rejects Python-only syntax (`\A`, `\Z`, `(?P...)`, inline flags, possessive
quantifiers, atomic groups) and the constructs whose meaning differs (`\b`, `\B`, backreferences).

Diagnostics (report "schema"; all errors unless marked):
  schema-invalid             the instance violates the schema (`file` = the instance, `path` =
                             [the JSON pointer into the instance])
  schema-missing-format      the instance has no string `format`
  schema-unknown-format      no schema for the instance's `format`
  schema-parse-error         a file (instance or schema) does not load
  schema-unsupported-keyword a keyword this validator does not implement (`path` = [pointer
                             into the schema])
  schema-ref-unresolved      a `$ref` that is not a local pointer to a schema in the same file
  schema-malformed           a keyword with a value of the wrong shape (bad type name, regex...)
  schema-id-mismatch         `$id` or the `format` constant does not match the file name
  schema-format-mismatch     (check --expect) the instance's `format` is not the expected one
  schema-output-not-portable `$defs/output` uses a construct outside the model-facing subset
  schema-metadata-missing    (warning) no `title` or `description` at the top level

Output: one `reviewkit-report/0` envelope on stdout. Exit status 0 no errors, 1 errors,
2 setup failure (bad arguments, a missing or unreadable FILE or --schema; a file that reads
but does not parse is a `schema-parse-error`). Standard library only; Python 3.11+. Nothing
here is project-specific (D35).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tomllib
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rk_common as rk  # noqa: E402

CHECK = "schema"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
ID_BASE = "https://reviewkit.invalid/schemas/"
DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas")
FORMAT_RE = re.compile(r"^reviewkit-([a-z0-9]+(?:-[a-z0-9]+)*)/(0|[1-9][0-9]*)$")
FILE_RE = re.compile(r"^reviewkit-([a-z0-9]+(?:-[a-z0-9]+)*)-(0|[1-9][0-9]*)\.schema\.json$")
MAX_DEPTH = 256

# --- the supported keyword set ----------------------------------------------------------------

ANNOTATIONS = {"$schema", "$id", "$comment", "title", "description", "examples", "default"}
SCHEMA_ONE = {"additionalProperties", "propertyNames", "items", "not", "if", "then", "else"}
SCHEMA_MAP = {"properties", "patternProperties", "$defs"}
SCHEMA_LIST = {"prefixItems", "oneOf", "anyOf", "allOf"}
ASSERTIONS = {"$ref", "type", "enum", "const", "required", "minItems", "maxItems", "uniqueItems",
              "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum",
              "exclusiveMaximum", "minProperties"}
SUPPORTED = ANNOTATIONS | SCHEMA_ONE | SCHEMA_MAP | SCHEMA_LIST | ASSERTIONS
TYPES = ("null", "boolean", "object", "array", "number", "string", "integer")

# The model-facing subset for `$defs/output`: what both `claude -p --json-schema` and
# `codex exec --output-schema` (strict structured output) accept. Every object closes its
# properties and requires all of them (an optional field is nullable instead).
OUTPUT_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum",
                   "const", "anyOf", "description", "title", "$comment"}


def esc(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def unesc(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def rel(p: str) -> str:
    """A path for messages: relative to the working directory when that is shorter."""
    try:
        r = os.path.relpath(p)
    except ValueError:
        return p
    return r if len(r) < len(p) else p


def show(v: Any, limit: int = 80) -> str:
    try:
        s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        s = repr(v)
    return s if len(s) <= limit else s[: limit - 3] + "..."


# --- regular expressions ------------------------------------------------------------------------
#
# JSON Schema patterns are ECMA-262 regular expressions (no flags). Python `re` differs where it
# matters for anchored formats: its `$` also matches before a final "\n", its `.` matches "\r",
# and its `\d` and `\w` match non-ASCII digits and letters. `ecma_regex` translates the common
# subset and refuses the rest, so a schema means the same here as in any other validator.

ANY_BUT_LINE_END = "[^\\n\\r\\u2028\\u2029]"
OUT_CLASS_ESC = {"d": "[0-9]", "D": "[^0-9]", "w": "[A-Za-z0-9_]", "W": "[^A-Za-z0-9_]"}
IN_CLASS_ESC = {"d": "0-9", "w": "A-Za-z0-9_"}
BRACE_QUANT = re.compile(r"\{[0-9]+(?:,[0-9]*)?\}")


class RegexError(Exception):
    pass


def ecma_regex(p: str) -> str:
    """The Python `re` pattern that means what the ECMA-262 pattern `p` means; RegexError for
    syntax that is Python-only, not ECMA-262, or means something different in the two."""
    out: list[str] = []
    i, n = 0, len(p)
    in_class = False
    quantified = False  # the previous token was a quantifier (`a++` is possessive in Python only)
    while i < n:
        c = p[i]
        if c == "\\":
            if i + 1 >= n:
                raise RegexError("a trailing backslash")
            d = p[i + 1]
            i += 2
            quantified = False
            if in_class:
                if d in ("D", "W"):
                    raise RegexError(f"\\{d} inside a character class is not supported")
                out.append(IN_CLASS_ESC.get(d, "\\" + d))
                continue
            if d in "AZzG":
                raise RegexError(f"\\{d} is a Python anchor, not ECMA-262 (use ^ and $)")
            if d in "bB":
                raise RegexError(f"\\{d}: word boundaries differ between ECMA-262 and Python")
            if d == "k" or d.isdigit() and d != "0":
                raise RegexError("backreferences are not supported")
            out.append(OUT_CLASS_ESC.get(d, "\\" + d))
            continue
        if in_class:
            if c == "]":
                in_class = False
            elif c == "[":
                c = "\\["  # literal in ECMA-262; a nested set (future syntax) in Python
            out.append(c)
            i += 1
            continue
        if c == "[":
            neg = p.startswith("[^", i)
            j = i + (2 if neg else 1)
            if j < n and p[j] == "]":  # ECMA-262 `[]` matches nothing, `[^]` anything
                out.append("[\\s\\S]" if neg else "(?!)")
                i = j + 1
                quantified = False
                continue
            in_class = True
            out.append("[^" if neg else "[")
            i = j
            quantified = False
            continue
        if c == "(" and p.startswith("(?", i):
            rest = p[i + 2:]
            if rest.startswith((":", "=", "!", "<=", "<!")):
                out.append("(?")
                i += 2
            elif rest.startswith("<"):  # (?<name>...) is (?P<name>...) in Python
                out.append("(?P<")
                i += 3
            else:
                raise RegexError(f"group syntax {p[i:i + 4]!r}... is not ECMA-262 (or not supported)")
            quantified = False
            continue
        if c == "+" and quantified:
            raise RegexError("possessive quantifiers are Python-only")
        if c == "{":
            m = BRACE_QUANT.match(p, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                quantified = True
                continue
            out.append("\\{")  # a literal brace in ECMA-262 (Annex B)
            i += 1
            quantified = False
            continue
        if c == "$":
            out.append("\\Z")
        elif c == ".":
            out.append(ANY_BUT_LINE_END)
        elif c == "}":
            out.append("\\}")
        else:
            out.append(c)
        # `?` after a quantifier makes it lazy (`a+?`); a `+` after that is still possessive-free
        quantified = c in "*+" or (c == "?" and not quantified)
        i += 1
    if in_class:
        raise RegexError("an unterminated character class")
    return "".join(out)


_REGEX_CACHE: dict[str, re.Pattern] = {}


def compile_ecma(p: str) -> re.Pattern:
    """Compile an ECMA-262 pattern; RegexError if it is outside the supported subset."""
    if p not in _REGEX_CACHE:
        try:
            _REGEX_CACHE[p] = re.compile(ecma_regex(p))
        except re.error as e:
            raise RegexError(f"does not compile: {e}") from None
    return _REGEX_CACHE[p]


# --- JSON semantics -----------------------------------------------------------------------------


def is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def json_type(v: Any) -> str:
    """The JSON type name of a decoded value (`integer` for integral numbers)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "integer" if math.isfinite(v) and v.is_integer() else "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "TOML " + type(v).__name__  # a TOML date, time or date-time: no JSON type


def type_matches(t: str, v: Any) -> bool:
    jt = json_type(v)
    if t == "number":
        return jt in ("number", "integer")
    return jt == t


def json_equal(a: Any, b: Any) -> bool:
    """Equality as JSON Schema defines it: true is not 1, 1 equals 1.0, objects ignore order."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_number(a) and is_number(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if type(a) is not type(b):
        return False
    return a == b


# --- loading ------------------------------------------------------------------------------------


class LoadError(Exception):
    pass


class ReadError(LoadError):
    """The file could not be read at all (missing, unreadable): a setup failure."""


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise LoadError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _no_constant(name):
    raise LoadError(f"{name} is not JSON")


def load_instance(path: str) -> Any:
    """Load a JSON or TOML file; raise LoadError with the reason."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ReadError(f"cannot read {path}: {e.strerror or e}") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise LoadError(f"{path} is not UTF-8: {e}") from None
    if path.endswith(".toml"):
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise LoadError(f"{path} is not valid TOML: {e}") from None
    if path.endswith(".json"):
        try:
            return json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_no_constant)
        except json.JSONDecodeError as e:
            raise LoadError(f"{path} is not valid JSON: {e}") from None
        except LoadError as e:
            raise LoadError(f"{path} is not valid JSON: {e}") from None
    raise LoadError(f"{path}: unsupported file type (expected .json or .toml)")


# --- lint ---------------------------------------------------------------------------------------


def resolve(root: Any, ref: str) -> tuple[bool, Any]:
    """Resolve a local `#/...` JSON pointer against the root schema."""
    if not isinstance(ref, str) or not ref.startswith("#"):
        return False, None
    frag = ref[1:]
    if frag == "":
        return True, root
    if not frag.startswith("/"):
        return False, None
    cur = root
    for tok in frag[1:].split("/"):
        tok = unesc(tok)
        if isinstance(cur, dict) and tok in cur:
            cur = cur[tok]
        elif isinstance(cur, list) and tok.isdigit() and int(tok) < len(cur):
            cur = cur[int(tok)]
        else:
            return False, None
    return True, cur


def lint_schema(root: Any, file: str) -> list[tuple[str, str, str, str]]:
    """Problems of one schema document: (code, severity, message, schema pointer)."""
    out: list[tuple[str, str, str, str]] = []

    def bad(code: str, msg: str, ptr: str, sev: str = "error") -> None:
        out.append((code, sev, msg, ptr))

    def nonneg_int(v: Any) -> bool:
        return json_type(v) == "integer" and v >= 0

    def walk(s: Any, ptr: str) -> None:
        if isinstance(s, bool):
            return
        if not isinstance(s, dict):
            bad("schema-malformed", f"a schema must be an object or a boolean, found {json_type(s)}", ptr)
            return
        for k, v in s.items():
            kp = f"{ptr}/{esc(k)}"
            if k not in SUPPORTED:
                bad("schema-unsupported-keyword", f"unsupported keyword {k!r}", kp)
                continue
            if k == "$schema":
                if ptr != "" or v != DRAFT:
                    bad("schema-malformed", f"$schema must be {DRAFT!r}, at the top level only", kp)
            elif k == "$id":
                if ptr != "":
                    bad("schema-malformed", "$id is supported at the top level only", kp)
                elif not isinstance(v, str):
                    bad("schema-malformed", "$id must be a string", kp)
            elif k in ("$comment", "title", "description"):
                if not isinstance(v, str):
                    bad("schema-malformed", f"{k} must be a string", kp)
            elif k == "examples":
                if not isinstance(v, list):
                    bad("schema-malformed", "examples must be an array", kp)
            elif k == "$ref":
                ok, target = resolve(root, v)
                if not ok:
                    bad("schema-ref-unresolved",
                        f"$ref {show(v)} is not a local pointer to a value of this file", kp)
                elif not isinstance(target, (dict, bool)):
                    bad("schema-ref-unresolved", f"$ref {show(v)} does not point to a schema", kp)
            elif k == "type":
                ts = v if isinstance(v, list) else [v]
                if (not ts or any(t not in TYPES for t in ts)
                        or (isinstance(v, list) and len(set(map(str, ts))) != len(ts))):
                    bad("schema-malformed", f"type {show(v)}: expected one of {list(TYPES)} or a list of them", kp)
            elif k == "enum":
                if not isinstance(v, list) or not v:
                    bad("schema-malformed", "enum must be a non-empty array", kp)
            elif k == "required":
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v) or len(set(v)) != len(v):
                    bad("schema-malformed", "required must be an array of distinct strings", kp)
            elif k in ("minItems", "maxItems", "minLength", "maxLength", "minProperties"):
                if not nonneg_int(v):
                    bad("schema-malformed", f"{k} must be a non-negative integer", kp)
            elif k == "uniqueItems":
                if not isinstance(v, bool):
                    bad("schema-malformed", "uniqueItems must be a boolean", kp)
            elif k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                if not is_number(v):
                    bad("schema-malformed", f"{k} must be a number", kp)
            elif k == "pattern":
                if not isinstance(v, str):
                    bad("schema-malformed", "pattern must be a string", kp)
                else:
                    try:
                        compile_ecma(v)
                    except RegexError as e:
                        bad("schema-malformed", f"pattern {v!r}: {e}", kp)
            elif k in SCHEMA_ONE:
                walk(v, kp)
            elif k in SCHEMA_MAP:
                if not isinstance(v, dict):
                    bad("schema-malformed", f"{k} must be an object of schemas", kp)
                    continue
                for name, sub in v.items():
                    if k == "patternProperties":
                        try:
                            compile_ecma(name)
                        except RegexError as e:
                            bad("schema-malformed", f"pattern {name!r}: {e}", f"{kp}/{esc(name)}")
                    walk(sub, f"{kp}/{esc(name)}")
            elif k in SCHEMA_LIST:
                if not isinstance(v, list) or not v:
                    bad("schema-malformed", f"{k} must be a non-empty array of schemas", kp)
                    continue
                for i, sub in enumerate(v):
                    walk(sub, f"{kp}/{i}")

    walk(root, "")
    if isinstance(root, dict):
        defs = root.get("$defs")
        if isinstance(defs, dict) and "output" in defs:
            out_root = defs["output"]
            if not isinstance(out_root, dict) or out_root.get("type") != "object" or "anyOf" in out_root:
                bad("schema-output-not-portable",
                    "the output root must be a single object schema (type \"object\", no anyOf)",
                    "/$defs/output")
            lint_output(out_root, "/$defs/output", bad)
    return out


def lint_output(s: Any, ptr: str, bad) -> None:
    """`$defs/output` is handed to a model CLI on its own: no references, every object closed and
    all its properties required (nullable when optional), only the portable keywords."""
    if not isinstance(s, dict):
        bad("schema-output-not-portable", "every output schema must be an object", ptr)
        return
    for k in s:
        if k not in OUTPUT_KEYWORDS:
            bad("schema-output-not-portable", f"keyword {k!r} is outside the model-facing subset", f"{ptr}/{esc(k)}")
    ts = s.get("type")
    ts = ts if isinstance(ts, list) else [ts] if ts is not None else []
    if "object" in ts or "properties" in s:
        props = s.get("properties", {})
        if s.get("additionalProperties") is not False:
            bad("schema-output-not-portable", "an output object must set additionalProperties: false", ptr)
        req = s.get("required", [])
        if isinstance(props, dict) and (not isinstance(req, list) or not all(isinstance(x, str) for x in req)
                                        or sorted(req) != sorted(props)):
            bad("schema-output-not-portable",
                "an output object must require every property (make optional ones nullable)", ptr)
        if isinstance(props, dict):
            for name, sub in props.items():
                lint_output(sub, f"{ptr}/properties/{esc(name)}", bad)
    if "items" in s:
        lint_output(s["items"], f"{ptr}/items", bad)
    for i, sub in enumerate(s.get("anyOf", []) if isinstance(s.get("anyOf"), list) else []):
        lint_output(sub, f"{ptr}/anyOf/{i}", bad)
    if not ts and "anyOf" not in s and "enum" not in s and "const" not in s:
        bad("schema-output-not-portable", "an output schema must state its type", ptr)


def schema_file_checks(root: Any, fname: str) -> list[tuple[str, str, str, str]]:
    """The checks that tie a schema file to its name (lint only)."""
    out = []
    m = FILE_RE.fullmatch(fname)
    if not m:
        out.append(("schema-id-mismatch",
                    "error", f"file name {fname!r} is not reviewkit-<name>-<version>.schema.json", ""))
        return out
    if not isinstance(root, dict):
        return out
    want_id = ID_BASE + fname
    if root.get("$id") != want_id:
        out.append(("schema-id-mismatch", "error", f"$id is {show(root.get('$id'))}, expected {want_id!r}", "/$id"))
    want_fmt = f"reviewkit-{m.group(1)}/{m.group(2)}"
    fmt = root.get("properties", {}).get("format") if isinstance(root.get("properties"), dict) else None
    if not isinstance(fmt, dict) or fmt.get("const") != want_fmt:
        out.append(("schema-id-mismatch", "error",
                    f"properties.format.const must be {want_fmt!r}, found {show(fmt.get('const') if isinstance(fmt, dict) else None)}",
                    "/properties/format"))
    if "format" not in (root.get("required") or []):
        out.append(("schema-id-mismatch", "error", "the format field must be required", "/required"))
    for k in ("title", "description"):
        if not isinstance(root.get(k), str) or not root.get(k):
            out.append(("schema-metadata-missing", "warning", f"no top-level {k}", ""))
    if root.get("$schema") != DRAFT:
        out.append(("schema-malformed", "error", f"$schema must be {DRAFT!r}", "/$schema"))
    return out


# --- validation ---------------------------------------------------------------------------------


class Validator:
    def __init__(self, root: Any):
        self.root = root

    def errors(self, inst: Any, schema: Any, iptr: str = "", depth: int = 0) -> list[tuple[str, str]]:
        """Every violation, as (instance pointer, message)."""
        if schema is True:
            return []
        if schema is False:
            return [(iptr, "no value is allowed here")]
        if not isinstance(schema, dict):
            return [(iptr, "malformed schema")]
        if depth > MAX_DEPTH:
            return [(iptr, "schema nesting too deep (a $ref cycle?)")]
        errs: list[tuple[str, str]] = []
        add = errs.append
        for k in schema:
            if k not in SUPPORTED:
                add((iptr, f"schema uses the unsupported keyword {k!r}"))

        if "$ref" in schema:
            ok, target = resolve(self.root, schema["$ref"])
            if not ok:
                add((iptr, f"unresolved $ref {show(schema['$ref'])}"))
            else:
                errs += self.errors(inst, target, iptr, depth + 1)

        if "type" in schema:
            ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(type_matches(t, inst) for t in ts):
                want = " or ".join(ts)
                add((iptr, f"expected {want}, found {json_type(inst)}"))
                return errs  # the remaining keywords would only repeat the type error

        if "enum" in schema and not any(json_equal(inst, e) for e in schema["enum"]):
            add((iptr, f"value {show(inst)} is not one of {show(schema['enum'], 200)}"))
        if "const" in schema and not json_equal(inst, schema["const"]):
            add((iptr, f"value {show(inst)} must be {show(schema['const'])}"))

        if isinstance(inst, dict):
            errs += self._object(inst, schema, iptr, depth)
        if isinstance(inst, list):
            errs += self._array(inst, schema, iptr, depth)
        if isinstance(inst, str):
            n = len(inst)
            if "minLength" in schema and n < schema["minLength"]:
                add((iptr, f"string shorter than {schema['minLength']} characters"))
            if "maxLength" in schema and n > schema["maxLength"]:
                add((iptr, f"string longer than {schema['maxLength']} characters"))
            if "pattern" in schema and not compile_ecma(schema["pattern"]).search(inst):
                add((iptr, f"string {show(inst)} does not match the pattern {schema['pattern']!r}"))
        if is_number(inst):
            if "minimum" in schema and inst < schema["minimum"]:
                add((iptr, f"{inst} is below the minimum {schema['minimum']}"))
            if "maximum" in schema and inst > schema["maximum"]:
                add((iptr, f"{inst} is above the maximum {schema['maximum']}"))
            if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
                add((iptr, f"{inst} must be greater than {schema['exclusiveMinimum']}"))
            if "exclusiveMaximum" in schema and inst >= schema["exclusiveMaximum"]:
                add((iptr, f"{inst} must be less than {schema['exclusiveMaximum']}"))

        for sub in schema.get("allOf", []):
            errs += self.errors(inst, sub, iptr, depth + 1)
        if "anyOf" in schema:
            results = [self.errors(inst, sub, iptr, depth + 1) for sub in schema["anyOf"]]
            if all(results):
                add((iptr, "matches none of the anyOf alternatives" + self._closest(results)))
        if "oneOf" in schema:
            results = [self.errors(inst, sub, iptr, depth + 1) for sub in schema["oneOf"]]
            good = [i for i, r in enumerate(results) if not r]
            if not good:
                add((iptr, "matches none of the oneOf alternatives" + self._closest(results)))
            elif len(good) > 1:
                add((iptr, f"matches the oneOf alternatives {good}; exactly one must match"))
        if "not" in schema and not self.errors(inst, schema["not"], iptr, depth + 1):
            add((iptr, "matches the schema under `not`" + self._describe(schema["not"])))
        if "if" in schema:
            if not self.errors(inst, schema["if"], iptr, depth + 1):
                if "then" in schema:
                    errs += self.errors(inst, schema["then"], iptr, depth + 1)
            elif "else" in schema:
                errs += self.errors(inst, schema["else"], iptr, depth + 1)
        return errs

    @staticmethod
    def _describe(s: Any) -> str:
        if isinstance(s, dict) and isinstance(s.get("description"), str):
            return f" ({s['description']})"
        return ""

    @staticmethod
    def _closest(results: list[list[tuple[str, str]]]) -> str:
        i = min(range(len(results)), key=lambda j: len(results[j]))
        shown = "; ".join(f"{p or '/'}: {m}" for p, m in results[i][:3])
        more = f" (+{len(results[i]) - 3} more)" if len(results[i]) > 3 else ""
        return f"; closest is alternative {i}: {shown}{more}"

    def _object(self, inst: dict, schema: dict, iptr: str, depth: int) -> list[tuple[str, str]]:
        errs: list[tuple[str, str]] = []
        for k in schema.get("required", []):
            if k not in inst:
                errs.append((iptr, f"missing required key {k!r}"))
        if "minProperties" in schema and len(inst) < schema["minProperties"]:
            errs.append((iptr, f"fewer than {schema['minProperties']} keys"))
        props = schema.get("properties", {})
        pats = schema.get("patternProperties", {})
        for k, v in inst.items():
            kp = f"{iptr}/{esc(k)}"
            matched = False
            if k in props:
                matched = True
                errs += self.errors(v, props[k], kp, depth + 1)
            for p, sub in pats.items():
                if compile_ecma(p).search(k):
                    matched = True
                    errs += self.errors(v, sub, kp, depth + 1)
            if not matched and "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                if ap is False:
                    known = sorted(props)
                    hint = f" (known: {', '.join(known)})" if known and len(known) <= 30 else ""
                    errs.append((kp, f"unknown key {k!r}{hint}"))
                else:
                    errs += self.errors(v, ap, kp, depth + 1)
            if "propertyNames" in schema:
                for _, m in self.errors(k, schema["propertyNames"], kp, depth + 1):
                    errs.append((kp, f"key {k!r}: {m}"))
        return errs

    def _array(self, inst: list, schema: dict, iptr: str, depth: int) -> list[tuple[str, str]]:
        errs: list[tuple[str, str]] = []
        n = len(inst)
        if "minItems" in schema and n < schema["minItems"]:
            errs.append((iptr, f"fewer than {schema['minItems']} items"))
        if "maxItems" in schema and n > schema["maxItems"]:
            errs.append((iptr, f"more than {schema['maxItems']} items"))
        prefix = schema.get("prefixItems", [])
        for i, sub in enumerate(prefix[:n]):
            errs += self.errors(inst[i], sub, f"{iptr}/{i}", depth + 1)
        if "items" in schema:
            for i in range(len(prefix), n):
                errs += self.errors(inst[i], schema["items"], f"{iptr}/{i}", depth + 1)
        if schema.get("uniqueItems"):
            for i in range(n):
                for j in range(i):
                    if json_equal(inst[i], inst[j]):
                        errs.append((f"{iptr}/{i}", f"item {i} repeats item {j}"))
                        break
        return errs


# --- commands -----------------------------------------------------------------------------------


class Schemas:
    """Schema files, loaded and linted once each."""

    def __init__(self) -> None:
        self.cache: dict[str, tuple[Any, list, str | None]] = {}

    def get(self, path: str) -> tuple[Any, list, str | None]:
        """(root, lint problems, load error)."""
        if path not in self.cache:
            try:
                root = load_instance(path)
                self.cache[path] = (root, lint_schema(root, path), None)
            except LoadError as e:
                self.cache[path] = (None, [], str(e))
        return self.cache[path]


def report_lint(rep: rk.Report, problems, file: str, seen: set) -> int:
    n = 0
    for code, sev, msg, ptr in problems:
        key = (file, code, msg, ptr)
        if key in seen:
            continue
        seen.add(key)
        rep.add(code, f"{file}: {msg}" + (f" at {ptr}" if ptr else ""), sev, path=[ptr], file=file)
        n += sev == "error"
    return n


def validate_into(rep: rk.Report, inst: Any, root: Any, sub: Any, file: str) -> int:
    errs = Validator(root).errors(inst, sub)
    for ptr, msg in errs:
        rep.add("schema-invalid", f"{file}: {ptr or '/'}: {msg}", path=[ptr], file=file)
    return len(errs)


def require_files(cmd: str, files: list[str]) -> None:
    """A FILE that does not exist is a setup failure (a mistyped path, an unmatched glob)."""
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        raise rk.SetupError(f"{cmd}: no such file: {', '.join(missing)}")


def cmd_check(args: list[str]) -> list[rk.Report]:
    sdir = DEFAULT_DIR
    expect: str | None = None
    files: list[str] = []
    it = iter(args)
    for a in it:
        if a in ("--schemas", "--expect"):
            v = next(it, None)
            if v is None:
                raise rk.SetupError(f"check: {a} needs a value")
            if a == "--schemas":
                sdir = v
            else:
                expect = v
        elif a.startswith("--schemas="):
            sdir = a.split("=", 1)[1]
        elif a.startswith("--expect="):
            expect = a.split("=", 1)[1]
        elif a.startswith("--"):
            raise rk.SetupError(f"check: unknown option {a}")
        else:
            files.append(a)
    if not files:
        raise rk.SetupError("check: no files given")
    if not os.path.isdir(sdir):
        raise rk.SetupError(f"check: schema directory {sdir} not found")
    if expect is not None and not FORMAT_RE.fullmatch(expect):
        raise rk.SetupError(f"check: --expect {expect!r} is not reviewkit-<name>/<version>")
    require_files("check", files)
    rep = rk.Report(CHECK)
    if expect is not None:
        rep.data["expect"] = expect
    schemas = Schemas()
    seen: set = set()
    rows = []
    for f in files:
        row: dict[str, Any] = {"file": f, "format": None, "schema": None, "ok": False, "errors": 0}
        rows.append(row)
        try:
            inst = load_instance(f)
        except ReadError as e:
            raise rk.SetupError(f"check: {e}") from None
        except LoadError as e:
            rep.add("schema-parse-error", str(e), file=f)
            row["errors"] = 1
            continue
        fmt = inst.get("format") if isinstance(inst, dict) else None
        if not isinstance(fmt, str):
            what = "is not an object" if not isinstance(inst, dict) else (
                "has no format field" if "format" not in inst else "has a format that is not a string")
            rep.add("schema-missing-format", f"{f} {what}", path=["/format" if isinstance(inst, dict) else ""], file=f)
            row["errors"] = 1
            continue
        row["format"] = fmt
        if expect is not None and fmt != expect:
            rep.add("schema-format-mismatch", f"{f}: format is {fmt!r}, expected {expect!r}",
                    path=["/format"], file=f)
            row["errors"] = 1
            continue
        m = FORMAT_RE.fullmatch(fmt)
        spath = os.path.join(sdir, f"reviewkit-{m.group(1)}-{m.group(2)}.schema.json") if m else None
        if spath is None or not os.path.isfile(spath):
            rep.add("schema-unknown-format",
                    f"{f}: no schema for format {fmt!r}" + (f" (looked for {rel(spath)})" if spath else
                                                          " (not reviewkit-<name>/<version>)"),
                    path=["/format"], file=f)
            row["errors"] = 1
            continue
        row["schema"] = spath
        root, problems, err = schemas.get(spath)
        if err is not None:
            if (spath, "parse") not in seen:
                seen.add((spath, "parse"))
                rep.add("schema-parse-error", err, file=spath)
            row["errors"] = 1
            continue
        if report_lint(rep, problems, spath, seen) or any(p[1] == "error" for p in problems):
            row["errors"] = 1
            continue
        n = validate_into(rep, inst, root, root, f)
        row["errors"] = n
        row["ok"] = n == 0
    rep.data["files"] = rows
    return [rep]


def cmd_validate(args: list[str]) -> list[rk.Report]:
    schema = None
    defname = None
    files: list[str] = []
    it = iter(args)
    for a in it:
        if a in ("--schema", "--def"):
            v = next(it, None)
            if v is None:
                raise rk.SetupError(f"validate: {a} needs a value")
            if a == "--schema":
                schema = v
            else:
                defname = v
        elif a.startswith("--schema="):
            schema = a.split("=", 1)[1]
        elif a.startswith("--def="):
            defname = a.split("=", 1)[1]
        elif a.startswith("--"):
            raise rk.SetupError(f"validate: unknown option {a}")
        else:
            files.append(a)
    if schema is None:
        raise rk.SetupError("validate: --schema is required")
    if not files:
        raise rk.SetupError("validate: no files given")
    try:
        root = load_instance(schema)
    except LoadError as e:
        raise rk.SetupError(f"validate: {e}") from None
    sub = root
    if defname is not None:
        defs = root.get("$defs") if isinstance(root, dict) else None
        if not isinstance(defs, dict) or defname not in defs:
            raise rk.SetupError(f"validate: {schema} has no $defs/{defname}")
        sub = defs[defname]
    rep = rk.Report(CHECK)
    rep.data["schema"] = schema
    if defname is not None:
        rep.data["def"] = defname
    rows = []
    require_files("validate", files)
    if report_lint(rep, lint_schema(root, schema), schema, set()):
        rep.data["files"] = [{"file": f, "ok": False, "errors": None} for f in files]
        return [rep]
    for f in files:
        try:
            inst = load_instance(f)
        except ReadError as e:
            raise rk.SetupError(f"validate: {e}") from None
        except LoadError as e:
            rep.add("schema-parse-error", str(e), file=f)
            rows.append({"file": f, "ok": False, "errors": 1})
            continue
        n = validate_into(rep, inst, root, sub, f)
        rows.append({"file": f, "ok": n == 0, "errors": n})
    rep.data["files"] = rows
    return [rep]


def cmd_lint(args: list[str]) -> list[rk.Report]:
    dirs = [a for a in args if not a.startswith("--")]
    opts = [a for a in args if a.startswith("--")]
    if opts:
        raise rk.SetupError(f"lint: unknown option {opts[0]}")
    if len(dirs) > 1:
        raise rk.SetupError("lint: at most one directory")
    sdir = dirs[0] if dirs else DEFAULT_DIR
    if not os.path.isdir(sdir):
        raise rk.SetupError(f"lint: schema directory {sdir} not found")
    names = sorted(n for n in os.listdir(sdir) if n.endswith(".schema.json"))
    rep = rk.Report(CHECK)
    rep.data["dir"] = sdir
    rows = []
    if not names:
        rep.add("schema-parse-error", f"{sdir}: no *.schema.json files", file=sdir)
    seen: set = set()
    for n in names:
        p = os.path.join(sdir, n)
        before = rep.errors
        try:
            root = load_instance(p)
        except LoadError as e:
            rep.add("schema-parse-error", str(e), file=p)
            rows.append({"file": p, "ok": False})
            continue
        report_lint(rep, lint_schema(root, p) + schema_file_checks(root, n), p, seen)
        rows.append({"file": p, "format": FILE_RE.fullmatch(n) and
                     f"reviewkit-{FILE_RE.fullmatch(n).group(1)}/{FILE_RE.fullmatch(n).group(2)}",
                     "agent_output": isinstance(root, dict) and isinstance(root.get("$defs"), dict)
                     and "output" in root["$defs"],
                     "ok": rep.errors == before})
    rep.data["schemas"] = rows
    return [rep]


COMMANDS = {"check": cmd_check, "validate": cmd_validate, "lint": cmd_lint}


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(__doc__)
        sys.exit(0)
    if not argv:
        sys.exit(rk.emit_setup_error("", "", "no command (expected check, validate or lint)"))
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        sys.exit(rk.emit_setup_error(cmd, "", f"unknown command {cmd!r} (expected check, validate or lint)"))
    try:
        reports = COMMANDS[cmd](rest)
    except rk.SetupError as e:
        sys.exit(rk.emit_setup_error(cmd, "", str(e)))
    sys.exit(rk.emit_reports(cmd, "", reports))


if __name__ == "__main__":
    main()
