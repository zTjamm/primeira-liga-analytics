"""Проверки скрипта обновления сервера.

Скрипт deploy/update_server.sh ломается незаметно: он падает не на своих
ошибках, а на поведении git, и цена отказа — сервер навсегда остаётся на
старом коде, считая данные по прежней модели и показывая их как свежие.

Зафиксированная 31 октября поломка: перед merge скрипт делал
`git checkout HEAD -- <изменённые upstream пути>`. Для файлов, которых в
HEAD ещё нет (upstream их добавляет), checkout ничего не делает, рабочий
файл остаётся неотслеживаемым, и git merge отказывается его затирать:

    error: The following untracked working tree files would be overwritten
    by merge: data/forecast_log_rpl.jsonl

Леджер РФПЛ вёлся на сервере раньше, чем попал в git, — и цикл ушёл в
вечный abort. Поэтому здесь проверяется не «скрипт похож на прежний», а
три инварианта, из-за которых отказ вообще случился.

Запуск:
    python -m tests.test_deploy
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PASS = "\u2713"
FAIL = "\u2717"
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "update_server.sh"


def _check(name: str, got, want) -> bool:
    ok = got == want
    print(f"  [{PASS if ok else FAIL}] {name}"
          + ("" if ok else f"  (получено {got!r}, ждали {want!r})"))
    return ok


def _check_bool(name: str, cond: bool, note: str = "") -> bool:
    print(f"  [{PASS if cond else FAIL}] {name}"
          + (f"  ({note})" if note and not cond else ""))
    return cond


def test_script_exists() -> list[bool]:
    print("Скрипт обновления сервера")
    return [_check_bool("deploy/update_server.sh существует",
                        SCRIPT.exists(), str(SCRIPT))]


def test_untracked_handling() -> list[bool]:
    """Сердце регрессии: неотслеживаемые файлы, которые добавляет
    upstream, обязаны убираться с сохранением копии, а не оставляться
    merge'у на спотык."""
    text = SCRIPT.read_text(encoding="utf-8")
    ok = []

    ok.append(_check_bool(
        "путь проверяется на наличие в HEAD, а не копается вслепую",
        'git cat-file -e "HEAD:' in text,
        "нет ветки для файлов, которых ещё нет в HEAD"))
    ok.append(_check_bool(
        "неотслеживаемый файл убирается перед merge",
        "rm -f \"$f\"" in text,
        "файл остался бы untracked и merge ушёл бы в abort"))
    ok.append(_check_bool(
        "копия убранного файла кладётся в бэкап",
        "$BAK/upstream_new/" in text,
        "при отказе merge файл пропал бы насовсем"))
    ok.append(_check_bool(
        "возврат бэкапа не затирает то, что уже пришло из git",
        'cp -an "$BAK/upstream_new/." . 2>/dev/null' in text,
        "нужен именно -n: иначе старая копия вытеснит свежую из merge"))
    return ok


def test_order_and_safety() -> list[bool]:
    """Бэкап обязан делаться ДО любого отката: откат — это удаление
    данных, а леджер append-only и не восстанавливается ниоткуда."""
    text = SCRIPT.read_text(encoding="utf-8")
    ok = []

    pos_bak = text.find('cp -a data/forecast_log_*.jsonl "$BAK"')
    pos_changed = text.find("CHANGED=$(git diff --name-only HEAD FETCH_HEAD")
    ok.append(_check_bool("бэкап леджера сделан до отката перед merge",
                          0 < pos_bak < pos_changed,
                          f"bакап={pos_bak}, откат={pos_changed}"))

    # Падение шага расчёта не должно доезжать до публикации: сайт
    # обязан показывать прошлый успешный прогон, а не пустоту.
    ok.append(_check_bool(
        "падение шага останавливает цикл",
        'run_step "models.predict" $PY -m models.predict || exit 1' in text))

    # Сервер не должен считать на старом коде, если merge не удался.
    ok.append(_check_bool(
        "при отказе merge код не используется",
        "merge не удался" in text and "restore_bak" in text))

    # nounset ловит опечатки в переменных, pipefail — чтобы отказ git в
    # конвейере не прятался за успешным tail.
    ok.append(_check_bool("включены nounset и pipefail",
                          "set -uo pipefail" in text))
    return ok


def test_syntax() -> list[bool]:
    """bash -n, если bash доступен. На Windows его может не быть — тогда
    проверка честно пропускается, а не считается пройденной молча.

    Перед проверкой переводы строк нормализуются: на checkout с CRLF
    bash воспринимает '\\r' как часть слова, и скрипт выглядел бы сломанным
    там, где он цел."""
    print("\nСинтаксис:")
    bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    if not Path(bash).exists():
        print(f"  [—] bash не найден ({bash}) — пропущено")
        return []
    src = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    fd, tmp = tempfile.mkstemp(suffix=".sh")
    os.close(fd)
    try:
        Path(tmp).write_text(src, encoding="utf-8", newline="\n")
        r = subprocess.run([bash, "-n", tmp],
                           capture_output=True, text=True)
    finally:
        os.unlink(tmp)
    return [_check_bool("bash -n проходит",
                        r.returncode == 0,
                        (r.stderr or r.stdout).strip()[:200])]


def main() -> int:
    results: list[bool] = []
    results += test_script_exists()
    results += test_untracked_handling()
    results += test_order_and_safety()
    results += test_syntax()
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print(f"Провалено {results.count(False)} из {len(results)} проверок.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
