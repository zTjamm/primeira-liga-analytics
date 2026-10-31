import { getJournal, getRplPredictions } from "@/lib/data";

export const dynamic = "force-dynamic";

/**
 * Методика РФПЛ.
 *
 * Страница намеренно короче португальской: здесь нет сравнения с линией
 * букмекеров (котировок на РФПЛ у нас нет) и нет азиатского хэндикапа.
 * Подбор порога и проверка калибровки СВОИ, отдельные от португальского
 * раздела: цифры берутся из отчётов models/rpl_selective.py и
 * models/calibrate.py, а не переносятся из чужой валидации.
 */
export default async function RplMethodologyPage() {
  const [file, journal] = await Promise.all([
    getRplPredictions(),
    getJournal("rpl"),
  ]);
  const played = journal?.summary?.total ?? 0;

  return (
    <div className="space-y-6">
      <section className="panel p-5">
        <h1 className="text-lg font-semibold">Методика РФПЛ</h1>
        <p className="mt-2 text-sm text-muted">
          РФПЛ — третий вид спорта проекта и полностью изолированный
          пайплайн: свои данные, своя модель, свой файл прогнозов. Ни один
          параметр не берётся из португальского футбола или баскетбола.
        </p>
      </section>

      <section className="panel p-5">
        <h2 className="text-base font-semibold">Данные</h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-muted">
          <li>
            <span className="text-text">История результатов</span> —
            football-data.co.uk/new/RUS.csv:{" "}
            {file ? <span className="num">{file.n_matches_history}</span> : "—"}{" "}
            сыгранных матчей с 2012/13, коэффициенты букмекеров с 2022/23.
          </li>
          <li>
            <span className="text-text">Расписание</span> — официальный API
            premierliga.ru (POST /api/getMatches). CSV содержит только
            сыгранные матчи, поэтому расписание идёт отдельным потоком.
          </li>
          <li>
            <span className="text-text">Обновление</span> — CSV перекачивается
            при каждом запуске, кэш используется только при недоступности
            сети и о этом пишется явно.
          </li>
        </ul>
      </section>

      <section className="panel p-5">
        <h2 className="text-base font-semibold">Модель</h2>
        <p className="mt-2 text-sm text-muted">
          Dixon-Coles — та же математика, что в португальском футболе, но
          обучение идёт исключительно на матчах РФПЛ. Экспоненциальное
          затухание (половинный период 180 дней) делает недавние матчи
          весомее сезонной давности.
        </p>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-muted">
          <li>
            Исходы П1 / Х / П2 и тотал 2.5 считаются из матрицы счётов
            пуассоновской модели с поправкой на низкие счёты.
          </li>
          <li>
            Вероятности показываются всегда. Вердикт (выбор исхода) — только
            когда наиболее вероятный исход ≥ {file?.threshold ?? 0.55}; по
            тоталу 2.5 действует свой порог{" "}
            {file?.total_threshold != null ? (
              <span className="num">{file.total_threshold.toFixed(2)}</span>
            ) : (
              "—"
            )}
            .
          </li>
        </ul>
      </section>

      <section className="panel p-5">
        <h2 className="text-base font-semibold">Пороги и калибровка</h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-muted">
          <li>
            <span className="text-text">Порог по исходу — свой, а не
            перенесённый.</span> Подбор идёт walk-forward на данных РФПЛ:
            порог выбирается на валидационном сезоне 2023/24 по разнице
            «точность модели минус точность правила „всегда на хозяев“» и
            проверяется на тестовых 2024/25 и 2025/26, которые при подборе
            не участвовали. На тесте выбранный порог даёт 69.0% точности при
            покрытии 38% против 57.6% у правила «всегда на хозяев»
            (без отсева — 56.5%). {file?.threshold_source}
          </li>
          <li>
            <span className="text-text">Порог по тоталу подбирался
            отдельно</span> — рынки разные, и порог, годный для исхода, на
            тотале около линии отсекает лишнее. Отдельный подбор дал 0.65:
            на тесте 72.2% при n=108 против 57.9% без отсева.{" "}
            {file?.total_threshold_source}
          </li>
          <li>
            <span className="text-text">Калибровки вероятностей нет.</span>{" "}
            Температурная проверка (models/calibrate.py, скользящее
            «учил на сезоне N → мерил на N+1») для РФПЛ отвергнута: log-loss
            не улучшается. Поэтому вероятности показываются сырыми —
            подгонять их под «правдоподобный» вид, когда проверка этого не
            подтвердила, значило бы выглядеть точнее, чем мы есть.
          </li>
        </ul>
      </section>

      <section className="panel p-5 border-l-2 border-line">
        <h2 className="text-base font-semibold">Чего здесь нет</h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-muted">
          <li>
            <span className="text-text">Нет сравнения с букмекерами.</span>{" "}
            Линии в прогнозе нет — есть только наша модель, и честнее
            молчать о сравнении, чем проводить его по одним нашим числам.
          </li>
          <li>
            <span className="text-text">Журнал точности есть, но его нельзя
            читать как результат.</span> Сыгранных прогнозов{" "}
            <span className="num">{played}</span> — это выборка, в которой
            разница в двадцать процентных пунктов укладывается в шум.
            Цифры на странице журнала показываются, но выводы из них делать
            рано.
          </li>
          <li>
            <span className="text-text">Нет азиатского хэндикапа и
            индивидуальных тоталов</span> — в отличие от португальского
            раздела.
          </li>
        </ul>
      </section>
    </div>
  );
}
