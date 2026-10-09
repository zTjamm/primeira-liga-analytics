/** Полоса распределения вероятностей П1 / X / П2. */
export function ProbBar({
  home,
  draw,
  away,
  height = 8,
}: {
  home: number;
  draw: number;
  away: number;
  height?: number;
}) {
  const total = home + draw + away || 1;
  const h = (home / total) * 100;
  const d = (draw / total) * 100;
  const a = (away / total) * 100;
  return (
    <div className="bar-track" style={{ height }}>
      <div className="bar-seg bg-home" style={{ width: `${h}%` }} />
      <div className="bar-seg bg-draw" style={{ width: `${d}%` }} />
      <div className="bar-seg bg-away" style={{ width: `${a}%` }} />
    </div>
  );
}

/** Три числа исходов с подсветкой наиболее вероятного. */
export function ProbRow({
  home,
  draw,
  away,
  labels = ["П1", "X", "П2"],
}: {
  home: number;
  draw: number;
  away: number;
  labels?: [string, string, string];
}) {
  const items = [
    { v: home, label: labels[0], cls: "text-home" },
    { v: draw, label: labels[1], cls: "text-draw" },
    { v: away, label: labels[2], cls: "text-away" },
  ];
  const best = Math.max(home, draw, away);
  return (
    <div className="grid grid-cols-3 gap-2">
      {items.map((it) => (
        <div key={it.label} className="text-center">
          <div
            className={`num text-lg font-semibold ${it.v === best ? it.cls : "text-muted"}`}
          >
            {(it.v * 100).toFixed(1)}%
          </div>
          <div className="text-[11px] text-muted">{it.label}</div>
        </div>
      ))}
    </div>
  );
}
