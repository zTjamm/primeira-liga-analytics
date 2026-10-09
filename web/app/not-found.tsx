import Link from "next/link";

export default function NotFound() {
  return (
    <div className="panel p-10 text-center">
      <h1 className="mb-2 text-lg font-semibold">Матч не найден</h1>
      <p className="mx-auto max-w-sm text-sm text-muted">
        Возможно, он уже сыгран или перенесён. Актуальный список — на главной.
      </p>
      <Link href="/" className="link mt-4 inline-block text-sm underline">
        ← к списку матчей
      </Link>
    </div>
  );
}
