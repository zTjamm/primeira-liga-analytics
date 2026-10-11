import Link from "next/link";
import { getJournal, type Journal } from "@/lib/data";
import { longDate } from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

const STAGE_ORDER = ["T-72h+", "T-24h", "T-6h", "T-4h"];

/** Ожидаемый счёт и тотал лежат в extra. Читаем с проверкой типа: в журнале
 *  общий формат на оба спорта, и часть записей могла быть сделана до того,
 *  как эти поля туда положили. */
function extraNum(p: { extra: Record<string, unknown> | null }, key: string): number | null {
  const v = p.extra?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export default async function BasketballJournalPage() {
  const journal = await getJournal("basketball");
  if (!journal) {
    return (
      <EmptyState
        title="Журнал ВТБ ещё не создан"
        hint="Каждый прогон пайплайна записывает прогнозы в журнал, и он никогда не перезаписывается."
        command="python -m models.bt_predict"
      />
    );
  }

  const { resolved, pending, summary } = journal;
  const next48 = pending.filter(
    (p) =>
      p.kickoff &&
      new Date(p.kickoff).getTime() - Date.now() < 48 * 3600 * 1000 &&
      new Date(p.kickoff).getTime() > Date.now(),
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Журнал прогнозов</h1>
        <p className="mt-1 text-sm text-muted">
          Все сделанные прогнозы с пометкой, совпало или нет. Записи никогда не
          перезаписываются, поэтому видно и то, как менялось мнение к началу матча.
        </p>
      </div>

      {summary.total > 0 ? (
        <Summary summary={summary} />
      ) : (
        <EmptyState
          title="Сыгранных прогнозов пока нет"
          /* Дату сюда намеренно не вписываем: она устаревала бы вслед за
             сезоном, а ветка показывается именно когда записей нет. */
          hint="Журнал ведётся с текущего запуска. Как только API вернёт результат очередного матча, здесь появится пометка «угадано / мимо»."
        />
      )}

      <section>
        <h2 className="mb-3 flex items-center gap-3 text-sm font-medium text-muted">
          Ближайшие 48 часов
          <span className="h-px flex-1 bg-line" />
          <span className="text-xs">матчей: {next48.length}</span>
        </h2>
        {next48.length === 0 ? (
          <p className="panel p-4 text-sm text-muted">
            В ближайшие двое суток матчей нет. Полный список — на{" "}
            <Link href="/basketball" className="link underline">
              странице матчей
            </Link>
            .
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {next48.map((p) => {
              const hours =
                p.kickoff && p.hours_before !== null
                  ? (new Date(p.kickoff).getTime() - Date.now()) / 3600000
                  : null;
              const fav = p.p_home > p.p_away ? "H" : "A";
              return (
                <div key={p.key} className="panel p-4">
                  <div className="mb-2 flex items-center justify-between text-xs text-muted">
                    <span>
                      {longDate(p.date)}
                      {p.matchday ? ` · № ${p.matchday}` : ""}
                    </span>
                    {hours !== null && hours > 0 && (
                      <span
                        className={`rounded px-1.5 py-0.5 ${
                          hours <= 4 ? "bg-home/20 text-home" : "bg-panel-2"
                        }`}
                      >
                        {hours < 1 ? "меньше часа" : `через ${hours.toFixed(0)} ч`}
                      </span>
                    )}
                  </div>
                  <div className="mb-3 flex items-start gap-2">
                    <div className="min-w-0 flex-1 text-right text-[13px] font-medium leading-tight">
                      {p.home_name}
                    </div>
                    <div className="num shrink-0 rounded bg-panel-2 px-1.5 py-0.5 text-center text-[11px] leading-tight text-muted">
                      <div>{fav === "H" ? "П1" : "П2"}</div>
                      <div className="opacity-70">
                        {(
                          Math.max(p.p_home, p.p_away) * 100
                        ).toFixed(0)}
                        %
                      </div>
                    </div>
                    <div className="min-w-0 flex-1 text-[13px] font-medium leading-tight">
                      {p.away_name}
                    </div>
                  </div>
                  <div className="bar-track" style={{ height: 6 }}>
                    <div className="bar-seg bg-home" style={{ width: `${p.p_home * 100}%` }} />
                    <div className="bar-seg bg-draw" style={{ width: `${p.p_draw * 100}%` }} />
                    <div className="bar-seg bg-away" style={{ width: `${p.p_away * 100}%` }} />
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted">
                    <span className="num">
                      {(p.p_home * 100).toFixed(1)} / {(p.p_away * 100).toFixed(1)}
                    </span>
                    <span>срез {p.stage}</span>
                  </div>
                  {/* Ожидаемый счёт и тотал — те же величины, что и на вкладке
                      «Матчи». Лежат в extra, общем для обоих спортов журнале. */}
                  {(() => {
                    const eh = extraNum(p, "exp_home_score");
                    const ea = extraNum(p, "exp_away_score");
                    const o = extraNum(p, "over_155");
                    if (eh === null || ea === null) return null;
                    return (
                      <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted">
                        <span className="num">
                          счёт {eh.toFixed(0)}:{ea.toFixed(0)}
                        </span>
                        {/* Это ВЕРОЯТНОСТЬ, а не вердикт. Порог 0.55,
                            применённый к футбольному тоталу, для ВТБ не
                            обоснован: линия 155 выбрана нами как середина
                            распределения, а не взята с рынка, и калировки
                            по баскетболу нет. Поэтому решение не
                            показывается — показывается только то, что
                            модель думает о тотале. */}
                        {o !== null && (
                          <span
                            className="cursor-help"
                            title="Вероятность тотала больше 155 очков по нашей модели. Это не рекомендация: линия 155 выбрана нами, а не взята с рынка, и коэффициентов у ВТБ нет"
                          >
                            ТБ155{" "}
                            <span className="num">{(o * 100).toFixed(0)}%</span>
                          </span>
                        )}
                      </div>
                    );
                  })()}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {resolved.length > 0 && (
        <section>
          <h2 className="mb-3 flex items-center gap-3 text-sm font-medium text-muted">
            Сыгранные матчи
            <span className="h-px flex-1 bg-line" />
            <span className="text-xs">матчей: {resolved.length}</span>
          </h2>
          <div className="panel overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th className="py-2 pl-4 text-left font-normal">дата</th>
                  <th className="py-2 text-left font-normal">матч</th>
                  <th className="py-2 text-right font-normal">счёт</th>
                  <th className="py-2 text-right font-normal">прогноз П1/П2</th>
                  <th className="py-2 text-right font-normal">
                    вердикт
                    <span className="block text-[10px] font-normal opacity-70">
                      исход / тотал
                    </span>
                  </th>
                  <th className="py-2 pr-4 text-right font-normal">изменился</th>
                </tr>
              </thead>
              <tbody>
                {resolved.map((r) => (
                  <tr key={r.key} className="border-b border-line/40">
                    <td className="num py-2 pl-4 text-xs text-muted whitespace-nowrap">
                      {longDate(r.date)}
                      <span className="ml-1 opacity-60">{r.stage}</span>
                    </td>
                    <td className="py-2">
                      {r.home_name} — {r.away_name}
                    </td>
                    <td className="num py-2 text-right">{r.score}</td>
                    <td className="num py-2 text-right text-xs">
                      {(r.p_home * 100).toFixed(0)}% / {(r.p_away * 100).toFixed(0)}%
                    </td>
                    {/* Процент — на НАЗВАННЫЙ исход, а не на фактический.
                        Раньше здесь стоял p_actual: при промахе это
                        вероятность того, что произошло вопреки прогнозу,
                        и рядом со словом «мимо» она читалась как «мы были
                        уверены, но ошиблись». Что именно мы назвали —
                        видно теперь в самой строке. */}
                    {/* Две строки, как в футболе. Тотал — отдельный
                        рынок, а не дополнение: складывать их через «и»
                        нельзя, ошибиться хотя бы на одном значит
                        потерять всё сразу.

                        Отсева по тоталу здесь нет, в отличие от футбола:
                        порог 0.55 обоснован на 918 футбольных матчах, а по
                        ВТБ калировки нет — линия 155 выбрана нами, а не
                        взята с рынка. Переносить порог без обоснования
                        означало бы притворяться точнее, чем мы есть. */}
                    <td className="py-2 text-right align-top">
                      <div className="whitespace-nowrap">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[11px] ${
                            r.hit ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
                          }`}
                        >
                          {r.hit ? "угадано" : "мимо"}{" "}
                          {r.picked === "H" ? "П1" : "П2"}
                        </span>{" "}
                        <span className="num text-[11px] text-muted">
                          {((r.picked_p ?? 0) * 100).toFixed(0)}%
                        </span>
                      </div>
                      {r.total_hit !== null && r.total_label && (
                        <div className="mt-0.5 whitespace-nowrap">
                          <span
                            className={`rounded px-1.5 py-0.5 text-[11px] ${
                              r.total_hit ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
                            }`}
                          >
                            {r.total_hit ? "угадано" : "мимо"}{" "}
                            {r.total_label}
                          </span>{" "}
                          <span className="num text-[11px] text-muted">
                            {((r.total_p ?? 0) * 100).toFixed(0)}%
                          </span>
                        </div>
                      )}
                    </td>
                    <td className="num py-2 pr-4 text-right text-xs text-muted">
                      {r.n_versions > 1 ? (
                        <span className={r.flipped ? "text-text" : ""}>
                          {r.flipped ? "да" : "нет"} · {r.shift.toFixed(2)}
                        </span>
                      ) : (
                        "1 срез"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-muted">
            «Вердикт» — угадан ли исход с наибольшей вероятностью. Процент рядом —
            вероятность, с которой мы называли ИМЕННО ЭТОТ исход: 53% означает, что
            на П1 мы ставили чуть больше половины и потому ошиблись, а не что были
            уверены. Ничьих в лиге почти не бывает (около 0.5%), поэтому на вердикт
            они практически не влияют.
          </p>
        </section>
      )}
    </div>
  );
}

function Summary({ summary }: { summary: Journal["summary"] }) {
  const o = summary.overall;
  const tm = summary.total_market;
  const tLine = tm?.line != null ? String(tm.line) : null;
  const stages = STAGE_ORDER.filter((s) => summary.by_stage[s]);
  return (
    <section className="panel p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Итоги по журналу</h2>
        <p className="text-xs text-muted">матчей: {summary.total}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="точность: исход" value={`${((o.accuracy ?? 0) * 100).toFixed(1)}%`} />
        {tm && (
          <Stat
            label={tLine ? `точность: тотал ${tLine}` : "точность: тотал"}
            value={tm.accuracy !== null ? `${(tm.accuracy * 100).toFixed(1)}%` : "—"}
          />
        )}
        <Stat label="log-loss" value={o.logloss !== null ? o.logloss.toFixed(4) : "—"} />
        <Stat
          /* Калибровка, а не уверенность: средняя вероятность, которую
             модель выдавала на ТОТ исход, который случился. Низкое число
             здесь — это плохо (мы удивляемся исходам), высокое при низкой
             точности — тоже плохо (мы слишком самоуверенны). В таблице
             выше стоит другое: вероятность названного нами исхода. */
          label="калибровка: ср. вероятность на факт"
          value={`${((o.avg_p_actual ?? 0) * 100).toFixed(1)}%`}
        />
        <Stat label="мнение менялось" value={`${summary.flipped} / ${summary.multi_stage}`} />
      </div>
      {stages.length > 1 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th className="py-2 text-left font-normal">этап</th>
                <th className="py-2 text-right font-normal">матчей</th>
                <th className="py-2 text-right font-normal">log-loss</th>
                <th className="py-2 text-right font-normal">точность</th>
              </tr>
            </thead>
            <tbody className="num">
              {stages.map((s) => {
                const st = summary.by_stage[s];
                return (
                  <tr key={s} className="border-b border-line/40">
                    <td className="py-2 font-sans">{s}</td>
                    <td className="py-2 text-right">{st.n}</td>
                    <td className="py-2 text-right">{st.logloss?.toFixed(4) ?? "—"}</td>
                    <td className="py-2 text-right">
                      {st.accuracy !== undefined ? `${(st.accuracy * 100).toFixed(1)}%` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
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
