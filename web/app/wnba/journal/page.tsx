import Link from "next/link";
import { getWnbaJournal, type Journal } from "@/lib/data";
import { longDate } from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";
import { JournalSourceBadge } from "@/components/JournalSourceBadge";

export const dynamic = "force-dynamic";

const STAGE_ORDER = ["T-72h+", "T-24h", "T-6h", "T-4h"];

export default async function WnbaJournalPage() {
  const journal = await getWnbaJournal();
  if (!journal) {
    return (
      <EmptyState
        title="Журнал WNBA ещё не создан"
        hint="Каждый прогон пайплайна записывает прогнозы в журнал, и он никогда не перезаписывается."
        command="python -m models.wnba_predict"
      />
    );
  }

  const { resolved, pending, summary } = journal;
  const next48 = pending.filter(
    (p) =>
      p.kickoff &&
      new Date(p.kickoff).getTime() - Date.now() < 48 * 3600 * 1000 &&
      new Date(p.kickoff).getTime() > Date.now(),
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Журнал прогнозов — WNBA</h1>
        <p className="mt-1 text-sm text-muted">
          Все сделанные прогнозы с пометкой, совпало или нет. Живые прогнозы
          не затираются: walk-forward записи лежат в отдельном журнале и
          отмечены «WF». Историю дополняет walk-forward оценка модели на
          прошлых сезонах — там модель обучена только на прошлом, прогноз
          сделан уже после матча. Ничья в баскетболе удалена: вероятность
          перераспределена на П1 и П2.
        </p>
      </div>

      {summary.total > 0 ? (
        <Summary summary={summary} />
      ) : (
        <EmptyState
          title="Сыгранных прогнозов пока нет"
          hint="Журнал ведётся с текущего запуска. Как только в данных появятся результаты матчей, здесь покажется точность и ROI."
        />
      )}

      <section>
        <h2 className="mb-3 flex items-center gap-3 text-sm font-medium text-muted">
          Ближайшие 48 часов
          <span className="h-px flex-1 bg-line" />
          <span className="text-xs">матчей: {next48.length}</span>
        </h2>
        {next48.length === 0 ? (
          <p className="panel p-4 text-sm text-muted">
            В ближайшие двое суток матчей нет. Полный список — на{" "}
            <Link href="/wnba" className="link underline">
              странице матчей
            </Link>
            .
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {next48.map((p) => {
              const hours =
                p.kickoff && p.hours_before !== null
                  ? (new Date(p.kickoff).getTime() - Date.now()) / 3600000
                  : null;
              const fav = p.p_home > p.p_away ? "H" : "A";
              return (
                <div key={p.key} className="panel p-4">
                  <div className="mb-2 flex items-center justify-between text-xs text-muted">
                    <span>
                      {longDate(p.date)}
                      {p.matchday ? ` · № ${p.matchday}` : ""}
                    </span>
                    {hours !== null && hours > 0 && (
                      <span
                        className={`rounded px-1.5 py-0.5 ${
                          hours <= 4 ? "bg-home/20 text-home" : "bg-panel-2"
                        }`}
                      >
                        {hours < 1 ? "меньше часа" : `через ${hours.toFixed(0)} ч`}
                      </span>
                    )}
                  </div>
                  <div className="mb-3 flex items-start gap-2">
                    <div className="min-w-0 flex-1 text-right text-[13px] font-medium leading-tight">
                      {p.home_name}
                    </div>
                    <div className="num shrink-0 rounded bg-panel-2 px-1.5 py-0.5 text-center text-[11px] leading-tight text-muted">
                      <div>{fav === "H" ? "П1" : "П2"}</div>
                      <div className="opacity-70">
                        {(Math.max(p.p_home, p.p_away) * 100).toFixed(0)}%
                      </div>
                    </div>
                    <div className="min-w-0 flex-1 text-[13px] font-medium leading-tight">
                      {p.away_name}
                    </div>
                  </div>
                  <div className="bar-track" style={{ height: 6 }}>
                    <div className="bar-seg bg-home" style={{ width: `${p.p_home * 100}%` }} />
                    <div className="bar-seg bg-draw" style={{ width: `${p.p_draw * 100}%` }} />
                    <div className="bar-seg bg-away" style={{ width: `${p.p_away * 100}%` }} />
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted">
                    <span className="num">
                      {(p.p_home * 100).toFixed(1)} / {(p.p_away * 100).toFixed(1)}
                    </span>
                    <span>срез {p.stage}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {resolved.length > 0 && (
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
                  <th className="py-2 text-right font-normal">прогноз П1/П2</th>
                  <th className="py-2 text-right font-normal">
                    вердикт
                    <span className="block text-[10px] font-normal opacity-70">исход</span>
                  </th>
                  {summary.overall?.roi !== null ? (
                    <th className="py-2 pr-4 text-right font-normal">ROI</th>
                  ) : null}
                  <th className="py-2 pr-4 text-right font-normal">изменился</th>
                </tr>
              </thead>
              <tbody>
                {resolved.map((r) => (
                  <tr key={r.key} className="border-b border-line/40">
                    <td className="num py-2 pl-4 text-xs text-muted whitespace-nowrap">
                      {longDate(r.date)}
                      <span className="ml-1 opacity-60">{r.stage}</span>
                      <JournalSourceBadge source={r.source} />
                    </td>
                    <td className="py-2">{r.home_name} — {r.away_name}</td>
                    <td className="num py-2 text-right">{r.score}</td>
                    <td className="num py-2 text-right text-xs">
                      {(r.p_home * 100).toFixed(0)}% / {(r.p_away * 100).toFixed(0)}%
                    </td>
                    <td className="py-2 text-right align-top">
                      <div className="whitespace-nowrap">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[11px] ${
                            r.hit ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
                          }`}
                        >
                          {r.hit ? "угадано" : "мимо"}{" "}
                          {r.picked === "H" ? "П1" : "П2"}
                        </span>{" "}
                        <span className="num text-[11px] text-muted">
                          {((r.picked_p ?? 0) * 100).toFixed(0)}%
                        </span>
                      </div>
                    </td>
                    {summary.overall?.roi !== null && (
                      <td className="num py-2 pr-4 text-right">
                        <div className={`text-[11px] ${
                          (r.profit ?? 0) >= 0 ? "text-good" : "text-bad"
                        }`}>
                          {r.profit != null ? `${r.profit >= 0 ? "+" : ""}${r.profit}` : "—"}
                        </div>
                        <div className="text-[10px] text-muted">
                          {r.roi != null ? `${(r.roi * 100).toFixed(0)}%` : "—"}
                        </div>
                      </td>
                    )}
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
          <p className="mt-3 text-xs text-muted">
            «Вердикт» — угадан ли исход с наибольшей вероятностью. Процент рядом —
            вероятность, с которой мы называли ИМЕННО ЭТОТ исход. Ничьих в WNBA
            нет: вероятность перераспределена на П1 и П2.
          </p>
        </section>
      )}
    </div>
  );
}

function Summary({ summary }: { summary: Journal["summary"] }) {
  const o = summary.overall;
  const om = summary.outcome_market;
  const stages = STAGE_ORDER.filter((s) => summary.by_stage[s]);
  const roi = o.roi;

  return (
    <section className="panel p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Итоги по журналу</h2>
        <p className="text-xs text-muted">матчей: {summary.total}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat
          label={
            om
              ? `точность: исход${om.n !== summary.total ? ` (по ${om.n} из ${summary.total})` : ""}`
              : "точность: исход"
          }
          value={om?.accuracy != null ? `${(om.accuracy * 100).toFixed(1)}%` : "—"}
        />
        {roi != null ? (
          <Stat
            label={`ROI${o.total_staked ? ` (ставок ${o.total_staked})` : ""}`}
            value={`${(roi * 100).toFixed(1)}%`}
          />
        ) : null}
        <Stat label="clean profit" value={o.total_profit != null ? `${o.total_profit >= 0 ? "+" : ""}${o.total_profit}` : "—"} />
        <Stat label="log-loss (все матчи)" value={o.logloss !== null ? o.logloss.toFixed(4) : "—"} />
        <Stat
          label="калибровка: ср. вероятность на факт"
          value={`${((o.avg_p_actual ?? 0) * 100).toFixed(1)}%`}
        />
        <Stat label="мнение менялось" value={`${summary.flipped} / ${summary.multi_stage}`} />
      </div>
      {summary.total < 30 && (
        <p className="mt-3 rounded bg-panel-2 p-2.5 text-xs leading-relaxed text-muted">
          Матчей мало ({summary.total}), и все проценты выше — шум. На выборке
          такого размера разброс огромен: и 5 попаданий из 7, и 2 из 7
          статистически совместимы со случайностью. Выводы о качестве модели —
          только на сотнях матчей.
        </p>
      )}
      {stages.length > 1 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th className="py-2 text-left font-normal">этап</th>
                <th className="py-2 text-right font-normal">матчей</th>
                <th className="py-2 text-right font-normal">log-loss</th>
                <th className="py-2 text-right font-normal">точность</th>
                {roi != null ? (
                  <th className="py-2 text-right font-normal">ROI</th>
                ) : null}
              </tr>
            </thead>
            <tbody className="num">
              {stages.map((s) => {
                const st = summary.by_stage[s];
                return (
                  <tr key={s} className="border-b border-line/40">
                    <td className="py-2 font-sans">{s}</td>
                    <td className="py-2 text-right">{st.n}</td>
                    <td className="py-2 text-right">{st.logloss?.toFixed(4) ?? "—"}</td>
                    <td className="py-2 text-right">
                      {st.accuracy !== undefined ? `${(st.accuracy * 100).toFixed(1)}%` : "—"}
                    </td>
                    {roi != null && st.roi != null ? (
                      <td className="py-2 text-right">
                        {(st.roi * 100).toFixed(1)}%
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
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
