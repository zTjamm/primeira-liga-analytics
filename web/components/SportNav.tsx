"use client";

/**
 * Навигация по разделам.
 *
 * Клиентский компонент из-за usePathname: в layout серверного компонента
 * путь недоступен, а вкладки должны зависеть от того, какой спорт открыт.
 * Раньше в шапке выводились вкладки обоих спортов подряд, и в списке
 * оказывались два «Матчи» и два «Точность» без указания, к какому
 * спорту они относятся.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";

export interface SportPages {
  id: string;
  label: string;
  short: string;
  base: string;
  pages: { href: string; label: string }[];
}

export const SPORTS: SportPages[] = [
  {
    id: "football",
    label: "Primeira Liga",
    short: "Футбол",
    base: "/",
    pages: [
      { href: "/", label: "Матчи" },
      { href: "/history", label: "Журнал" },
      { href: "/teams", label: "Команды" },
      { href: "/accuracy", label: "Точность" },
      { href: "/methodology", label: "Методика" },
      { href: "/experiments", label: "Эксперименты" },
    ],
  },
  {
    id: "basketball",
    label: "Единая лига ВТБ",
    short: "Баскетбол",
    base: "/basketball",
    pages: [
      { href: "/basketball", label: "Матчи" },
      { href: "/basketball/journal", label: "Журнал" },
      { href: "/basketball/teams", label: "Команды" },
      { href: "/basketball/accuracy", label: "Точность" },
      { href: "/basketball/methodology", label: "Методика" },
    ],
  },
  {
    id: "rpl",
    label: "Российская Премьер-Лига",
    short: "РФПЛ",
    base: "/rpl",
    pages: [
      { href: "/rpl", label: "Матчи" },
      { href: "/rpl/journal", label: "Журнал" },
      { href: "/rpl/teams", label: "Команды" },
      { href: "/rpl/methodology", label: "Методика" },
    ],
  },
  {
    id: "wnba",
    label: "WNBA",
    short: "WNBA",
    base: "/wnba",
    pages: [
      { href: "/wnba", label: "Матчи" },
      { href: "/wnba/journal", label: "Журнал" },
      { href: "/wnba/teams", label: "Команды" },
      { href: "/wnba/accuracy", label: "Точность" },
      { href: "/wnba/methodology", label: "Методика" },
    ],
  },
  {
    id: "nhl",
    label: "Национальная хоккейная лига",
    short: "НХЛ",
    base: "/nhl",
    pages: [
      { href: "/nhl", label: "Матчи" },
      { href: "/nhl/journal", label: "Журнал" },
      { href: "/nhl/teams", label: "Команды" },
      { href: "/nhl/accuracy", label: "Точность" },
      { href: "/nhl/methodology", label: "Методика" },
    ],
  },
  {
    id: "nba",
    label: "Национальная баскетбольная ассоциация",
    short: "NBA",
    base: "/nba",
    pages: [
      { href: "/nba", label: "Матчи" },
      { href: "/nba/journal", label: "Журнал" },
      { href: "/nba/teams", label: "Команды" },
      { href: "/nba/accuracy", label: "Точность" },
      { href: "/nba/methodology", label: "Методика" },
    ],
  },
];

function sportFor(path: string): SportPages {
  if (path.startsWith("/basketball")) return SPORTS[1];
  if (path.startsWith("/rpl")) return SPORTS[2];
  if (path.startsWith("/wnba")) return SPORTS[3];
  if (path.startsWith("/nhl")) return SPORTS[4];
  if (path.startsWith("/nba")) return SPORTS[5];
  return SPORTS[0];
}

export function SportNav() {
  const path = usePathname() ?? "/";
  const current = sportFor(path);

  return (
    <div>
      <div className="flex items-center justify-between gap-4 py-3">
        <Link href={current.base} className="font-semibold tracking-tight">
          {current.label}
        </Link>
        <nav className="flex items-center gap-4 text-sm">
          {SPORTS.map((s) => (
            <Link
              key={s.id}
              href={s.base}
              className={s.id === current.id ? "text-text" : "link"}
              aria-current={s.id === current.id ? "page" : undefined}
            >
              {s.short}
            </Link>
          ))}
        </nav>
      </div>
      <div className="-mt-1 flex gap-4 overflow-x-auto pb-1 text-sm">
        {current.pages.map((p) => {
          const active = path === p.href;
          return (
            <Link
              key={p.href}
              href={p.href}
              className={`whitespace-nowrap border-b-2 pb-1 transition-colors ${
                active
                  ? "border-home text-text"
                  : "border-transparent text-muted hover:text-text"
              }`}
              aria-current={active ? "page" : undefined}
            >
              {p.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
