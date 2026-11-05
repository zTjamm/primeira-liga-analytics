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
# Скрипты запуска витрины и обёртка обновления. Жили только на сервере —
# и выравнивание сервера по GitHub (git reset --hard) их удаляло.
# pm2 запускает витрину именно через web/run.sh, поэтому после такой
# потери процесс уходил в «errored», а причина была видна только в
# /root/.pm2/logs/liga-analytics-error.log. Теперь они в репозитории,
# и проверка ниже не даст им исчезнуть снова.
RUN_SCRIPT = ROOT / "web" / "run.sh"
UPDATE_WRAPPER = ROOT / "update.sh"
SHELL_SCRIPTS = (SCRIPT, RUN_SCRIPT, UPDATE_WRAPPER)


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

    # Отступ 5 ноября 2026: сервер вёл update.sh и web/run.sh как
    # неотслеживаемые файлы (они жили только на сервере), а коммит с
    # ними в GitHub ронял merge. Скрипт разбирался с untracked только
    # внутри data/ и web/public/data/, поэтому код не был защищён.
    ok.append(_check_bool(
        "upstream разбирается по всему дереву, а не только по данным",
        "TOUCHED=$(git diff --name-only HEAD FETCH_HEAD)" in text
        and "NEWFILES=$(git diff --name-only --diff-filter=A HEAD FETCH_HEAD)" in text,
        "список ограничен data/ — новый файл в коде снова уронит merge"))
    ok.append(_check_bool(
        "неотслеживаемый файл проверяется на отслеживаемость",
        "git ls-files --error-unmatch" in text,
        "иначе откат затронет уже отслеживаемые файлы"))
    return ok


def test_order_and_safety() -> list[bool]:
    """Бэкап обязан делаться ДО любого отката: откат — это удаление
    данных, а леджер append-only и не восстанавливается ниоткуда."""
    text = SCRIPT.read_text(encoding="utf-8")
    ok = []

    pos_bak = text.find('cp -a data/forecast_log_*.jsonl "$BAK"')
    # Откат — любое `git checkout HEAD --` по путям, которых касается
    # upstream. Ищем по смыслу, а не по имени переменной: переименование
    # не должно ронять проверку, а вот откат после бэкапа — должен.
    pos_changed = text.find('git checkout HEAD -- "$f"')
    ok.append(_check_bool("бэкап леджера сделан до отката перед merge",
                          0 < pos_bak < pos_changed,
                          f"бэкап={pos_bak}, откат={pos_changed}"))

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


def test_runtime_scripts_exist() -> list[bool]:
    """Точка входа pm2 и обёртка обновления обязаны лежать в репозитории."""
    print("\nСкрипты запуска:")
    ok = []
    ok.append(_check_bool("web/run.sh существует (pm2 запускает витрину через него)",
                          RUN_SCRIPT.exists(), str(RUN_SCRIPT)))
    ok.append(_check_bool("update.sh существует (обёртка deploy/update_server.sh)",
                          UPDATE_WRAPPER.exists(), str(UPDATE_WRAPPER)))
    if RUN_SCRIPT.exists():
        text = RUN_SCRIPT.read_text(encoding="utf-8")
        ok.append(_check_bool("web/run.sh запускает next на порту 8080",
                              "next start -p 8080" in text))
        ok.append(_check_bool("web/run.sh ограничивает кучу node",
                              "--max-old-space-size" in text))
    if UPDATE_WRAPPER.exists():
        text = UPDATE_WRAPPER.read_text(encoding="utf-8")
        ok.append(_check_bool("update.sh зовёт deploy/update_server.sh",
                              "deploy/update_server.sh" in text))
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
    ok = []
    for script in SHELL_SCRIPTS:
        if not script.exists():
            ok.append(_check_bool(f"bash -n проходит: {script.name}",
                                  False, "файла нет"))
            continue
        src = script.read_text(encoding="utf-8").replace("\r\n", "\n")
        fd, tmp = tempfile.mkstemp(suffix=".sh")
        os.close(fd)
        try:
            Path(tmp).write_text(src, encoding="utf-8", newline="\n")
            r = subprocess.run([bash, "-n", tmp],
                               capture_output=True, text=True)
        finally:
            os.unlink(tmp)
        ok.append(_check_bool(f"bash -n проходит: {script.name}",
                              r.returncode == 0,
                              (r.stderr or r.stdout).strip()[:200]))
    return ok


def test_web_rebuild() -> list[bool]:
    """Витрина должна пересобираться, когда изменился код.

    Молчаливая поломка: страницы читают JSON при каждом запросе, поэтому
    свежие данные видны сразу — и на этом основании сборку решили не
    трогать. Но правка React-компонента попадает на сайт только после
    `npm run build`. Без пересборки сайт отвечает 200 и показывает
    старый код: заметить это можно только сравнив страницу с GitHub.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    ok = []

    ok.append(_check_bool(
        "сравнивается собранный коммит с текущим",
        ".built_commit" in text and "git rev-parse HEAD" in text,
        "без метки сборка не поймёт, что её надо повторить"))
    ok.append(_check_bool(
        "сборка запускается через npm run build",
        "npm run build" in text))
    ok.append(_check_bool(
        "после сборки pm2 перезапускается",
        "pm2 restart liga-analytics" in text,
        "иначе процесс продолжит отдавать старый билд"))
    ok.append(_check_bool(
        "упавшая сборка не оставляет витрину без билеа",
        "web/.next.prev" in text and "mv web/.next.prev web/.next" in text,
        "next build пишет прямо в .next — неудача убила бы и старый билд"))
    ok.append(_check_bool(
        "метка собранного коммита записывается после сборки",
        "> web/.next/.built_commit" in text,
        "иначе сборка повторялась бы каждый прогон"))
    ok.append(_check_bool(
        "сборка не запускается при нехватке памяти",
        "AVAIL_MB" in text and "-lt 700" in text,
        "на сервере 1.9 Ги RAM рядом с xray: OOM-killer не разбирает, "
        "кого убивать"))
    return ok


def test_self_update() -> list[bool]:
    """Скрипт должен уметь обновить сам себя.

    bash разбирает весь блок `{ ... }` до выполнения. Если merge меняет
    сам update_server.sh, до конца цикла работает старая версия — то
    есть защита merge не может спасти саму себя: исправленный скрипт
    вступает в силу только на следующем прогоне. Именно так 5 ноября
    2026 merge с исправлением упал на старом скрипте.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    ok = []

    ok.append(_check_bool(
        "контрольная сумма скрипта снимается до merge",
        "SELF_SUM_BEFORE=$(md5sum" in text))
    ok.append(_check_bool(
        "после merge сумма сверяется заново",
        "SELF_SUM_AFTER=$(md5sum" in text))
    ok.append(_check_bool(
        "на новой версии цикл продолжается заново",
        'exec /usr/bin/bash "$SELF"' in text,
        "иначе исправление вступит в силу только через пять минут"))
    ok.append(_check_bool(
        "замок отпускается перед перезапуском",
        "exec 9>&-" in text,
        "наследованный дескриптор удержал бы замок, и новый запуск "
        "молча пропустил бы цикл"))
    ok.append(_check_bool(
        "перезапуск происходит после возврата данных",
        text.find("restore_bak || exit 1") < text.find('exec /usr/bin/bash "$SELF"'),
        "перезапуск раньше restore_bak оставил бы леджеры откаченными"))

    # Перезапускаться можно только на заведомо рабочем скрипте.
    ok.append(_check_bool(
        "перед перезапуском скрипт проверяется на синтаксис",
        'bash -n "$SELF"' in text,
        "иначе сломанный merge обрушит и следующий прогон"))

    # Длительность цикла должна считаться с первого запуска: при
    # перезапуске на новой версии это уже второй процесс, и без
    # экспорта времени каждый из них считал бы только свой кусок.
    ok.append(_check_bool(
        "время старта цикла экспортируется",
        "export CYCLE_START=" in text,
        "иначе при перезапуске длительность считалась бы дважды"))
    ok.append(_check_bool(
        "длительность цикла попадает в лог",
        "-- цикл занял" in text))
    ok.append(_check_bool(
        "длинный цикл вызывает предупреждение",
        "-gt 240" in text,
        "таймер запускает цикл каждые пять минут: длинный цикл "
        "пропускает следующий запуск, и это видно только по логу"))
    return ok


def main() -> int:
    results: list[bool] = []
    results += test_script_exists()
    results += test_untracked_handling()
    results += test_order_and_safety()
    results += test_web_rebuild()
    results += test_self_update()
    results += test_runtime_scripts_exist()
    results += test_syntax()
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print(f"Провалено {results.count(False)} из {len(results)} проверок.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
