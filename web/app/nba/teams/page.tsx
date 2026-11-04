import { getNbaStrength, getNbaPredictions } from "@/lib/data";
import { TeamsTable } from "@/components/TeamsTable";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function NbaTeamsPage() {
  const [strength, preds] = await Promise.all([
    getNbaStrength(),
    getNbaPredictions(),
  ]);

  if (!strength && !preds) {
    return (
      <EmptyState
        title="Данные по командам NBA ещё не загружены"
        hint="Запустите: python -m etl.fetch_nba && python -m models.nba_predict"
        command="python -m etl.fetch_nba"
      />
    );
  }

  const activeIds: string[] = [];
  for (const m of preds?.upcoming ?? []) {
    activeIds.push(m.home_id, m.away_id);
  }

  return (
    <TeamsTable
      title="Команды NBA"
      subtitle="Сила команд по модели. Обе величины читаются одинаково: больше — лучше."
      strength={strength}
      activeIds={activeIds}
      missingNote="Файл с рейтингами команд (nba_strength.json) пока не сгенерирован. Когда появится — таблица заполнится атакой, обороной и силой для каждого клуба."
    />
  );
}
