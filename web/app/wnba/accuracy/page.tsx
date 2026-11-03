import Link from "next/link";
import { getWnbaJournal, type Journal } from "@/lib/data";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function WnbaAccuracyPage() {
  const journal = await getWnbaJournal();

  if (!journal) {
    return (
      <EmptyState
        title="Метрики WNBA ещё не посчитаны"
        hint="Прогоните пайплайн: модель обучается на прошлых матчах, и метрики появятся в журнале."
        command="python -m models.wnba_predict"
      />
    );
  }

  const { summary } = journal;
  const o = summary.overall;
  const om = summary.outcome_market;

  if (summary.total === 0) {
    return (
      <EmptyState
        title="Нет сыгранных прогнозов для оценки"
        hint="Как только в данных появятся результаты матчей, здесь будут точность, log-loss и ROI."
      />
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Точность модели — WNBA</h1>
        <p className="mt-1 text-sm text-muted">
          Честная оценка: сначала ограничения, потом цифры.
        </p>
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
        </div>
      </section>

      <section className="panel p-5">
        <h2 className="mb-3 text-sm font-semibold">По этапам</h2>
        {(() => {
          const stages = Object.entries(summary.by_stage).filter(
            ([, v]) => v.n > 0,
          );
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
                        {st.accuracy !== undefined ? `${(st.accuracy * 100).toFixed(1)}%` : "—"}
                      </td>
                      {o.roi != null && st.roi != null ? (
                        <td className="py-2 text-right">
                          {(st.roi * 100).toFixed(1)}%
                        </td>
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
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            Сравнения с букмекерской линией нет: коэффициентов на WNBA в
            открытых источниках не найдено, поэтому утверждать выигрыш в
            деньгах нельзя.
          </li>
          <li>
            Три сезона — это мало. Разницу между моделями нельзя отличить от
            особенностей конкретного сезона.
          </li>
          <li>
            Статистики матча нет вообще: ни владения, ни процента
            реализации. Атака и оборона разделены только математически, из
            данных это не проверяется.
          </li>
        </ul>
      </section>

      <p className="text-xs text-muted">
        Прогнозы — на{" "}
        <Link href="/wnba" className="link underline">
          странице матчей
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
