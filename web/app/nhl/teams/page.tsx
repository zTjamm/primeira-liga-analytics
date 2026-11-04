import { getNhlStrength, getNhlPredictions } from "@/lib/data";
import { TeamsTable } from "@/components/TeamsTable";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function NhlTeamsPage() {
  const [strength, preds] = await Promise.all([
    getNhlStrength(),
    getNhlPredictions(),
  ]);

  if (!strength && !preds) {
    return (
      <EmptyState
        title="Данные по командам НХЛ ещё не загружены"
        hint="Запустите: python -m etl.fetch_nhl && python -m models.nhl_predict"
        command="python -m etl.fetch_nhl"
      />
    );
  }

  const activeIds: string[] = [];
  for (const m of preds?.upcoming ?? []) {
    activeIds.push(m.home_id, m.away_id);
  }

  return (
    <TeamsTable
      title="Команды НХЛ"
      subtitle="Сила команд по модели. Обе величины читаются одинаково: больше — лучше."
      strength={strength}
      activeIds={activeIds}
      missingNote="Файл с рейтингами команд (nhl_strength.json) пока не сгенерирован. Когда появится — таблица заполнится атакой, обороной и силой для каждого клуба."
    />
  );
}
