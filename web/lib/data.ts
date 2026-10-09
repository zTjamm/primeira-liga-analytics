/**
 * Загрузка артефактов пайплайна.
 *
 * Файлы в public/data кладёт GitHub Actions. При локальной разработке их может
 * не быть, поэтому каждый загрузчик возвращает null, а страницы показывают
 * понятное объяснение вместо падения. Это важно: витрина не должна выглядеть
 * сломанной из-за того, что пайплайн ещё не отработал.
 */
import { cache } from "react";
import { readFile } from "node:fs/promises";
import path from "node:path";

const DATA_DIR = path.join(process.cwd(), "public", "data");

export type Outcome = "H" | "D" | "A";

export interface Prediction {
  fd_match_id: number;
  date: string;
  kickoff_utc: string;
  matchday: number | null;
  home_id: string;
  away_id: string;
  home_name: string;
  away_name: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  p_home_elo: number;
  p_draw_elo: number;
  p_home_dc: number;
  p_draw_dc: number;
  prediction: Outcome;
  confidence: number;
  over25: number;
  under25: number;
  xg_home: number;
  xg_away: number;
  elo_home: number;
  elo_away: number;
  elo_diff: number;
  attack_home: number;
  defence_home: number;
  attack_away: number;
  defence_away: number;
}

export interface PredictionsFile {
  generated_for_season: string;
  history_matches: number;
  upcoming: Prediction[];
}

export interface MetricRow {
  model: string;
  n: number;
  logloss: number;
  brier: number;
  accuracy: number;
  roi: number | null;
  n_bets: number;
  season?: string;
}

export interface BacktestFile {
  validation_seasons: string[];
  test_seasons: string[];
  chosen_weight_dc: number;
  weight_grid: { w: number; logloss: number }[];
  test: MetricRow[];
  test_per_season: MetricRow[];
}

export interface EloRow {
  team: string;
  rating: number;
}

export interface DcRow {
  team: string;
  attack: number;
  defence: number;
  overall: number;
}

export interface ValidationFile {
  checks: { check: string; ok: boolean; detail: string }[];
  coverage: {
    season: string;
    matches: number;
    xg: number;
    shots: number;
    closing: number;
    from_api: number;
  }[];
}

export interface CalibrationFile {
  n_matches: number;
  actual_freq: { H: number; D: number; A: number };
  chosen: string;
  methods: Record<
    string,
    {
      logloss: number;
      brier: number;
      accuracy: number;
      mean_probs: { H: number; D: number; A: number };
      calibration_gap: number;
      mean_overround: number;
    }
  >;
}

async function readJson<T>(file: string): Promise<T | null> {
  try {
    const raw = await readFile(path.join(DATA_DIR, file), "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export const getPredictions = cache(() => readJson<PredictionsFile>("predictions.json"));
export const getBacktest = cache(() => readJson<BacktestFile>("backtest.json"));
export const getEloTable = cache(() => readJson<EloRow[]>("elo_table.json"));
export const getDcStrength = cache(() => readJson<DcRow[]>("dc_strength.json"));
export const getValidation = cache(() => readJson<ValidationFile>("validation.json"));
export const getCalibration = cache(() => readJson<CalibrationFile>("market_calibration.json"));

export function getPredictionById(
  predictions: PredictionsFile | null,
  id: string,
): Prediction | null {
  if (!predictions) return null;
  const n = Number(id);
  if (!Number.isFinite(n)) return null;
  return predictions.upcoming.find((m) => m.fd_match_id === n) ?? null;
}

/** Русские названия клубов: из справочника для исторических. */
const TEAM_NAMES: Record<string, string> = {
  porto: "FC Porto",
  benfica: "Sport Lisboa e Benfica",
  "sp Lisbon": "Sporting Clube de Portugal",
  braga: "Sporting Clube de Braga",
  guimaraes: "Vitória SC",
  famalicao: "FC Famalicão",
  estoril: "GD Estoril Praia",
  moreirense: "Moreirense FC",
  arouca: "FC Arouca",
  gil_vicente: "Gil Vicente FC",
  rio_ave: "Rio Ave FC",
  maritimo: "CS Marítimo",
  santa_clara: "CD Santa Clara",
  nacional: "CD Nacional",
  casa_pia: "Casa Pia AC",
  alverca: "FC Alverca",
  estrela: "CF Estrela da Amadora",
  academico: "Académico de Viseu FC",
};

export function teamName(id: string): string {
  return TEAM_NAMES[id] ?? id;
}
