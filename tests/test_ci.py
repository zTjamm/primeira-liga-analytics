"""Проверка пайплайна GitHub Actions.

Мотивация простая: workflow уже один раз молча сломался. Правки скриптом
вставили шаги с отступом 4 пробела вместо 6, и GitHub Actions отвергал файл
на разборе — все прогоны падали за 0 секунд. Признак заметный, но только
если смотреть в интерфейс Actions: локальные тесты были зелёные, данные на
сайте просто перестали обновляться, и это выглядело как «сломался open-meteo».

Поэтому YAML разбирается здесь же, на каждом запуске тестов.

Проверяется не только синтаксис: пайплайн может быть валиден, но шаг без
run или без имени не выполнится, и об этом тоже лучше узнать сразу.

Запуск:
    python -m tests.test_ci
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "pipeline.yml"

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


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  {detail}" if not ok and detail else ""))
    return ok


def main() -> int:
    print("Проверка пайплайна\n" + "-" * 46)

    if not WORKFLOW.exists():
        print(f"  [{FAIL}] файла нет: {WORKFLOW}")
        return 1

    raw = WORKFLOW.read_text(encoding="utf-8")

    ok = True

    # 1. Разбор YAML. Собщение об ошибке указывает строку — этого хватает,
    #    чтобы чинить, не открывая файл целиком.
    try:
        doc = yaml.safe_load(raw)
        print(f"  [{PASS}] YAML разбирается")
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"строка {mark.line + 1}" if mark else ""
        print(f"  [{FAIL}] YAML не разбирается  {where}")
        print(f"         {getattr(exc, 'problem', exc)}")
        return 1

    jobs = doc.get("jobs") or {}
    ok &= _check("есть хотя бы один job", bool(jobs))

    # 5. Ключи верхнего уровня: GitHub Actions отвергает файл при любом
    #    неизвестном ключе, а YAML — нет. Именно так в env заехала
    #    FINISHED_HOURS без отступа: файл разбирался, прогоны падали за
    #    0 секунд, и причина была неочевидна.
    allowed = {"name", "on", True, "env", "defaults", "concurrency",
               "permissions", "jobs", "run-name"}
    unknown = [k for k in doc if k not in allowed]
    ok &= _check("нет лишних ключей верхнего уровня", not unknown,
                 "лишние: " + ", ".join(map(str, unknown[:4])))

    # 6. Переменные окружения должны быть именно в env, а не на верхнем
    #    уровне: иначе ${ПЕРЕМЕННАЯ} в скриптах подставится пустотой и
    #    арифметика в gate молча даст неверный результат.
    if isinstance(doc.get("env"), dict):
        ok &= _check("в env есть WINDOW_HOURS и FINISHED_HOURS",
                     {"WINDOW_HOURS", "FINISHED_HOURS"} <= set(doc["env"]),
                     "в env: " + ", ".join(sorted(doc["env"])))
    else:
        ok &= _check("блок env разбирается как объект", False)

    if not jobs:
        return 1

    job = next(iter(jobs.values()))
    steps = job.get("steps") or []
    ok &= _check("в job есть шаги", bool(steps))

    # 2. Шаг без run и без uses не выполнится. Имя при этом необязательно:
    #    у шагов вида uses: actions/checkout GitHub подставляет его сам, и
    #    требовать имя там — ложная ошибка.
    broken = []
    for i, st in enumerate(steps):
        if not (st.get("run") or st.get("uses")):
            broken.append(f"шаг {i + 1} без run и без uses")
    ok &= _check("у всех шагов есть run или uses", not broken,
                 "; ".join(broken[:3]))

    # 3. Обязательные шаги на месте — иначе пайплайн зелёный, а сайт мёртв.
    names = [st.get("name") or "" for st in steps]
    missing = [n for n in REQUIRED_STEPS if n not in names]
    ok &= _check("обязательные шаги на месте", not missing,
                 "нет: " + ", ".join(missing[:3]))

    # 4. Отступы: YAML это ловит, но полезно показать заранее, где именно.
    bad_indent = []
    for i, line in enumerate(raw.split("\n"), 1):
        if re.match(r"^ {1,5}- name:", line) and i > 40:
            bad_indent.append(i)
    ok &= _check("отступы шагов ровные", not bad_indent,
                 f"подозрительные строки: {bad_indent[:5]}")

    print("-" * 46)
    if ok:
        print("Пайплайн в порядке.")
        return 0
    print("Есть проблемы с пайплайном.")
    return 1


import re  # noqa: E402 — используется в check выше, импорт внизу для ясности


if __name__ == "__main__":
    sys.exit(main())