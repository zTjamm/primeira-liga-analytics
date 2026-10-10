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
  /** Выдан ли вердикт. При false вероятности остаются, но выбор исхода
   *  показывать нельзя: он не лучше монетки. */
  ah_fair_line: number;
  ah_cover_home: number;
  ah_cover_away: number;
  ah_push: number;
  ah_home_2plus: number;
  ah_home_exactly_1: number;
  ah_away_2plus: number;
  verdict_given: boolean;
  verdict_reason: string;
  team_games: number;
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

/* ------------------------------------------------------------------ ВТБ */

export interface VtbPrediction {
  match_id: string;
  date: string;
  weekday: string;
  day_month: string;
  kickoff_msk: string | null;
  phase_name: string | null;
  match_number: number | null;
  home_id: string;
  away_id: string;
  home_name: string;
  away_name: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  prediction: "H" | "D" | "A";
  confidence: number;
  exp_home_score: number;
  exp_away_score: number;
  exp_total: number;
  over_155: number;
  sigma_margin: number;
  strength_home: number;
  strength_away: number;
  hours_before: number | null;
}

export interface VtbPredictionsFile {
  league: string;
  season: string;
  generated_at: string;
  history_matches: number;
  model: string;
  draws_possible: boolean;
  upcoming: VtbPrediction[];
}

export interface VtbStrengthRow {
  team: string;
  name: string;
  attack: number;
  defence: number;
  overall: number;
}

export interface VtbBacktestFile {
  n_matches_total: number;
  n_predictions: number;
  seasons: string[];
  overall: MetricRow[];
  per_season: MetricRow[];
  home_win_share_data: number;
  draw_share_data: number;
}

export interface VtbIngestFile {
  seasons_available: string[];
  total_rows: number;
  trainable: number;
  by_season: Record<string, number>;
  dropped_allstars: number;
  dropped_playoffs: number;
  dropped_not_played: number;
  upcoming_current: number;
  teams_in_registry: number;
  teams_thin_history: number;
  date_range: [string, string];
  avg_points_per_team: number;
  avg_total_points: number;
  home_win_share: number;
  overtime_share: number;
  attendance_median: number;
}

/** Условия погоды на момент матча (подмножество — не всё используется в вёрстке). */
export interface Weather {
  temp_c: number | null;
  humidity: number | null;
  precip_mm: number | null;
  precip_prob: number | null;
  wind_kmh: number | null;
  gusts_kmh: number | null;
  cloud_pct: number | null;
  stadium: string;
  island: string;
}

export interface ForecastVersion {
  stage: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  hours_before: number;
  generated_at: string;
}

export interface ForecastRecord {
  fd_match_id: number;
  date: string;
  kickoff_utc: string;
  matchday: number | null;
  home_id: string;
  away_id: string;
  home_name: string;
  away_name: string;
  generated_at: string;
  hours_before: number;
  stage: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  over25: number | null;
  xg_home: number | null;
  xg_away: number | null;
  weather: Weather | null;
  versions: ForecastVersion[];
}

export interface ForecastHistory {
  generated_at: string;
  resolved: (ForecastRecord & {
    actual: "H" | "D" | "A";
    score: string;
    hit: boolean;
    p_actual: number | null;
    logloss: number | null;
    n_versions: number;
    shift: number;
    flipped: boolean;
  })[];
  pending: ForecastRecord[];
  summary: {
    total: number;
    overall: {
      n: number;
      accuracy: number;
      logloss: number | null;
      avg_p_actual: number;
    };
    by_stage: Record<
      string,
      { n: number; accuracy: number; logloss: number | null; avg_p_actual: number }
    >;
    multi_stage: number;
    flipped: number;
    avg_shift: number | null;
  };
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

export interface MarketSideScore {
  n: number;
  freq: number;
  logloss_model: number;
  logloss_line: number;
  brier_model: number;
  brier_line: number;
  accuracy_model: number;
  accuracy_line: number;
  calib_model: number;
  calib_line: number;
}

export interface MarketStrategy {
  bets: number;
  wins: number;
  hit_rate: number;
  avg_odds: number;
  roi: number;
  total_units: number;
}

/** Сравнение рынков с закрывающей линией. Отвечает на вопрос «стоит ли
 *  показывать тот рынок, где у нас выше вероятность»: не стоит, и вот
 *  измеренное обоснование. */
export interface MarketTotalsFile {
  test_seasons: string[];
  markets: {
    outcome_home: MarketSideScore;
    outcome_away: MarketSideScore;
    total_25: MarketSideScore;
  };
  strategy: {
    winner_1x2_model: MarketStrategy;
    winner_1x2_market: MarketStrategy;
    total_25_model: MarketStrategy;
    total_25_market: MarketStrategy;
  };
  overlap: { home_wins: number; over_25: number; both: number; note: string };
}

export const getMarketTotals = cache(() => readJson<MarketTotalsFile>("market_totals.json"));

export interface SelectiveRow {
  n: number;
  coverage: number;
  accuracy: number;
  accuracy_always_home: number;
  edge: number;
}

export interface SelectiveFile {
  val_season: string;
  test_seasons: string[];
  chosen_threshold: number;
  validation: Record<string, SelectiveRow>;
  test: Record<string, SelectiveRow>;
  thin_sample_matches: number;
}

export const getSelective = cache(() => readJson<SelectiveFile>("selective.json"));

export interface BenchmarkLine {
  n: number;
  logloss: number;
  accuracy: number;
  margin?: number;
}

export interface BenchmarkFile {
  test_seasons: string[];
  n: number;
  lines: { model: BenchmarkLine; avg?: BenchmarkLine; bfx?: BenchmarkLine };
  common?: {
    n: number;
    gap_avg: number;
    gap_bfx: number;
  };
  asian_handicap?: {
    n: number;
    margin: number;
    lines: number[];
    whole_line: number;
  };
  asian_quarter?: {
    n: number;
    logloss_model: number;
    logloss_line: number;
    accuracy_model: number;
    accuracy_line: number;
    /** Матчи, где модель и линия разошлись. Их соотношение
     *  проверяется тестом МакНемара: без этого разница в точности
     *  выглядит значимой, хотя может быть шумом. */
    discordant: number;
    only_model: number;
    only_line: number;
    p_value: number;
    accuracy_edge_significant: boolean;
  };
}

export const getBenchmark = cache(() => readJson<BenchmarkFile>("market_benchmark.json"));
export const getForecastHistory = cache(() => getJournal("football"));

export const getVtbPredictions = cache(
  () => readJson<VtbPredictionsFile>("vtb_predictions.json"),
);
export const getVtbStrength = cache(() => readJson<VtbStrengthRow[]>("vtb_strength.json"));
export const getVtbBacktest = cache(() => readJson<VtbBacktestFile>("vtb_backtest.json"));
export const getVtbIngest = cache(() => readJson<VtbIngestFile>("vtb_ingest.json"));

/* --------------------------------------------------------------- журнал */

export interface JournalVersion {
  stage: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  hours_before: number;
  generated_at: string;
}

export interface JournalRecord {
  key: string;
  date: string;
  kickoff: string | null;
  matchday: number | null;
  home_id: string;
  away_id: string;
  home_name: string;
  away_name: string;
  generated_at: string;
  hours_before: number;
  stage: string;
  p_home: number;
  p_draw: number;
  p_away: number;
  extra: Record<string, unknown> | null;
  versions: JournalVersion[];
}

export interface Journal {
  sport: string;
  generated_at: string;
  resolved: (JournalRecord & {
    actual: "H" | "D" | "A";
    score: string;
    hit: boolean;
    p_actual: number | null;
    logloss: number | null;
    n_versions: number;
    shift: number;
    flipped: boolean;
  })[];
  pending: JournalRecord[];
  summary: {
    total: number;
    overall: {
      n: number;
      accuracy: number;
      logloss: number | null;
      avg_p_actual: number;
    };
    by_stage: Record<
      string,
      { n: number; accuracy: number; logloss: number | null; avg_p_actual: number }
    >;
    multi_stage: number;
    flipped: number;
    avg_shift: number | null;
  };
}

export const getJournal = cache((sport: "football" | "basketball") =>
  readJson<Journal>(`${sport}_journal.json`),
);

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


/* Изучение статистики матча: корреляция признака с исходом против
   корреляции с остатком прогноза. */
export interface FeatureStudyFile {
  test_seasons: string[];
  n: number;
  history: number;
  stats: {
    stat: string;
    n: number;
    r_raw: number;
    r_residual: number;
    verdict: string;
  }[];
  offsides_available: boolean;
  note: string;
}

export const getFeatureStudy = cache(() => readJson<FeatureStudyFile>("feature_study.json"));

/* Диагностика по командам. */
export interface TeamDiagFile {
  test_seasons: string[];
  min_games: number;
  teams: {
    team: string;
    n: number;
    logloss_model: number;
    logloss_line: number;
    gap: number;
    hit_rate: number;
    thin: boolean;
  }[];
  spread: {
    solid: number;
    min: number;
    max: number;
    median: number;
    worse_than_line: number;
  };
}

export const getTeamDiag = cache(() => readJson<TeamDiagFile>("team_diagnostics.json"));

/* Проверка параметров: вес смеси и период затухания. */
export interface ParamsCheckFile {
  val_seasons: string[];
  test_seasons: string[];
  weight_grid: { w_dc: number; val: number; test: number }[];
  half_life_grid: { half_life: number; val: number; test: number }[];
}

export const getParamsCheck = cache(() => readJson<ParamsCheckFile>("params_check.json"));

/* Диагностика ошибок: калибровка, смещение, перекалибровка, сжатие к рынку. */
export interface ErrorDiagFile {
  val_season: string;
  test_seasons: string[];
  bias: Record<string, { model: number; actual: number; gap: number }>;
  market_anchor: {
    best_w_market: number;
    test_model: number;
    test_market: number;
    test_blend: number;
  };
  recalibration: {
    plain: number;
    temperature_T: number;
    temperature: number;
    dirichlet: number;
    market: number;
  };
}

export const getErrorDiag = cache(() => readJson<ErrorDiagFile>("error_diagnostics.json"));