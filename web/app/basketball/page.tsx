import Link from "next/link";
import { getVtbPredictions, getVtbStrength, getVtbBacktest, getVtbIngest } from "@/lib/data";
import type { VtbPrediction } from "@/lib/data";
import { EmptyState } from "@/components/EmptyState";
import { signed } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Полоса исходов. Ничьих в лиге не бывает, поэтому третий сегмент
 *  показываем честно нулевым, а не прячем. */
function Probs({ p, fav }: { p: VtbPrediction; fav: "H" | "D" | "A" }) {
  const items = [
    { v: p.p_home, label: "П1", cls: "bg-home", txt: "text-home" },
    { v: p.p_draw, label: "X", cls: "bg-draw", txt: "text-draw" },
    { v: p.p_away, label: "П2", cls: "bg-away", txt: "text-away" },
  ];
  return (
    <>
      <div className="bar-track" style={{ height: 6 }}>
        {items.map((i) => (
          <div key={i.label} className={`bar-seg ${i.cls}`} style={{ width: `${i.v * 100}%` }} />
        ))}
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[11px]">
        {items.map((i) => (
          <span key={i.label} className={fav === (i.label === "П1" ? "H" : i.label === "X" ? "D" : "A") ? i.txt : "text-muted"}>
            {i.label} {(i.v * 100).toFixed(1)}%
          </span>
        ))}
      </div>
    </>
  );
}

export default async function BasketballPage() {
  const [preds, strength, backtest, ingest] = await Promise.all([
    getVtbPredictions(),
    getVtbStrength(),
    getVtbBacktest(),
    getVtbIngest(),
  ]);

  if (!preds || preds.upcoming.length === 0) {
    return (
      <EmptyState
        title="Прогнозы ВТБ ещё не собраны"
        hint="Данные берутся из официального API Единой лиги. Запусти обновление, чтобы появились прогнозы на ближайшие матчи."
        command="python -m models.bt_predict"
      />
    );
  }

  const upcoming = preds.upcoming;

  // группируем по фазе: в сезоне 2026/27 формат двухэтапный,
  // и «тур» из API означает разное в разных фазах
  const byPhase = new Map<string, VtbPrediction[]>();
  for (const m of upcoming) {
    const key = m.phase_name?.replace(/\s+/g, " ").trim() || "Матчи";
    const list = byPhase.get(key) ?? [];
    list.push(m);
    byPhase.set(key, list);
  }

  const best = backtest?.overall.find((r) => r.model.startsWith("модель"));
  const naive = backtest?.overall.find((r) => r.model.startsWith("naive"));

  return (
    <div className="space-y-6">
      <section className="panel p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="text-lg font-semibold">Единая лига ВТБ · {upcoming.length} матчей</h1>
          <p className="text-xs text-muted">
            сезон {preds.season} · обучено на{" "}
            <span className="num">{preds.history_matches}</span> матчах
          </p>
        </div>

        {ingest && (
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="матчей в истории" value={String(ingest.trainable)} />
            <Stat label="очков на матч" value={String(ingest.avg_total_points)} />
            <Stat label="победы хозяев" value={`${(ingest.home_win_share * 100).toFixed(1)}%`} />
            <Stat label="ничьих" value="нет" />
          </div>
        )}

        {best && naive && (
          <p className="mt-4 border-l-2 border-line pl-3 text-sm text-muted">
            Модель обходит наивный baseline:{" "}
            <span className="num text-good">{best.logloss.toFixed(4)}</span> log-loss против{" "}
            <span className="num text-muted">{naive.logloss.toFixed(4)}</span>, точность{" "}
            <span className="num text-good">{(best.accuracy * 100).toFixed(1)}%</span> против{" "}
            <span className="num text-muted">{(naive.accuracy * 100).toFixed(1)}%</span>. Сравнения
            с букмекерской линией нет — коэффициентов на эту лигу в открытых
            источниках не найдено, поэтому утверждать выигрыш в деньгах нельзя.{" "}
            <Link href="/basketball/accuracy" className="link underline">
              Подробнее
            </Link>
          </p>
        )}
      </section>

      {[...byPhase.entries()].map(([phase, matches]) => (
        <section key={phase}>
          <h2 className="mb-3 flex items-center gap-3 text-sm font-medium text-muted">
            {phase}
            <span className="h-px flex-1 bg-line" />
            <span className="text-xs">матчей: {matches.length}</span>
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {matches.map((m) => (
              <div key={m.match_id} className="panel p-4">
                <div className="mb-2.5 flex items-center justify-between text-xs text-muted">
                  <span>
                    {m.day_month}
                    {m.weekday ? `, ${m.weekday}` : ""}
                    {m.kickoff_msk ? ` · ${m.kickoff_msk.slice(11)}` : ""}
                  </span>
                  {m.hours_before !== null && m.hours_before >= 0 && m.hours_before < 48 && (
                    <span className="num">через {m.hours_before.toFixed(0)} ч</span>
                  )}
                </div>

                <div className="mb-3 flex items-start gap-2">
                  <div className="min-w-0 flex-1 text-right text-[13px] font-medium leading-tight">
                    {m.home_name}
                  </div>
                  <div className="num shrink-0 rounded bg-panel-2 px-1.5 py-0.5 text-[11px] text-muted">
                    {m.prediction === "H" ? "П1" : "П2"}
                  </div>
                  <div className="min-w-0 flex-1 text-[13px] font-medium leading-tight">
                    {m.away_name}
                  </div>
                </div>

                <Probs p={m} fav={m.prediction} />

                <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
                  <span className="num">
                    счёт {m.exp_home_score.toFixed(0)}:{m.exp_away_score.toFixed(0)}
                  </span>
                  <span className="num">
                    ТБ155 {(m.over_155 * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}

      {strength && (
        <section>
          <h2 className="mb-3 flex items-center gap-3 text-sm font-medium text-muted">
            Сила команд
            <span className="h-px flex-1 bg-line" />
          </h2>
          <div className="panel overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th className="py-2 pl-4 text-left font-normal">#</th>
                  <th className="py-2 text-left font-normal">команда</th>
                  <th className="py-2 text-right font-normal">атака</th>
                  <th className="py-2 text-right font-normal">оборона</th>
                  <th className="py-2 pr-4 text-right font-normal">сила</th>
                </tr>
              </thead>
              <tbody>
                {strength.map((r, i) => (
                  <tr key={r.team} className="border-b border-line/40">
                    <td className="num py-2 pl-4 text-muted">{i + 1}</td>
                    <td className="py-2">{r.name}</td>
                    <td className="num py-2 text-right text-muted">{signed(r.attack, 2)}</td>
                    <td className="num py-2 text-right text-muted">{signed(r.defence, 2)}</td>
                    <td className="num py-2 pr-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <div className="h-1.5 w-24 rounded bg-panel-2">
                          <div
                            className={`h-full rounded ${r.overall >= 0 ? "bg-good ml-auto" : "bg-bad mr-auto"}`}
                            style={{ width: `${Math.min(100, (Math.abs(r.overall) / 7) * 100)}%` }}
                          />
                        </div>
                        {signed(r.overall, 2)}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-muted">
            В аддитивной модели больше — лучше и по атаке, и по обороне, поэтому сила это
            сумма. Разрыв между лучшей и худшей командой —{" "}
            <span className="num">
              {(
                Math.max(...strength.map((r) => r.overall)) -
                Math.min(...strength.map((r) => r.overall))
              ).toFixed(1)}
            </span>{" "}
            очка при шуме разницы около 20 очков: сигнал есть, но он не настолько
            велик, чтобы давать вероятности вроде 0.9.
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
