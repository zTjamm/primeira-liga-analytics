import { getNbaJournal } from "@/lib/data";
import { AccuracySummary } from "@/components/AccuracySummary";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function NbaAccuracyPage() {
  const journal = await getNbaJournal();

  if (!journal) {
    return (
      <EmptyState
        title="Метрики NBA ещё не посчитаны"
        hint="Прогоните пайплайн: модель обучается на прошлых матчах, и метрики появятся в журнале."
        command="python -m models.nba_predict"
      />
    );
  }

  const { summary } = journal;

  if (summary.total === 0) {
    return (
      <EmptyState
        title="Нет сыгранных прогнозов для оценки"
        hint="Как только в данных появятся результаты матчей, здесь будут точность, log-loss и ROI."
      />
    );
  }

  return (
    <AccuracySummary
      title="Точность модели — NBA"
      intro="Честная оценка: сначала ограничения, потом цифры."
      summary={summary}
      matchesHref="/nba"
      matchesLabel="странице матчей"
      limitations={
        <>
          <li>
            Сравнения с букмекерской линией нет: коэффициентов на NBA в
            открытых источниках не найдено, поэтому утверждать выигрыш в
            деньгах нельзя.
          </li>
          <li>
            Точность считается только по матчам, где вердикт был выдан.
            Отсеянные матчи показаны отдельно — иначе процент выглядел бы
            лучше, чем он есть.
          </li>
          <li>
            Три сезона — это мало. Разницу между моделями нельзя отличить от
            особенностей конкретного сезона.
          </li>
          <li>
            Статистики матча почти нет: ни владения, ни процента реализации.
            Атака и оборона разделены только математически, из данных это не
            проверяется.
          </li>
        </>
      }
    />
  );
}
