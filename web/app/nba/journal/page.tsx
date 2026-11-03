import Link from "next/link";
import { getNbaJournal, type Journal } from "@/lib/data";
import { longDate } from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function NbaJournalPage() {
  const journal = await getNbaJournal();
  if (!journal) {
    return (
      <EmptyState
        title="Журнал NBA ещё не создан"
        hint="Запустите: python -m etl.fetch_nba && python -m models.nba_predict"
        command="python -m etl.fetch_nba"
      />
    );
  }

  const { resolved, pending, summary } = journal;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Журнал прогнозов — NBA</h1>
        <p className="mt-1 text-sm text-muted">
          Все сделанные прогнозы с пометкой, совпало или нет. Записи никогда не
          перезаписываются.
        </p>
      </div>

      {summary.total > 0 ? (
        <Summary summary={summary} />
      ) : (
        <EmptyState
          title="Сыгранных прогнозов пока нет"
          hint="Журнал ведётся с текущего запуска."
        />
      )}

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
                  <th className="py-2 text-right font-normal">вердикт</th>
                  <th className="py-2 pr-4 text-right font-normal">изменился</th>
                </tr>
              </thead>
              <tbody>
                {resolved.map((r) => (
                  <tr key={r.key} className="border-b border-line/40">
                    <td className="num py-2 pl-4 text-xs text-muted whitespace-nowrap">
                      {longDate(r.date)}
                      <span className="ml-1 opacity-60">{r.stage}</span>
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
            вероятность, с которой мы называли ИМЕННО ЭТОТ исход.
          </p>
        </section>
      )}
    </div>
  );
}

function Summary({ summary }: { summary: Journal["summary"] }) {
  const o = summary.overall;
  const stages = Object.entries(summary.by_stage).filter(([, v]) => v.n > 0);

  return (
    <section className="panel p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Итоги по журналу</h2>
        <p className="text-xs text-muted">матчей: {summary.total}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="точность: исход" value={`${(o.accuracy * 100).toFixed(1)}%`} />
        <Stat
          label="log-loss (все матчи)"
          value={o.logloss !== null ? o.logloss.toFixed(4) : "—"}
        />
        <Stat
          label="калибровка: ср. вероятность на факт"
          value={`${((o.avg_p_actual ?? 0) * 100).toFixed(1)}%`}
        />
        <Stat label="мнение менялось" value={`${summary.flipped} / ${summary.multi_stage}`} />
      </div>
      {stages.length > 1 && (
        <div className="mt-4 overflow-x-auto">
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
              {[...stages].map(([s, st]) => (
                <tr key={s} className="border-b border-line/40">
                  <td className="py-2 font-sans">{s}</td>
                  <td className="py-2 text-right">{st.n}</td>
                  <td className="py-2 text-right">{st.logloss?.toFixed(4) ?? "—"}</td>
                  <td className="py-2 text-right">
                    {st.accuracy !== undefined ? `${(st.accuracy * 100).toFixed(1)}%` : "—"}
                  </td>
                </tr>
              ))}
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
