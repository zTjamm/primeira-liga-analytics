import Link from "next/link";
import { getJournal, type Journal } from "@/lib/data";
import { longDate } from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

const STAGE_ORDER = ["T-72h+", "T-24h", "T-6h", "T-4h"];
const OUTCOME_LABEL: Record<string, string> = { H: "П1", D: "X", A: "П2" };

export default async function RplJournalPage() {
  const journal = await getJournal("rpl");
  if (!journal) {
    return (
      <EmptyState
        title="Журнал РФПЛ ещё не создан"
        hint="Каждый прогон пайплайна дописывает прогнозы в леджер, и записи никогда не перезаписываются."
        command="python -m models.rpl_predict"
      />
    );
  }

  const { resolved, pending, summary } = journal;
  const next48 = pending.filter((p) => {
    if (!p.kickoff) return false;
    const t = new Date(p.kickoff).getTime();
    return t - Date.now() < 48 * 3600 * 1000 && t > Date.now();
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Журнал прогнозов РФПЛ</h1>
        <p className="mt-1 text-sm text-muted">
          Все прогнозы с пометкой, совпало или нет. Леджер ведётся с первого
          запуска, поэтому видно и то, как менялось мнение к началу матча.
        </p>
      </div>

      {summary.total > 0 ? (
        <Summary summary={summary} />
      ) : (
        <EmptyState
          title="Сыгранных прогнозов пока нет"
          hint="Журнал начался вместе с пайплайном. Матчи, сыгранные до этого, в него не попадают: прогноза на них не было, и подставлять задним числом было бы нечестно. Как только premierliga.ru вернёт результат, здесь появятся «угадано / мимо»."
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
            <Link href="/rpl" className="link underline">
              странице матчей
            </Link>
            .
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {next48.map((p) => {
              const hours = p.kickoff
                ? (new Date(p.kickoff).getTime() - Date.now()) / 3600000
                : null;
              const conf = Math.max(p.p_home, p.p_draw, p.p_away);
              const given = p.extra?.verdict_given !== false;
              const picked = (["H", "D", "A"] as const)[
                [p.p_home, p.p_draw, p.p_away].indexOf(conf)
              ];
              return (
                <div key={p.key} className="panel p-4">
                  <div className="mb-2 flex items-center justify-between text-xs text-muted">
                    <span>
                      {longDate(p.date)}
                      {p.matchday ? ` · ${p.matchday}` : ""}
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
                    <div
                      className="num shrink-0 rounded bg-panel-2 px-1.5 py-0.5 text-center text-[11px] leading-tight text-muted"
                      title={
                        given
                          ? "Наиболее вероятный исход"
                          : "Вердикт не выдан: вероятности почти равны"
                      }
                    >
                      <div className={given ? "" : "opacity-50"}>
                        {given ? OUTCOME_LABEL[picked] : "—"}
                      </div>
                      <div className="opacity-70">{(conf * 100).toFixed(0)}%</div>
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
                      {(p.p_home * 100).toFixed(1)} / {(p.p_draw * 100).toFixed(1)} /{" "}
                      {(p.p_away * 100).toFixed(1)}
                    </span>
                    <span>срез {p.stage}</span>
                  </div>
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
                  <th className="py-2 text-right font-normal">П1 / Х / П2</th>
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
                      {(r.p_home * 100).toFixed(0)}% / {(r.p_draw * 100).toFixed(0)}% /{" "}
                      {(r.p_away * 100).toFixed(0)}%
                    </td>
                    {/* Процент — на НАЗВАННЫЙ исход, а не на фактический:
                        иначе при промахе рядом со словом «мимо» стояла бы
                        вероятность того, что произошло вопреки прогнозу. */}
                    <td className="py-2 text-right align-top">
                      {r.verdict_given === false ? (
                        <span
                          className="rounded bg-line/20 px-1.5 py-0.5 text-[11px] text-muted"
                          title={r.verdict_reason ?? undefined}
                        >
                          без вердикта
                        </span>
                      ) : (
                        <>
                          <div className="whitespace-nowrap">
                            <span
                              className={`rounded px-1.5 py-0.5 text-[11px] ${
                                r.hit ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
                              }`}
                            >
                              {r.hit ? "угадано" : "мимо"} {OUTCOME_LABEL[r.picked]}
                            </span>{" "}
                            <span className="num text-[11px] text-muted">
                              {((r.picked_p ?? 0) * 100).toFixed(0)}%
                            </span>
                          </div>
                        </>
                      )}
                      {r.total_hit !== null && r.total_label && (
                        <div className="mt-0.5 whitespace-nowrap">
                          {r.total_given === false ? (
                            <span
                              className="rounded bg-line/20 px-1.5 py-0.5 text-[11px] text-muted"
                              title={r.total_no_reason ?? undefined}
                            >
                              тотал без вердикта
                            </span>
                          ) : (
                            <>
                              <span
                                className={`rounded px-1.5 py-0.5 text-[11px] ${
                                  r.total_hit
                                    ? "bg-good/15 text-good"
                                    : "bg-bad/15 text-bad"
                                }`}
                              >
                                {r.total_hit ? "угадано" : "мимо"} {r.total_label}
                              </span>{" "}
                              <span className="num text-[11px] text-muted">
                                {((r.total_p ?? 0) * 100).toFixed(0)}%
                              </span>
                            </>
                          )}
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
            «Без вердикта» означает, что модель отказалась выбирать: вероятность
            наиболее вероятного исхода была ниже порога. Такие матчи в точность
            не входят — иначе отсев был бы незаметен, а результат завышен.
            Порог подобран на самих матчах РФПЛ (walk-forward: валидация
            2023/24, проверка на 2024/25 и 2025/26) — цифры подбора в
            разделе «Методика».
          </p>
        </section>
      )}
    </div>
  );
}

function Summary({ summary }: { summary: Journal["summary"] }) {
  const o = summary.overall;
  const om = summary.outcome_market;
  const tm = summary.total_market;
  const tLine = tm?.line != null ? String(tm.line) : null;
  const stages = STAGE_ORDER.filter((s) => summary.by_stage[s]);
  return (
    <section className="panel p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Итоги по журналу</h2>
        <p className="text-xs text-muted">сыграно прогнозов: {summary.total}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          label={
            om
              ? `точность: исход${om.n !== summary.total ? ` (по ${om.n} из ${summary.total})` : ""}`
              : "точность: исход"
          }
          value={om?.accuracy != null ? `${(om.accuracy * 100).toFixed(1)}%` : "—"}
        />
        {tm && (
          <Stat
            label={tLine ? `точность: тотал ${tLine}` : "точность: тотал"}
            value={tm.accuracy !== null ? `${(tm.accuracy * 100).toFixed(1)}%` : "—"}
          />
        )}
        <Stat label="log-loss (все матчи)" value={o.logloss !== null ? o.logloss.toFixed(4) : "—"} />
        <Stat
          label="отсеяно порогом"
          value={`${(tm?.withheld ?? 0)} по тоталу`}
        />
      </div>
      {summary.total < 30 && (
        <p className="mt-3 rounded bg-panel-2 p-2.5 text-xs leading-relaxed text-muted">
          Матчей мало ({summary.total}), и все проценты выше — шум. На выборке
          такого размера разброс огромен, и выводы о качестве модели делать
          нельзя: ждём сотен матчей.
        </p>
      )}
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
