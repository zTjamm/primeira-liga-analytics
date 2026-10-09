/** Форматирование чисел и дат для витрины. */

const MONTHS = [
  "янв", "фев", "мар", "апр", "мая", "июн",
  "июл", "авг", "сен", "окт", "ноя", "дек",
];

const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

/** 09.10 — короткий формат без года. */
export function shortDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** 9 октября, пт — с годом, для группировки по турам. */
export function longDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  const wd = WEEKDAYS[d.getUTCDay()] ?? "";
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}, ${wd}`;
}

/** Время начала по UTC → по московскому времени (+3). */
export function kickoffMoscow(utc: string): string {
  const d = new Date(utc);
  if (Number.isNaN(d.getTime())) return "";
  const msk = new Date(d.getTime() + 3 * 3600 * 1000);
  return `${String(msk.getUTCHours()).padStart(2, "0")}:${String(msk.getUTCMinutes()).padStart(2, "0")}`;
}

export function pct(x: number, digits = 1): string {
  return `${(x * 100).toFixed(digits)}%`;
}

export function signed(x: number, digits = 3): string {
  return `${x >= 0 ? "+" : ""}${x.toFixed(digits)}`;
}

export const OUTCOME_LABEL: Record<string, string> = {
  H: "П1",
  D: "X",
  A: "П2",
};

export const OUTCOME_FULL: Record<string, string> = {
  H: "Победа хозяев",
  D: "Ничья",
  A: "Победа гостей",
};
