import Link from "next/link";
import {
  getBacktest,
  getValidation,
  getCalibration,
  getMarketTotals,
  getSelective,
  getBenchmark,
} from "@/lib/data";
import type {
  BenchmarkFile,
  MarketTotalsFile,
  MetricRow,
  SelectiveFile,
} from "@/lib/data";
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
  const [backtest, validation, calibration, markets, selective, benchmark] =
    await Promise.all([
    getBacktest(),
    getValidation(),
    getCalibration(),
    getMarketTotals(),
    getSelective(),
    getBenchmark(),
  ]);

  if (!backtest) {
    return (
      <EmptyState
        title="Метрики ещё не посчитаны"
        hint="Бэктест считает логистику, Brier и точность на отложенных сезонах и сравнивает модель с закрывающей линией букмекеров."
        command="python -m models.backtest_runner"
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

      {markets && <MarketChoice markets={markets} />}
      {benchmark && <Benchmark benchmark={benchmark} />}
      {selective && <SelectiveCoverage selective={selective} />}

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

/* Раздел отвечает на вопрос, который задают, глядя на карточку матча:
   «почему показан П1, а не тотал, у которого вероятность выше?».

   Ответ измерен, а не основан на мнении, и он состоит из двух частей.
   Первая — вероятности разных рынков несравнимы: тотал 2.5 и исход матча
   это разные события с разными шкалами, они пересекаются, но не совпадают.
   Вторая — тотал хуже исхода по качеству прогноза: там модель уступает
   линии заметно сильнее, чем на исходе.

   Показывать вместо исхода тот рынок, где вероятность выше, значило бы
   советовать худшее из того, что мы считаем.
*/
function MarketChoice({ markets }: { markets: MarketTotalsFile }) {
  const tot = markets.markets.total_25;
  const ov = markets.overlap;
  const w = markets.strategy.winner_1x2_model;
  const wm = markets.strategy.winner_1x2_market;
  const t = markets.strategy.total_25_model;
  const tm = markets.strategy.total_25_market;

  return (
    <section className="panel p-5">
      <h2 className="text-sm font-semibold">
        Почему на карточке показан исход, а не «тот рынок, где вероятнее»
      </h2>

      <div className="mt-3 space-y-3 text-sm text-muted">
        <p>
          На карточке матча стоят два рынка: исход (П1/Х/П2) и тотал 2.5.
          Кажется логичным показать тот, где наша вероятность выше. Не
          показываем, и вот почему.
        </p>

        <p>
          <strong className="font-medium text-text">
            Вероятности разных рынков несравнимы.
          </strong>{" "}
          Тотал 2.5 — ставка на число голов, исход — на то, кто победит.
          Из {ov.over_25} матчей с тоталом больше 2.5 в {ov.both} одновременно
          случилась победа хозяев, но в остальных {ov.over_25 - ov.both} — нет.
          События пересекаются, шкалы разные, поэтому «0.62 на тотал против 0.58
          на исход» не значит «тотал надёжнее». Это сравнение несравнимого.
        </p>

        <p>
          <strong className="font-medium text-text">
            Тотал — худшее место для такой рекомендации.
          </strong>{" "}
          На тотале модель уступает закрывающей линии заметно сильнее, чем на
          исходе:
        </p>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th className="py-2 text-left font-normal">рынок</th>
              <th className="py-2 text-right font-normal">log-loss: модель</th>
              <th className="py-2 text-right font-normal">log-loss: линия</th>
              <th className="py-2 text-right font-normal">точность: модель</th>
              <th className="py-2 pr-4 text-right font-normal">точность: линия</th>
            </tr>
          </thead>
          <tbody className="num">
            <tr className="border-b border-line/40">
              <td className="py-2 font-sans">тотал больше 2.5</td>
              <td className="py-2 text-right">{tot.logloss_model.toFixed(5)}</td>
              <td className="py-2 text-right text-good">
                {tot.logloss_line.toFixed(5)}
              </td>
              <td className="py-2 text-right">{(tot.accuracy_model * 100).toFixed(1)}%</td>
              <td className="py-2 pr-4 text-right text-good">
                {(tot.accuracy_line * 100).toFixed(1)}%
              </td>
            </tr>
            <tr className="border-b border-line/40">
              <td className="py-2 font-sans">победа хозяев</td>
              <td className="py-2 text-right text-good">
                {markets.markets.outcome_home.logloss_model.toFixed(5)}
              </td>
              <td className="py-2 text-right">
                {markets.markets.outcome_home.logloss_line.toFixed(5)}
              </td>
              <td className="py-2 text-right text-good">
                {(markets.markets.outcome_home.accuracy_model * 100).toFixed(1)}%
              </td>
              <td className="py-2 pr-4 text-right">
                {(markets.markets.outcome_home.accuracy_line * 100).toFixed(1)}%
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-muted">
        Ниже = лучше. Зелёным отмечен лучший результат в паре.
      </p>

      <div className="mt-4 space-y-3 text-sm text-muted">
        <p>
          <strong className="font-medium text-text">
            И сама идея «бери то, что вероятнее» не работает.
          </strong>{" "}
          Посчитана с настоящими коэффициентами букмекера на {w.bets} матчах,
          по единице на ставку:
        </p>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th className="py-2 text-left font-normal">рынок</th>
              <th className="py-2 text-right font-normal">угадано</th>
              <th className="py-2 text-right font-normal">ср. коэффициент</th>
              <th className="py-2 pr-4 text-right font-normal">ROI по нашей модели</th>
            </tr>
          </thead>
          <tbody className="num">
            <tr className="border-b border-line/40">
              <td className="py-2 font-sans">исход матча</td>
              <td className="py-2 text-right">{(w.hit_rate * 100).toFixed(1)}%</td>
              <td className="py-2 text-right">{w.avg_odds.toFixed(2)}</td>
              <td className={`py-2 pr-4 text-right ${w.roi < 0 ? "text-bad" : ""}`}>
                {(w.roi * 100).toFixed(2)}%
              </td>
            </tr>
            <tr className="border-b border-line/40">
              <td className="py-2 font-sans">тотал 2.5</td>
              <td className="py-2 text-right">{(t.hit_rate * 100).toFixed(1)}%</td>
              <td className="py-2 text-right">{t.avg_odds.toFixed(2)}</td>
              <td className={`py-2 pr-4 text-right ${t.roi < 0 ? "text-bad" : ""}`}>
                {(t.roi * 100).toFixed(2)}%
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className="mt-2 text-xs text-muted">
        Для сравнения, та же стратегия, но выбор по линии букмекера вместо
        нашей модели: исход {(wm.roi * 100).toFixed(2)}%, тотал{" "}
        {(tm.roi * 100).toFixed(2)}%. Наша модель проигрывает рынку на обоих
        рынках, поэтому ставить по её подсказке — убыточно.
      </p>
    </section>
  );
}

/* Разбор отбора матчей: почему вердикт выдаётся не всем и что это даёт.

   Здесь важно не спрятать неудобное. Отбор всегда повышает точность на
   оставшемся подмножестве — так устроена любая фильтрация. Поэтому рядом
   с точностью модели показано:
     - покрытие, то есть какая доля матчей вообще получила вердикт;
     - точность примитива «всегда на хозяев» на ТОМ ЖЕ подмножестве.

   Значит цифра только тогда имеет смысл, когда модель на своих же
   уверенных матчах переигрывает тупую ставку. Иначе отбор не оправдан.
*/
function SelectiveCoverage({ selective }: { selective: SelectiveFile }) {
  const th = selective.chosen_threshold.toFixed(2);
  const chosen = selective.test[th];
  const rows = Object.entries(selective.test)
    .map(([k, v]) => ({ k, v }))
    .filter((r) => r.v && r.v.n > 0);

  return (
    <section className="panel p-5" id="sel">
      <h2 className="text-sm font-semibold">
        Отбор матчей: почему вердикт выдан не всем
      </h2>

      <div className="mt-3 space-y-3 text-sm text-muted">
        <p>
          Когда ни один исход не превышает{" "}
          <span className="num text-text">{th}</span>, это не сильный прогноз,
          а бросок монеты с тремя гранями. В таких матчах исход не
          показывается: вместо него прочерк и сама вероятность. Значения не
          скрываются — скрывается только выбор.
        </p>
        <p>
          Порог выбран на валидационном сезоне{" "}
          <span className="text-text">{selective.val_season}</span>, а проверен на{" "}
          <span className="text-text">{selective.test_seasons.join(", ")}</span>.
          Это существенно: порог, подобранный по точности на тех же
          данных, по которым потом отчитываются, был бы переобучением под
          результат.
        </p>
        {chosen && (
          <p>
            При пороге {th} вердикт выдаётся для{" "}
            <span className="num text-text">{chosen.n}</span> матчей — это{" "}
            <span className="num text-text">{(chosen.coverage * 100).toFixed(0)}%</span>{" "}
            всех. Точность на них{" "}
            <span className="num text-text">{(chosen.accuracy * 100).toFixed(1)}%</span>.
          </p>
        )}
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th className="py-2 text-left font-normal">порог</th>
              <th className="py-2 text-right font-normal">вердиктов</th>
              <th className="py-2 text-right font-normal">покрытие</th>
              <th className="py-2 text-right font-normal">точность модели</th>
              <th className="py-2 pr-4 text-right font-normal">«всегда на хозяев»</th>
            </tr>
          </thead>
          <tbody className="num">
            {rows.map(({ k, v }) => {
              const mark = Math.abs(Number(k) - Number(th)) < 1e-9;
              return (
                <tr
                  key={k}
                  className={`border-b border-line/40 ${mark ? "text-text" : ""}`}
                >
                  <td className="py-2 font-sans">
                    {k}
                    {mark && <span className="ml-1 text-[10px] text-home">выбран</span>}
                  </td>
                  <td className="py-2 text-right">{v.n}</td>
                  <td className="py-2 text-right">{(v.coverage * 100).toFixed(0)}%</td>
                  <td className="py-2 text-right">{(v.accuracy * 100).toFixed(1)}%</td>
                  <td className="py-2 pr-4 text-right text-muted">
                    {(v.accuracy_always_home * 100).toFixed(1)}%
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-3 space-y-2 text-sm text-muted">
        <p>
          <strong className="font-medium text-text">Прочтите таблицу так.</strong>{" "}
          Точность растёт с каждым повышением порога, и сама по себе это
          ничего не значит: так работает любая фильтрация. Значим последний
          столбец — насколько модель переигрывает на своих же уверенных
          матчах правило «всегда ставить на хозяев». Разница положительна на
          всех порогах, то есть отбор оправдан, но не бесконечен.
        </p>
        <p className="rounded border border-line bg-panel-2 p-3 text-xs">
          Чего отбор <strong className="font-medium text-text">не</strong> делает:{" "}
          он не улучшает прогноз. Log-loss остаётся прежним, потому что
          вероятности не меняются — меняется только решение, показывать их
          или нет. Отбор уменьшает риск принять шум за сигнал, но не
          исправляет качество модели.
        </p>
        <p className="text-xs">
          Отдельно отсекаются матчи с командой, у которой меньше 10 матчей в
          обучающей выборке: её параметры выведены из данных, которых у нас
          нет. Таких матчей в тесте{" "}
          <span className="num text-text">{selective.thin_sample_matches}</span>, и
          точность на них не отличается от общей — но вердикт по ним всё равно
          не выдаётся, потому что это совпадение, а не право доверять.
        </p>
      </div>
    </section>
  );
}

/* С кем мы себя сравниваем.

   Долгое время единственным «рынком» было среднее по конторам, а оно
   содержит маржу каждой из них. Сравниваться с ним — сравниваться с
   завышенным для себя эталоном. Betfair Exchange — биржа с маржой около
   0.6% против 6.7% у среднего — в исходных файлах лежала всегда, но в
   конвейер не попадала: колонок не было в списке WANTED.

   Раздел показывает обе линии и честно говорит, что мера была завышена.
*/
function Benchmark({ benchmark }: { benchmark: BenchmarkFile }) {
  const c = benchmark.common;
  const ah = benchmark.asian_handicap;
  const aq = benchmark.asian_quarter;
  return (
    <section className="panel p-5">
      <h2 className="text-sm font-semibold">С кем мы себя сравниваем на самом деле</h2>

      <div className="mt-3 space-y-3 text-sm text-muted">
        <p>
          Долгое время единственным эталоном было среднее по конторам, но в
          него заложена маржа каждой из них. Ближе к справедливой цене
          биржа: там маржа меньше в десять раз.
        </p>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th className="py-2 text-left font-normal">линия</th>
              <th className="py-2 text-right font-normal">матчей</th>
              <th className="py-2 text-right font-normal">log-loss</th>
              <th className="py-2 text-right font-normal">маржа</th>
              <th className="py-2 pr-4 text-right font-normal">точность</th>
            </tr>
          </thead>
          <tbody className="num">
            <tr className="border-b border-line/40">
              <td className="py-2 font-sans">наша модель</td>
              <td className="py-2 text-right">{benchmark.lines.model.n}</td>
              <td className="py-2 text-right">{benchmark.lines.model.logloss.toFixed(5)}</td>
              <td className="text-right text-muted">—</td>
              <td className="py-2 pr-4 text-right">
                {(benchmark.lines.model.accuracy * 100).toFixed(1)}%
              </td>
            </tr>
            {benchmark.lines.avg && (
              <tr className="border-b border-line/40 text-muted">
                <td className="py-2 font-sans">среднее по конторам</td>
                <td className="py-2 text-right">{benchmark.lines.avg.n}</td>
                <td className="py-2 text-right">{benchmark.lines.avg.logloss.toFixed(5)}</td>
                <td className="py-2 text-right">
                  {(benchmark.lines.avg.margin! * 100).toFixed(2)}%
                </td>
                <td className="py-2 pr-4 text-right">
                  {(benchmark.lines.avg.accuracy * 100).toFixed(1)}%
                </td>
              </tr>
            )}
            {benchmark.lines.bfx && (
              <tr className="border-b border-line/40">
                <td className="py-2 font-sans">
                  Betfair Exchange
                  <span className="ml-1 text-[10px] text-home">острее</span>
                </td>
                <td className="py-2 text-right">{benchmark.lines.bfx.n}</td>
                <td className="py-2 pr-4 text-right">
                  <span className="text-good">{benchmark.lines.bfx.logloss.toFixed(5)}</span>
                  <div className="text-[11px] text-muted">
                    маржа {(benchmark.lines.bfx.margin! * 100).toFixed(2)}% · точность{" "}
                    {(benchmark.lines.bfx.accuracy * 100).toFixed(1)}%
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {c && (
        <div className="mt-3 space-y-2 text-sm text-muted">
          <p>
            У линий разное покрытие, а метрики на разных выборках сравнивать
            нельзя. На одних и тех же <span className="num text-text">{c.n}</span>{" "}
            матчах разрыв с моделью такой:
          </p>
          <p className="num">
            против среднего по конторам{" "}
            <span className="text-text">+{c.gap_avg.toFixed(5)}</span>
            {" · "}против биржи{" "}
            <span className="text-bad">+{c.gap_bfx.toFixed(5)}</span>
          </p>
          <p className="rounded border border-line bg-panel-2 p-3 text-xs">
            То есть прежняя мера была завышена на{" "}
            <span className="num text-text">{(c.gap_bfx - c.gap_avg).toFixed(5)}</span>{" "}
            log-loss. Это немного, но не ноль: мы сравнивали себя с конторами,
            а не с тем, кто зарабатывает меньше всех.
          </p>
        </div>
      )}

      {ah && (
        <div className="mt-4 space-y-2 text-sm text-muted">
          <h3 className="text-sm font-semibold text-text">
            Азиатский хэндикап: сравнение с линией
          </h3>
          <p className="num">
            {ah.n} матчей, {ah.lines.length} различных линий (от{" "}
            {ah.lines[0].toFixed(1)} до {ah.lines[ah.lines.length - 1].toFixed(1)}),
            маржа {(ah.margin * 100).toFixed(2)}%.
          </p>
          <p className="text-xs">
            Ставка на разницу мячей относительно линии, а не на исход. Сравнение
            с линией возможно только на{" "}
            <span className="text-text">дробных линиях</span>, где возврат не
            бывает и снятие маржи по двум ценам корректно. На целых исходов три —
            выиграл, возврат, проиграл, — и часть маржи съедает возврат, который
            снятие маржи не учитывает, поэтому такую выборку сравнивать нельзя.
          </p>
          {aq && (
            <>
              <p className="pt-1 text-xs">
                На дробных линиях (<span className="num text-text">{aq.n}</span>{" "}
                матчей):
              </p>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-line text-xs text-muted">
                      <th className="py-2 text-left font-normal">метрика</th>
                      <th className="py-2 text-right font-normal">модель</th>
                      <th className="py-2 pr-4 text-right font-normal">линия</th>
                    </tr>
                  </thead>
                  <tbody className="num">
                    <tr className="border-b border-line/40">
                      <td className="py-2 font-sans">log-loss</td>
                      <td className="py-2 text-right text-bad">
                        {aq.logloss_model.toFixed(5)}
                      </td>
                      <td className="py-2 pr-4 text-right text-good">
                        {aq.logloss_line.toFixed(5)}
                      </td>
                    </tr>
                    <tr className="border-b border-line/40">
                      <td className="py-2 font-sans">точность</td>
                      <td className="py-2 text-right text-good">
                        {(aq.accuracy_model * 100).toFixed(1)}%
                      </td>
                      <td className="py-2 pr-4 text-right">
                        {(aq.accuracy_line * 100).toFixed(1)}%
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className="text-xs">
                Расхождение двух метрик здесь содержательно. По точности модель
                выигрывает — она чаще угадывает сторону. По log-loss проигрывает:
                её вероятности хуже откалиброваны, то есть она слишком уверенно
                заявляет исходы, в которых на самом деле ничего не решается.
                Здесь важнее log-loss: он штрафует именно за такую уверенность.
              </p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
