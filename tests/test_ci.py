"""Проверка пайплайнов GitHub Actions.

Мотивация простая: workflow уже один раз молча сломался. Правки скриптом
вставили шаги с отступом 4 пробела вместо 6, и GitHub Actions отвергал файл
на разборе — все прогоны падали за 0 секунд. Признак заметный, но только
если смотреть в интерфейс Actions: локальные тесты были зелёные, данные на
сайте просто перестали обновляться, и это выглядело как «сломался open-meteo».

Поэтому YAML разбирается здесь же, на каждом запуске тестов.

Проверяется не только синтаксис: пайплайн может быть валиден, но шаг без
run или без имени не выполнится, и об этом тоже лучше узнать сразу.

Проверяются ВСЕ workflow'и в .github/workflows, а не только основной:
файл с опечаткой в отступе роняет все прогоны этого workflow, и заметить
это можно только по интерфейсу Actions.

Запуск:
    python -m tests.test_ci
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS_DIR / "pipeline.yml"

PASS = "\u2713"
FAIL = "\u2717"

# Шаги, без которых пайплайн теряет смысл: без сбора данных нечего
# публиковать, без публикации сайт не обновляется.
REQUIRED_STEPS = (
    "Собрать данные и обновить расписание",
    "Собрать Единую лигу ВТБ",
    "Прогнозы и журнал",
    "Бэктесты",
    "Опубликовать артефакты",
    "Опубликовать артефакты в репозиторий",
)

# Ключи верхнего уровня, которые GitHub Actions понимает. Любой другой
# ключ делает файл невалидным: Actions отвергает весь workflow, YAML —
# нет.
ALLOWED_TOP = {"name", "on", True, "env", "defaults", "concurrency",
               "permissions", "jobs", "run-name"}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  {detail}" if not ok and detail else ""))
    return ok


def _check_workflow(path: Path) -> bool:
    """Общая проверка любого workflow: разбор, ключи, job, шаги."""
    print(f"\n{path.name}")
    raw = path.read_text(encoding="utf-8")

    try:
        doc = yaml.safe_load(raw)
        print(f"  [{PASS}] YAML разбирается")
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"строка {mark.line + 1}" if mark else ""
        print(f"  [{FAIL}] YAML не разбирается  {where}")
        print(f"         {getattr(exc, 'problem', exc)}")
        return False

    ok = True
    jobs = doc.get("jobs") or {}
    ok &= _check("есть хотя бы один job", bool(jobs))

    unknown = [k for k in doc if k not in ALLOWED_TOP]
    ok &= _check("нет лишних ключей верхнего уровня", not unknown,
                 "лишние: " + ", ".join(map(str, unknown[:4])))

    for name, job in jobs.items():
        steps = job.get("steps") or []
        ok &= _check(f"в job «{name}» есть шаги", bool(steps))

        # Шаг без run и без uses не выполнится. Имя при этом необязательно:
        # у шагов вида uses: actions/checkout GitHub подставляет его сам, и
        # требовать имя там — ложная ошибка.
        broken = []
        for i, st in enumerate(steps):
            if not (st.get("run") or st.get("uses")):
                broken.append(f"шаг {i + 1} без run и без uses")
        ok &= _check(f"в job «{name}» у всех шагов есть run или uses",
                     not broken, "; ".join(broken[:3]))

    return ok


def main() -> int:
    print("Проверка пайплайнов\n" + "-" * 46)

    if not WORKFLOW.exists():
        print(f"  [{FAIL}] файла нет: {WORKFLOW}")
        return 1

    files = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    if not files:
        print(f"  [{FAIL}] в {WORKFLOWS_DIR} нет ни одного workflow")
        return 1

    ok = True
    for path in files:
        ok &= _check_workflow(path)

    # Обязательные шаги основного пайплайна — иначе он зелёный, а сайт мёртв.
    raw = WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    job = next(iter((doc.get("jobs") or {}).values()))
    steps = job.get("steps") or []
    names = [st.get("name") or "" for st in steps]
    missing = [n for n in REQUIRED_STEPS if n not in names]
    ok &= _check("обязательные шаги основного пайплайна на месте", not missing,
                 "нет: " + ", ".join(missing[:3]))

    # Отступы: YAML это ловит, но полезно показать заранее, где именно.
    bad_indent = []
    for i, line in enumerate(raw.split("\n"), 1):
        if re.match(r"^ {1,5}- name:", line) and i > 40:
            bad_indent.append(i)
    ok &= _check("отступы шагов ровные", not bad_indent,
                 f"подозрительные строки: {bad_indent[:5]}")

    print("-" * 46)
    if ok:
        print("Пайплайны в порядке.")
        return 0
    print("Есть проблемы с пайплайнами.")
    return 1


import re  # noqa: E402 — используется в check выше, импорт внизу для ясности


if __name__ == "__main__":
    sys.exit(main())
