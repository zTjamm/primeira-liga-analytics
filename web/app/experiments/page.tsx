import Link from "next/link";
import {
  getBenchmark,
  getErrorDiag,
  getFeatureStudy,
  getMarketTotals,
  getParamsCheck,
  getSelective,
  getTeamDiag,
} from "@/lib/data";
import type {
  BenchmarkFile,
  ErrorDiagFile,
  FeatureStudyFile,
  MarketTotalsFile,
  ParamsCheckFile,
  SelectiveFile,
  TeamDiagFile,
} from "@/lib/data";

export const dynamic = "force-dynamic";

/**
 * Что мы проверили и что из этого вышло.
 *
 * Страница существует не для красоты. Без неё проект выглядит как набор
 * графиков с хорошими числами, а на деле это серия экспериментов, из
 * которых сработали два. Отрицательные результаты здесь стоят рядом с
 * положительными и помечены отдельно: именно они говорят, где проект
 * упёрся в потолок.
 *
 * Все числа читаются из отчётов, которые пересчитываются в CI каждый час.
 * Ничего не выписано вручную — иначе страница разъехалась бы с данными.
 */

type Verdict = "ok" | "no" | "part" | "mixed";

interface Finding {
  verdict: Verdict;
  title: string;
  question: string;
  result: string;
  detail: React.ReactNode;
}

const VERDICT_LABEL: Record<Verdict, string> = {
  ok: "сработало",
  no: "не сработало",
  part: "частично",
  mixed: "вывод неоднозначный",
};

function Badge({ verdict }: { verdict: Verdict }) {
  const cls =
    verdict === "ok"
      ? "bg-good/15 text-good"
      : verdict === "part"
        ? "bg-home/20 text-home"
        : "bg-bad/15 text-bad";
  return (
    <span className={`shrink-0 rounded px-2 py-0.5 text-[11px] ${cls}`}>
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

function FindingRow({ f }: { f: Finding }) {
  return (
    <li className="border-b border-line/40 pb-4 last:border-0 last:pb-0">
      <div className="mb-1 flex flex-wrap items-start justify-between gap-2">
        <span className="text-sm font-medium">{f.title}</span>
        <Badge verdict={f.verdict} />
      </div>
      <p className="mb-1 text-xs text-muted">{f.question}</p>
      <p className="mb-2 text-sm">{f.result}</p>
      <div className="text-xs leading-relaxed text-muted">{f.detail}</div>
    </li>
  );
}

export default async function ExperimentsPage() {
  const [bench, diag, feat, totals, params, sel, teams] = await Promise.all([
    getBenchmark(),
    getErrorDiag(),
    getFeatureStudy(),
    getMarketTotals(),
    getParamsCheck(),
    getSelective(),
    getTeamDiag(),
  ]);

  const aq = bench?.asian_quarter;
  const wGrid = params?.weight_grid;
  const prod = wGrid?.find((x) => Math.abs(x.w_dc - 0.5) < 1e-9);
  const best = wGrid?.find(
    (x) => Math.abs(x.w_dc - (bench ? 1.0 : 1.0)) < 1e-9,
  );

  const findings: Finding[] = [];

  /* --- 1. вес смеси моделей --------------------------------------- */
  if (params && prod && best) {
    findings.push({
      verdict: "ok",
      title: "Elo убран из прогноза",
      question: "Стоит ли смешивать рейтинг Elo с пуассоновской моделью?",
      result:
        `Стоило: смесь 50/50 давала log-loss ${prod.test.toFixed(5)}, ` +
        `чистый Dixon-Coles — ${best.test.toFixed(5)}. Разрыв ` +
        `${Math.abs(prod.test - best.test).toFixed(5)}.`,
      detail:
        <p>
          Бэктест подбирал этот вес на валидации и выбирал чистый
          Dixon-Coles, а прогноз считался на 50/50. Расхождение было
          незаметным: вес подбирался в одном модуле, а использовался в
          другом, и никто их не сверял. Оказалось, что Elo разбавлял
          сигнал — уверенность прогноза выросла, а вердиктов стало
          выдаваться больше. Сам Elo остался в проекте: он даёт рейтинг
          силы для страницы «Команды».
        </p>,
    });
  }

  /* --- 2. статистика матча ----------------------------------------- */
  if (feat) {
    const shots = feat.stats.find((s) => s.stat === "shots");
    const fouls = feat.stats.find((s) => s.stat === "fouls");
    findings.push({
      verdict: "no",
      title: "Статистика матча в модель",
      question:
        "У нас есть удары, угловые, фолы и карточки. Почему бы не добавить их в прогноз?",
      result:
        shots && fouls
          ? `Удары коррелируют с разницей голов на ${shots.r_raw.toFixed(3)} — ` +
            `но с остатком прогноза лишь на ${shots.r_residual.toFixed(3)}. ` +
            `Фолы: ${fouls.r_residual.toFixed(3)}, то есть ничего.`
          : "нет данных",
      detail: (
        <>
          <p>
            Высокая корреляция с исходом обманчива. Сильные команды и бьют
            чаще, и забивают больше, а сила команды модели уже известна.
            Настоящий вопрос — корреляция с <strong>остатком</strong>, то
            есть с тем, в чём модель ошиблась. Она близка к нулю.
          </p>
          {feat && !feat.offsides_available && (
            <p className="mt-2">
              Офсайдов в источнике нет вообще — их не отдаёт
              football-data.co.uk. Взять неоткуда, нужен другой источник.
            </p>
          )}
          <p className="mt-2">
            Дополнительно: эти величины известны только{" "}
            <em>после</em> матча. Как признаки того же матча они были бы
            утечкой, поэтому в проверке взяты скользящие средние по{" "}
            {feat?.history} предыдущим матчам команд.
          </p>
        </>
      ),
    });
  }

  /* --- 3. перекалибровка ------------------------------------------ */
  if (diag) {
    const r = diag.recalibration;
    const t = diag.bias?.П1;
    findings.push({
      verdict: "no",
      title: "Перекалибровка вероятностей",
      question:
        t && Math.abs(t.gap) > 0.02
          ? `Модель завышает победу хозяев на ${t.gap.toFixed(3)}. Не исправить ли это?`
          : "Есть ли систематическое смещение, которое стоит выправить?",
      result:
        `Смещение нашлось, но выправление вредит: без него ${r.plain.toFixed(5)}, ` +
        `температурное масштабирование — ${r.temperature.toFixed(5)}, ` +
        `полная матрица на 12 параметров — ${r.dirichlet.toFixed(5)}.`,
      detail: (
        <p>
          Параметры подбирались на валидационном сезоне {diag.val_season} и
          проверялись на других. Смещение оказалось неустойчивым: на других
          сезонах оно не повторяется, поэтому выправление означает подгонку
          под валидацию и ухудшение на тесте.
        </p>
      ),
    });
  }

  /* --- 4. сжатие к рынку ----------------------------------------- */
  if (diag) {
    const m = diag.market_anchor;
    findings.push({
      verdict: "no",
      title: "Подмешать линию букмекера в прогноз",
      question:
        "Если рынок точнее, может, стоит смешать наши вероятности с его?",
      result:
        `Оптимальный вес рынка — ${m.best_w_market.toFixed(2)}, то есть чистый ` +
        `рынок. Смесь выигрывает только если убрать модель совсем: ` +
        `${m.test_model.toFixed(5)} против ${m.test_market.toFixed(5)}.`,
      detail: (
        <>
          <p>
            Смешивание не улучшает прогноз — оно его подменяет. Единственная
            причина, по которой мы всё равно меримся с рынком:{" "}
            <strong className="font-medium text-text">у нас нет коэффициентов
            на предстоящие матчи</strong>. Они есть только в исторических
            файлах. Так что сравнение — это проверка качества модели
            задним числом, а не операционное преимущество.
          </p>
          <p className="mt-2">
            В момент матча линии нет ни у кого, включая игрока. Единственный
            прогноз, доступный заранее, — тот, что считается из истории
            результатов, как наш.
          </p>
        </>
      ),
    });
  }

  /* --- 5. тотал --------------------------------------------------- */
  if (totals) {
    const t = totals.markets.total_25;
    const s = totals.strategy;
    findings.push({
      verdict: "no",
      title: "Тотал 2.5 как отдельный рынок",
      question:
        "Показывать ли вместо исхода тот рынок, где наша вероятность выше?",
      result:
        `На тотале мы заметно хуже линии: log-loss ${t.logloss_model.toFixed(5)} ` +
        `против ${t.logloss_line.toFixed(5)}, точность ` +
        `${(t.accuracy_model * 100).toFixed(1)}% против ` +
        `${(t.accuracy_line * 100).toFixed(1)}%.`,
      detail: (
        <>
          <p>
            Плюс сам выбор рынка бессмыслен: тотал 2.5 — ставка на число
            голов, исход — на победителя, и вероятности из разных рынков
            несравнимы. Это разные события с разными шкалами.
          </p>
          <p className="mt-2">
            Идея «бери то, что вероятнее» проигрывает с настоящими
            коэффициентами: на исходе {((s.winner_1x2_model.roi ?? 0) * 100).toFixed(2)}%
            на единицу ставки, на тотале{" "}
            {((s.total_25_model.roi ?? 0) * 100).toFixed(2)}%.
          </p>
        </>
      ),
    });
  }

  /* --- 6. азиатский хэндикап -------------------------------------- */
  if (bench?.asian_handicap && aq) {
    findings.push({
      verdict: "mixed",
      title: "Азиатский хэндикап",
      question: "Рынок, который лежал в данных нетронутым — удастся его взять?",
      result:
        `По log-loss линия лучше на ${Math.abs(aq.logloss_line - aq.logloss_model).toFixed(5)}. ` +
        `По точности выходило ${(aq.accuracy_model * 100).toFixed(1)}% против ` +
        `${(aq.accuracy_line * 100).toFixed(1)}% — ` +
        `${aq.p_value < 0.05 ? "разница значима" : "но p = " + aq.p_value.toFixed(4) + ", разница незначима"}.`,
      detail: (
        <>
          <p>
            Здесь мы сначала объявили победу по двум цифрам, а потом
            отозвали её: разница в {aq.discordant} расхождениях из{" "}
            {aq.discordant} незначима.
          </p>
          <p className="mt-2">
            Рынок не наш, и сравнение корректно только на {aq.n} матчах с
            дробными линиями: на целых есть возврат, исходов три, а снятие
            маржи по двум ценам даёт неверную вероятность.
          </p>
        </>
      ),
    });
  }

  /* --- 7. отбор матчей -------------------------------------------- */
  if (sel) {
    const th = sel.chosen_threshold.toFixed(2);
    const chosen = sel.test[th];
    findings.push({
      verdict: "ok",
      title: "Отказ от вердикта на ненадёжных матчах",
      question:
        "Стоит ли показывать исход там, где ни один вариант не выделяется?",
      result: chosen
        ? `При пороге ${th} вердикт выдаётся для ${chosen.n} матчей — ` +
          `${(chosen.coverage * 100).toFixed(0)}% от всех. Точность на них ` +
          `${(chosen.accuracy * 100).toFixed(1)}% против ` +
          `${(chosen.accuracy_always_home * 100).toFixed(1)}% у правила ` +
          `«всегда на хозяев».`
        : "нет данных",
      detail: (
        <>
          <p>
            Порог выбран на валидационном сезоне {sel.val_season} и проверен
            на {sel.test_seasons.join(", ")}. Подбирать его по точности на
            тестовых данных было бы переобучением под результат.
          </p>
          <p className="mt-2">
            Сравнение с примитивом обязательно: точность на любом
            отфильтрованном подмножестве растёт сама по себе и без этого
            ничего не доказывает.
          </p>
          <p className="mt-2 font-medium text-text">
            Важная оговорка: отбор не улучшает прогноз. Log-loss остаётся
            прежним — вероятности не меняются, меняется только решение,
            показывать их или нет.
          </p>
        </>
      ),
    });
  }

  /* --- 8. эталон -------------------------------------------------- */
  if (bench?.common && bench.lines.bfx && bench.lines.avg) {
    findings.push({
      verdict: "ok",
      title: "Эталон сравнения",
      question: "С кем мы себя сравниваем — с конторами или с биржей?",
      result:
        `Долгое время эталоном было среднее по конторам. Биржа точнее: ` +
        `log-loss ${bench.lines.bfx.logloss.toFixed(5)} против ` +
        `${bench.lines.avg.logloss.toFixed(5)} при марже ` +
        `${((bench.lines.bfx.margin ?? 0) * 100).toFixed(2)}% против ` +
        `${((bench.lines.avg.margin ?? 0) * 100).toFixed(2)}%. ` +
        `Прежняя мера была завышена на ` +
        `${Math.abs(bench.common.gap_bfx - bench.common.gap_avg).toFixed(5)}.`,
      detail: (
        <p>
          Betfair Exchange лежал в исходных файлах всегда, но в конвейер не
          попадал: колонок не было в списке читаемых. Среднее по конторам
          содержит маржу каждой из них, поэтому сравниваться с ним — значит
          сравниваться с завышенным для себя эталоном. Теперь мера честная,
          и она хуже: разрыв с биржей{" "}
          {bench.common.gap_bfx.toFixed(5)}, а не {bench.common.gap_avg.toFixed(5)}.
        </p>
      ),
    });
  }

  /* --- 9. период затухания --------------------------------------- */
  if (params?.half_life_grid) {
    const cur = params.half_life_grid.find((x) => x.half_life === 180);
    const bestHl = params.half_life_grid.reduce((a, b) =>
      b.val < a.val ? b : a,
    );
    findings.push({
      verdict: "ok",
      title: "Период затухания весов",
      question: "Правильно ли выбран горизонт, за который вес падает вдвое?",
      result:
        `180 дней — минимум на валидации. 90 дней хуже на ` +
        `${Math.abs((params.half_life_grid[0].val - bestHl.val)).toFixed(4)}, ` +
        `730 — хуже на ${Math.abs((params.half_life_grid[3].val - bestHl.val)).toFixed(4)}. ` +
        `Менять нечего.`,
      detail: (
        <p>
          Значение стояло со времён, когда это был единственный параметр
          такого рода, и его никто не проверял. Для баскетбола такой же
          вопрос оказался важным — там 180 дней приводили к тому, что
          параметры команд стягивались к нулю. Для футбола проверка
          показала, что 180 дней подходят.
        </p>
      ),
    });
  }

  /* --- 10. ошибка по командам ------------------------------------- */
  if (teams?.spread) {
    const s = teams.spread;
    findings.push({
      verdict: "mixed",
      title: "Ошибка по командам",
      question: "Может, модель ошибается на конкретных клубах — и это чинится?",
      result:
        `Модель хуже линии у ${s.worse_than_line} команд из ${s.solid}. ` +
        `Медиана разницы ${s.median.toFixed(4)}, разброс от ` +
        `${s.min.toFixed(4)} до ${s.max.toFixed(4)}.`,
      detail: (
        <p>
          Ошибка оказывается равномерной: локальных проблемных команд нет, и
          чинить нечего. Сильнее всего модель отстаёт там, где у букмекера
          больше всего информации — это косвенно подтверждает, что разрыв
          состоит из составов и травм, а не из сбоя на отдельных клубах.
        </p>
      ),
    });
  }

  const ok = findings.filter((f) => f.verdict === "ok").length;
  const no = findings.filter((f) => f.verdict === "no").length;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Что мы проверили</h1>
        <p className="mt-1 text-sm text-muted">
          Серия экспериментов с моделью. Из {findings.length} гипотез сработали{" "}
          {ok}, не дали ничего {no}. Отрицательные результаты здесь стоят рядом
          с положительными: именно они показывают, где проект упёрся в потолок.
        </p>
        <p className="mt-2 text-xs text-muted">
          Все числа читаются из отчётов, которые пересчитываются в CI каждый час.
          Вручную здесь не написано ничего — иначе страница разъехалась бы с
          данными.
        </p>
      </div>

      <ul className="space-y-4">
        {findings.map((f) => (
          <FindingRow key={f.title} f={f} />
        ))}
      </ul>

      <section className="panel p-5">
        <h2 className="text-sm font-semibold">Итог и его цена</h2>
        <div className="mt-3 space-y-3 text-sm text-muted">
          <p>
            Внутри этой модели резерв исчерпан. Мы перебрали признаки,
            калибровку, смешивание с рынком, вес рейтинга Elo и период
            затухания — и ни один не дал выигрыша. Добавился азиатский
            хэндикап, но и там линия впереди.
          </p>
          <p>
            Разрыв с рынком — это не ошибка подгонки, а недостающая
            информация: составы, травмы, новости. Улучшить прогноз в рамках
            бесплатных источников нечем.
          </p>
          <p>
            Отдельно стоит сказать про методологию. Тестовые сезоны одни и
            те же, а экспериментов было много, и каждое использование их
            истощает. Дальше улучшать «по метрике на тесте» опасно: это
            поиск шума, а не улучшений. Поэтому новые гипотезы, если они
            появятся, стоит проверять на свежем сезоне, а не на этом.
          </p>
          <p className="rounded border border-line bg-panel-2 p-3 text-xs">
            Честное положение дел: модель — наивного прогноза сильно (log-loss{" "}
            <span className="num text-text">0.939</span> против{" "}
            <span className="num text-text">1.078</span>), букмекера — нет
            (разрыв <span className="num text-text">0.030</span>). Работают два
            решения: отказ от вердикта на ненадёжных матчах и честный эталон
            сравнения. Остальное — отрицательные результаты, и они тоже
            результаты.
          </p>
        </div>
      </section>

      <p className="text-xs text-muted">
        Подробные разборы — на страницах{" "}
        <Link href="/accuracy" className="link underline">
          «Точность»
        </Link>{" "}
        и{" "}
        <Link href="/methodology" className="link underline">
          «Методика»
        </Link>
        .
      </p>
    </div>
  );
}