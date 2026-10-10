import Link from "next/link";
import { notFound } from "next/navigation";
import { getPredictions, getPredictionById, getJournal, teamName } from "@/lib/data";
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
  const [predictions, journal] = await Promise.all([
    getPredictions(),
    getJournal("football"),
  ]);
  const m = getPredictionById(predictions, id);

  /*
   * predictions.json содержит только ПРЕДСТОЯЩИЕ матчи: как только матч
   * сыгран, он оттуда исчезает. Но журнал помнит и прогноз, и результат,
   * и на него ссылается таблица сыгранных матчей. Поэтому ищем в обоих
   * источниках — иначе ссылка из журнала вела бы в 404 ровно у тех
   * матчей, ради которых журнал и нужен.
   */
  const played = !m
    ? (journal?.resolved ?? [])
        .filter((r) => r.key === id)
        .sort((a, b) => (b.generated_at > a.generated_at ? 1 : -1))[0]
    : undefined;

  if (!m && !played) notFound();

  // Сыгранный матч: из predictions.json он уже исчез, поэтому показываем
  // то, что сохранилось в журнале — прогноз и факт рядом.
  if (played) {
    const fav = played.p_home > played.p_away ? "H" : "A";
    return (
      <div className="space-y-5">
        <Link href="/history" className="link text-sm">
          ← журнал прогнозов
        </Link>

        <section className="panel p-5">
          <div className="mb-1 text-xs text-muted">
            {longDate(played.date)}
            {played.kickoff ? ` · ${kickoffMoscow(played.kickoff)} МСК` : ""}
            {played.matchday ? ` · тур ${played.matchday}` : ""}
            {` · срез ${played.stage}`}
          </div>
          <h1 className="text-lg font-semibold">
            {played.home_name} — {played.away_name}
          </h1>

          <div className="mt-4 space-y-2">
            <ProbBar
              home={played.p_home}
              draw={played.p_draw}
              away={played.p_away}
              height={10}
            />
            <ProbRow
              home={played.p_home}
              draw={played.p_draw}
              away={played.p_away}
            />
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted">Прогноз был:</span>
            <span className="rounded bg-panel-2 px-2 py-0.5 font-medium">
              {OUTCOME_FULL[fav]}
            </span>
            <span className="num text-muted">
              {(
                Math.max(played.p_home, played.p_away) * 100
              ).toFixed(1)}
              %
            </span>
            <span
              className={`rounded px-2 py-0.5 font-medium ${
                played.hit ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
              }`}
            >
              {played.hit ? "угадано" : "мимо"}
            </span>
          </div>

          <div className="mt-3 space-y-1 text-sm text-muted">
            <p>
              Фактический счёт:{" "}
              <span className="num text-text">{played.score}</span>
              {played.actual ? ` — ${OUTCOME_FULL[played.actual].toLowerCase()}` : ""}
            </p>
            {played.p_actual !== null && (
              <p>
                Вероятность, выданная на фактический исход:{" "}
                <span className="num text-text">
                  {(played.p_actual * 100).toFixed(1)}%
                </span>
                {played.logloss !== null && (
                  <>
                    {" · "}вклад в log-loss:{" "}
                    <span className="num text-text">{played.logloss.toFixed(4)}</span>
                  </>
                )}
              </p>
            )}
            {played.n_versions > 1 && (
              <p>
                Срезов: <span className="num">{played.n_versions}</span>, сдвиг между
                первым и последним:{" "}
                <span className="num">{played.shift.toFixed(3)}</span>
                {played.flipped ? " — мнение изменилось" : " — мнение не изменилось"}
              </p>
            )}
          </div>
        </section>

        <p className="text-xs text-muted">
          Матч уже сыгран, поэтому он ушёл из списка предстоящих. Подробный разбор
          качества модели — на странице{" "}
          <Link href="/accuracy" className="link underline">
            «Точность»
          </Link>
          .
        </p>
      </div>
    );
  }

  // Дальше — только предстоящий матч. Явная проверка нужна для типов: после
  // раннего возврата по played компилятор не сужает m, потому что условие
  // выше было составным (!m && !played).
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

        {/* Азиатский хэндикап. Связанная линия — та, при которой
            оба стороны поровены. У нас есть линия букмекера для
            предстоящих матчей нет: коэффициенты лиь ходят
            только в исторических файлах. */}
        <div className="mt-4 rounded border border-line bg-panel-2 p-3">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-xs text-muted">
              Азиатский хэндикап · справедливая линия
            </span>
            <span className="num text-sm font-medium">
              {m.ah_fair_line > 0 ? `+${m.ah_fair_line.toFixed(1)}` : m.ah_fair_line.toFixed(1)}
            </span>
          </div>
          <div className="bar-track" style={{ height: 6 }}>
            <div className="bar-seg bg-home" style={{ width: `${m.ah_cover_home * 100}%` }} />
            <div className="bar-seg bg-panel" style={{ width: `${m.ah_push * 100}%` }} />
            <div className="bar-seg bg-away" style={{ width: `${m.ah_cover_away * 100}%` }} />
          </div>
          <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted">
            <span className="num">хозяев {(m.ah_cover_home * 100).toFixed(1)}%</span>
            <span className="num">
              {m.ah_push > 0.005 && `возврат ${(m.ah_push * 100).toFixed(1)}%`}
            </span>
            <span className="num">гостей {(m.ah_cover_away * 100).toFixed(1)}%</span>
          </div>
          <p className="mt-2 text-[11px] text-muted">
            Выигрывают в два и более{" "}
            <span className="num text-text">{(m.ah_home_2plus * 100).toFixed(1)}%</span>, ровно в один{" "}
            <span className="num text-text">{(m.ah_home_exactly_1 * 100).toFixed(1)}%</span>, гости в два и более{" "}
            <span className="num text-text">{(m.ah_away_2plus * 100).toFixed(1)}%</span>.
            {" "}Минус однак от одного разрера обычности.
          </p>
        </div>
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
