import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Primeira Liga — аналитика матчей",
  description:
    "Вероятности исходов матчей Primeira Liga на основе 27 сезонов истории, " +
    "пуассоновских моделей и честного walk-forward бэктеста.",
};

const SPORTS = [
  {
    id: "football",
    label: "Primeira Liga",
    note: "футбол",
    base: "/",
    pages: [
      { href: "/", label: "Матчи" },
      { href: "/history", label: "Журнал" },
      { href: "/teams", label: "Команды" },
      { href: "/accuracy", label: "Точность" },
    ],
  },
  {
    id: "basketball",
    label: "Единая лига ВТБ",
    note: "баскетбол",
    base: "/basketball",
    pages: [
      { href: "/basketball", label: "Матчи" },
      { href: "/basketball/accuracy", label: "Точность" },
    ],
  },
] as const;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="min-h-screen">
        <header className="sticky top-0 z-20 border-b border-line bg-panel/60 backdrop-blur">
          <div className="mx-auto max-w-6xl px-4">
            <div className="flex items-center justify-between gap-4 py-3">
              <Link href="/" className="font-semibold tracking-tight">
                Аналитика <span className="text-home">матчей</span>
              </Link>
              <nav className="flex items-center gap-4 text-sm">
                <Link href="/" className="link">
                  Футбол
                </Link>
                <Link href="/basketball" className="link">
                  Баскетбол
                </Link>
              </nav>
            </div>
            <div className="-mt-1 flex gap-4 overflow-x-auto pb-1 text-sm">
              {SPORTS.flatMap((s) =>
                s.pages.map((p) => (
                  <Link key={p.href} href={p.href} className="link whitespace-nowrap pb-1">
                    {p.label}
                  </Link>
                )),
              )}
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>

        <footer className="mt-12 border-t border-line py-6 text-xs text-muted">
          <div className="mx-auto max-w-6xl px-4 space-y-1">
            <p>
              Футбол: football-data.co.uk и football-data.org. Баскетбол: официальный
              API Единой лиги ВТБ.
            </p>
            <p>
              Проект — исследовательский, не рекомендация и не гарантия. По футболу
              прогнозы уступают закрывающей линии букмекеров, по баскетболу сравнение
              с линией невозможно: цифры честно показаны на страницах «Точность».
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
