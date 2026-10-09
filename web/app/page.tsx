import Link from "next/link";
import { getPredictions, getBacktest } from "@/lib/data";
import { longDate } from "@/lib/format";
import { MatchCard } from "@/components/MatchCard";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [predictions, backtest] = await Promise.all([getPredictions(), getBacktest()]);

  if (!predictions || predictions.upcoming.length === 0) {
    return (
      <EmptyState
        title="Прогнозы ещё не собраны"
        hint="Артефакты появляются после прогона пайплайна: он скачивает историю матчей, строит модель и считает прогнозы на ближайшие туры."
        command="python -m etl.run"
      />
    );
  }

  const upcoming = predictions.upcoming;

  // Группируем по дате — так календарь читается лучше, чем плоский список
  const byDate = new Map<string, typeof upcoming>();
  for (const m of upcoming) {
    const list = byDate.get(m.date) ?? [];
    list.push(m);
    byDate.set(m.date, list);
  }
  const dates = [...byDate.keys()].sort();

  const market = backtest?.test.find((r) => r.model.startsWith("market"));
  const ours = backtest?.test.find((r) => r.model.startsWith("blend"));

  return (
    <div className="space-y-6">
      <section className="panel p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="text-lg font-semibold">
            Ближайшие матчи · {upcoming.length}
          </h1>
          <p className="text-xs text-muted">
            сезон {predictions.generated_for_season} · обучено на{" "}
            <span className="num">{predictions.history_matches}</span> матчах
          </p>
        </div>

        {market && ours && (
          <p className="mt-3 border-l-2 border-line pl-3 text-sm text-muted">
            Честная позиция: на отложенных тестовых сезонах модель даёт{" "}
            <span className="num text-text">{ours.logloss.toFixed(3)}</span> log-loss
            против <span className="num text-text">{market.logloss.toFixed(3)}</span> у
            закрывающей линии букмекеров. Подробности — на странице{" "}
            <Link href="/accuracy" className="link underline">
              «Точность»
            </Link>
            .
          </p>
        )}
      </section>

      {dates.map((date) => (
        <section key={date}>
          <h2 className="mb-3 flex items-center gap-3 text-sm font-medium text-muted">
            {longDate(date)}
            <span className="h-px flex-1 bg-line" />
            <span className="text-xs">матчей: {byDate.get(date)!.length}</span>
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {byDate.get(date)!.map((m) => (
              <MatchCard key={m.fd_match_id} m={m} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

