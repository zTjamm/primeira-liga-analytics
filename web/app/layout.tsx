import type { Metadata } from "next";
import Link from "next/link";
import { SportNav } from "@/components/SportNav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Primeira Liga и Единая лига ВТБ — аналитика матчей",
  description:
    "Вероятности исходов матчей по португальской Primeira Liga и Единой лиге ВТБ " +
    "на исторических данных, с честным walk-forward бэктестом.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="min-h-screen">
        <header className="sticky top-0 z-20 border-b border-line bg-panel/70 backdrop-blur">
          <div className="mx-auto max-w-6xl px-4">
            <SportNav />
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>

        <footer className="mt-12 border-t border-line py-6 text-xs text-muted">
          <div className="mx-auto max-w-6xl px-4 space-y-1">
            <p>
              Футбол: football-data.co.uk (история, статистика, коэффициенты) и
              football-data.org (расписание, таблица). Баскетбол: официальный API
              Единой лиги ВТБ.
            </p>
            <p>
              Проект — исследовательский, не рекомендация и не гарантия. По футболу
              прогнозы уступают закрывающей линии букмекеров, по баскетболу сравнение
              с линией невозможно. Цифры показаны честно на страницах «Точность»
              каждого раздела.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
