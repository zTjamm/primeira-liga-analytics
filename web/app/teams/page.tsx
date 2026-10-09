import { getEloTable, getDcStrength, getPredictions, teamName } from "@/lib/data";
import { EmptyState } from "@/components/EmptyState";
import { signed } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function TeamsPage() {
  const [elo, dc, predictions] = await Promise.all([
    getEloTable(),
    getDcStrength(),
    getPredictions(),
  ]);

  if (!elo || !dc) {
    return (
      <EmptyState
        title="Рейтинги ещё не посчитаны"
        hint="Elo и Dixon-Coles обучаются в пайплайне на всей доступной истории, включая клубы прошлых сезонов."
      />
    );
  }

  /*
   * Показываем только клубы текущего сезона. В справочнике их 43, но
   * двадцать с лишним — клубы прошлых лет: их параметры схлопываются к нулю,
   * а Elo после возврата к среднему даёт ровно 1500. Выводить двадцать
   * одинаковых строк по 1500 — это шум, а не информация.
   */
  const current = new Set<string>();
  for (const m of predictions?.upcoming ?? []) {
    current.add(m.home_id);
    current.add(m.away_id);
  }

  const dcById = new Map(dc.map((d) => [d.team, d]));
  const rows = elo
    .filter((e) => current.size === 0 || current.has(e.team))
    .map((e) => ({ ...e, dc: dcById.get(e.team) ?? null }))
    .sort((a, b) => b.rating - a.rating);

  const hidden = elo.length - rows.length;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Команды</h1>
        <p className="mt-1 text-sm text-muted">
          Два независимых рейтинга: Elo (накопительный, с домашним преимуществом)
          и Dixon-Coles (сила в текущем окне обучения).
        </p>
      </div>

      <section className="panel p-5">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th className="py-2 text-left font-normal">#</th>
                <th className="py-2 text-left font-normal">команда</th>
                <th className="py-2 text-right font-normal">Elo</th>
                <th className="py-2 text-right font-normal">атака</th>
                <th className="py-2 text-right font-normal">оборона</th>
                <th className="py-2 text-right font-normal">сила</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.team} className="border-b border-line/40">
                  <td className="num py-2 text-muted">{i + 1}</td>
                  <td className="py-2">{teamName(r.team)}</td>
                  <td className="num py-2 text-right">{r.rating.toFixed(0)}</td>
                  <td className="num py-2 text-right text-muted">
                    {r.dc ? signed(r.dc.attack) : "—"}
                  </td>
                  <td className="num py-2 text-right text-muted">
                    {r.dc ? signed(r.dc.defence) : "—"}
                  </td>
                  <td className="py-2 text-right" style={{ width: 150 }}>
                    {r.dc ? <StrengthBar value={r.dc.overall} /> : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mt-3 text-xs text-muted">
          В параметризации Dixon-Coles{" "}
          <span className="num">lambda = exp(атака − оборона)</span>, поэтому{" "}
          <strong className="font-medium text-text">
            больше обороны = лучше оборона
          </strong>
          : параметр вычитается, и чем он выше, тем меньше соперник забивает. По
          той же причине сила — это сумма атаки и обороны, а не разность.
        </p>
        {hidden > 0 && (
          <p className="mt-1 text-xs text-muted">
            Скрыто {hidden} клубов прошлых сезонов: их параметры давно неактуальны,
            а рейтинг Elo после возврата к среднему равен 1500, то есть они
            ничего не различают.
          </p>
        )}
      </section>
    </div>
  );
}

/** Полоса силы: зелёным вправо (сильнее), красным влево (слабее). */
function StrengthBar({ value }: { value: number }) {
  const scale = 0.9; // типичный размах параметров в лиге
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
