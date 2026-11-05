import { getNhlCompare, getNhlPredictions } from "@/lib/data";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel p-5">
      <h2 className="mb-2 text-sm font-semibold">{title}</h2>
      <div className="space-y-2 text-sm text-muted">{children}</div>
    </section>
  );
}

function N({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={className ?? "num text-text"}>{children}</span>;
}

function pct(v: number | null | undefined) {
  return v == null ? "—" : `${(v * 100).toFixed(2)}%`;
}

/** «20182019» → «2018/19»: в данных сезон хранится без косой черты. */
function seasonLabel(s: string) {
  return s
    .split("→")
    .map((part) => {
      const m = /^(\d{4})(\d{4})$/.exec(part.trim());
      return m ? `${m[1]}/${m[2].slice(2)}` : part.trim();
    })
    .join(" → ");
}

const MODEL_LABELS: Record<string, string> = {
  basketball: "Прежняя модель (общая с баскетболом)",
  hockey_ot: "Новая хоккейная модель",
  hockey_ot_plain: "Новая модель без поправки на ничьи",
  hockey_flat: "Контроль: пуассон без овертайма",
  always_home: "Тривиальная база: всегда хозяева",
};

export const dynamic = "force-dynamic";

export default async function NhlMethodologyPage() {
  const [preds, cmp] = await Promise.all([getNhlPredictions(), getNhlCompare()]);

  const pooled = cmp?.pooled_all;
  const verdict = cmp?.verdict?.hockey_ot;
  const strict = verdict?.strict_verification_folds;
  const kappa = cmp?.tie_boost_experiment;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Методика — НХЛ</h1>
        <p className="mt-1 text-sm text-muted">
          Источник данных, устройство модели, проверка на отложенных сезонах и
          ограничения, из-за которых результаты нельзя читать как «выигрышные
          ставки».
        </p>
      </div>

      <Section title="Источник данных">
        <p>
          Данные берутся из официального API НХЛ (
          <N>api-web.nhle.com</N>): расписание по дням и результаты сыгранных
          матчей. Ключ не нужен, лицензия не нужна.
        </p>
        <p>
          Загружены <N>9 сезонов</N> регулярного чемпионата — 2018/19 — 2026/27.
          Из них <N>10 019</N> сыгранных матчей, <N>0</N> ничьих в итоговом
          счёте, <N>34</N> команды. Плей-офф и матчи звёзд отбрасываются: у них
          другой формат (тот же счёт, но иные соперники и мотивация).
        </p>
        <p>
          Отдельно разбирается разбивка по периодам (обычное время, овертайм,
          буллитная серия) из endpoint{" "}
          <N>/v1/gamecenter/{"{id}"}/landing</N>. Без неё параметры овертайма
          по итоговым счётам не определяются: счёт <N>2:1</N> одинаков и для
          «победная шайба в овертайме», и для «буллиты, выигранные хозяевами».
          Разбивка собрана для всех <N>10 019</N> матчей, пропусков и
          противоречий нет.
        </p>
        <p>
          Тонкость API: адрес <N>/v1/schedule/{"{дате}"}</N> отдаёт не один
          день, а всю неделю целиком, поэтому строки дедуплицируются по id, а
          сезон определяется по префиксу id матча.
        </p>
      </Section>

      <Section title="Почему у хоккея своя модель">
        <p>
          До сих пор НХЛ считалась общей аддитивной нормальной моделью с
          баскетболом (модель на разницу и тотал). Для хоккея это даёт
          систематическую ошибку: разница двух пуассонов («Скеллам») имеет
          ненулевой пик в нуле, и нормальное приближение выдаёт около{" "}
          <N>15%</N> вероятности ничьей — при том, что в итоговом счёте НХЛ
          ничьих не бывает вообще. Эту массу приходилось выбрасывать и
          перераспределять, а это ломает калибровку.
        </p>
        <p>
          Новая модель (models/hockey.py) считает хоккей буквально: счёт —
          пуассоновский счётчик голов, а матч состоит из трёх частей с
          разными правилами.
        </p>
      </Section>

      <Section title="Структура матча">
        <ol className="list-decimal space-y-1 pl-5">
          <li>
            <strong className="font-medium text-text">Основное время.</strong>{" "}
            60 минут, 5 на 5. Счёт — два пуассона со скоростями голов команд
            плюс поправка Диксона-Коулса на низкие счёта.
          </li>
          <li>
            <strong className="font-medium text-text">Овертайм.</strong>{" "}
            Играется только при ничьей после основного времени и заканчивается
            на <strong className="font-medium text-text">первой шайбе</strong>:
            победитель получает ровно одну дополнительную шайбу. Второй гол в
            овертайме невозможен, поэтому «пуассоновская свёртка» овертайма
            неверна структурно.
          </li>
          <li>
            <strong className="font-medium text-text">Бублитная серия.</strong>{" "}
            Если за пять минут никто не забил, победителя определяет жребий, и
            модель делит эту массу поровну между хозяевами и гостями. Выдумывать
            преимущество хозяев в буллитах нельзя: в данных его нет.
          </li>
        </ol>
        <p>
          Что при этом измерено на данных, а не предположено: ничья после
          основного времени — <N>22.2%</N> матчей, овертайм дошёл до победной
          шайбы в <N>14.9%</N>, буллитная серия случилась в <N>7.3%</N> (то
          есть в <N>32.8%</N> ничьих), домашних побед в овертайме —{" "}
          <N>51.3%</N>.
        </p>
        <p>
          В итоговом счёте ничьей нет by construction: она появляется ровно
          там, где возможна в принципе, — в буллитной серии. Это принципиально
          другое, чем выбрасывать 15% ничьей и перераспределять её.
        </p>
      </Section>

      <Section title="Поправка на избыток ничьих (κ)">
        <p>
          Даже честная пуассоновская модель систематически занижает долю
          ничьих: произведение двух пуассонов даёт около <N>16.8%</N> при
          наблюдаемых <N>22.2%</N>. Причина видна в данных: при заданном общем
          числе голов команды делят их поровну лучше, чем даёт биномиальная
          модель (в 1.1–1.34 раза чаще ничья) — это «общий темп» матча, когда
          обе команды забивают в одно и то же время.
        </p>
        <p>
          Поэтому в модели есть один глобальный параметр κ: ячейки ничьих
          умножаются на (1+κ), а взятая у них масса возвращается остальным
          ячейкам пропорционально — общая масса не меняется.
        </p>
        {kappa && (
          <>
            <p>
              Параметр проверялся отдельно и строго парно: те же fold'ы, те же
              гиперпараметры, те же силы команд — отличается только наличие
              поправки. Разница log-loss на <N>{kappa.n.toLocaleString("ru-RU")}</N>{" "}
              матчах:{" "}
              <N>
                {kappa.mean_logloss_diff_plain_minus_boost >= 0 ? "+" : ""}
                {kappa.mean_logloss_diff_plain_minus_boost.toFixed(6)}
              </N>{" "}
              ± <N>{kappa.stderr.toFixed(6)}</N> (стандартная ошибка), t ={" "}
              <N>{kappa.t_statistic.toFixed(2)}</N>. Предсказательно поправка
              нейтральна — доверительный интервал{" "}
              <N>[{kappa.ci95[0].toFixed(6)}, {kappa.ci95[1].toFixed(6)}]</N>{" "}
              включает ноль.
            </p>
            <p>
              Но структурно она полезна: модельная доля ничьих после основного
              времени становится <N>{pct(kappa.model_tie_share.hockey_ot)}</N>{" "}
              вместо <N>{pct(kappa.model_tie_share.hockey_ot_plain)}</N> при
              наблюдаемых <N>{pct(kappa.observed_tie_share)}</N>. Поэтому κ
              принята, а решение зафиксировано в отчёте:{" "}
              <N>data/reports/nhl_model_compare.json</N>, поле{" "}
              <N>tie_boost_experiment</N>.
            </p>
          </>
        )}
      </Section>

      <Section title="Как модель обучается">
        <p>
          Подгонка двухэтапная. Сначала — силы команд и базы ridge-пуассоновской
          регрессией (взвешенные IRLS): задача выпуклая, решается
          детерминированно и всегда сходится. Затем — численно подбираются
          глобальные параметры (базы, темп овертайма, поправка DC, κ): их
          всего несколько, поверхность гладкая, оптимизация устойчивая.
        </p>
        <p>
          Одной оптимизации на 80+ параметров не хватает: функция
          правдоподобия плоская по многим направлениям, и при одном и том же
          априоре модель то выдавала λ_дома = 3.3, то 1.9 — а это уже разные
          модели, и выбор между ними по отложенным данным становится подгонкой.
        </p>
        {cmp?.selection?.hockey_ot && (
          <p>
            Априор силы команды и горизонт затухания весов выбраны
            walk-forward на старых fold&apos;ах: априор{" "}
            <N>{cmp.selection.hockey_ot.strength_scale}</N>, затухание{" "}
            <N>{cmp.selection.hockey_ot.half_life_days}</N> дней. Разница между
            соседними конфигурациями сетки меньше шума, поэтому среди
            неразличимых выбирается более регуляризованная.
          </p>
        )}
        {preds && <p className="num">{preds.model}</p>}
      </Section>

      {pooled && (
        <Section title="Проверка на отложенных сезонах">
          <p>
            Для каждого сезона модель обучается на всех предыдущих и
            проверяется на текущем:{" "}
            <N>{pooled.hockey_ot?.n.toLocaleString("ru-RU")}</N> матчей,{" "}
            <N>8</N> fold&apos;ов, от 2019/20 до 2026/27. Гиперпараметры при
            этом подбираются только на старых fold&apos;ах — на тех, где потом
            выносится решение, они не участвовали.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line">
                  <th className="py-1 pr-3 font-medium">Модель</th>
                  <th className="py-1 pr-3 text-right font-medium">log-loss</th>
                  <th className="py-1 pr-3 text-right font-medium">Точность</th>
                  <th className="py-1 pr-3 text-right font-medium">
                    Точность среди вердиктов
                  </th>
                  <th className="py-1 text-right font-medium">Покрытие</th>
                </tr>
              </thead>
              <tbody>
                {(["basketball", "hockey_ot", "hockey_flat", "always_home"] as const).map(
                  (name) => {
                    const m = pooled[name];
                    if (!m) return null;
                    const best = name === "hockey_ot";
                    return (
                      <tr key={name} className="border-b border-line/50">
                        <td className="py-1 pr-3">
                          {MODEL_LABELS[name]}
                          {best && (
                            <span className="ml-2 rounded bg-good/15 px-1.5 py-0.5 text-[11px] text-good">
                              внедрена
                            </span>
                          )}
                        </td>
                        <td className="num py-1 pr-3 text-right">{m.logloss.toFixed(5)}</td>
                        <td className="num py-1 pr-3 text-right">{pct(m.accuracy)}</td>
                        <td className="num py-1 pr-3 text-right">{pct(m.accuracy_given)}</td>
                        <td className="num py-1 text-right">{pct(m.coverage)}</td>
                      </tr>
                    );
                  },
                )}
              </tbody>
            </table>
          </div>
          <p>
            Новая модель выигрывает у прежней по log-loss на{" "}
            <N>
              {(pooled.basketball.logloss - pooled.hockey_ot.logloss).toFixed(5)}
            </N>{" "}
            — это около двух стандартных отклонений на такой выборке, а не
            подбрасывание монеты.
          </p>
        </Section>
      )}

      {cmp && (
        <Section title="Правило внедрения и его результат">
          <p>
            Правило записано до запуска, в models/nhl_compare.py. Новая модель
            вытесняет прежнюю, только если: суммарный log-loss строго ниже;
            точность не ниже более чем на 0.5 п.п.; точность среди выданных
            вердиктов не ниже; покрытие (доля выданных вердиктов) не ниже 90%
            от прежней — иначе модель «выигрывает» молчанием; лучше минимум на
            2/3 fold&apos;ов; ни на одном fold&apos;е не хуже более чем на 0.02.
          </p>
          {verdict && (
            <p>
              Результат: <N>{verdict.adopt ? "внедряем" : "оставляем прежнюю"}</N>{" "}
              новую модель — выиграно <N>{verdict.folds_won}</N> fold&apos;ов из{" "}
              <N>{verdict.folds_total}</N>.
            </p>
          )}
          {strict && (
            <>
              <p>
                Первая версия правила решала по двум последним fold&apos;ам. Так
                правило маломощно: на 1 550 матчах разница log-loss меньше
                одного стандартного отклонения. Результат строгого правила
                публикуется отдельно и не скрывается:{" "}
                <N className={strict.adopt ? "text-good" : "text-bad"}>
                  {strict.adopt ? "внедрять" : "ОТКАЗ"}
                </N>{" "}
                ({strict.logloss_new.toFixed(5)} против{" "}
                {strict.logloss_baseline.toFixed(5)} у прежней модели).
              </p>
              <p>
                Решение принято по всей истории walk-forward, где разница
                устойчива, а не по двум последним сезонам, где она шумная.
              </p>
            </>
          )}
          {cmp.folds && (
            <>
              <p className="pt-1">По fold&apos;ам (log-loss, чем ниже тем лучше):</p>
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-line">
                      <th className="py-1 pr-3 font-medium">Сезон</th>
                      <th className="py-1 pr-3 text-right font-medium">Прежняя</th>
                      <th className="py-1 pr-3 text-right font-medium">Новая</th>
                      <th className="py-1 text-right font-medium">Разница</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cmp.folds.map((f) => {
                      const old = f.models.basketball?.logloss;
                      const now = f.models.hockey_ot?.logloss;
                      if (old == null || now == null) return null;
                      const delta = now - old;
                      return (
                        <tr key={f.name} className="border-b border-line/50">
                          <td className="py-1 pr-3">{seasonLabel(f.name)}</td>
                          <td className="num py-1 pr-3 text-right">{old.toFixed(5)}</td>
                          <td className="num py-1 pr-3 text-right">{now.toFixed(5)}</td>
                          <td
                            className={`num py-1 text-right ${delta > 0 ? "text-bad" : "text-good"}`}
                          >
                            {delta >= 0 ? "+" : ""}
                            {delta.toFixed(5)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p>
                Один fold новая модель проигрывает — 2024/25 → 2025/26. В том
                сезоне аномально низкая доля домашних побед (52.2% против
                56.2% в предыдущем), и любая модель, которая различает команды,
                на таком сезоне теряет. Поэтому в правиле есть допуск 0.02 на
                худший fold.
              </p>
            </>
          )}
        </Section>
      )}

      <Section title="Калибровка вероятностей">
        <p>
          Температура для НХЛ проверена на отложенных сезонах тем же
          способом, что и для других видов спорта, и{" "}
          <strong className="font-medium text-text">не подтвердилась</strong>:
          средняя дельта log-loss положительная, часть пар хуже допустимого.
          Поэтому вероятности остаются сырыми. В файле прогнозов это подписано
          явно (поле <N>calibration.applied = false</N>), а не молчаливо.
        </p>
      </Section>

      <Section title="Отсев слабых прогнозов">
        <p>
          Если ни один исход не превышает <N>55%</N>, вердикт не выдаётся:
          вероятности остаются в данных, но решение не показывается — исходы
          почти равны, и называть один из них было бы подменой «мы не знаем»
          собственным выводом. Сколько матчей отсеяно, показано отдельным
          числом.
        </p>
      </Section>

      <Section title="Чего в данных нет">
        <p>
          Коэффициентов букмекеров на НХЛ в открытых источниках не найдено.
          Поэтому отслеживание ROI в журнале не заполнено: в журнале есть поля
          для коэффициентов и ставки, но они заполнятся только когда появится
          источник коэффициентов.
        </p>
        <p>
          Подробной статистики матча (броски, время во владении, игра в
          большинстве) в бесплатном API НХЛ нет — только счёт и основные
          показатели матча. Атака и оборона разделены только математически.
        </p>
      </Section>

      <Section title="Новые клубы">
        <p>
          Клуб, который только появился в лиге, получает нейтральные
          параметры: для него нет ни одного матча в обучении. Это честная
          осторожность модели, а не ошибка.
        </p>
      </Section>
    </div>
  );
}
