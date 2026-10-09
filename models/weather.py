"""Погода в момент матча.

Зачем это нужно. В Португалии матчи проходят в трёх разных климатических режимах:
континентальная лига (Брага, Порту, Гимараш), Мадейра (Маритиму, Насьонал) и
Азоры (Санта-Клара). Дождь и ветер там не декоративные: мокрое поле и порывы
заметно снижают результативность. Это бесплатный сигнал, которого нет у
пуассоновской модели, и он доступен за 4 часа до матча.

Open-Meteo работает без ключа. Ограничение по честности: собранные признаки
НЕ подмешиваются в вероятности, пока это не проверено бэктестом. Иначе мы
проверим улучшение на тех же матчах, на которых подбирали, и получим красивую
неправду. Пока данные собираются, хранятся и показываются.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import requests

from etl.config import PROCESSED

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "primeira-liga-analytics/1.0"})

API = "https://api.open-meteo.com/v1/forecast"

# Координаты стадионов текущего сезона. Азоры и Мадейра — отдельные острова,
# поэтому усреднять по Португалии нельзя.
STADIUMS: dict[str, dict] = {
    "porto":        {"name": "Estádio do Dragão",            "lat": 41.1617, "lon": -8.6294},
    "benfica":      {"name": "Estádio da Luz",               "lat": 38.7528, "lon": -9.1894},
    "sp Lisbon":    {"name": "Estádio José Alvalade",        "lat": 38.7528, "lon": -9.1866},
    "braga":        {"name": "Estádio Municipal de Braga",   "lat": 41.5454, "lon": -8.4265},
    "guimaraes":    {"name": "Estádio Cidade de Coimbra",    "lat": 40.2136, "lon": -8.4767},
    "famalicao":    {"name": "Estádio Municipal de Famalicão", "lat": 41.4494, "lon": -8.6019},
    "estoril":      {"name": "Estádio António da Mota",      "lat": 38.7069, "lon": -9.4219},
    "moreirense":   {"name": "Parque Joaquim de Almeida",    "lat": 41.1934, "lon": -8.5997},
    "arouca":       {"name": "Estádio Municipal de Arouca",  "lat": 40.9519, "lon": -8.2625},
    "gil_vicente":  {"name": "Estádio Cidade de Barcelos",   "lat": 41.6167, "lon": -8.6350},
    "rio_ave":      {"name": "Estádio dos Arcos",            "lat": 41.3728, "lon": -8.7125},
    "maritimo":     {"name": "Estádio do Marítimo",          "lat": 32.6456, "lon": -16.9094},
    "santa_clara":  {"name": "Estádio de São Miguel",        "lat": 37.7440, "lon": -25.6740},
    "nacional":     {"name": "Estádio da Madeira",           "lat": 32.6494, "lon": -16.8889},
    "casa_pia":     {"name": "Estádio Pina Manique",         "lat": 38.7034, "lon": -9.3977},
    "alverca":      {"name": "Estádio do Alverca",           "lat": 38.9428, "lon": -9.0189},
    "estrela":      {"name": "Estádio José Gomes",           "lat": 38.7497, "lon": -9.1594},
    "academico":    {"name": "Estádio Cidade de Viseu",      "lat": 40.6617, "lon": -7.9089},
}

HOURLY = "temperature_2m,relative_humidity_2m,precipitation,precipitation_probability,wind_speed_10m,wind_gusts_10m,cloud_cover"


def fetch_window(days_ahead: int = 7) -> dict[str, list[dict]]:
    """Погода по часам для всех стадионов на горизонт дней вперёд."""
    start = date.today()
    end = start + timedelta(days=days_ahead)
    out: dict[str, list[dict]] = {}

    for team, s in STADIUMS.items():
        try:
            r = SESSION.get(
                API,
                params={
                    "latitude": s["lat"], "longitude": s["lon"],
                    "hourly": HOURLY, "timezone": "UTC",
                    "start_date": start.isoformat(), "end_date": end.isoformat(),
                    "wind_speed_unit": "kmh",
                },
                timeout=30,
            )
            r.raise_for_status()
            hourly = r.json().get("hourly")
            if not hourly:
                out[team] = []
                continue
            times = hourly["time"]
            rows = []
            for i, t in enumerate(times):
                rows.append({
                    "time_utc": t,
                    "temp_c": hourly["temperature_2m"][i],
                    "humidity": hourly["relative_humidity_2m"][i],
                    "precip_mm": hourly["precipitation"][i],
                    "precip_prob": hourly["precipitation_probability"][i],
                    "wind_kmh": hourly["wind_speed_10m"][i],
                    "gusts_kmh": hourly["wind_gusts_10m"][i],
                    "cloud_pct": hourly["cloud_cover"][i],
                })
            out[team] = rows
        except Exception:  # noqa: BLE001 — погода не должна ронять пайплайн
            out[team] = []
    return out


def conditions_at(weather: dict[str, list[dict]], team: str, kickoff_utc: str) -> dict | None:
    """Ближайший час к началу матча — на матч длиной 90 минут этого достаточно,
    но точнее брать среднее за час начала и следующий."""
    rows = weather.get(team) or []
    if not rows:
        return None
    target = datetime.fromisoformat(kickoff_utc.replace("Z", ""))
    candidates = [r for r in rows if abs((datetime.fromisoformat(r["time_utc"]) - target).total_seconds()) <= 3600]
    if not candidates:
        return None

    def avg(key: str) -> float | None:
        vals = [c[key] for c in candidates if c.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "temp_c": avg("temp_c"),
        "humidity": avg("humidity"),
        "precip_mm": avg("precip_mm"),
        "precip_prob": avg("precip_prob"),
        "wind_kmh": avg("wind_kmh"),
        "gusts_kmh": avg("gusts_kmh"),
        "cloud_pct": avg("cloud_pct"),
        "stadium": STADIUMS[team]["name"],
        "island": _island(team),
    }


def _island(team: str) -> str:
    lat = STADIUMS[team]["lat"]
    if lat > 36.5 and lat < 38.5:
        return "Азоры"
    if lat < 34:
        return "Мадейра"
    return "континент"


def save(weather: dict[str, list[dict]]) -> None:
    """Кладём полный прогноз в отдельный файл: он нужен для бэктеста,
    когда матч уже сыгран и признак можно проверить."""
    path = PROCESSED / "weather_forecast.json"
    path.write_text(json.dumps({
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "api": "open-meteo.com",
        "stadiums": STADIUMS,
        "weather": weather,
    }, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    w = fetch_window()
    save(w)
    ok = sum(1 for v in w.values() if v)
    print(f"Погода получена для {ok}/{len(STADIUMS)} стадионов")
    for team in ("porto", "maritimo", "santa_clara"):
        rows = w.get(team) or []
        if rows:
            print(f"  {team:>12} ({_island(team)}): {len(rows)} часов, пример на середине дня "
                  f"{rows[12]['temp_c']}°C, осадки {rows[12]['precip_prob']}%, ветер {rows[12]['wind_kmh']} км/ч")
