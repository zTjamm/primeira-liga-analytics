import { getRplPredictions } from "@/lib/data";

export const dynamic = "force-dynamic";

/**
 * Методика РФПЛ.
 *
 * Страница намеренно короче португальской: здесь нет валидации на
 * отложенных сезонах, нет сравнения с линией букмекеров и нет собственного
 * подбора порога. Утверждать обратное значило бы переносить чужие
 * результаты на чужую лигу.
 */
export default async function RplMethodologyPage() {
  const file = await getRplPredictions();

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
            когда наиболее вероятный исход ≥ {file?.threshold ?? 0.55}.
          </li>
        </ul>
      </section>

      <section className="panel p-5 border-l-2 border-line">
        <h2 className="text-base font-semibold">Чего здесь нет</h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-muted">
          <li>
            <span className="text-text">Порог 0.55 не подбирался на РФПЛ:</span>{" "}
            {file?.threshold_source ?? "он перенесён из другого раздела"}.{" "}
            Откалиброванным он считается только там, где валидировался.
          </li>
          <li>
            <span className="text-text">Нет сравнения с букмекерами.</span>{" "}
            Линии в прогнозе нет — есть только наша модель, и честнее
            молчать о сравнении, чем проводить его по одним нашим числам.
          </li>
          <li>
            <span className="text-text">Нет журнала точности.</span> Журнал
            заработает, когда накопится достаточно сыгранных матчей сезона
            2026/27; до тех пор цифры точности были бы статистическим шумом.
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
