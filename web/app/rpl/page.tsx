import Link from "next/link";
import { getRplPredictions } from "@/lib/data";
import type { RplPrediction } from "@/lib/data";
import { longDate, kickoffMoscow, OUTCOME_LABEL } from "@/lib/format";
import { ProbBar } from "@/components/ProbBar";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

/** Карточка матча РФПЛ. Отдельная от MatchCard, а не переиспользованная:
 *  у РФПЛ нет страницы матча, нет азиатского хэндикапа и нет коэффициентов
 *  в самом прогнозе — тащить чужой компонент значило бы показывать пустые
 *  поля как будто они посчитаны. */
function RplCard({ m }: { m: RplPrediction }) {
  const over = m.total_picked === "over";
  const totalConf = Math.max(m.over25, m.under25);
  return (
    <div className="panel block p-4">
      <div className="mb-3 flex items-center justify-between text-xs text-muted">
        <span>
          {longDate(m.date)} · {kickoffMoscow(m.kickoff_utc)} МСК
          {m.matchday ? ` · ${m.matchday}` : ""}
        </span>
        <span className="num">
          xG {m.xg_home.toFixed(2)} : {m.xg_away.toFixed(2)}
        </span>
      </div>

      <div className="mb-3 flex items-start gap-3">
        <div className="min-w-0 flex-1 text-right text-[13px] font-medium leading-tight">
          {m.home_name}
        </div>
        <div
          className="num mt-0.5 shrink-0 rounded bg-panel-2 px-1.5 py-0.5 text-center text-[11px] leading-tight text-muted"
          title={
            m.verdict_given
              ? "Наиболее вероятный исход и его вероятность"
              : `Вердикт не выдан. ${m.verdict_reason}`
          }
        >
          <div className={m.verdict_given ? "" : "opacity-50"}>
            {m.verdict_given ? OUTCOME_LABEL[m.prediction] : "—"}
          </div>
          <div className="opacity-70">{(m.confidence * 100).toFixed(0)}%</div>
        </div>
        <div className="min-w-0 flex-1 text-[13px] font-medium leading-tight">
          {m.away_name}
        </div>
      </div>

      <ProbBar home={m.p_home} draw={m.p_draw} away={m.p_away} height={6} />

      <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
        <span className="num">
          {(m.p_home * 100).toFixed(1)} / {(m.p_draw * 100).toFixed(1)} /{" "}
          {(m.p_away * 100).toFixed(1)}
        </span>
        <span
          className="cursor-help"
          title={
            m.total_given
              ? "Тотал — отдельный рынок с тем же порогом 0.55. Для РФПЛ линия наша собственная (2.5), а не рыночная"
              : `Вердикт по тоталу не выдан. ${m.total_reason}`
          }
        >
          {/* Подпись линии стоит всегда, даже при отсеве. Прочерк «—»
              читался как «тотала нет» (на карточке Зенит — Ахмат так и
              вышло): расчёт есть, процент есть, спрятано лишь решение.
              Поэтому показываем ТБ/ТМ и метку «без вердикта». */}
          <span className={m.total_given ? "" : "opacity-50"}>
            {over ? "ТБ 2.5" : "ТМ 2.5"}
          </span>{" "}
          <span className="num">{(totalConf * 100).toFixed(0)}%</span>
          {!m.total_given && (
            <span className="ml-1.5 rounded bg-line/20 px-1 py-0.5 text-[10px] text-muted">
              без вердикта
            </span>
          )}
        </span>
      </div>

      {!m.verdict_given && (
        <p className="mt-2 border-t border-line/60 pt-2 text-[11px] leading-snug text-muted">
          <span className="text-text">Вердикт не выдан.</span>{" "}
          {m.team_games > 0 && m.team_games < 10
            ? `У одной из команд всего ${m.team_games} матчей в обучении — параметры условны.`
            : `Вероятности почти равны (${(m.confidence * 100).toFixed(0)}%), исход определяется шумом.`}
        </p>
      )}
      {/* Причина отсева по тоталу — текстом, а не только тултипом:
          на телефоне подсказок нет, и строка «— 53%» выглядела бы
          как отсутствующий рынок. */}
      {!m.total_given && (
        <p className={`${m.verdict_given ? "mt-2 border-t border-line/60 pt-2 " : "mt-1 "}text-[11px] leading-snug text-muted`}>
          <span className="text-text">Тотал 2.5 без вердикта.</span>{" "}
          {m.total_reason}
        </p>
      )}
    </div>
  );
}

export default async function RplPage() {
  const file = await getRplPredictions();

  if (!file || file.upcoming.length === 0) {
    return (
      <EmptyState
        title="Прогнозы РФПЛ ещё не собраны"
        hint="РФПЛ живёт отдельно от португальского футбола: свои данные (football-data.co.uk + API premierliga.ru), своя модель, свой файл прогнозов."
        command="python -m etl.fetch_rpl && python -m models.rpl_predict"
      />
    );
  }

  const upcoming = file.upcoming;
  const withVerdict = upcoming.filter((m) => m.verdict_given);

  const byDate = new Map<string, RplPrediction[]>();
  for (const m of upcoming) {
    const list = byDate.get(m.date) ?? [];
    list.push(m);
    byDate.set(m.date, list);
  }
  const dates = [...byDate.keys()].sort();

  return (
    <div className="space-y-6">
      <section className="panel p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="text-lg font-semibold">
            Ближайшие матчи · {upcoming.length}
          </h1>
          <p className="text-xs text-muted">
            обучено на <span className="num">{file.n_matches_history}</span>{" "}
            матчах РФПЛ · {file.model}
          </p>
        </div>
        <p className="mt-2 text-xs text-muted">
          Вердикт выдан у <span className="num text-text">{withVerdict.length}</span> из{" "}
          <span className="num">{upcoming.length}</span>. Порог{" "}
          <span className="num">{file.threshold.toFixed(2)}</span> —{" "}
          {file.threshold_source}.{" "}
          <Link href="/rpl/methodology" className="link underline">
            Методика
          </Link>
        </p>
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
              <RplCard key={m.match_id} m={m} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
