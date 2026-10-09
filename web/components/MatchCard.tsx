import Link from "next/link";
import type { Prediction } from "@/lib/data";
import { kickoffMoscow, longDate, OUTCOME_LABEL } from "@/lib/format";
import { ProbBar } from "./ProbBar";

export function MatchCard({ m }: { m: Prediction }) {
  const over = m.over25 >= 0.5;
  return (
    <Link
      href={`/matches/${m.fd_match_id}`}
      className="panel block p-4 transition-colors hover:border-home/50"
    >
      <div className="mb-3 flex items-center justify-between text-xs text-muted">
        <span>
          {longDate(m.date)} · {kickoffMoscow(m.kickoff_utc)} МСК
          {m.matchday ? ` · тур ${m.matchday}` : ""}
        </span>
        <span className="num">
          xG {m.xg_home.toFixed(2)} : {m.xg_away.toFixed(2)}
        </span>
      </div>

      {/* Названия не обрезаем: в португальских клубах длинные официальные имена,
          а «Sporting Clube de B…» читается хуже, чем перенос на две строки. */}
      <div className="mb-3 flex items-start gap-3">
        <div className="min-w-0 flex-1 text-right text-[13px] font-medium leading-tight">
          {m.home_name}
        </div>
        {/* Подпись уточняет, что это аргумент в пользу исхода, а не
            сравнение с тоталом: рынки разные, и вероятности между собой
            несравнимы. Подробности — на странице «Точность». */}
        <div
          className="num mt-0.5 shrink-0 rounded bg-panel-2 px-2 py-0.5 text-[11px] text-muted"
          title="Наиболее вероятный исход. С тоталом 2.5 не сравнивается: это разные рынки"
        >
          {OUTCOME_LABEL[m.prediction]}
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
          title="Отдельный рынок от исхода. На тотале модель уступает линии букмекера"
        >
          {over ? "ТБ 2.5" : "ТМ 2.5"}{" "}
          <span className="num">{(Math.max(m.over25, m.under25) * 100).toFixed(0)}%</span>
        </span>
      </div>
    </Link>
  );
}
