"""Проверки качества данных.

Лучше упасть на этапе пайплайна с понятным сообщением, чем месяц считать
модель на мусоре. Каждая проверка возвращает строку отчёта.
"""
from __future__ import annotations

import polars as pl


def report(matches: pl.DataFrame) -> list[dict]:
    rows: list[dict] = []

    def add(check: str, ok: bool, detail: str = "") -> None:
        rows.append({"check": check, "ok": ok, "detail": detail})

    # Дубликаты
    dupes = matches.filter(pl.col("match_id").is_duplicated())
    add("нет дублей match_id", dupes.height == 0, f"{dupes.height} дублей")

    # Счёт согласован с FTR
    bad_ftr = matches.filter(
        pl.when(pl.col("fthg") > pl.col("ftag")).then(pl.col("ftr") != "H")
        .when(pl.col("fthg") < pl.col("ftag")).then(pl.col("ftr") != "A")
        .otherwise(pl.col("ftr") != "D")
    )
    add("FTR согласован со счётом", bad_ftr.height == 0, f"{bad_ftr.height} расхождений")

    # Нет самоигра
    add("нет матчей команды с самой собой", matches.filter(pl.col("home_id") == pl.col("away_id")).height == 0)

    # Неправдоподобные тоталы
    add("тотал в пределах 0..25",
        matches.filter((pl.col("fthg") + pl.col("ftag")) > 25).height == 0)

    # xG согласован со счётом (мягкая проверка: расхождение больше 6 — подозрительно)
    with_xg = matches.filter(pl.col("xg_home").is_not_null() & pl.col("xg_away").is_not_null())
    if with_xg.height:
        extreme = with_xg.filter(
            ((pl.col("xg_home") - pl.col("fthg")).abs() > 6) | ((pl.col("xg_away") - pl.col("ftag")).abs() > 6)
        )
        add("xG не расходится со счётом", extreme.height == 0, f"{extreme.height} из {with_xg.height}")

    # Статистика в разумных пределах
    st = matches.filter(pl.col("shots_home").is_not_null())
    if st.height:
        bad = st.filter((pl.col("shots_home") > 45) | (pl.col("shots_away") > 45))
        add("удары выглядят правдоподобно", bad.height == 0, f"{bad.height} из {st.height}")

    # Коэффициенты: обратная маржа должна быть разумной
    cl = matches.filter(pl.col("odds_close").list.len() == 3)
    if cl.height:
        margin = (
            cl.select(pl.col("odds_close"))
            .with_columns(
                pl.col("odds_close").list.eval(pl.element().cast(pl.Float64))
                .map_elements(lambda o: 1 / o[0] + 1 / o[1] + 1 / o[2], return_dtype=pl.Float64)
                .alias("overround")
            )
            .select("overround")
        )
        m = margin["overround"]
        add("оверраунд коэффициентов в 1.0..1.20",
            bool(m.min() >= 0.99 and m.max() <= 1.25),
            f"min={m.min():.3f} max={m.max():.3f} mean={m.mean():.3f}")

    return rows


def coverage(matches: pl.DataFrame) -> pl.DataFrame:
    """Покрытие данных по сезонам — чтобы видеть, чего хватает."""
    return (
        matches.group_by("season")
        .agg(
            pl.len().alias("matches"),
            pl.col("xg_home").is_not_null().sum().alias("xg"),
            pl.col("shots_home").is_not_null().sum().alias("shots"),
            pl.col("has_closing").sum().alias("closing"),
            pl.col("source").eq("api").sum().alias("from_api"),
        )
        .sort("season")
    )
