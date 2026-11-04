/**
 * Итоги по журналу прогнозов: точность, log-loss, ROI и разбивка по этапам.
 *
 * Общая для НХЛ, NBA и WNBA: журнал ведётся одним модулем
 * (models/forecast.py), поэтому и формат итогов одинаковый.
 */
import Link from "next/link";
import type { Journal } from "@/lib/data";

export function AccuracySummary({
  title,
  intro,
  summary,
  matchesHref,
  matchesLabel,
  limitations,
}: {
  title: string;
  intro: string;
  summary: Journal["summary"];
  matchesHref: string;
  matchesLabel: string;
  limitations: React.ReactNode;
}) {
  const o = summary.overall;
  const om = summary.outcome_market;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="mt-1 text-sm text-muted">{intro}</p>
      </div>

      <section className="panel p-5">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">Итоги по сыгранным матчам</h2>
          <p className="text-xs text-muted">прогнозов: {summary.total}</p>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            label={
              om
                ? `точность: исход${om.n !== summary.total ? ` (по ${om.n} из ${summary.total})` : ""}`
                : "точность: исход"
            }
            value={om?.accuracy != null ? `${(om.accuracy * 100).toFixed(1)}%` : "—"}
          />
          {o.roi != null ? (
            <Stat label="ROI" value={`${(o.roi * 100).toFixed(1)}%`} />
          ) : null}
          {o.total_profit != null ? (
            <Stat
              label="чистая прибыль"
              value={`${o.total_profit >= 0 ? "+" : ""}${o.total_profit}`}
            />
          ) : null}
          <Stat
            label="log-loss (все матчи)"
            value={o.logloss !== null ? o.logloss.toFixed(4) : "—"}
          />
          <Stat
            label="калибровка: ср. вероятность на факт"
            value={`${((o.avg_p_actual ?? 0) * 100).toFixed(1)}%`}
          />
          <Stat label="ставок" value={o.total_staked != null ? `${o.total_staked}` : "—"} />
          {om && om.withheld > 0 ? (
            <Stat
              label="отсеяно порогом"
              value={`${om.withheld} (${((om.withheld / summary.total) * 100).toFixed(0)}%)`}
            />
          ) : null}
        </div>
      </section>

      <section className="panel p-5">
        <h2 className="mb-3 text-sm font-semibold">По этапам</h2>
        {(() => {
          const stages = Object.entries(summary.by_stage).filter(([, v]) => v.n > 0);
          if (stages.length === 0) {
            return <p className="panel p-4 text-sm text-muted">Нет данных по этапам.</p>;
          }
          return (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-xs text-muted">
                    <th className="py-2 text-left font-normal">этап</th>
                    <th className="py-2 text-right font-normal">матчей</th>
                    <th className="py-2 text-right font-normal">log-loss</th>
                    <th className="py-2 text-right font-normal">точность</th>
                    {o.roi != null ? (
                      <th className="py-2 text-right font-normal">ROI</th>
                    ) : null}
                  </tr>
                </thead>
                <tbody className="num">
                  {[...stages].map(([s, st]) => (
                    <tr key={s} className="border-b border-line/40">
                      <td className="py-2 font-sans">{s}</td>
                      <td className="py-2 text-right">{st.n}</td>
                      <td className="py-2 text-right">{st.logloss?.toFixed(4) ?? "—"}</td>
                      <td className="py-2 text-right">
                        {st.accuracy !== undefined
                          ? `${(st.accuracy * 100).toFixed(1)}%`
                          : "—"}
                      </td>
                      {o.roi != null && st.roi != null ? (
                        <td className="py-2 text-right">{(st.roi * 100).toFixed(1)}%</td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })()}
      </section>

      <section className="panel-flat p-4 text-sm text-muted">
        <p className="font-medium text-text">Чего этот результат не доказывает</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">{limitations}</ul>
      </section>

      <p className="text-xs text-muted">
        Прогнозы — на{" "}
        <Link href={matchesHref} className="link underline">
          {matchesLabel}
        </Link>
        .
      </p>
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
