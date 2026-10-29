#!/usr/bin/env python3
"""Pre-flight check of deploy/.env as Docker Compose reads it. Never prints a value.

`env_file: .env` goes through the dotenv parser of compose-spec/compose-go
(dotenv/parser.go, ported from commit 6ac9323ede62dd526aaf1bc18f07ee1ce3596d42):
comments, `export`, spaces around `=` or `:`, quotes, escapes and the last assignment
winning all follow it. Interpolation ($VAR) and a key without a value take their value
from the server's environment, so for the checked keys they are refused in any
assignment, not guessed. Compose interpolates every assignment in order and stops at an
invalid template or an unset required variable (${VAR?msg}), so those are refused in any
key, even when a later assignment overrides it (template/template.go, same commit).

Rules:
  MCP_BEARER_TOKEN  the rule `pester mcp` enforces at startup: an RFC 6750 token of 32+
                    ASCII letters, digits and -._~+/ (optional trailing =), nothing around
                    it. Caddy compares the header with the same value.
  MCP_DOMAIN        one non-empty word, or Caddy falls back to mcp.example.com.

Usage: python3 deploy/check_env.py deploy/.env      (exit 0 ok, 1 refused)
"""

from __future__ import annotations

import re
import sys

# Same grammar as _BEARER_TOKEN_RE in src/pester/mcp/server.py.
TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/-]{32,}=*")
_EXPORT_RE = re.compile(r"export[\t\n\f\r ]+")  # Go RE2 \s is [\t\n\f\r ]
_ESCAPE_RE = re.compile(r'\\(?:[abcfnrtv$"\\]|0[0-9]{0,3})')
# template.DefaultPattern: $$, $name, ${name}, ${name[:]-+?word}, or an invalid "${".
_TEMPLATE_RE = re.compile(
    r"\$(?:(?P<escaped>\$)|(?P<named>[_a-z][_a-z0-9]*)"
    r"|\{(?:(?P<braced>[_a-z][_a-z0-9]*(?::?[-+?](.*))?)\}|(?P<invalid>)))",
    re.IGNORECASE,
)
_REQUIRED_RE = re.compile(r"[_a-z][_a-z0-9]*:?\?", re.IGNORECASE)
_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
_ESCAPES.update({"\\": "\\", '"': '"'})
# isSpace in parser.go: white space without the line break.
_SPACE = "\t\v\f\r \x85\xa0"
# Go unicode.IsSpace.
_ANY_SPACE = _SPACE + "\n\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
_ANY_SPACE += "\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


class EnvError(Exception):
    """The file does not parse; Compose refuses it too."""


def _expand_escapes(value: str) -> str:
    """expandEscapes: shell escapes in double quotes; `\\$` stays a literal `$$`."""

    def one(match: re.Match[str]) -> str:
        seq = match.group(0)
        if seq == "\\$":
            return "$$"
        if seq.startswith("\\0"):
            # Go rewrites \0NNN to \NNN, then needs three octal digits up to 255;
            # otherwise the rewritten text stays.
            digits = seq[2:]
            if len(digits) == 3 and set(digits) <= set("01234567") and int(digits, 8) < 256:
                return chr(int(digits, 8))
            return "\\" + digits
        return _ESCAPES.get(seq[1], seq)

    return _ESCAPE_RE.sub(one, value)


def _statement_start(src: str) -> str:
    """getStatementStart: skip white space and comment lines."""
    while True:
        src = src.lstrip(_ANY_SPACE)
        if not src.startswith("#"):
            return src
        newline = src.find("\n")
        if newline < 0:
            return ""
        src = src[newline:]


def _key(src: str, line: int) -> tuple[str, str, bool]:
    """locateKeyName: (key, rest, inherited)."""
    if _EXPORT_RE.match(src):
        src = src[len("export") :].lstrip(_SPACE)
    for i, char in enumerate(src):
        if char in _SPACE:
            continue
        if char in "=:\n":
            key, offset, inherited = src[:i], i + 1, char == "\n"
            break
        if char in "_.-[]" or char.isalpha() or char.isnumeric():
            continue
        raise EnvError(f"line {line}: unexpected character in a variable name")
    else:  # the file ends right after the key
        key, offset, inherited = src, len(src), True
    return key.rstrip(_ANY_SPACE), src[offset:].lstrip(_SPACE), inherited


def _template(value: str) -> str:
    """How Compose's interpolation treats a value: literal, interpolated, required
    (fails when the variable is unset on the server) or invalid (always fails)."""
    how = "literal"
    for match in _TEMPLATE_RE.finditer(value):
        if match.group("invalid") is not None:
            return "invalid"
        braced = match.group("braced")
        if braced and _REQUIRED_RE.match(braced):
            how = "required"
        elif how == "literal":
            how = "interpolated"
    return how


def _value(src: str, line: int) -> tuple[str, str, bool]:
    """extractVarValue: (value, rest, interpolated: Compose runs it through templates)."""
    if not src.startswith(("'", '"')):
        value, _, rest = src.partition("\n")
        value = value.split(" #", 1)[0].rstrip(_ANY_SPACE)
        return value, rest, True
    quote, chars, escaped = src[0], [], False
    for i in range(1, len(src)):
        char = src[i]
        if char != quote:
            if not escaped and char == "\\":
                escaped = True
                continue
            if escaped:
                escaped = False
                chars.append("\\")
            chars.append(char)
            continue
        if escaped:
            escaped = False
            chars.append(char)
            continue
        value = "".join(chars)
        if quote == '"':
            value = _expand_escapes(value)
            return value, src[i + 1 :], True
        return value, src[i + 1 :], False
    raise EnvError(f"line {line}: quoted value is not terminated")


def parse(src: str) -> list[tuple[int, str, str, str]]:
    """Every assignment in order as (line, key, value, how); how is literal,
    interpolated, required, invalid (see _template) or inherited (no value)."""
    out: list[tuple[int, str, str, str]] = []
    rest = src
    while True:
        rest = _statement_start(rest)
        if not rest:
            return out
        line = src[: len(src) - len(rest)].count("\n") + 1
        key, rest, inherited = _key(rest, line)
        if not key:
            raise EnvError(f"line {line}: key cannot be empty")
        if " " in key:
            raise EnvError(f"line {line}: key cannot contain a space")
        if inherited:
            out.append((line, key, "", "inherited"))
            continue
        value, rest, interpolated = _value(rest, line)
        out.append((line, key, value, _template(value) if interpolated else "literal"))


def problems(src: str) -> list[str]:
    """Why Compose would hand the containers a bad token or domain; empty when fine."""
    if src.startswith("\ufeff"):
        src = src[1:]
    try:
        assignments = parse(src)
    except EnvError as exc:
        return [f"deploy/.env does not parse the way Docker Compose reads it ({exc})"]
    found = []
    for line, key, _, how in assignments:
        if how == "invalid":
            found.append(
                f"line {line}: {key} has an invalid $ template; Docker Compose stops on it"
            )
        elif how == "required":
            found.append(
                f"line {line}: {key} requires a variable from the server's environment; "
                "Docker Compose stops when it is unset"
            )
        elif key in ("MCP_BEARER_TOKEN", "MCP_DOMAIN") and how != "literal":
            found.append(f"line {line}: write {key} literally (no $ interpolation, no bare key)")
    env = {key: (value, how) for _, key, value, how in assignments}
    value, how = env.get("MCP_BEARER_TOKEN", ("", "missing"))
    if how == "missing":
        found.append("MCP_BEARER_TOKEN is not set in deploy/.env")
    elif how == "inherited":
        found.append("MCP_BEARER_TOKEN has no value in deploy/.env (it would come from the server)")
    elif how == "interpolated":
        found.append("MCP_BEARER_TOKEN uses $ interpolation; write the token itself")
    elif not TOKEN_RE.fullmatch(value):
        found.append(
            "MCP_BEARER_TOKEN in deploy/.env must be 32+ characters of A-Z a-z 0-9 -._~+/ "
            "(RFC 6750) with nothing around it. Generate one: openssl rand -hex 32"
        )
    value, how = env.get("MCP_DOMAIN", ("", "missing"))
    if how != "literal" or not re.fullmatch(r"\S+", value):
        found.append(
            "MCP_DOMAIN missing, empty or not a plain value in deploy/.env. It must be a domain "
            "you control with an A-record pointing to the VPS; without it Caddy uses "
            "'mcp.example.com' and TLS provisioning fails."
        )
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Usage: python3 deploy/check_env.py deploy/.env", file=sys.stderr)
        return 1
    try:
        with open(argv[0], "rb") as handle:
            src = handle.read().decode("utf-8")
    except FileNotFoundError:
        print(f"ERROR: {argv[0]} not found. cp deploy/.env.example deploy/.env and fill it in.")
        return 1
    except (OSError, UnicodeDecodeError):
        print(f"ERROR: {argv[0]} is not readable UTF-8 text.")
        return 1
    found = problems(src)
    for problem in found:
        print(f"ERROR: {problem}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
