/**
 * Таблица рейтингов команд.
 *
 * Общая для НХЛ, NBA и WNBA: модель у них одна и та же
 * (models/basketball.py), поэтому и формат таблицы одинаковый —
 * отличаются только данные.
 */
import type { VtbStrengthRow } from "@/lib/data";
import { signed } from "@/lib/format";

export function TeamsTable({
  title,
  subtitle,
  strength,
  activeIds,
  missingNote,
}: {
  title: string;
  subtitle: string;
  strength: VtbStrengthRow[] | null;
  /** id команд текущего сезона: остальные в таблице не показываем. */
  activeIds: string[];
  missingNote: string;
}) {
  const active = new Set<string>(activeIds);
  const rows = (strength ?? []).filter(
    (r) => active.size === 0 || active.has(r.team),
  );
  const hidden = (strength ?? []).length - rows.length;
  const spread =
    (strength ?? []).length > 0
      ? Math.max(...(strength ?? []).map((r) => r.overall)) -
        Math.min(...(strength ?? []).map((r) => r.overall))
      : 0;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="mt-1 text-sm text-muted">{subtitle}</p>
      </div>

      {!strength && (
        <section className="panel-flat p-4 text-sm text-muted">
          <p className="font-medium text-text">Примечание</p>
          <p className="mt-1">{missingNote}</p>
        </section>
      )}

      {rows.length > 0 && (
        <section className="panel p-5">
          <div className="overflow-x-auto">
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
                {rows.map((r, i) => (
                  <tr key={r.team} className="border-b border-line/40">
                    <td className="num py-2 pl-4 text-muted">{i + 1}</td>
                    <td className="py-2">{r.name}</td>
                    <td className="num py-2 text-right text-muted">
                      {signed(r.attack, 2)}
                    </td>
                    <td className="num py-2 text-right text-muted">
                      {signed(r.defence, 2)}
                    </td>
                    <td className="py-2 pr-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <div className="h-1.5 w-28 rounded bg-panel-2">
                          <div
                            className={`h-full rounded ${
                              r.overall >= 0 ? "bg-good ml-auto" : "bg-bad mr-auto"
                            }`}
                            style={{
                              width: `${Math.min(100, (Math.abs(r.overall) / 7) * 100)}%`,
                            }}
                          />
                        </div>
                        <span className="num w-11 text-right">
                          {signed(r.overall, 2)}
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 space-y-2 text-sm text-muted">
            <p>
              Параметр обороны вычитается, поэтому чем он выше, тем меньше
              забивает соперник. Сила команды — это сумма атаки и обороны.
            </p>
            {spread > 0 && (
              <p>
                Разрыв между лучшей и худшей командой —{" "}
                <span className="num text-text">{spread.toFixed(1)}</span> очка.
              </p>
            )}
            {hidden > 0 && (
              <p>Скрыто {hidden} клубов, не участвующих в текущем сезоне.</p>
            )}
          </div>
        </section>
      )}

      {rows.length === 0 && active.size > 0 && (
        <section className="panel p-5">
          <p className="text-sm text-muted">
            В ближайших матчах участвуют команды:{" "}
            {[...active].map((id, i) => (
              <span key={i} className="text-text">
                {id}
                {i < active.size - 1 ? ", " : ""}
              </span>
            ))}
            . Рейтинги ещё не рассчитаны.
          </p>
        </section>
      )}
    </div>
  );
}
