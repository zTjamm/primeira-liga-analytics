import Link from "next/link";
import { getJournal, type Journal, type Weather } from "@/lib/data";
import { kickoffMoscow, longDate, OUTCOME_LABEL } from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";
import { ProbBar } from "@/components/ProbBar";

export const dynamic = "force-dynamic";

const STAGE_ORDER = ["T-72h+", "T-24h", "T-6h", "T-4h"];

/** Время начала матча в миллисекундах. В журнале поле nullable:
 *  у части записей API не отдал время, и такие строки просто не считаем
 *  ближайшими, вместо того чтобы ронять рендер на new Date(null). */
function kickoffMs(p: { kickoff: string | null }): number | null {
  if (!p.kickoff) return null;
  const t = new Date(p.kickoff).getTime();
  return Number.isFinite(t) ? t : null;
}

/** Погода лежит в extra — она специфична для футбола и для баскетбола
 *  в общем формате журнала просто отсутствует. */
function weatherOf(p: { extra: Record<string, unknown> | null }) {
  const w = p.extra?.weather;
  return w && typeof w === "object" ? (w as Weather) : null;
}

export default async function HistoryPage() {
  const hist = await getJournal("football");
  if (!hist) {
    return (
      <EmptyState
        title="Журнал прогнозов ещё не создан"
        hint="Каждый прогон пайплайна записывает прогнозы в журнал. Как только матчи будут сыграны, здесь появятся пометки «угадано / не угадано»."
      />
    );
  }

  const { resolved, pending, summary } = hist;
  const now = Date.now();
  const next24 = pending.filter((p) => {
    const t = kickoffMs(p);
    return t !== null && t > now && t - now < 24 * 3600 * 1000;
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Журнал прогнозов</h1>
        <p className="mt-1 text-sm text-muted">
          Каждый прогон записывается в журнал и никогда не перезаписывается, поэтому
          видно не только что предсказали, но и менялось ли мнение к началу матча.
        </p>
      </div>

      {resolved.length === 0 ? (
        <EmptyState
          title="Сыгранных прогнозов пока нет"
          hint="Журнал наполняется по мере игры. Первый тур был сыгран 20 сентября, а прогнозы ведутся с 9 октября — совпадения по датам и не будет ещё несколько туров. Пока можно смотреть прогнозы на ближайшие сутки ниже."
        />
      ) : (
        <Summary summary={summary} />
      )}

      <section>
        <h2 className="mb-3 flex items-center gap-3 text-sm font-medium text-muted">
          Ближайшие 24 часа
          <span className="h-px flex-1 bg-line" />
          <span className="text-xs">матчей: {next24.length}</span>
        </h2>
        {next24.length === 0 ? (
          <p className="panel p-4 text-sm text-muted">
            В ближайшие сутки матчей нет. Ближайшие туры — на{" "}
            <Link href="/" className="link underline">
              главной
            </Link>
            .
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {next24.map((p) => {
              const hours = ((kickoffMs(p) ?? now) - now) / 3600000;
              return (
                <Link
                  key={p.key}
                  href={`/matches/${p.key}`}
                  className="panel block p-4 transition-colors hover:border-home/50"
                >
                  <div className="mb-3 flex items-center justify-between text-xs text-muted">
                    <span>
                      {longDate(p.date)}
                      {p.kickoff ? ` · ${kickoffMoscow(p.kickoff)} МСК` : ""}
                    </span>
                    <span
                      className={`rounded px-1.5 py-0.5 ${
                        hours <= 4 ? "bg-home/20 text-home" : "bg-panel-2"
                      }`}
                    >
                      через {hours < 1 ? "меньше часа" : `${hours.toFixed(1)} ч`}
                    </span>
                  </div>
                  <div className="mb-3 flex items-center gap-3">
                    <div className="min-w-0 flex-1 text-right text-[13px] font-medium leading-tight">
                      {p.home_name}
                    </div>
                    <div className="num shrink-0 rounded bg-panel-2 px-2 py-0.5 text-[11px] text-muted">
                      {OUTCOME_LABEL[p.stage.startsWith("T") ? topOutcome(p) : "—"]}
                    </div>
                    <div className="min-w-0 flex-1 text-[13px] font-medium leading-tight">
                      {p.away_name}
                    </div>
                  </div>
                  <ProbBar home={p.p_home} draw={p.p_draw} away={p.p_away} height={6} />
                  <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
                    <span className="num">
                      {(p.p_home * 100).toFixed(1)} / {(p.p_draw * 100).toFixed(1)} /{" "}
                      {(p.p_away * 100).toFixed(1)}
                    </span>
                    <span>срез {p.stage}</span>
                  </div>
                  {(() => {
                    const w = weatherOf(p);
                    return w ? (
                      <p className="mt-1 text-[11px] text-muted">
                        {w.temp_c}°C, осадки {w.precip_prob}%, ветер {w.wind_kmh} км/ч ·{" "}
                        {w.island}
                      </p>
                    ) : null;
                  })()}
                </Link>
              );
            })}
          </div>
        )}
      </section>

      {resolved.length > 0 && (
        <>
          <section>
            <h2 className="mb-3 flex items-center gap-3 text-sm font-medium text-muted">
              Сыгранные матчи
              <span className="h-px flex-1 bg-line" />
              <span className="text-xs">матчей: {resolved.length}</span>
            </h2>
            <div className="panel overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-xs text-muted">
                    <th className="py-2 pl-4 text-left font-normal">дата</th>
                    <th className="py-2 text-left font-normal">матч</th>
                    <th className="py-2 text-right font-normal">счёт</th>
                    <th className="py-2 text-right font-normal">прогноз П1/Х/П2</th>
                    <th className="py-2 text-right font-normal">вердикт</th>
                    <th className="py-2 pr-4 text-right font-normal">изменился</th>
                  </tr>
                </thead>
                <tbody>
                  {resolved.map((r) => (
                    <tr key={r.key} className="border-b border-line/40">
                      <td className="py-2 pl-4 text-xs text-muted whitespace-nowrap">
                        {r.date}
                        <span className="ml-1 opacity-60">{r.stage}</span>
                      </td>
                      <td className="py-2">
                        <Link
                          href={`/matches/${r.key}`}
                          className="link hover:underline"
                        >
                          {r.home_name} — {r.away_name}
                        </Link>
                      </td>
                      <td className="num py-2 text-right">{r.score}</td>
                      <td className="num py-2 text-right text-xs">
                        {(r.p_home * 100).toFixed(0)} / {(r.p_draw * 100).toFixed(0)} /{" "}
                        {(r.p_away * 100).toFixed(0)}
                      </td>
                      <td className="py-2 text-right">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[11px] ${
                            r.hit ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
                          }`}
                        >
                          {r.hit ? "угадано" : "мимо"}
                        </span>
                        {r.p_actual !== null && (
                          <span className="num ml-1.5 text-[11px] text-muted">
                            {(r.p_actual * 100).toFixed(0)}%
                          </span>
                        )}
                      </td>
                      <td className="num py-2 pr-4 text-right text-xs text-muted">
                        {r.n_versions > 1 ? (
                          <span className={r.flipped ? "text-text" : ""}>
                            {r.flipped ? "да" : "нет"} · {r.shift.toFixed(2)}
                          </span>
                        ) : (
                          "1 срез"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <p className="text-xs text-muted">
            «Вердикт» — угадан ли исход с наибольшей вероятностью. Процент рядом —
            насколько высокой была вероятность, выданная на фактический исход: чем
            она выше, тем сильнее ошибка при неверном результате. «Изменился» — сдвиг
            вероятностей между первым и последним срезом; значения больше 0.01
            означают, что модель пересмотрела мнение.
          </p>
        </>
      )}
    </div>
  );
}

function topOutcome(p: { p_home: number; p_draw: number; p_away: number }): string {
  const arr = [p.p_home, p.p_draw, p.p_away];
  return ["H", "D", "A"][arr.indexOf(Math.max(...arr))];
}

function Summary({ summary }: { summary: Journal["summary"] }) {
  if (!summary.total) return null;
  const o = summary.overall;
  const stages = STAGE_ORDER.filter((s) => summary.by_stage[s]);
  const best = stages.length
    ? stages.reduce((a, b) =>
        (summary.by_stage[a].logloss ?? 9) <= (summary.by_stage[b].logloss ?? 9) ? a : b,
      )
    : null;
  const earliest = stages[0];

  return (
    <section className="panel p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Итоги по журналу</h2>
        <p className="text-xs text-muted">матчей: {summary.total}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="точность" value={`${((o.accuracy ?? 0) * 100).toFixed(1)}%`} />
        <Stat label="log-loss" value={o.logloss !== null ? o.logloss.toFixed(4) : "—"} />
        <Stat
          label="средняя вероятность на факт"
          value={o.avg_p_actual !== undefined ? `${(o.avg_p_actual * 100).toFixed(1)}%` : "—"}
        />
        <Stat
          label="мнение менялось"
          value={`${summary.flipped} / ${summary.multi_stage}`}
        />
      </div>

      {stages.length > 1 && (
        <div className="mt-4">
          <p className="mb-2 text-xs text-muted">
            Помогает ли обновление ближе к матчу — главный вопрос, ради которого
            ведётся журнал. Сравниваем один и тот же матч, предсказанный на разных
            этапах.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th className="py-2 text-left font-normal">этап</th>
                  <th className="py-2 text-right font-normal">матчей</th>
                  <th className="py-2 text-right font-normal">log-loss</th>
                  <th className="py-2 text-right font-normal">точность</th>
                </tr>
              </thead>
              <tbody className="num">
                {stages.map((s) => {
                  const st = summary.by_stage[s];
                  return (
                    <tr key={s} className="border-b border-line/40">
                      <td className="py-2 font-sans">
                        {s}
                        {s === best && (
                          <span className="ml-2 rounded bg-good/15 px-1.5 py-0.5 text-[10px] text-good">
                            лучший
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-right">{st.n}</td>
                      <td className="py-2 text-right">{st.logloss?.toFixed(4) ?? "—"}</td>
                      <td className="py-2 text-right">
                        {st.accuracy !== undefined ? `${(st.accuracy * 100).toFixed(1)}%` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {earliest && best && earliest !== best && (
            <p className="mt-2 text-xs text-muted">
              Лучший этап — <span className="num text-text">{best}</span>, а худший —{" "}
              <span className="num text-text">{earliest}</span>.
            </p>
          )}
        </div>
      )}
    </section>
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

