import Link from "next/link";
import { getJournal, type Journal, type JournalRecord } from "@/lib/data";
import { longDate, OUTCOME_LABEL } from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";
import { JournalSourceBadge } from "@/components/JournalSourceBadge";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "100% — самые уверенные прогнозы",
  description:
    "Прогнозы всех моделей проекта с наибольшей уверенностью: вероятность " +
    "выбранного исхода выше порога. Сыгранные матчи подсвечены по факту.",
};

type Sport = "football" | "rpl" | "basketball" | "wnba" | "nhl" | "nba";

/**
 * Модели проекта. Отдельной «модели на матч» в журналах нет: каждая запись —
 * это итоговый прогноз раздела (смесь/распределение Пуассона со своими
 * параметрами), поэтому подписью служит сам раздел. Так честнее, чем
 * выдавать walk-forward запись за отдельную модель.
 */
const MODELS: { sport: Sport; label: string }[] = [
  { sport: "football", label: "Футбол · Primeira Liga" },
  { sport: "rpl", label: "Футбол · РФПЛ" },
  { sport: "basketball", label: "Баскетбол · ВТБ" },
  { sport: "wnba", label: "Баскетбол · WNBA" },
  { sport: "nhl", label: "Хоккей · НХЛ" },
  { sport: "nba", label: "Баскетбол · NBA" },
];

const THRESHOLDS = [60, 65, 70, 75, 80];
const DEFAULT_THRESHOLD = 70;
/**
 * Окно таблицы. Сверху — «сегодня + 3 дня», снизу — фиксированная дата
 * 01.06.2026: с неё накоплена история, по которой видно фактический процент
 * успеха. Границы включительные.
 */
const DATE_FROM = "2026-06-01";
const DAYS_AHEAD = 3;

/** Дата в московском времени (UTC+3): матчи в журналах — по местным датам. */
function mskDate(ms: number): string {
  return new Date(ms + 3 * 3600 * 1000).toISOString().slice(0, 10);
}

/** 2026-10-15 → 15.10.2026 (для текста, а не для сортировки). */
function ruDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

interface TopRow {
  key: string;
  date: string;
  model: string;
  home: string;
  away: string;
  picked: "H" | "D" | "A";
  p: number;
  /** null — матч ещё не сыгран: счёта и вердикта нет. */
  score: string | null;
  hit: boolean | null;
  source?: string | null;
}

function pickOf(r: JournalRecord): { picked: "H" | "D" | "A"; p: number } {
  const h = r.p_home ?? 0;
  const d = r.p_draw ?? 0;
  const a = r.p_away ?? 0;
  if (h >= d && h >= a) return { picked: "H", p: h };
  if (a >= d) return { picked: "A", p: a };
  return { picked: "D", p: d };
}

function parseThreshold(raw: string | string[] | undefined): number {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const num = Number(value);
  if (!Number.isFinite(num)) return DEFAULT_THRESHOLD;
  // Поддерживаем и ?p=0.7, и ?p=70 — в обоих случаях порог один и тот же.
  const pct = Math.round(num <= 1 ? num * 100 : num);
  return THRESHOLDS.includes(pct) ? pct : DEFAULT_THRESHOLD;
}

export default async function TopPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sp = await searchParams;
  const thresholdPct = parseThreshold(sp.p);
  const threshold = thresholdPct / 100;
  // Верхняя граница окна считается от текущего момента: завтра она
  // сдвинется сама, нижняя (01.06.2026) остаётся на месте.
  const dateTo = mskDate(Date.now() + DAYS_AHEAD * 86400000);
  const inWindow = (date: string) => date >= DATE_FROM && date <= dateTo;

  const journals = await Promise.all(
    MODELS.map(async (m) => ({ ...m, journal: await getJournal(m.sport) })),
  );

  type LoadedModel = { sport: Sport; label: string; journal: Journal };
  const present = journals.filter(
    (m): m is LoadedModel => m.journal !== null,
  );

  if (present.length === 0) {
    return (
      <EmptyState
        title="Прогнозов пока нет"
        hint="Журналы разделов появятся после прогона пайплайна: каждый запуск записывает прогнозы, а результаты подтягиваются после матчей."
      />
    );
  }

  const rows: TopRow[] = [];
  for (const { label, journal } of present) {
    for (const r of journal.resolved) {
      if (!inWindow(r.date)) continue;
      const p = r.picked_p ?? pickOf(r).p;
      if (!Number.isFinite(p) || p < threshold) continue;
      rows.push({
        key: `${label}:${r.key}`,
        date: r.date,
        model: label,
        home: r.home_name,
        away: r.away_name,
        picked: r.picked ?? pickOf(r).picked,
        p,
        score: r.score,
        hit: r.hit,
        source: r.source ?? (r.extra?.source as string | undefined) ?? null,
      });
    }
    for (const r of journal.pending) {
      if (!inWindow(r.date)) continue;
      const { picked, p } = pickOf(r);
      if (!Number.isFinite(p) || p < threshold) continue;
      rows.push({
        key: `${label}:${r.key}`,
        date: r.date,
        model: label,
        home: r.home_name,
        away: r.away_name,
        picked,
        p,
        score: null,
        hit: null,
      });
    }
  }

  // Сверху вниз — по убыванию даты: верхняя строка это «сегодня + 3 дня»,
  // нижняя — 01.06.2026. Внутри одной даты первыми идут более уверенные.
  rows.sort((a, b) => b.date.localeCompare(a.date) || b.p - a.p);

  const shown = rows;
  const resolvedRows = shown.filter((r) => r.hit !== null);
  const hits = resolvedRows.filter((r) => r.hit).length;
  const pendingRows = shown.length - resolvedRows.length;
  const accuracy = resolvedRows.length > 0 ? hits / resolvedRows.length : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">100% — самые уверенные прогнозы</h1>
        <p className="mt-1 text-sm text-muted">
          Все разделы проекта в одной таблице. Окно: с {ruDate(DATE_FROM)} по{" "}
          {ruDate(dateTo)} (сегодня + {DAYS_AHEAD} дня). Сортировка — по
          убыванию даты: сверху ближайшие матчи, снизу {ruDate(DATE_FROM)}. Внутри дня
          первыми идут более уверенные прогнозы. Название вкладки — про
          уверенность модели, а не про гарантию: фактическая точность показана
          ниже и всегда меньше 100%. Сыгранные матчи стоят в общем списке и
          подсвечены по факту — зелёный прогноз сбылся, красный нет.
        </p>
      </div>

      <section className="panel p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Порог уверенности</h2>
          <nav className="flex flex-wrap items-center gap-2 text-sm">
            {THRESHOLDS.map((t) => (
              <Link
                key={t}
                href={t === DEFAULT_THRESHOLD ? "/top" : `/top?p=${t}`}
                className={`num rounded px-2 py-0.5 ${
                  t === thresholdPct
                    ? "bg-home/20 text-text"
                    : "bg-panel-2 text-muted hover:text-text"
                }`}
                aria-current={t === thresholdPct ? "page" : undefined}
              >
                {t}%
              </Link>
            ))}
          </nav>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="прогнозов в таблице" value={String(shown.length)} />
          <Stat
            label="точность на сыгранных"
            value={accuracy !== null ? `${(accuracy * 100).toFixed(1)}%` : "—"}
          />
          <Stat label="угадано" value={`${hits} / ${resolvedRows.length}`} />
          <Stat label="ожидают матча" value={String(pendingRows)} />
        </div>

        {resolvedRows.length > 0 && resolvedRows.length < 30 && (
          <p className="mt-3 rounded bg-panel-2 p-2.5 text-xs leading-relaxed text-muted">
            Сыгранных матчей мало ({resolvedRows.length}) — точность выше шум.
            На такой выборке и 70%, и 90% статистически совместимы со случайностью.
          </p>
        )}
      </section>

      {shown.length === 0 ? (
        <EmptyState
          title={`В окне нет прогнозов с уверенностью ${thresholdPct}% и выше`}
          hint="Попробуйте понизить порог: на 60–65% прогнозов заметно больше."
        />
      ) : (
        <section>
          <div className="panel overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th className="py-2 pl-4 text-left font-normal">дата</th>
                  <th className="py-2 text-left font-normal">модель</th>
                  <th className="py-2 text-left font-normal">матч</th>
                  <th className="py-2 text-left font-normal">прогноз</th>
                  <th className="py-2 pr-4 text-right font-normal">счёт</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const hit = r.hit;
                  const tone =
                    hit === true ? "text-good" : hit === false ? "text-bad" : "text-muted";
                  const edge =
                    hit === true
                      ? "border-l-good"
                      : hit === false
                        ? "border-l-bad"
                        : "border-l-transparent";
                  return (
                    <tr key={r.key} className="border-b border-line/40">
                      <td
                        className={`num border-l-2 py-2 pl-3 text-xs text-muted whitespace-nowrap ${edge}`}
                      >
                        {longDate(r.date)}
                        <JournalSourceBadge source={r.source} />
                      </td>
                      <td className="py-2 pr-3">
                        <span className="whitespace-nowrap rounded bg-panel-2 px-1.5 py-0.5 text-[11px] text-muted">
                          {r.model}
                        </span>
                      </td>
                      <td className={`py-2 ${r.hit === null ? "" : "text-text"}`}>
                        {r.home} — {r.away}
                      </td>
                      <td className="py-2">
                        <span
                          className={`num whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ${
                            hit === true
                              ? "bg-good/15 text-good"
                              : hit === false
                                ? "bg-bad/15 text-bad"
                                : "bg-panel-2 text-muted"
                          }`}
                          title={
                            hit === true
                              ? "Прогноз сбылся"
                              : hit === false
                                ? "Прогноз не сбылся"
                                : "Матч ещё не сыгран"
                          }
                        >
                          {OUTCOME_LABEL[r.picked] ?? r.picked} ·{" "}
                          {(r.p * 100).toFixed(0)}%
                        </span>
                      </td>
                      <td className={`num py-2 pr-4 text-right ${tone}`}>
                        {r.score ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-muted">
            «Прогноз» — исход с наибольшей вероятностью и сама вероятность.
            Зелёный — сбылся, красный — нет, серый «—» — матч ещё не сыгран.
            Записи с меткой «WF» — walk-forward оценка модели на прошлых сезонах,
            без метки — живой прогноз, сделанный до матча.
          </p>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel-flat p-3">
      <div className="num text-base font-semibold">{value}</div>
      <div className="text-[11px] text-muted">{label}</div>
    </div>
  );
}
