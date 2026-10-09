import Link from "next/link";
import { notFound } from "next/navigation";
import { getPredictions, getPredictionById, teamName } from "@/lib/data";
import { kickoffMoscow, longDate, OUTCOME_FULL, signed } from "@/lib/format";
import { ProbBar, ProbRow } from "@/components/ProbBar";

export const dynamic = "force-dynamic";

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line/60 py-2 last:border-0">
      <span className="text-xs text-muted">{label}</span>
      <span className="num text-sm">{children}</span>
    </div>
  );
}

export default async function MatchPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const predictions = await getPredictions();
  const m = getPredictionById(predictions, id);
  if (!m) notFound();

  const over = m.over25 >= 0.5;

  return (
    <div className="space-y-5">
      <Link href="/" className="link text-sm">
        ← все матчи
      </Link>

      <section className="panel p-5">
        <div className="mb-1 text-xs text-muted">
          {longDate(m.date)} · {kickoffMoscow(m.kickoff_utc)} МСК
          {m.matchday ? ` · тур ${m.matchday}` : ""}
        </div>
        <h1 className="text-lg font-semibold">
          {m.home_name} — {m.away_name}
        </h1>

        <div className="mt-4 space-y-2">
          <ProbBar home={m.p_home} draw={m.p_draw} away={m.p_away} height={10} />
          <ProbRow home={m.p_home} draw={m.p_draw} away={m.p_away} />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Наиболее вероятный исход:</span>
          <span className="rounded bg-panel-2 px-2 py-0.5 font-medium">
            {OUTCOME_FULL[m.prediction]}
          </span>
          <span className="num text-muted">{(m.confidence * 100).toFixed(1)}%</span>
        </div>

        <p className="mt-3 text-xs text-muted">
          Тотал: {over ? "больше 2.5" : "меньше 2.5"} голов с вероятностью{" "}
          <span className="num text-text">
            {(Math.max(m.over25, m.under25) * 100).toFixed(1)}%
          </span>{" "}
          · ожидаемый счёт по модели{" "}
          <span className="num text-text">
            {m.xg_home.toFixed(2)} : {m.xg_away.toFixed(2)}
          </span>
        </p>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="panel p-5">
          <h2 className="mb-3 text-sm font-semibold">Из чего сложился прогноз</h2>
          <p className="mb-3 text-xs text-muted">
            Итоговая вероятность — среднее двух независимых моделей. Обе
            обучены walk-forward: только на матчах, сыгранных раньше.
          </p>
          <Row label="Elo: П1 / X / П2">
            <span>
              {(m.p_home_elo * 100).toFixed(1)} / {(m.p_draw_elo * 100).toFixed(1)} /{" "}
              {((1 - m.p_home_elo - m.p_draw_elo) * 100).toFixed(1)}
            </span>
          </Row>
          <Row label="Dixon-Coles: П1 / X / П2">
            <span>
              {(m.p_home_dc * 100).toFixed(1)} / {(m.p_draw_dc * 100).toFixed(1)} /{" "}
              {((1 - m.p_home_dc - m.p_draw_dc) * 100).toFixed(1)}
            </span>
          </Row>
          <Row label="Разница Elo (с домашним преимуществом)">
            {signed(m.elo_diff, 1)}
          </Row>
          <Row label="Elo: хозяева / гости">
            {m.elo_home.toFixed(0)} / {m.elo_away.toFixed(0)}
          </Row>
        </section>

        <section className="panel p-5">
          <h2 className="mb-3 text-sm font-semibold">Сила команд (Dixon-Coles)</h2>
          <p className="mb-3 text-xs text-muted">
            Параметры лог-пуассоновской модели. Обе величины читаются одинаково:
            <strong className="font-medium text-text"> больше — лучше</strong>. Ноль
            означает средний уровень лиги.
          </p>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="mb-1 text-xs font-medium">{teamName(m.home_id)}</div>
              <Row label="Атака">{signed(m.attack_home)}</Row>
              <Row label="Оборона">{signed(m.defence_home)}</Row>
            </div>
            <div>
              <div className="mb-1 text-xs font-medium">{teamName(m.away_id)}</div>
              <Row label="Атака">{signed(m.attack_away)}</Row>
              <Row label="Оборона">{signed(m.defence_away)}</Row>
            </div>
          </div>
        </section>
      </div>

      <section className="panel-flat p-4 text-xs text-muted">
        Прогноз — это калиброванная вероятность, а не предсказание счёта.
        Открывающие и закрывающие коэффициенты в модели не используются: они
        нужны только как эталон для проверки на странице «Точность».
      </section>
    </div>
  );
}
