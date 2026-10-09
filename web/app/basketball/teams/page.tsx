import { getVtbStrength, getVtbPredictions } from "@/lib/data";
import { EmptyState } from "@/components/EmptyState";
import { signed } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function BasketballTeamsPage() {
  const [strength, preds] = await Promise.all([getVtbStrength(), getVtbPredictions()]);

  if (!strength) {
    return (
      <EmptyState
        title="Рейтинги ВТБ ещё не посчитаны"
        hint="Сила команд оценивается пуассоновской моделью на всей доступной истории лиги."
        command="python -m models.bt_predict"
      />
    );
  }

  /*
   * Показываем только клубы текущего сезона. В справочнике их 15, но
   * часть ушла после 2024/25 (Пари Нижний Новгород, Астана) и лишь
   * один раз мелькнула в плей-офф. Их параметры заморожены на давно
   * неактуальных значениях — выводить их значило бы показывать шум.
   */
  const active = new Set<string>();
  for (const m of preds?.upcoming ?? []) {
    active.add(m.home_id);
    active.add(m.away_id);
  }
  const rows = strength.filter((r) => active.size === 0 || active.has(r.team));
  const hidden = strength.length - rows.length;

  const spread =
    Math.max(...strength.map((r) => r.overall)) -
    Math.min(...strength.map((r) => r.overall));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Команды</h1>
        <p className="mt-1 text-sm text-muted">
          Сила команд по аддитивной пуассоновской модели. Обе величины читаются
          одинаково: больше — лучше.
        </p>
      </div>

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
                      <span className="num w-11 text-right">{signed(r.overall, 2)}</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-4 space-y-2 text-sm text-muted">
          <p>
            <span className="num text-text">lambda = exp(атака − оборона)</span>, поэтому
            параметр обороны вычитается и{" "}
            <strong className="font-medium text-text">
              больше обороны = лучше оборона
            </strong>
            : чем выше значение, тем меньше забивает соперник. По той же причине сила
            это сумма, а не разность.
          </p>
          <p>
            Разрыв между лучшей и худшей командой —{" "}
            <span className="num text-text">{spread.toFixed(1)}</span> очка при шуме
            разницы около 19–20 очков. Сигнал есть и он устойчив, но не настолько
            велик, чтобы давать вероятности вроде 0.9: отсюда и умеренные прогнозы.
          </p>
          {hidden > 0 && (
            <p>
              Скрыто {hidden} клубов, не участвующих в текущем сезоне: их параметры
              заморожены на давно неактуальных значениях.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
