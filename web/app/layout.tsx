import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Primeira Liga — аналитика матчей",
  description:
    "Вероятности исходов матчей Primeira Liga на основе 27 сезонов истории, " +
    "пуассоновских моделей и честного walk-forward бэктеста.",
};

const NAV = [
  { href: "/", label: "Матчи" },
  { href: "/teams", label: "Команды" },
  { href: "/accuracy", label: "Точность" },
  { href: "/methodology", label: "Методика" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="min-h-screen">
        <header className="border-b border-line bg-panel/60 backdrop-blur sticky top-0 z-20">
          <div className="mx-auto max-w-6xl px-4">
            <div className="flex items-center justify-between gap-4 py-3">
              <Link href="/" className="font-semibold tracking-tight">
                Primeira<span className="text-home"> Liga</span>
                <span className="ml-2 text-xs font-normal text-muted">аналитика</span>
              </Link>
              <nav className="flex items-center gap-4 text-sm">
                {NAV.map((n) => (
                  <Link key={n.href} href={n.href} className="link">
                    {n.label}
                  </Link>
                ))}
              </nav>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>

        <footer className="mt-12 border-t border-line py-6 text-xs text-muted">
          <div className="mx-auto max-w-6xl px-4 space-y-1">
            <p>
              Данные: football-data.co.uk (история, статистика, коэффициенты) и
              football-data.org (расписание, таблица).
            </p>
            <p>
              Проект — исследовательский, не рекомендация и не гарантия. Прогнозы
              уступают закрывающей линии букмекеров: цифры честно показаны на
              странице «Точность».
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
