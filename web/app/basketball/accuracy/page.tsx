import Link from "next/link";
import { getVtbBacktest, getVtbIngest, getVtbPredictions } from "@/lib/data";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function BasketballAccuracyPage() {
  const [backtest, ingest, preds] = await Promise.all([
    getVtbBacktest(),
    getVtbIngest(),
    getVtbPredictions(),
  ]);

  if (!backtest) {
    return (
      <EmptyState
        title="Метрики ВТБ ещё не посчитаны"
        hint="Бэктест идёт строго вперёд по времени: модель обучается только на матчах, сыгранных раньше прогнозируемого."
        command="python -m models.bt_backtest"
      />
    );
  }

  const best = backtest.overall.find((r) => r.model.startsWith("модель"));
  const naive = backtest.overall.find((r) => r.model.startsWith("naive"));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Точность модели — Единая лига ВТБ</h1>
        <p className="mt-1 text-sm text-muted">
          Честная оценка: сначала ограничения, потом цифры.
        </p>
      </div>

      <section className="panel p-5">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">Walk-forward, обучение только на прошлом</h2>
          <p className="text-xs text-muted">прогнозов: {backtest.n_predictions}</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th className="py-2 text-left font-normal">модель</th>
                <th className="py-2 text-right font-normal">матчей</th>
                <th className="py-2 text-right font-normal">log-loss</th>
                <th className="py-2 text-right font-normal">Brier</th>
                <th className="py-2 text-right font-normal">точность</th>
              </tr>
            </thead>
            <tbody className="num">
              {backtest.overall.map((r) => (
                <tr key={r.model} className="border-b border-line/40">
                  <td className="py-2 font-sans">{r.model}</td>
                  <td className="py-2 text-right">{r.n}</td>
                  <td
                    className={`py-2 text-right ${
                      best && r.model === best.model ? "text-good" : ""
                    }`}
                  >
                    {r.logloss.toFixed(5)}
                  </td>
                  <td className="py-2 text-right">{r.brier.toFixed(5)}</td>
                  <td className="py-2 text-right">{(r.accuracy * 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {best && naive && (
          <p className="mt-3 text-sm text-muted">
            Выигрыш по log-loss:{" "}
            <span className="num text-good">
              {(naive.logloss - best.logloss).toFixed(5)}
            </span>
            , по точности:{" "}
            <span className="num text-good">
              {((best.accuracy - naive.accuracy) * 100).toFixed(1)} п.п.
            </span>
            . Это первое в проекте превосходство над наивным baseline по всем метрикам.
          </p>
        )}
      </section>

      <section className="panel p-5">
        <h2 className="mb-3 text-sm font-semibold">По сезонам</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th className="py-2 text-left font-normal">сезон</th>
                <th className="py-2 text-right font-normal">матчей</th>
                <th className="py-2 text-right font-normal">log-loss</th>
                <th className="py-2 text-right font-normal">точность</th>
              </tr>
            </thead>
            <tbody className="num">
              {backtest.per_season.map((r) => (
                <tr key={r.season ?? r.model} className="border-b border-line/40">
                  <td className="py-2 font-sans">{r.season ?? r.model}</td>
                  <td className="py-2 text-right">{r.n}</td>
                  <td className="py-2 text-right">{r.logloss.toFixed(5)}</td>
                  <td className="py-2 text-right">{(r.accuracy * 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-muted">
          Точность падает с 70% в первом сезоне до 67% во втором: возможно, верхушка
          лиги выравнивается и иерархия сжимается. Сезон 2026/27 даёт всего 22
          прогноза — для выводов недостаточно, и мы его не интерпретируем.
        </p>
      </section>

      <section className="panel p-5">
        <h2 className="mb-3 text-sm font-semibold">Откуда берётся сигнал</h2>
        <p className="text-sm text-muted">
          Сила команд в этой лиге меняется очень медленно: винрейт между половинами
          сезона коррелирует на{" "}
          <span className="num text-text">+0.87…+0.90</span>, а винрейт прошлого
          сезона сам по себе предсказывает следующий с точностью{" "}
          <span className="num text-text">75%</span>. Проверено отдельным контрольным
          экспериментом: простое сравнение винрейтов без всякой модели даёт 75.0%
          на отложенном сезоне.
        </p>
        <p className="mt-2 text-sm text-muted">
          Из-за этого горизонт затухания весов выставлен на 730 дней, а не на 180,
          как в футбольной модели: при 180 прошлый сезон терял бы половину веса за
          полгода, что противоречит измеренной устойчивости.
        </p>
      </section>

      {ingest && (
        <section className="panel p-5">
          <h2 className="mb-3 text-sm font-semibold">Что попало в данные</h2>
          <ul className="space-y-1 text-sm">
            <li>
              <span className="text-muted">источник:</span> официальный API Единой
              лиги, <span className="num">api.vtb-league.com</span>, без ключа
            </li>
            <li>
              <span className="text-muted">сезонов доступно:</span>{" "}
              <span className="num">{ingest.seasons_available.join(", ")}</span> —{" "}
              <span className="num text-text">глубше история не отдаётся</span>
            </li>
            <li>
              <span className="text-muted">обучающих матчей:</span>{" "}
              <span className="num">{ingest.trainable}</span> из{" "}
              <span className="num">{ingest.total_rows}</span> строк
            </li>
            <li>
              <span className="text-muted">исключено:</span> плей-офф{" "}
              <span className="num">{ingest.dropped_playoffs}</span> (серии, игры
              внутри коррелированы), матч всех звёзд{" "}
              <span className="num">{ingest.dropped_allstars}</span>, отменённые{" "}
              <span className="num">{ingest.dropped_not_played}</span>
            </li>
            <li>
              <span className="text-muted">средний тотал:</span>{" "}
              <span className="num">{ingest.avg_total_points}</span> очка, посещаемость
              медианой <span className="num">{ingest.attendance_median}</span>
            </li>
          </ul>
        </section>
      )}

      <section className="panel-flat p-4 text-sm text-muted">
        <p className="font-medium text-text">Чего этот результат не доказывает</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            Сравнения с букмекерской линией нет: коэффициентов на эту лигу в
            открытых источниках не найдено, поэтому утверждать выигрыш в деньгах
            нельзя.
          </li>
          <li>
            Два сезона — это мало. Разницу между двумя моделями нельзя отличить от
            особенностей конкретного сезона.
          </li>
          <li>
            Статистики матча нет вообще: ни владения, ни процента реализации. Атака
            и оборона разделены только математически, из данных это не проверяется.
          </li>
          <li>
            Новые клубы получают нейтральные параметры. Например, ЦСКА против
            «Динамо», который только появился, даёт лишь 0.51 — потому что для
            «Динамо» данных ровно ноль.
          </li>
        </ul>
      </section>

      <p className="text-xs text-muted">
        Прогнозы — на{" "}
        <Link href="/basketball" className="link underline">
          странице матчей
        </Link>
        .
      </p>
    </div>
  );
}
