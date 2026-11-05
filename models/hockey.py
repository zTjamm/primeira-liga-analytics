"""Модель НХЛ: пуассоновские скорости голов, овертайм и буллиты.

ЗАЧЕМ ОТДЕЛЬНАЯ МОДЕЛЬ, ЕСЛИ ЕСТЬ BasketballModel
--------------------------------------------------
Баскетбольная модель — двумерная нормальная на (разница, тотал). Для
хоккея она даёт систематическую ошибку, и ошибка не в подгонке, а в
семействе распределений:

* разница в шайбах описывается нормалью с σ ≈ 2.6, а из нормали с
  непрерывной поправкой P(ничья) ≈ 15%;
* в реальности ничьих в ИТОГОВОМ счёте НХЛ не бывает вообще: за
  девяти сезонов регулярного чемпионата их ровно ноль (счёт — в data/reports/nhl_ingest.json, поле draws).

Эта 15%-масса — артефакт нормального приближения (разность двух
пуассонов — «Скеллам» — дискретное распределение с ненулевым пиком в
нуле). Выбрасывать её приходится, а это ломает калибровку: оставшиеся
85% приходится перераспределять, и модель перестаёт показывать
разницу между матчами.

СТРУКТУРА МАТЧА — ГЛАВНОЕ, ЧТО ЗДЕСЬ СДЕЛАНО
----------------------------------------------
Матч НХЛ состоит из трёх частей, и каждая устроена не так, как
кажется по умолчанию:

1. ОСНОВНОЕ ВРЕМЯ (60 минут, 5 на 5). Счёт — пуассоновский счётчик:
   λ_дома и λ_в_гостях, независимые (с поправкой Диксона-Коулса на
   низкие счёты, ρ).

2. ОВЕРТАЙМ (5 минут, 3 на 3) играется ТОЛЬКО при ничьей после
   основного времени. И он заканчивается на ПЕРВОЙ шайбе: если
   забил хозяин — итог (r+1 : r), если гости — (r : r+1). Второй гол
   в овертайме невозможен, поэтому «пуассоновская свёртка» овертайма
   (как было в первой версии этой модели) неверна структурно: она
   разрешает обоим командам забить в овертайме.

3. БУЛЛИТЫ. Если за пять минут никто не забил, игра уходит в
   буллитную серию. Победителя определяет жребий, и модель делит эту
   массу поровну между хозяевами и гостями: в буллитных сериях
   преимущество хозяев статистически неотличимо от нуля, а выдумывать
   его нельзя.

ОТКУДА БЕРЁТСЯ ПАРАМЕТР ОВЕРТАЙМА
---------------------------------
По ИТОГОВОМУ счёту параметры овертайма не идентифицируются. Итог
(2:1) одинаков и для «2:2 в основное время, потом победная шайба
хозяев в овертайме», и для «2:2 в основном время, потом буллиты, где
выиграли хозяева». Итоговый счёт различает только ИМЯ победителя, но
не то, ЧЕМ он победил.

Поэтому модель обучается на разбивке по периодам (etl/
fetch_nhl_periods.py): для каждого матча известно, каким был счёт
после основного времени, был ли овертайм и была ли буллитная серия.
Это и делает структуру честной: доля буллитных серий в модели
измеряется, а не предполагается.

ЧЕГО ЭТО ДАЁТ ПО СУТИ
---------------------
* Ничья появляется ровно там, где она возможна в принципе, — в
  буллитной серии, и её доля близка к реальной (несколько процентов),
  а не 15%.
* Овертайм добавляет в распределение разницы именно те «лишние»
  исходы с разницей в одну шайбу, которых у нормали нет: победная
  шайба в овертайме даёт победу 2:1 или 3:2, и модель, которая это
  умеет, точнее оценивает близкие матчи.
* Скорости голов трактуются буквально: «λ = 3.0 шайбы за игру» —
  это понятная величина, которую можно проверить по статистике
  лиги, а не абстрактная σ.

ПОЧЕМУ ПОДГОНКА ДВЕХЭТАПНАЯ
---------------------------
Прямая оптимизация всей модели (силы команд + базы + овертайм +
поправка DC) численным методом по 80+ параметрам работает плохо:
функция правдоподобия плоская по многим направлениям, а численный
градиент требует пересчёта функции 80+ раз на итерацию. На практике
это давало то, что при одном и том же априоре модель то выдавала
λ_дома = 3.3, то λ_дома = 1.9 — а это уже разные модели, и выбор
между ними по отложенным данным становится подгонкой.

Поэтому подгонка разделена:

  Этап 1. Силы команд и базы — ridge-пуассоновская регрессия
          (взвешенные IRLS). Задача выпуклая, решается
          детерминированно и всегда сходится.

  Этап 2. Глобальные параметры (базы, темп овертайма, поправка DC) —
          численно, но уже всего четыре числа, при зафиксированных
          силах команд. Здесь поверхность гладкая и оптимизация
          устойчивая.

Запуск:
    python -m models.nhl_compare   # сравнение с текущей моделью
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import log

import numpy as np
import polars as pl
from scipy.optimize import minimize
from scipy.stats import poisson

from etl.config import PROCESSED, REPORTS

# Сколько голов держим в распределении счёта.
#
# 12 — с запасом: максимум в данных 11 шайб, а хвост за этой границей
# весит порядка 1e-8 и на метрики не влияет.
MAX_GOALS = 12

# Затухание весов по давноте.
#
# Как и в баскетболе — 730 дней: сила команды в хоккее меняется
# медленно (обмены, тренерский штаб, возраст), и гасить прошлый
# сезон наполовину за полгода противоречит данным.
HALF_LIFE_DAYS = 730.0

# Априорная сила параметров команд.
#
# Это не «коэффициент переобучения» в обычном смысле, а априорное
# распределение силы команды: strength_scale — типичное отклонение
# логарифма скорости голов команды от средней по лиге. 0.25 означает
# «сильная команда забивает примерно на 28% больше слабой».
#
# Почему нужен ненулевой априор: у команды в обучающем окне десятки
# матчей, и её оценка атаки шумит с дисперсией порядка 1/n. Без
# априора эти шумовые выбросы попадают прямо в прогноз.
#
# Значение подбирается walk-forward в models/nhl_compare.py.
DEFAULT_STRENGTH_SCALE = 0.25

# Длительность регулярного времени и овертайма, минуты.
REG_MINUTES = 60.0
OT_MINUTES = 5.0

# Границы параметров.
MAX_ATTACK = 1.5          # log: e^1.5 ≈ 4.5-кратная разница в темпе
MIN_BASE_RATE = 0.8       # шайб за игру в основное время
MAX_BASE_RATE = 5.0
# Темп гола в овертайме (3 на 3) в разах к основному времени.
#
# Верхняя граница generous: в овертайме на 3 на 3 забивают в разы
# чаще, и по данным разбивки по периодам реальный множитель около 5-7.
MIN_OT_MULTIPLIER = 0.2
MAX_OT_MULTIPLIER = 20.0
MAX_RHO = 0.15            # поправка Диксона-Коулса на низкие счёты

# Поправка на избыток ничьих (κ).
#
# Наблюдение из данных: ничья после основного времени случается в 22%
# матчей, а произведение двух пуассонов с тем же средним даёт только
# 16.8%. Разница не объясняется ни маргиналом (он пуассоновский), ни
# корреляцией команд (она отрицательная). Механизм такой: при заданном
# общем числе голов команды делят их ПОРОВНУ ЛУЧШЕ, чем даёт
# биномиальная модель (в 1.1-1.34 раза чаще ничья при T=4, 6, 8) —
# это «общий темп» матча, когда обе команды забивают больше в
# одно и то же время.
#
# Поправка вводится так, чтобы масса сохранялась: ячейки ничьих
# (r:r) умножаются на (1+κ), а остальные — на (1 − κ·S_diag/S_off),
# где S_diag и S_off — доли диагональных и прочих ячеек до поправки.
# Тогда общая масса не меняется, а модель может подвинуть её в ничьи.
#
# Диапазон: κ < 0 — «анти-ничьи» (разреженное распределение), κ > 0 —
# избыток ничьих. Значение подбирается walk-forward.
MAX_TIE_BOOST = 4.0
MIN_TIE_BOOST = -0.9
# Нижняя граница множителя для ячеек «не ничья»: нужна, чтобы при
# слишком большом κ вероятности не стали отрицательными.
MIN_OFF_FACTOR = 0.02

# Гиперпараметры прода: выбраны walk-forward в models/nhl_compare.py
# (средний log-loss на старых отложенных сезонах).
SELECTED_STRENGTH_SCALE = 0.12
SELECTED_HALF_LIFE_DAYS = 365.0


def production_config() -> tuple[float, float]:
    """(априор силы команды, горизонт затухания) для прода.

    Значения читаются из отчёта сравнения моделей
    (data/reports/nhl_model_compare.json), чтобы скрипты прогноза,
    backtest и калибровки не разошлись в гиперпараметрах. Если отчёта
    нет — берутся зашитые значения, выбранные тем же walk-forward.
    """
    path = REPORTS / "nhl_model_compare.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sel = (data.get("selection") or {}).get("hockey_ot") or {}
        scale = float(sel.get("strength_scale", SELECTED_STRENGTH_SCALE))
        half = float(sel.get("half_life_days", SELECTED_HALF_LIFE_DAYS))
        return scale, half
    except (OSError, ValueError, TypeError):
        return SELECTED_STRENGTH_SCALE, SELECTED_HALF_LIFE_DAYS


def production_tie_boost() -> bool:
    """Применять ли поправку на избыток ничьих (κ) в проде.

    Решение принимает models/nhl_compare.py: поправка принимается, если
    она не ухудшает walk-forward log-loss и делает долю ничьих в
    основном времени ближе к наблюдаемой. Ответ читается из отчёта
    (data/reports/nhl_model_compare.json), чтобы прода и проверка
    считались одним и тем же.

    Если отчёта нет — поправка НЕ применяется: лишний параметр без
    зафиксированного решения по нему в проде не должен появляться.
    """
    path = REPORTS / "nhl_model_compare.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("adopt_kappa", False))
    except (OSError, ValueError, TypeError):
        return False


def load_periods() -> dict[str, dict]:
    """Разбивка матчей по периодам: match_id → {reg_home, reg_away, ...}.

    Собирается etl/fetch_nhl_periods.py. Без неё модель обучается
    только по итоговым счётам, и тогда параметр овертайма (а значит,
    и доля буллитных серий) не идентифицируется — см. докстринг
    модуля. Отсутствие файла не должно ронять пайплайн: возвращается
    пустой словарь, и модель работает на итоговых счётах.
    """
    path = PROCESSED / "nhl_periods.parquet"
    if not path.exists():
        return {}
    df = pl.read_parquet(path)
    return {str(r["match_id"]): r for r in df.to_dicts()}


# ------------------------------------------------------------------ helpers


def _dc_tau(h, a, lam_h, lam_a, rho: float) -> np.ndarray:
    """Поправка Диксона-Коулса для четырёх «низких» счетов.

    Возвращает коэффициент τ для каждого наблюдения. Матрица счёта
    потом нормируется на сумму, поэтому поправка меняет не только
    отдельные ячейки, но и всю нормировку — как и в моделях футбола.
    """
    h, a, lam_h, lam_a = np.broadcast_arrays(
        np.asarray(h), np.asarray(a), np.asarray(lam_h), np.asarray(lam_a))
    tau = np.ones(h.shape, dtype=float)
    m00 = (h == 0) & (a == 0)
    m01 = (h == 0) & (a == 1)
    m10 = (h == 1) & (a == 0)
    m11 = (h == 1) & (a == 1)
    if m00.any():
        tau[m00] = 1.0 - lam_h[m00] * lam_a[m00] * rho
    if m01.any():
        tau[m01] = 1.0 + lam_h[m01] * rho
    if m10.any():
        tau[m10] = 1.0 + lam_a[m10] * rho
    if m11.any():
        tau[m11] = 1.0 - rho
    return tau


def _reg_norm(lam_h: np.ndarray, lam_a: np.ndarray, rho: float) -> np.ndarray:
    """Нормировка матрицы основного времени после поправки DC.

    Сумма по всем ячейкам τ·P равна единице плюс вклад четырёх
    изменённых ячеек — считаем его аналитически, без построения
    матрицы на каждый матч.
    """
    e = np.exp(-(lam_h + lam_a))
    p00 = e
    p01 = e * lam_a
    p10 = e * lam_h
    p11 = e * lam_h * lam_a
    z = (1.0
         + (-lam_h * lam_a * rho) * p00
         + (lam_h * rho) * p01
         + (lam_a * rho) * p10
         + (-rho) * p11)
    return np.clip(z, 1e-6, None)


def _tie_mass(lam_h, lam_a, rho: float, max_r: int = MAX_GOALS) -> np.ndarray:
    """Суммарная масса ничьих в основном времени (до нормировки).

    Это Σ_r P(r:r)·τ(r,r) — ровно та часть матрицы, которая попадает
    в диагональ. Нужна для поправки на избыток ничьих: она знает,
    сколько массы можно переложить в диагональ и сколько забрать
    из остальных ячеек.
    """
    lam_h, lam_a = np.broadcast_arrays(np.asarray(lam_h, dtype=float),
                                       np.asarray(lam_a, dtype=float))
    r = np.arange(max_r + 1)
    lh = lam_h[..., None]
    la = lam_a[..., None]
    p = poisson.pmf(r, lh) * poisson.pmf(r, la)
    tau = np.ones(np.broadcast_shapes(lh.shape, r.shape))
    tau = np.where(r == 0, 1.0 - lh * la * rho, tau)
    tau = np.where(r == 1, 1.0 - rho, tau)
    return np.sum(p * tau, axis=-1)


def _tie_factors(lam_h, lam_a, rho: float, tie_boost: float,
                 max_r: int = MAX_GOALS):
    """Множители для ячеек «ничья» и «не ничья» + итоговая масса.

    Поправка на избыток ничьих массосохраняющая: сколько массы
    прибавляем диагонали, столько же отнимаем у остальных ячеек,
    пропорционально их массе. Поэтому общая вероятность не меняется,
    а модель может подвинуть её в ничьи.

    Если κ настолько велик, что множитель для прочих ячеек стал бы
    отрицательным, он обрезается снизу (MIN_OFF_FACTOR), а итоговая
    масса пересчитывается — тогда вероятности всё равно остаются
    неотрицательными и нормированными.
    """
    z = _reg_norm(lam_h, lam_a, rho)
    if not tie_boost:
        return 1.0, 1.0, z
    sd = _tie_mass(lam_h, lam_a, rho, max_r)
    so = np.maximum(z - sd, 1e-12)
    off = np.maximum(1.0 - tie_boost * sd / so, MIN_OFF_FACTOR)
    diag = 1.0 + tie_boost
    total = sd * diag + so * off
    return diag, off, total


def _reg_pmf(h, a, lam_h, lam_a, rho: float,
             tie_boost: float = 0.0) -> np.ndarray:
    """Вероятность наблюдённого счёта основного времени."""
    base = (poisson.pmf(h, lam_h)
            * poisson.pmf(a, lam_a)
            * _dc_tau(h, a, lam_h, lam_a, rho))
    diag_mult, off_mult, total = _tie_factors(lam_h, lam_a, rho, tie_boost)
    same = np.asarray(h) == np.asarray(a)
    base = base * np.where(same, diag_mult, off_mult)
    return base / total


def _ot_split(lam_h, lam_a, ot_multiplier: float):
    """Как заканчивается овертайм.

    Возвращает (p_home, p_away, p_none):
      p_home — хозяева забивают первую шайбу овертайма;
      p_away — гости забивают первую шайбу овертайма;
      p_none — за пять минут никто не забил, игра ушла в буллиты.

    Овертайм заканчивается на первой шайбе, поэтому «первая шайба»
    распределяется между командами пропорционально их темпу, а хвост
    «не забил никто» — это вероятность нуля голов, exp(-(μ_h + μ_a)).
    """
    lam_h = np.asarray(lam_h, dtype=float)
    lam_a = np.asarray(lam_a, dtype=float)
    mu_h = lam_h * ot_multiplier * (OT_MINUTES / REG_MINUTES)
    mu_a = lam_a * ot_multiplier * (OT_MINUTES / REG_MINUTES)
    total = mu_h + mu_a
    p_none = np.exp(-total)
    safe = np.maximum(total, 1e-12)
    share_h = np.where(total > 0, mu_h / safe, 0.5)
    return share_h * (1.0 - p_none), (1.0 - share_h) * (1.0 - p_none), p_none


def _fit_ridge_poisson(hs, asc, hi, ai, w, n: int, prior: float,
                        iters: int = 60) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Этап 1: взвешенная ridge-пуассоновская регрессия.

    Модель:
        log λ_дома = b_h + attack[home] − defence[away]
        log λ_в_гостях = b_a + attack[away] − defence[home]

    Идентифицируемость: оба вектора центрируются (сумма ноль), базы
    поглощают сдвиг. Штраф prior·Σ(attack² + defence²) — априор
    «сила команды ~ N(0, 1/sqrt(2·prior))».

    Решается взвешенными IRLS: на каждой итерации считается гессиан
    Фишера (диагональный по λ) и решается система normal equations.
    Задача выпуклая, поэтому сходимость гарантирована и результат не
    зависит от начального приближения.
    """
    atk = np.zeros(n)
    dfn = np.zeros(n)
    b_h = float(log(max(hs.mean(), 0.2)))
    b_a = float(log(max(asc.mean(), 0.2)))

    p = 2 * n + 2
    idx_h = np.stack([hi, n + ai, np.full(len(hi), 2 * n)], axis=1)
    idx_a = np.stack([ai, n + hi, np.full(len(ai), 2 * n + 1)], axis=1)
    sign = np.array([1.0, -1.0, 1.0])

    for _ in range(iters):
        lam_h = np.exp(b_h + atk[hi] - dfn[ai])
        lam_a = np.exp(b_a + atk[ai] - dfn[hi])

        # Градиент лог-правдоподобия (по параметрам линейного
        # предиктора): dL/dlogλ = w·(y − λ).
        r_h = w * (hs - lam_h)
        r_a = w * (asc - lam_a)

        g = np.zeros(p)
        np.add.at(g, hi, r_h)
        np.add.at(g, ai, r_a)
        np.add.at(g, n + ai, -r_h)
        np.add.at(g, n + hi, -r_a)
        g[2 * n] = r_h.sum()
        g[2 * n + 1] = r_a.sum()
        g[:2 * n] -= prior * np.concatenate([atk, dfn])

        # Нормальные уравнения: H = XᵀWX + prior·I.
        wh = w * lam_h
        wa = w * lam_a
        H = np.zeros((p, p))
        for idx, wt in ((idx_h, wh), (idx_a, wa)):
            for u in range(3):
                np.add.at(H, (idx[:, u], idx[:, u]), wt)
                for v in range(u + 1, 3):
                    val = wt * sign[u] * sign[v]
                    np.add.at(H, (idx[:, u], idx[:, v]), val)
                    np.add.at(H, (idx[:, v], idx[:, u]), val)
        H[np.arange(2 * n), np.arange(2 * n)] += prior
        H[2 * n, 2 * n] += 1e-6
        H[2 * n + 1, 2 * n + 1] += 1e-6

        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(step)):
            break

        atk += step[:n]
        dfn += step[n:2 * n]
        b_h += float(step[2 * n])
        b_a += float(step[2 * n + 1])

        # Центрируем векторы и переносим сдвиг в базы: λ не меняются.
        m_a = atk.mean()
        m_d = dfn.mean()
        atk -= m_a
        dfn -= m_d
        b_h += m_a + m_d
        b_a += m_a + m_d

        if np.max(np.abs(step)) < 1e-8:
            break

    return atk, dfn, b_h, b_a


# ------------------------------------------------------------------- model


@dataclass
class HockeyModel:
    """Пуассоновская модель НХЛ с овертаймом и буллитами.

    Поля public — те же, что у BasketballModel (attack, defence,
    strength()), чтобы общий код (models/ball_sport.py, страницы сайта)
    работал с обеими моделями одинаково.
    """

    teams: list[str] = field(default_factory=list)
    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    # log скорости голов хозяев/гостей в основное время.
    base_home: float = log(3.0)
    base_away: float = log(3.0)
    # Во сколько раз выше темп гола в овертайме (3 на 3) по сравнению с
    # основным временем.
    ot_multiplier: float = 6.0
    # Поправка Диксона-Коулса.
    rho: float = 0.0
    # Поправка на избыток ничьих (см. константы модуля).
    tie_boost: float = 0.0
    # Учитывать ли структуру «основное время → овертайм → буллиты».
    # False — контрольный вариант: итоговый счёт считается целиком
    # пуассоновским, без овертайма.
    use_overtime: bool = True
    half_life_days: float = HALF_LIFE_DAYS
    strength_scale: float = DEFAULT_STRENGTH_SCALE
    _converged: bool = False
    _message: str = ""

    # ------------------------------------------------------------------ fit

    def fit(self, matches: list[dict], teams: list[str],
            half_life_days: float = HALF_LIFE_DAYS,
            strength_scale: float = DEFAULT_STRENGTH_SCALE,
            use_overtime: bool = True,
            ot_multiplier_init: float = 6.0,
            tie_boost_init: float = 0.0,
            use_tie_boost: bool = True,
            periods: dict[str, dict] | None = None) -> "HockeyModel":
        """Обучает модель.

        matches: словари с home_id, away_id, home_score, away_score,
        match_date. Команды вне списка teams получают нейтральные
        параметры (нулевые атака и оборона).

        periods: разбивка по периодам из etl/fetch_nhl_periods.py —
        словарь match_id → {reg_home, reg_away, ot_played, shootout,
        ot_winner}. Без неё модель обучается только по итоговым счётам,
        и параметр овертайма становится неидентифицируемым (см.
        докстринг модуля).

        strength_scale — априорное отклонение силы команды (в логарифме
        скорости голов). Чем меньше, тем сильнее модель «схлопывает»
        команды к средним; подбирается walk-forward, а не на глаз.
        """
        if not matches:
            raise ValueError("пустой обучающий набор")

        self.teams = list(teams)
        self.half_life_days = half_life_days
        self.strength_scale = strength_scale
        self.use_overtime = use_overtime
        self.attack = {t: 0.0 for t in self.teams}
        self.defence = {t: 0.0 for t in self.teams}

        idx = {t: i for i, t in enumerate(self.teams)}
        known = [m for m in matches
                 if m["home_id"] in idx and m["away_id"] in idx]
        if len(known) < 30:
            raise ValueError(f"мало матчей известных команд: {len(known)}")

        hs_final = np.array([m["home_score"] for m in known], dtype=float)
        as_final = np.array([m["away_score"] for m in known], dtype=float)
        hi = np.array([idx[m["home_id"]] for m in known])
        ai = np.array([idx[m["away_id"]] for m in known])

        last = max(m["match_date"] for m in known)
        ages = np.array([(last - m["match_date"]).days for m in known], dtype=float)
        w = np.power(0.5, ages / half_life_days)

        n = len(self.teams)
        prior = 1.0 / (2.0 * max(strength_scale, 1e-6) ** 2)

        # --- что именно наблюдалось в каждом матче ------------------------
        #
        # kind:
        #   0 — счёт основного времени, овертайма не было;
        #   1 — ничья в основное время, овертайм решил матч;
        #   2 — ничья в основное времени, буллитная серия;
        #   3 — известен только итоговый счёт (разбивки по периодам нет).
        #
        # kind 3 — запасной путь: он менее информативен, но позволяет
        # обучаться, даже если разбивка по периодам ещё не собрана.
        obs_h = hs_final.copy()
        obs_a = as_final.copy()
        kind = np.zeros(len(known), dtype=int)
        tie_h = np.zeros(len(known), dtype=float)
        tie_a = np.zeros(len(known), dtype=float)
        win_home = np.zeros(len(known), dtype=bool)

        if use_overtime and periods:
            for j, m in enumerate(known):
                per = periods.get(str(m["match_id"]))
                if not per:
                    kind[j] = 3
                    continue
                rh = per.get("reg_home")
                ra = per.get("reg_away")
                if rh is None or ra is None:
                    kind[j] = 3
                    continue
                obs_h[j] = rh
                obs_a[j] = ra
                if not per.get("ot_played"):
                    kind[j] = 0
                elif per.get("shootout"):
                    kind[j] = 2
                    tie_h[j] = tie_a[j] = rh
                else:
                    kind[j] = 1
                    tie_h[j] = tie_a[j] = rh
                    win_home[j] = (per.get("ot_winner") == "H")

        # Для контрольной модели (без овертайма) наблюдается всегда
        # итоговый счёт.
        if not use_overtime:
            obs_h = hs_final.copy()
            obs_a = as_final.copy()
            kind[:] = 0

        # --- этап 1: силы команд и базы (ridge-пуассон, IRLS) ----------
        # Оцениваются по СЧЁТУ ОСНОВНОГО ВРЕМЕНИ: λ — это темп за 60
        # минут, и овертайм к нему отношения не имеет.
        fit_h = np.where(kind == 3, hs_final, obs_h)
        fit_a = np.where(kind == 3, as_final, obs_a)
        atk, dfn, b_h, b_a = _fit_ridge_poisson(fit_h, fit_a, hi, ai, w, n, prior)

        # --- этап 2: глобальные параметры под полную модель --------------
        m0 = kind == 0
        m1 = kind == 1
        m2 = kind == 2
        m3 = kind == 3

        def neg_ll(params: np.ndarray) -> float:
            base_h, base_a, log_ot, rho = params[:4]
            # Поправка на избыток ничьих имеет смысл только там, где
            # ничья реально возможна — в структуре «основное время →
            # овертайм → буллиты». Контрольная модель без овертайма
            # остаётся чистым пуассоном.
            tie_boost = params[4] if len(params) > 4 else 0.0
            lam_h = np.exp(base_h + atk[hi] - dfn[ai])
            lam_a = np.exp(base_a + atk[ai] - dfn[hi])

            ot_mult = float(np.exp(log_ot))
            p_home, p_away, p_none = _ot_split(lam_h, lam_a, ot_mult)

            prob = np.zeros(len(known), dtype=float)

            # 0: счёт основного времени, овертайма не было.
            if m0.any():
                prob[m0] = _reg_pmf(obs_h[m0], obs_a[m0], lam_h[m0],
                                    lam_a[m0], rho, tie_boost)

            # 1: ничья в основном время, овертайм решил матч.
            if m1.any():
                tie_p = _reg_pmf(tie_h[m1], tie_a[m1], lam_h[m1],
                                 lam_a[m1], rho, tie_boost)
                prob[m1] = tie_p * np.where(win_home[m1], p_home[m1], p_away[m1])

            # 2: ничья в основном время, буллитная серия (жребий 50/50).
            if m2.any():
                tie_p = _reg_pmf(tie_h[m2], tie_a[m2], lam_h[m2],
                                 lam_a[m2], rho, tie_boost)
                prob[m2] = tie_p * p_none[m2] * 0.5

            # 3: известен только итоговый счёт.
            if m3.any():
                fh = hs_final[m3]
                fa = as_final[m3]
                lh = lam_h[m3]
                la = lam_a[m3]
                p = _reg_pmf(fh, fa, lh, la, rho, tie_boost)
                # Матч мог уйти в овертайм: тогда в основном времени
                # была ничья, а победитель получил ровно одну шайбу.
                home_win = fh == fa + 1
                away_win = fa == fh + 1
                if home_win.any():
                    idx = np.where(home_win)[0]
                    tie_p = _reg_pmf(fa[idx], fa[idx], lh[idx], la[idx],
                                     rho, tie_boost)
                    p[idx] += tie_p * (p_home[idx] + 0.5 * p_none[idx])
                if away_win.any():
                    idx = np.where(away_win)[0]
                    tie_p = _reg_pmf(fh[idx], fh[idx], lh[idx], la[idx],
                                     rho, tie_boost)
                    p[idx] += tie_p * (p_away[idx] + 0.5 * p_none[idx])
                prob[m3] = p

            ll = np.log(np.clip(prob, 1e-300, None))
            return -float(np.sum(w * ll))

        x0 = [b_h, b_a, log(max(ot_multiplier_init, 1e-3)), 0.0]
        bounds = [
            (log(MIN_BASE_RATE), log(MAX_BASE_RATE)),
            (log(MIN_BASE_RATE), log(MAX_BASE_RATE)),
            (log(MIN_OT_MULTIPLIER), log(MAX_OT_MULTIPLIER)),
            (-MAX_RHO, MAX_RHO),
        ]
        if use_overtime and use_tie_boost:
            x0.append(tie_boost_init)
            bounds.append((MIN_TIE_BOOST, MAX_TIE_BOOST))
        x0 = np.array(x0)
        res = minimize(neg_ll, x0, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 300, "maxfun": 3000})

        self.attack = {t: float(atk[idx[t]]) for t in self.teams}
        self.defence = {t: float(dfn[idx[t]]) for t in self.teams}
        self.base_home = float(res.x[0])
        self.base_away = float(res.x[1])
        self.ot_multiplier = float(np.exp(res.x[2]))
        self.rho = float(res.x[3])
        self.tie_boost = float(res.x[4]) if len(res.x) > 4 else 0.0
        self._converged = bool(res.success)
        self._message = str(res.message)
        return self

    # -------------------------------------------------------------- predict

    def lambdas(self, home: str, away: str) -> tuple[float, float]:
        """Скорости голов в основное время."""
        atk_h = self.attack.get(home, 0.0) - self.defence.get(away, 0.0)
        atk_a = self.attack.get(away, 0.0) - self.defence.get(home, 0.0)
        return (float(np.exp(self.base_home + atk_h)),
                float(np.exp(self.base_away + atk_a)))

    def _reg_matrix(self, lam_h: float, lam_a: float) -> np.ndarray:
        """Распределение счёта основного времени (нормированное)."""
        k = np.arange(MAX_GOALS + 1)
        reg = np.outer(poisson.pmf(k, lam_h), poisson.pmf(k, lam_a))
        reg[0, 0] *= 1.0 - lam_h * lam_a * self.rho
        reg[0, 1] *= 1.0 + lam_h * self.rho
        reg[1, 0] *= 1.0 + lam_a * self.rho
        reg[1, 1] *= 1.0 - self.rho
        # Поправка на избыток ничьих: столько массы переносится из
        # прочих ячеек в диагональ, сколько ей нужно, и обратно —
        # пропорционально массе ячеек. Итог нормируется заново.
        if self.tie_boost:
            sd = float(np.trace(reg))
            so = max(float(reg.sum()) - sd, 1e-12)
            off = max(1.0 - self.tie_boost * sd / so, MIN_OFF_FACTOR)
            diag = np.eye(len(reg), dtype=bool)
            reg = reg * np.where(diag, 1.0 + self.tie_boost, off)
        total = reg.sum()
        if total > 0:
            reg = reg / total
        return reg

    def score_matrix(self, home: str, away: str) -> np.ndarray:
        """Итоговое распределение счёта: matrix[i][j] = P(i шайб дома, j в гостях)."""
        lam_h, lam_a = self.lambdas(home, away)
        reg = self._reg_matrix(lam_h, lam_a)
        final = np.zeros_like(reg)
        for i in range(MAX_GOALS + 1):
            for j in range(MAX_GOALS + 1):
                if i != j:
                    final[i, j] = reg[i, j]
        if self.use_overtime:
            p_home, p_away, p_none = _ot_split(lam_h, lam_a, self.ot_multiplier)
            # Овертайм заканчивается на первой шайбе: победитель
            # получает ровно +1. Если за пять минут не забили — буллиты,
            # победитель серии определяется жребием (50/50).
            for r in range(MAX_GOALS):
                tie = reg[r, r]
                if tie <= 0:
                    continue
                final[r + 1, r] += tie * (p_home + 0.5 * p_none)
                final[r, r + 1] += tie * (p_away + 0.5 * p_none)
        # Матрица нормируется: в безовертаймном варианте часть массы
        # (ничьи по счёту) отбрасывается, а в овертаймном — ячейка
        # (12:12) не помещается в сетку.
        total = final.sum()
        if total > 0:
            final = final / total
        return final

    def tie_prob(self, home: str, away: str) -> float:
        """Вероятность ничьей после основного времени."""
        lam_h, lam_a = self.lambdas(home, away)
        reg = self._reg_matrix(lam_h, lam_a)
        return float(np.trace(reg))

    def shootout_prob(self, home: str, away: str) -> float:
        """Вероятность буллитной серии (безусловная).

        Это и есть «честная ничья» в хоккее: не 15% из нормальной
        аппроксимации, а единицы процентов, которые к тому же НЕ
        означают ничьей — победителя определяет жребий, и модель
        делит эту массу поровну.
        """
        if not self.use_overtime:
            return 0.0
        lam_h, lam_a = self.lambdas(home, away)
        _, _, p_none = _ot_split(lam_h, lam_a, self.ot_multiplier)
        return self.tie_prob(home, away) * float(p_none)

    def predict_outcome(self, home: str, away: str) -> np.ndarray:
        """Вероятности исхода матча: [P(хозяева), P(гости)]. Сумма = 1."""
        matrix = self.score_matrix(home, away)
        p_home = float(np.tril(matrix, -1).sum())
        p_away = float(np.triu(matrix, 1).sum())
        total = p_home + p_away
        if total <= 0:
            return np.array([0.5, 0.5])
        return np.array([p_home / total, p_away / total])

    def predict_1x2(self, home: str, away: str) -> np.ndarray:
        """Совместимость с общим конвейером: ничьей в НХЛ не бывает.

        Возвращает [P(хозяева), 0.0, P(гости)] — ровно тот формат,
        который ждут models/ball_sport.py и журнал.
        """
        p_home, p_away = self.predict_outcome(home, away)
        return np.array([p_home, 0.0, p_away])

    def over_under(self, home: str, away: str, line: float = 5.5) -> float:
        """Вероятность тотала больше линии (по умолчанию 5.5 шайб)."""
        matrix = self.score_matrix(home, away)
        idx = np.arange(matrix.shape[0])
        totals = idx[:, None] + idx[None, :]
        over = float(matrix[totals > line].sum())
        under = float(matrix[totals < line].sum())
        total = over + under
        return over / total if total > 0 else 0.5

    # ---------------------------------------------------------------- misc

    def strength(self) -> list[dict]:
        """Рейтинг-листа.

        Знаки как в моделях футбола: в формуле λ = exp(base + attack −
        defence) параметр defence вычитается, поэтому больше defence =
        лучше оборона. Итог — СУММА атаки и обороны.
        """
        rows = [
            {
                "team": t,
                "attack": round(self.attack[t], 3),
                "defence": round(self.defence[t], 3),
                "overall": round(self.attack[t] + self.defence[t], 3),
            }
            for t in self.teams
        ]
        return sorted(rows, key=lambda r: -r["overall"])

    def summary(self) -> str:
        home_rate = float(np.exp(self.base_home))
        away_rate = float(np.exp(self.base_away))
        return (f"λ_дома {home_rate:.2f} шайбы, λ_в_гостях {away_rate:.2f}, "
                f"дома +{home_rate / away_rate - 1:+.1%}, "
                f"овертайм ×{self.ot_multiplier:.2f}, ρ={self.rho:+.4f}, "
                f"κ={self.tie_boost:+.3f}, "
                f"сошёлся={self._converged}")
