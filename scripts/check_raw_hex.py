#!/usr/bin/env python3
"""CI-заготовка: ищет сырые hex-цвета в CSS вне var(--token, #fallback).

Использование:
    python scripts/check_raw_hex.py
    python scripts/check_raw_hex.py static/css/app.css static/css/viewer.css

Exit code 1, если найдены «сырые» hex-цвета.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_FILES = (
    Path('static/css/app.css'),
    Path('static/css/viewer.css'),
    Path('static/css/redesign.css'),
)
HEX_RE = re.compile(r'#[0-9a-fA-F]{3,8}\b')
VAR_RE = re.compile(r'var\(')
COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)


def _strip_comments(text: str) -> str:
    def blank(match: re.Match) -> str:
        return re.sub(r'[^\n]', ' ', match.group(0))

    return COMMENT_RE.sub(blank, text)


def _var_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in VAR_RE.finditer(text):
        depth = 1
        index = match.end()
        while index < len(text) and depth:
            char = text[index]
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            index += 1
        spans.append((match.start(), index))
    return spans


def check_file(path: Path) -> list[str]:
    text = _strip_comments(path.read_text(encoding='utf-8'))
    spans = _var_spans(text)
    problems = []
    for match in HEX_RE.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        line = text.count('\n', 0, match.start()) + 1
        line_start = text.rfind('\n', 0, match.start()) + 1
        line_end = text.find('\n', match.start())
        if line_end == -1:
            line_end = len(text)
        snippet = text[line_start:line_end].strip()
        problems.append(f'{path}:{line}: {match.group(0)}  {snippet}')
    return problems


def main() -> int:
    paths = [Path(arg) for arg in sys.argv[1:]] or list(DEFAULT_FILES)
    problems = []
    for path in paths:
        if not path.exists():
            print(f'{path}: file not found')
            return 2
        problems.extend(check_file(path))

    if not problems:
        print('OK: raw hex colors not found outside var() fallbacks')
        return 0

    for problem in problems:
        print(problem)
    print(f'Found {len(problems)} raw hex color(s) outside var() fallbacks')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
