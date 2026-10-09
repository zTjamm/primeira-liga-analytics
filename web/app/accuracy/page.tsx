import Link from "next/link";
import { getBacktest, getValidation, getCalibration } from "@/lib/data";
import type { MetricRow } from "@/lib/data";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

const MODEL_ORDER = ["blend", "dixon_coles", "elo", "naive", "empirical", "market"];

function isMarket(model: string) {
  return model.startsWith("market");
}

function MetricsTable({ rows, showSeason = false }: { rows: MetricRow[]; showSeason?: boolean }) {
  const sorted = [...rows].sort((a, b) => {
    const ai = MODEL_ORDER.findIndex((m) => a.model.toLowerCase().includes(m));
    const bi = MODEL_ORDER.findIndex((m) => b.model.toLowerCase().includes(m));
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
  const best = Math.min(...rows.filter((r) => !isMarket(r.model)).map((r) => r.logloss));

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th className="py-2 text-left font-normal">{showSeason ? "сезон" : "модель"}</th>
            <th className="py-2 text-right font-normal">матчей</th>
            <th className="py-2 text-right font-normal">log-loss</th>
            <th className="py-2 text-right font-normal">Brier</th>
            <th className="py-2 text-right font-normal">точность</th>
            <th className="py-2 text-right font-normal">ROI</th>
          </tr>
        </thead>
        <tbody className="num">
          {sorted.map((r, i) => {
            const isBest = !isMarket(r.model) && r.logloss === best;
            return (
              <tr
                key={`${r.season ?? ""}-${r.model}-${i}`}
                className={`border-b border-line/40 ${isMarket(r.model) ? "text-muted" : ""}`}
              >
                <td className="py-2 font-sans">
                  {showSeason ? r.season : r.model}
                  {isMarket(r.model) && <span className="ml-1 text-[10px]">эталон</span>}
                </td>
                <td className="py-2 text-right">{r.n}</td>
                <td className={`py-2 text-right ${isBest ? "text-good" : ""}`}>
                  {r.logloss.toFixed(5)}
                </td>
                <td className="py-2 text-right">{r.brier.toFixed(5)}</td>
                <td className="py-2 text-right">{(r.accuracy * 100).toFixed(2)}%</td>
                <td
                  className={`py-2 text-right ${r.roi !== null && r.roi < 0 ? "text-bad" : ""}`}
                >
                  {r.roi === null ? "—" : `${(r.roi * 100).toFixed(1)}%`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default async function AccuracyPage() {
  const [backtest, validation, calibration] = await Promise.all([
    getBacktest(),
    getValidation(),
    getCalibration(),
  ]);

  if (!backtest) {
    return (
      <EmptyState
        title="Метрики ещё не посчитаны"
        hint="Бэктест считает логистику, Brier и точность на отложенных сезонах и сравнивает модель с закрывающей линией букмекеров."
      />
    );
  }

  const market = backtest.test.find((r) => isMarket(r.model));
  const ours = backtest.test
    .filter((r) => !isMarket(r.model) && !r.model.startsWith("naive") && !r.model.startsWith("empirical"))
    .sort((a, b) => a.logloss - b.logloss)[0];
  const naive = backtest.test.find((r) => r.model.startsWith("naive"));
  // Положительный разрыв = мы хуже рынка: log-loss ниже значит лучше.
  const gap = market && ours ? ours.logloss - market.logloss : null;
  const marketWins = gap !== null && gap > 0;

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold">Точность модели</h1>

      <section className="panel p-5">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">Отложенные тестовые сезоны</h2>
          <p className="text-xs text-muted">
            {backtest.test_seasons.join(", ")} · {ours?.n ?? 0} матчей
          </p>
        </div>

        {gap !== null && (
          <p className="mb-4 rounded border border-line bg-panel-2 p-3 text-sm">
            Лучшая своя модель — <span className="num">{ours!.logloss.toFixed(5)}</span>,{" "}
            закрывающая линия букмекеров —{" "}
            <span className="num">{market!.logloss.toFixed(5)}</span>.{" "}
            <span className={marketWins ? "text-bad" : "text-good"}>
              {marketWins
                ? `Рынок лучше на ${gap.toFixed(5)} log-loss`
                : `Наша модель лучше на ${Math.abs(gap).toFixed(5)} log-loss`}
            </span>
            .{" "}
            {marketWins && (
              <span className="text-muted">
                Ниже log-loss — значит лучше, поэтому выигрывает рынок.
              </span>
            )}
          </p>
        )}

        <MetricsTable rows={backtest.test} />
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="panel p-5">
          <h2 className="mb-3 text-sm font-semibold">По сезонам</h2>
          <MetricsTable rows={backtest.test_per_season} showSeason />
          <p className="mt-3 text-xs text-muted">
            Отставание от рынка воспроизводится сезон за сезоном, а не в одном
            аномальном отрезке. Это устойчивый результат, а не шум.
          </p>
        </section>

        <section className="panel p-5">
          <h2 className="mb-3 text-sm font-semibold">Подбор веса бленда</h2>
          <p className="mb-3 text-xs text-muted">
            Вес выбирался на сезонах {backtest.validation_seasons.join(", ")} и не
            подгонялся под тестовые.
          </p>
          <WeightGrid grid={backtest.weight_grid} chosen={backtest.chosen_weight_dc} />
        </section>
      </div>

      {naive && (
        <section className="panel p-5">
          <h2 className="mb-2 text-sm font-semibold">Что считается успехом</h2>
          <p className="text-sm text-muted">
            Модель обязана обходить наивный baseline — одинаковые вероятности для
            всех матчей. Наш результат{" "}
            <span className="num text-good">
              {(naive.logloss - (ours?.logloss ?? 0)).toFixed(3)}
            </span>{" "}
            log-loss лучше, это и есть подтверждение, что пайплайн работает. Но
            обойти закрывающий рынок пока не удалось: на то, чтобы сократить
            разрыв, нужны признаки, которых у пуассоновской модели нет — форма,
            xG за последние матчи, дни отдыха, ударная статистика.
          </p>
        </section>
      )}

      {calibration && (
        <section className="panel p-5">
          <h2 className="mb-3 text-sm font-semibold">Снятие маржи: почему выбран метод</h2>
          <p className="mb-3 text-xs text-muted">
            Коэффициенты букмекера нельзя принимать за вероятности: в них заложена
            маржа около{" "}
            <span className="num">
              {((calibration.methods[calibration.chosen]?.mean_overround ?? 1) * 100 - 100).toFixed(1)}%
            </span>
            . Метод выбирался по калибровке — насколько средние вероятности
            совпадают с фактическими частотами исходов.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th className="py-2 text-left font-normal">метод</th>
                  <th className="py-2 text-right font-normal">log-loss</th>
                  <th className="py-2 text-right font-normal">П1 / X / П2</th>
                  <th className="py-2 text-right font-normal">отклонение</th>
                </tr>
              </thead>
              <tbody className="num text-sm">
                {Object.entries(calibration.methods).map(([name, m]) => (
                  <tr key={name} className="border-b border-line/40">
                    <td className="py-2 font-sans">
                      {name}
                      {name === calibration.chosen && (
                        <span className="ml-2 rounded bg-good/15 px-1.5 py-0.5 text-[10px] text-good">
                          выбран
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-right">{m.logloss.toFixed(5)}</td>
                    <td className="py-2 text-right text-xs">
                      {(m.mean_probs.H * 100).toFixed(1)} /{" "}
                      {(m.mean_probs.D * 100).toFixed(1)} /{" "}
                      {(m.mean_probs.A * 100).toFixed(1)}
                    </td>
                    <td className="py-2 text-right">{m.calibration_gap.toFixed(4)}</td>
                  </tr>
                ))}
                <tr className="border-b border-line/40 text-muted">
                  <td className="py-2 font-sans">факт</td>
                  <td className="py-2 text-right">—</td>
                  <td className="py-2 text-right text-xs">
                    {(calibration.actual_freq.H * 100).toFixed(1)} /{" "}
                    {(calibration.actual_freq.D * 100).toFixed(1)} /{" "}
                    {(calibration.actual_freq.A * 100).toFixed(1)}
                  </td>
                  <td className="py-2 text-right">—</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      )}

      {validation && (
        <section className="panel p-5">
          <h2 className="mb-3 text-sm font-semibold">Качество данных</h2>
          <ul className="space-y-1 text-sm">
            {validation.checks.map((c) => (
              <li key={c.check} className="flex items-baseline gap-2">
                <span className={c.ok ? "text-good" : "text-bad"}>
                  {c.ok ? "✓" : "✗"}
                </span>
                <span className="text-muted">{c.check}</span>
                {c.detail && <span className="num text-xs">{c.detail}</span>}
              </li>
            ))}
          </ul>
          <CoverageTable rows={validation.coverage} />
        </section>
      )}

      <p className="text-xs text-muted">
        Методика расчёта — на странице{" "}
        <Link href="/methodology" className="link underline">
          «Методика»
        </Link>
        .
      </p>
    </div>
  );
}

function WeightGrid({
  grid,
  chosen,
}: {
  grid: { w: number; logloss: number }[];
  chosen: number;
}) {
  const min = Math.min(...grid.map((g) => g.logloss));
  const max = Math.max(...grid.map((g) => g.logloss));
  return (
    <div>
      <div className="mb-2 flex items-end gap-1" style={{ height: 90 }}>
        {grid.map((g) => {
          const h = 20 + ((max - g.logloss) / (max - min || 1)) * 70;
          const isChosen = Math.abs(g.w - chosen) < 1e-6;
          return (
            <div key={g.w} className="flex flex-1 flex-col items-center gap-1">
              <div
                className="w-full rounded-t"
                style={{
                  height: h,
                  background: isChosen ? "var(--good)" : "var(--panel-2)",
                }}
                title={`w=${g.w}: ${g.logloss.toFixed(5)}`}
              />
              <span className="num text-[9px] text-muted">{g.w.toFixed(1)}</span>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-muted">
        ось X — вес Dixon-Coles от 0 (только Elo) до 1 (только Dixon-Coles).
        Кривая монотонна, поэтому смешивание не даёт выигрыша:{" "}
        <span className="num text-text">w={chosen.toFixed(2)}</span>.
      </p>
    </div>
  );
}

function CoverageTable({
  rows,
}: {
  rows: { season: string; matches: number; xg: number; shots: number; closing: number }[];
}) {
  const recent = rows.slice(-8);
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th className="py-2 text-left font-normal">сезон</th>
            <th className="py-2 text-right font-normal">матчей</th>
            <th className="py-2 text-right font-normal">удары</th>
            <th className="py-2 text-right font-normal">закр. коэфф.</th>
            <th className="py-2 text-right font-normal">xG</th>
          </tr>
        </thead>
        <tbody className="num text-sm">
          {recent.map((r) => (
            <tr key={r.season} className="border-b border-line/40">
              <td className="py-1.5 font-sans text-muted">{r.season}</td>
              <td className="py-1.5 text-right">{r.matches}</td>
              <td className="py-1.5 text-right">{r.shots || "—"}</td>
              <td className="py-1.5 text-right">{r.closing || "—"}</td>
              <td className="py-1.5 text-right">{r.xg || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-muted">
        Показаны последние 8 сезонов из {rows.length}. xG доступен только с
        сезона 2026/27, удары — с 2017/18, закрывающие коэффициенты — с 2019/20.
      </p>
    </div>
  );
}
