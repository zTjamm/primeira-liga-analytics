"""Справочник клубов: короткие имена из CSV → канонические ID.

Имена в football-data.co.uk менялись по ходу истории (`Sp Lisbon`, `Setubal`,
`Est Amadora`), а в API используются официальные названия. Здесь снимаем это
расхождение один раз, чтобы дальше по пайплайну ходили только ID.

`team_id` — стабильный ключ, не зависящий от написания в каком-либо источнике.
"""
from __future__ import annotations

# team_id -> (каноническое имя, имена из CSV, id в football-data.org или None)
TEAMS: dict[str, dict] = {
    "porto":       {"name": "FC Porto",                    "csv": ["Porto"],                                   "fd": 2013},
    "benfica":     {"name": "Sport Lisboa e Benfica",       "csv": ["Benfica"],                                 "fd": 2011},
    "sp Lisbon":   {"name": "Sporting Clube de Portugal",   "csv": ["Sp Lisbon"],                               "fd": 2012},
    "braga":       {"name": "Sporting Clube de Braga",     "csv": ["Sp Braga"],                                "fd": 2014},
    "guimaraes":   {"name": "Vitória SC",                  "csv": ["Guimaraes"],                               "fd": 2015},
    "famalicao":   {"name": "FC Famalicão",                "csv": ["Famalicao"],                               "fd": 2016},
    "estoril":     {"name": "GD Estoril Praia",            "csv": ["Estoril"],                                 "fd": 2017},
    "moreirense":  {"name": "Moreirense FC",               "csv": ["Moreirense"],                              "fd": 2018},
    "arouca":      {"name": "FC Arouca",                   "csv": ["Arouca"],                                  "fd": 2019},
    "gil_vicente": {"name": "Gil Vicente FC",              "csv": ["Gil Vicente"],                             "fd": 2020},
    "rio_ave":     {"name": "Rio Ave FC",                  "csv": ["Rio Ave"],                                 "fd": 2021},
    "maritimo":    {"name": "CS Marítimo",                 "csv": ["Maritimo"],                                "fd": 2022},
    "santa_clara": {"name": "CD Santa Clara",              "csv": ["Santa Clara"],                             "fd": 2023},
    "nacional":    {"name": "CD Nacional",                 "csv": ["Nacional"],                                "fd": 2024},
    "casa_pia":    {"name": "Casa Pia AC",                 "csv": ["Casa Pia"],                                "fd": 2025},
    "alverca":     {"name": "FC Alverca",                  "csv": ["Alverca"],                                 "fd": 2026},
    "estrela":     {"name": "CF Estrela da Amadora",       "csv": ["Estrela"],                                 "fd": 2027},
    "academico":   {"name": "Académico de Viseu FC",       "csv": ["Academico Viseu"],                         "fd": 2028},

    # Клубы прошлых сезонов
    "boavista":    {"name": "Boavista",                    "csv": ["Boavista"],                                "fd": None},
    "setubal":     {"name": "Vitória Setúbal",             "csv": ["Setubal"],                                 "fd": None},
    "est_amadora": {"name": "Estoril / Amadora (legacy)",   "csv": ["Est Amadora"],                             "fd": None},
    "chaves":      {"name": "Chaves",                      "csv": ["Chaves"],                                  "fd": None},
    "vizela":      {"name": "FC Vizela",                   "csv": ["Vizela"],                                  "fd": None},
    "tondela":     {"name": "CD Tondela",                  "csv": ["Tondela"],                                 "fd": None},
    "farense":     {"name": "SC Farense",                  "csv": ["Farense"],                                 "fd": None},
    "portimonense":{"name": "Portimonense",                "csv": ["Portimonense"],                            "fd": None},
    "feirense":    {"name": "CD Feirense",                 "csv": ["Feirense", "Feirense "],                   "fd": None},
    "olhanense":   {"name": "SC Olhanense",                "csv": ["Olhanense"],                               "fd": None},
    "penafiel":    {"name": "Penafiel",                    "csv": ["Penafiel"],                                "fd": None},
    "varzim":      {"name": "FC Varzim",                   "csv": ["Varzim"],                                  "fd": None},
    "leiria":      {"name": "CD Leiria",                   "csv": ["Leiria"],                                  "fd": None},
    "uniao_madeira":{"name": "União da Madeira",           "csv": ["Uniao Madeira"],                            "fd": None},
    "trofense":    {"name": "CD Trofense",                 "csv": ["Trofense"],                                "fd": None},
    "leixoes":     {"name": "Leixões",                     "csv": ["Leixoes"],                                 "fd": None},
    "belenenses":  {"name": "Belenenses",                  "csv": ["Belenenses"],                              "fd": None},
    "pacos":       {"name": "Paços Ferreira",              "csv": ["Pacos Ferreira"],                          "fd": None},
    "salgueiros":  {"name": "Salgueiros",                  "csv": ["Salgueiros"],                              "fd": None},
    "avs":         {"name": "AVS",                         "csv": ["AVS"],                                     "fd": None},
    "aves":        {"name": "CD Aves",                     "csv": ["Aves"],                                    "fd": None},
    "academica":   {"name": "Académica",                   "csv": ["Academica"],                               "fd": None},
    "beira_mar":   {"name": "Beira-Mar",                   "csv": ["Beira Mar"],                               "fd": None},
    "campomaiorense":{"name": "Campomaiorense",            "csv": ["Campomaiorense"],                           "fd": None},
    "naval":       {"name": "CD Naval",                    "csv": ["Naval"],                                   "fd": None},
}

# Разворачиваем в обратный индекс: имя из CSV (с учётом мусора) → team_id
CSV_TO_ID: dict[str, str] = {}
for _tid, _meta in TEAMS.items():
    for _c in _meta["csv"]:
        CSV_TO_ID[_c] = _tid
        CSV_TO_ID[_c.strip()] = _tid

FD_NAME_TO_ID: dict[str, str] = {}
for _tid, _meta in TEAMS.items():
    if _meta["fd"] is not None:
        FD_NAME_TO_ID[_meta["name"]] = _tid


def resolve_csv(name: str) -> str | None:
    """Имя из CSV → team_id. None, если клуб неизвестен (тогда добавить в TEAMS)."""
    if name is None:
        return None
    return CSV_TO_ID.get(name) or CSV_TO_ID.get(name.strip())


def resolve_fd(name: str) -> str | None:
    return FD_NAME_TO_ID.get(name)
