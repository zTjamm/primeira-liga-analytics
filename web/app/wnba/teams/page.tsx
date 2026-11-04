import { getWnbaStrength, getWnbaPredictions } from "@/lib/data";
import { TeamsTable } from "@/components/TeamsTable";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function WnbaTeamsPage() {
  const [strength, preds] = await Promise.all([
    getWnbaStrength(),
    getWnbaPredictions(),
  ]);

  if (!strength && !preds) {
    return (
      <EmptyState
        title="Данные по командам WNBA ещё не загружены"
        hint="Запустите: python -m etl.fetch_wnba && python -m models.wnba_predict"
        command="python -m etl.fetch_wnba"
      />
    );
  }

  const activeIds: string[] = [];
  for (const m of preds?.upcoming ?? []) {
    activeIds.push(m.home_id, m.away_id);
  }

  return (
    <TeamsTable
      title="Команды WNBA"
      subtitle="Сила команд по модели. Обе величины читаются одинаково: больше — лучше."
      strength={strength}
      activeIds={activeIds}
      missingNote="Файл с рейтингами команд (wnba_strength.json) пока не сгенерирован. Когда появится — таблица заполнится атакой, обороной и силой для каждого клуба."
    />
  );
}
