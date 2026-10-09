/** Заглушка, когда артефакт пайплайна ещё не собран. */
export function EmptyState({
  title,
  hint,
  command,
}: {
  title: string;
  hint: string;
  command?: string;
}) {
  return (
    <div className="panel p-8 text-center">
      <h2 className="mb-2 text-base font-semibold">{title}</h2>
      <p className="mx-auto max-w-md text-sm text-muted">{hint}</p>
      {command && <code className="mt-4 block text-xs text-muted">{command}</code>}
    </div>
  );
}
