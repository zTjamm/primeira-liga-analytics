"""Проверка, что все Python-файлы репозитория вообще компилируются.

Зачем. Модуль с неверным отступом может не сломать импорт другого
модуля, но упасть в момент запуска — и тогда пайплайн останавливается
на этом шаге, а следующие шаги (в том числе публикация) не
выполняются. В проекте это случилось: models/bt_predict.py был сломан
с 3 ноября, и цикл обновления молча не публиковал данные, пока его
не нашли.

Проверка дешёвая: компилируем байты, а не импортируем — так ловим
синтаксис и отступы, не запуская кода и не тягая данные.

Запуск:
    python -m tests.test_syntax
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "node_modules", ".next", "__pycache__", ".git"}

PASS = "\u2713"
FAIL = "\u2717"


def main() -> int:
    files = sorted(
        p for p in ROOT.rglob("*.py")
        if not any(part in SKIP_DIRS for part in p.parts)
    )
    bad: list[tuple[Path, str]] = []
    for p in files:
        raw = p.read_bytes()
        try:
            compile(raw, str(p), "exec")
        except SyntaxError as exc:
            bad.append((p, f"{exc.msg} (строка {exc.lineno})"))
        except ValueError as exc:
            bad.append((p, str(exc)))

    print("Проверка синтаксиса всех модулей")
    print("-" * 46)
    print(f"  файлов проверено: {len(files)}")
    for p, msg in bad:
        try:
            rel = p.relative_to(ROOT)
        except ValueError:
            rel = p
        print(f"  [{FAIL}] {rel}: {msg}")
    if bad:
        print("-" * 46)
        print(f"Провалено файлов: {len(bad)}.")
        return 1
    print(f"  [{PASS}] все файлы компилируются")
    print("-" * 46)
    print("Все модули в порядке.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
