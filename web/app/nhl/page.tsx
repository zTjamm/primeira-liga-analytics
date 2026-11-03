import { getNhlPredictions } from "@/lib/data";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function NhlPage() {
  const preds = await getNhlPredictions();
  if (!preds) {
    return (
      <div className="space-y-6">
        <h1 className="text-lg font-semibold">НХЛ</h1>
        <p className="text-sm text-muted">
          Данные пока не загружены. Запустите: python -m etl.fetch_nhl
        </p>
      </div>
    );
  }

  const upcoming = preds.upcoming || [];

  return (
    <div className="space-y-6">
      <section className="panel p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="text-lg font-semibold">НХЛ · {upcoming.length} матчей</h1>
          <p className="text-xs text-muted">
            сезон {preds.season} · обучено на{" "}
            <span className="num">{preds.history_matches}</span> матчах
          </p>
        </div>
        <p className="mt-2 text-xs text-muted">{preds.model}</p>
      </section>

      {upcoming.length > 0 && (
        <section className="panel p-5">
          <h2 className="mb-3 text-sm font-semibold">Ближайшие матчи</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th className="py-2 text-left font-normal">дата</th>
                  <th className="py-2 text-left font-normal">матч</th>
                  <th className="py-2 text-right font-normal">П1 / П2</th>
                  <th className="py-2 text-right font-normal">вердикт</th>
                </tr>
              </thead>
              <tbody>
                {upcoming.map((m: any, i: number) => (
                  <tr key={m.match_id || i} className="border-b border-line/40">
                    <td className="num py-2 text-xs text-muted">{m.date}</td>
                    <td className="py-2">{m.home_name} — {m.away_name}</td>
                    <td className="num py-2 text-right text-xs">
                      {(m.p_home * 100).toFixed(1)} / {(m.p_away * 100).toFixed(1)}
                    </td>
                    <td className="py-2 text-right text-xs">
                      {m.verdict_given ? (
                        <span className="rounded bg-good/15 px-1.5 py-0.5 text-good">
                          {m.prediction} {(m.confidence * 100).toFixed(0)}%
                        </span>
                      ) : (
                        <span className="rounded bg-line/20 px-1.5 py-0.5 text-muted">
                          без вердикта
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <p className="text-xs text-muted">
        Калибровка: {preds.calibration?.applied ? `T=${preds.calibration.T?.toFixed(4)}` : "не применена"}
        {" · "}
        <Link href="/nhl/journal" className="link underline">
          Журнал прогнозов
        </Link>
      </p>
    </div>
  );
}
