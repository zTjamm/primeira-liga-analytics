import { getRplTeams, getRplPredictions } from "@/lib/data";
import { EmptyState } from "@/components/EmptyState";
import { signed } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function RplTeamsPage() {
  const [teamsFile, predictions] = await Promise.all([
    getRplTeams(),
    getRplPredictions(),
  ]);

  if (!teamsFile) {
    return (
      <EmptyState
        title="Рейтинг РФПЛ ещё не посчитан"
        hint="Модель Dixon-Coles обучается только на матчах РФПЛ — параметры не переносятся из португальского футбола."
        command="python -m models.rpl_predict"
      />
    );
  }

  /*
   * Показываем клубы текущего сезона: в истории 38 команд, и клубы
   * прошлых лет дают устаревшие параметры. Состав сезона берём из
   * предстоящих матчей — это ровно те клубы, что играют сейчас.
   */
  const current = new Set<string>();
  for (const m of predictions?.upcoming ?? []) {
    current.add(m.home_name);
    current.add(m.away_name);
  }

  const rows = teamsFile.teams
    .filter((t) => current.size === 0 || current.has(t.team))
    .sort((a, b) => b.overall - a.overall);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Команды РФПЛ</h1>
        <p className="mt-1 text-sm text-muted">
          Параметры Dixon-Coles, обученного на{" "}
          <span className="num">{teamsFile.n_matches}</span> матчах РФПЛ.
          Показаны клубы текущего сезона.
        </p>
      </div>

      <section className="panel p-5">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th className="py-2 text-left font-normal">#</th>
                <th className="py-2 text-left font-normal">команда</th>
                <th className="py-2 text-right font-normal">атака</th>
                <th className="py-2 text-right font-normal">оборона</th>
                <th className="py-2 text-right font-normal">сила</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.team} className="border-b border-line/40">
                  <td className="num py-2 text-muted">{i + 1}</td>
                  <td className="py-2">{r.team}</td>
                  <td className="num py-2 text-right text-muted">
                    {signed(r.attack)}
                  </td>
                  <td className="num py-2 text-right text-muted">
                    {signed(r.defence)}
                  </td>
                  <td className="py-2 text-right" style={{ width: 150 }}>
                    <StrengthBar value={r.overall} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mt-3 text-xs text-muted">
          <span className="num">lambda = exp(атака − оборона)</span>: больше
          обороны — значит лучше оборона, поэтому сила это сумма атаки и
          обороны, а не разность.
        </p>
      </section>
    </div>
  );
}

function StrengthBar({ value }: { value: number }) {
  const scale = 0.9;
  const frac = Math.min(1, Math.abs(value) / scale);
  const good = value >= 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 rounded bg-panel-2">
        <div
          className={`h-full rounded ${good ? "bg-good ml-auto" : "bg-bad mr-auto"}`}
          style={{ width: `${frac * 100}%` }}
        />
      </div>
      <span className="num w-11 text-right text-xs text-muted">
        {signed(value, 2)}
      </span>
    </div>
  );
}
