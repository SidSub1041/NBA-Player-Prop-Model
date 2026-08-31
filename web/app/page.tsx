"use client";

import { useEffect, useState } from "react";
import type { PropsData, PropPick, DayResult } from "@/lib/types";

const DASHBOARD_URL =
  "https://public.tableau.com/app/profile/sidharth.subramanian7031/viz/NBAPlayerPropModel/Dashboard1";

/* ── Shared bits ──────────────────────────────────────────────────── */

function BallLogo({ size, ink }: { size: number; ink: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="none" aria-hidden="true">
      <circle cx="50" cy="50" r="45" fill="var(--accent)" />
      <path
        d="M5 50 H95 M50 5 V95 M18 15 C40 40 40 60 18 85 M82 15 C60 40 60 60 82 85"
        stroke={ink}
        strokeWidth="4"
        fill="none"
      />
    </svg>
  );
}

function PlayerSilhouette({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="#8a7458" aria-hidden="true">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" />
    </svg>
  );
}

function GradeBadge({ grade }: { grade: string }) {
  const cls: Record<string, string> = {
    "A+": "grade-a-plus",
    A: "grade-a",
    "B+": "grade-b-plus",
    B: "grade-b",
    C: "grade-c",
    D: "grade-d",
    F: "grade-d",
  };
  return (
    <span className={`inline-block px-3 py-1.5 text-sm font-bold ${cls[grade] ?? "grade-d"}`}>
      {grade}
    </span>
  );
}

function EdgeTag({ edge }: { edge: string }) {
  const over = edge === "over";
  return (
    <span
      className="font-display inline-flex items-center gap-1.5 px-4 py-2 text-xl tracking-widest"
      style={{
        background: over ? "var(--over)" : "var(--under)",
        color: over ? "var(--over-ink)" : "var(--under-ink)",
      }}
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="3.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {over ? <path d="M12 20V5M6 11l6-6 6 6" /> : <path d="M12 4v15M6 13l6 6 6-6" />}
      </svg>
      {over ? "OVER" : "UNDER"}
    </span>
  );
}

function SectionHead({ title, tag }: { title: string; tag: string }) {
  return (
    <div className="flex items-baseline gap-5">
      <h2 className="font-display text-5xl sm:text-6xl tracking-wide leading-none">{title}</h2>
      <div className="court-rule hidden sm:block h-1 flex-grow" />
      <div className="hidden sm:block text-[13px] font-bold tracking-widest text-[var(--text-muted)]">
        {tag}
      </div>
    </div>
  );
}

/* ── Pick card ────────────────────────────────────────────────────── */

function PickCard({ pick }: { pick: PropPick }) {
  const hr = pick.hitrate !== null ? Math.round(pick.hitrate * 100) : null;
  const odds = pick.edge === "over" ? pick.ud_over_odds : pick.ud_under_odds;
  const statLabel = pick.stat.replace("points", "PTS").replace("rebounds", "REB").replace("assists", "AST").toUpperCase();
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] p-6 flex flex-col gap-4">
      <div className="flex items-center gap-4">
        <div className="w-12 h-12 shrink-0 rounded-full bg-[var(--card-hover)] border-2 border-[var(--border-strong)] flex items-center justify-center">
          <PlayerSilhouette size={26} />
        </div>
        <div className="flex flex-col gap-0.5 min-w-0 flex-grow overflow-hidden">
          <div className="font-display text-2xl sm:text-3xl tracking-wide leading-none whitespace-nowrap overflow-hidden text-ellipsis">
            {pick.player_name.toUpperCase()}
          </div>
          <div className="flex items-center gap-2 text-[13px] font-semibold text-[var(--text-muted)]">
            <span className="bg-[var(--card-hover)] border border-[var(--border-strong)] px-2 py-0.5 tracking-wider">
              {pick.team}
            </span>
            <span>vs</span>
            <span className="bg-[var(--card-hover)] border border-[var(--border-strong)] px-2 py-0.5 tracking-wider">
              {pick.opponent}
            </span>
            <span>· {pick.position}</span>
          </div>
        </div>
        <GradeBadge grade={pick.grade} />
      </div>
      <div className="flex items-center gap-4 bg-[var(--card-inset)] px-5 py-4">
        <div className="font-display text-4xl tracking-wide leading-none">
          {statLabel} {pick.ud_line ?? "—"}
        </div>
        <EdgeTag edge={pick.edge} />
        {odds && (
          <div className="ml-auto flex flex-col items-end">
            <div className="text-[13px] font-bold text-[var(--text-muted)]">{odds}</div>
            <div className="text-[11px] text-[var(--text-faint)]">payout</div>
          </div>
        )}
      </div>
      <div className="flex items-center gap-3">
        <div className="text-[11px] font-bold tracking-widest text-[var(--text-muted)] w-20 shrink-0">
          HIT RATE
        </div>
        <div className="flex-grow h-2 bg-[var(--card-hover)] overflow-hidden">
          <div className="h-2 bg-[var(--accent)]" style={{ width: `${hr ?? 0}%` }} />
        </div>
        <div className="text-sm font-bold">{hr !== null ? `${hr}%` : "N/A"}</div>
        {pick.season_avg !== null && (
          <div className="text-xs text-[var(--text-faint)]">· avg {pick.season_avg}</div>
        )}
      </div>
    </div>
  );
}

/* ── The run: cumulative units line chart ─────────────────────────── */

function UnitsChart({ results }: { results: DayResult[] }) {
  const W = 840;
  const H = 240;
  const PAD_L = 56;
  const PAD_R = 48;
  const PAD_T = 24;
  const PAD_B = 28;

  let cum = 0;
  const pts = results.map((r) => {
    cum += r.units ?? 0;
    return { date: r.date, cum };
  });
  if (pts.length === 0) return null;

  const lo = Math.min(0, ...pts.map((p) => p.cum));
  const hi = Math.max(0, ...pts.map((p) => p.cum));
  const span = Math.max(hi - lo, 1);
  const y = (v: number) => PAD_T + ((hi - v) / span) * (H - PAD_T - PAD_B);
  const x = (i: number) =>
    pts.length === 1
      ? W / 2
      : PAD_L + (i / (pts.length - 1)) * (W - PAD_L - PAD_R);

  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)} ${y(p.cum)}`).join(" ");
  const area = `${line} L${x(pts.length - 1)} ${y(0)} L${x(0)} ${y(0)} Z`;
  const last = pts[pts.length - 1];
  const fmtDate = (d: string) => {
    const dt = new Date(d + "T00:00:00");
    return dt
      .toLocaleDateString("en-US", { month: "short", day: "numeric" })
      .toUpperCase();
  };
  const fmtU = (u: number) => `${u >= 0 ? "+" : ""}${u.toFixed(2)}u`;
  const midIdx = Math.floor((pts.length - 1) / 2);
  const gridVals = [hi, (hi + lo) / 2, lo].filter(
    (v, i, a) => a.indexOf(v) === i,
  );

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-auto"
      aria-label="Cumulative units by day"
    >
      {gridVals.map((v) => (
        <g key={v}>
          <path d={`M${PAD_L} ${y(v)} H${W - PAD_R}`} stroke="var(--border-soft)" strokeWidth="1" />
          <text x={PAD_L - 8} y={y(v) + 4} fill="var(--text-faint)" fontSize="11" textAnchor="end">
            {fmtU(v)}
          </text>
        </g>
      ))}
      <path d={`M${PAD_L} ${y(0)} H${W - PAD_R}`} stroke="#4e3d28" strokeWidth="1" />
      <path d={area} fill="var(--accent)" opacity="0.1" />
      <path d={line} stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" fill="none" />
      {pts.map((p, i) => (
        <circle key={p.date} cx={x(i)} cy={y(p.cum)} r={i === pts.length - 1 ? 5 : 4} fill="var(--accent)" stroke="var(--card)" strokeWidth="2" />
      ))}
      <text x={W - PAD_R + 4} y={y(last.cum) - 10} fill="var(--text)" fontSize="13" fontWeight="700" textAnchor="end">
        {fmtU(last.cum)}
      </text>
      <text x={x(0)} y={H - 6} fill="var(--text-faint)" fontSize="11" textAnchor="middle">
        {fmtDate(pts[0].date)}
      </text>
      {pts.length > 2 && (
        <text x={x(midIdx)} y={H - 6} fill="var(--text-faint)" fontSize="11" textAnchor="middle">
          {fmtDate(pts[midIdx].date)}
        </text>
      )}
      {pts.length > 1 && (
        <text x={x(pts.length - 1)} y={H - 6} fill="var(--text-faint)" fontSize="11" textAnchor="middle">
          {fmtDate(last.date)}
        </text>
      )}
    </svg>
  );
}

/* ── Shot chart: one ball per graded pick ─────────────────────────── */

function ShotChart({ results }: { results: DayResult[] }) {
  const days = results.filter((r) => r.total_picks > 0).slice(-20);
  if (days.length === 0) return null;
  const fmtDate = (d: string) => {
    const dt = new Date(d + "T00:00:00");
    return dt
      .toLocaleDateString("en-US", { month: "short", day: "numeric" })
      .toUpperCase();
  };
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] p-7 flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <div className="text-[15px] font-bold">Shot chart</div>
        <div className="text-xs text-[var(--text-faint)]">
          one ball per graded pick, by day
        </div>
        <div className="ml-auto flex items-center gap-4 text-xs text-[var(--text-muted)]">
          <span className="inline-flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full bg-[var(--accent)] inline-block" />
            Make
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full border-2 border-[var(--text-faint)] box-border inline-block" />
            Miss
          </span>
        </div>
      </div>
      <div className="flex items-end gap-8 overflow-x-auto px-2 pt-2">
        {days.map((d) => (
          <div key={d.date} className="flex flex-col-reverse items-center gap-1.5 shrink-0">
            <div className="text-[11px] text-[var(--text-faint)] mt-1.5 whitespace-nowrap">
              {fmtDate(d.date)}
            </div>
            {Array.from({ length: d.hits }).map((_, i) => (
              <div key={`h${i}`} className="w-[18px] h-[18px] rounded-full bg-[var(--accent)]" />
            ))}
            {Array.from({ length: d.misses }).map((_, i) => (
              <div key={`m${i}`} className="w-[18px] h-[18px] rounded-full border-2 border-[var(--text-faint)] box-border" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── The Receipts ─────────────────────────────────────────────────── */

function Receipts({ results }: { results: DayResult[] }) {
  const graded = results.filter((r) => r.total_picks > 0);
  const totalPicks = graded.reduce((s, r) => s + r.total_picks, 0);
  const hits = graded.reduce((s, r) => s + r.hits, 0);
  const misses = graded.reduce((s, r) => s + r.misses, 0);
  const voided = results.reduce((s, r) => s + (r.voided ?? 0), 0);
  const rate = totalPicks > 0 ? Math.round((hits / totalPicks) * 100) : 0;
  const allUnits = results.reduce((s, r) => s + (r.units ?? 0), 0);
  const lastUnits = graded.length > 0 ? (graded[graded.length - 1].units ?? 0) : 0;
  const fmtU = (u: number) => `${u >= 0 ? "+" : ""}${u.toFixed(2)}u`;
  const uColor = (u: number) =>
    u > 0 ? "text-[var(--over)]" : u < 0 ? "text-red-400" : "text-[var(--text)]";

  if (totalPicks === 0) {
    return (
      <div className="bg-[var(--card)] border border-[var(--border)] p-10 text-center text-sm text-[var(--text-muted)]">
        No graded results yet. Picks are graded automatically after games finish.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 lg:grid-cols-[380px_minmax(0,1fr)] gap-6">
        <div className="bg-[var(--card)] border border-[var(--border)] p-8 flex flex-col justify-center gap-4">
          <div className="text-[13px] font-bold tracking-widest text-[var(--text-muted)]">
            HIT RATE
          </div>
          <div className="text-8xl font-bold leading-none">{rate}%</div>
          <div className="flex flex-col gap-2">
            <div className="h-2.5 bg-[var(--border)] overflow-hidden">
              <div className="h-2.5 bg-[var(--accent)]" style={{ width: `${rate}%` }} />
            </div>
            <div className="flex justify-between text-xs text-[var(--text-faint)]">
              <span>
                {hits} makes · {misses} misses
              </span>
              <span>{totalPicks} graded</span>
            </div>
          </div>
          <div className="border-t border-[var(--border)] pt-4 flex gap-6">
            <div className="flex flex-col gap-0.5">
              <div className={`text-xl font-bold ${uColor(allUnits)}`}>{fmtU(allUnits)}</div>
              <div className="text-[11px] tracking-wider text-[var(--text-faint)]">ALL-TIME</div>
            </div>
            <div className="flex flex-col gap-0.5">
              <div className={`text-xl font-bold ${uColor(lastUnits)}`}>{fmtU(lastUnits)}</div>
              <div className="text-[11px] tracking-wider text-[var(--text-faint)]">LAST DAY</div>
            </div>
            <div className="flex flex-col gap-0.5">
              <div className="text-xl font-bold">{voided}</div>
              <div className="text-[11px] tracking-wider text-[var(--text-faint)]">VOIDED</div>
            </div>
          </div>
        </div>
        <div className="bg-[var(--card)] border border-[var(--border)] p-7 flex flex-col gap-3">
          <div className="flex items-baseline gap-3">
            <div className="text-[15px] font-bold">The run</div>
            <div className="text-xs text-[var(--text-faint)]">
              cumulative units, flat 1u per pick
            </div>
          </div>
          <UnitsChart results={results} />
        </div>
      </div>
      <ShotChart results={results} />
    </div>
  );
}

/* ── The System ───────────────────────────────────────────────────── */

const SYSTEM_STEPS = [
  { n: "01", title: "SCAN MATCHUPS", body: "Defense vs. position, every game tonight." },
  { n: "02", title: "FIND STARTERS", body: "Depth charts + injury report, live." },
  { n: "03", title: "MAP ZONES", body: "Shot zones and playtypes vs. the defense." },
  { n: "04", title: "SCORE IT", body: "80% of conditions must pass. No exceptions." },
  { n: "05", title: "VERIFY HITS", body: "Season hit rate confirms it. 56% seals an A+." },
];

/* ── Grade ranking for deduplication ──────────────────────────────── */

const GRADE_RANK: Record<string, number> = {
  "A+": 6, A: 5, "B+": 4, B: 3, C: 2, D: 1, F: 0,
};

function deduplicatePicks(picks: PropPick[]): PropPick[] {
  const best = new Map<string, PropPick>();
  for (const p of picks) {
    const key = `${p.player_name}::${p.stat}::${p.edge}`;
    const existing = best.get(key);
    if (!existing || (GRADE_RANK[p.grade] ?? 0) > (GRADE_RANK[existing.grade] ?? 0)) {
      best.set(key, p);
    }
  }
  return Array.from(best.values());
}

/* ── Main page ────────────────────────────────────────────────────── */

export default function Home() {
  const [data, setData] = useState<PropsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
    fetch(`${base}/data/latest.json`)
      .then((res) => {
        if (!res.ok) throw new Error("No data available yet");
        return res.json();
      })
      .then((d: PropsData) => setData(d))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-pulse text-[var(--text-muted)]">Loading model data...</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center space-y-2">
          <p className="text-red-400 font-medium">{error ?? "No data available"}</p>
          <p className="text-sm text-[var(--text-muted)]">
            Run the model to generate picks: <code>python main.py</code>
          </p>
        </div>
      </div>
    );
  }

  const picks = deduplicatePicks([...data.valid_picks, ...(data.valid_combos ?? [])]);
  const results = data.model_results ?? [];
  const dateLabel = new Date(data.date + "T00:00:00")
    .toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
    .toUpperCase()
    .replace(",", " ·");

  return (
    <main className="flex flex-col">
      {/* Nav */}
      <div className="flex items-center gap-4 px-6 lg:px-16 py-5 border-b border-[var(--border-soft)]">
        <BallLogo size={30} ink="#171009" />
        <div className="font-display text-2xl tracking-widest">PROP MODEL</div>
        <div className="ml-auto flex items-center gap-7">
          <div className="hidden sm:block text-[13px] font-semibold tracking-wider text-[var(--text-muted)]">
            {dateLabel}
          </div>
          <div className="hidden md:block text-[13px] font-semibold tracking-wider text-[var(--text-muted)]">
            UPDATED {data.run_at.split(" ").slice(-2).join(" ").toUpperCase()}
          </div>
          <a
            href={DASHBOARD_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[13px] font-semibold tracking-wider text-[var(--accent)]"
          >
            DASHBOARD
          </a>
          <a
            href="https://github.com/SidSub1041/NBA-Player-Prop-Model"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[13px] font-semibold tracking-wider text-[var(--accent)]"
          >
            CODE
          </a>
        </div>
      </div>

      {/* Hero */}
      <div className="hardwood relative overflow-hidden border-b-[6px] border-[var(--border-strong)]">
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(180deg, rgba(23,16,9,0.45) 0%, rgba(23,16,9,0.15) 45%, rgba(23,16,9,0.55) 100%)",
          }}
        />
        <svg
          width="760"
          height="760"
          viewBox="0 0 760 760"
          fill="none"
          aria-hidden="true"
          className="absolute opacity-50 -top-80 -right-44 pointer-events-none"
        >
          <circle cx="380" cy="380" r="330" stroke="#F2E8D8" strokeWidth="10" fill="none" />
          <circle cx="380" cy="380" r="110" stroke="#F2E8D8" strokeWidth="10" fill="none" />
          <path d="M50 380 H710" stroke="#F2E8D8" strokeWidth="10" />
        </svg>
        <div className="relative flex flex-col gap-5 px-6 lg:px-16 py-20 lg:py-24 max-w-4xl">
          <div className="flex items-center gap-2.5">
            <span className="bg-[var(--bg)] text-[var(--text)] text-xs font-bold tracking-[3px] px-3.5 py-1.5">
              DAILY MODEL
            </span>
            <span className="bg-[var(--over)] text-[var(--over-ink)] text-xs font-bold tracking-[3px] px-3.5 py-1.5">
              LIVE
            </span>
          </div>
          <h1
            className="font-display text-7xl sm:text-8xl lg:text-9xl leading-[0.9] tracking-wide text-[var(--text-bright)]"
            style={{ textShadow: "0 4px 0 rgba(23,16,9,0.35)" }}
          >
            DAILY PICKS:
          </h1>
          <div
            className="text-lg sm:text-xl font-semibold"
            style={{ textShadow: "0 1px 0 rgba(23,16,9,0.4)" }}
          >
            {data.candidates_analyzed} props scanned. {picks.length} made the cut.
          </div>
          <div className="flex items-center gap-4 mt-2">
            <a
              href="#board"
              className="font-display inline-flex items-center gap-2.5 bg-[var(--bg)] text-[var(--text)] text-xl tracking-widest px-7 py-3.5"
            >
              SEE THE BOARD
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="var(--accent)"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </a>
            <div className="hidden sm:block text-[13px] font-semibold tracking-wider opacity-85">
              FREE. EVERY MORNING.
            </div>
          </div>
        </div>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 px-6 lg:px-16 pt-10 pb-2">
        {(() => {
          const graded = results.filter((r) => r.total_picks > 0);
          const tot = graded.reduce((s, r) => s + r.total_picks, 0);
          const hits = graded.reduce((s, r) => s + r.hits, 0);
          const units = results.reduce((s, r) => s + (r.units ?? 0), 0);
          const tiles = [
            { label: "PLAYS", value: String(picks.length), sub: "graded A or better", color: "var(--text)" },
            { label: "HIT RATE", value: tot > 0 ? `${Math.round((hits / tot) * 100)}%` : "—", sub: "all graded picks", color: "var(--text)" },
            { label: "UNITS", value: `${units >= 0 ? "+" : ""}${units.toFixed(2)}u`, sub: "flat 1u, all-time", color: units >= 0 ? "var(--over)" : "#f87171" },
            { label: "DAYS", value: String(results.length || "—"), sub: "tracked & graded", color: "var(--text)" },
          ];
          return tiles.map((t) => (
            <div
              key={t.label}
              className="bg-[var(--card)] border border-[var(--border)] border-t-[3px] border-t-[var(--accent)] px-6 py-5 flex flex-col gap-1.5"
            >
              <div className="text-xs font-bold tracking-[3px] text-[var(--text-muted)]">
                {t.label}
              </div>
              <div className="text-4xl font-bold leading-none" style={{ color: t.color }}>
                {t.value}
              </div>
              <div className="text-xs text-[var(--text-faint)]">{t.sub}</div>
            </div>
          ));
        })()}
      </div>

      {/* The Board */}
      <div id="board" className="flex flex-col gap-7 px-6 lg:px-16 pt-16 pb-6">
        <SectionHead title="THE BOARD" tag="TODAY'S PLAYS" />
        {picks.length === 0 ? (
          <div className="bg-[var(--card)] border border-[var(--border)] p-12 text-center flex flex-col gap-2">
            <div className="font-display text-4xl tracking-wide">NO PLAYS TONIGHT.</div>
            <div className="text-sm text-[var(--text-muted)]">
              The model found no A-grade edges today. Passing is a position.
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {picks.map((p) => (
              <PickCard key={`${p.player_name}-${p.stat}-${p.edge}`} pick={p} />
            ))}
          </div>
        )}
      </div>

      {/* The Receipts */}
      <div className="flex flex-col gap-7 px-6 lg:px-16 pt-16 pb-6">
        <SectionHead title="THE RECEIPTS" tag="EVERY PICK. GRADED." />
        <Receipts results={results} />
        <a
          href={DASHBOARD_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="self-start font-display inline-flex items-center gap-2.5 bg-[var(--card)] border border-[var(--border)] text-[var(--text)] text-lg tracking-widest px-6 py-3"
        >
          FULL DASHBOARD ON TABLEAU
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--accent)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </a>
      </div>

      {/* The System */}
      <div className="flex flex-col gap-7 px-6 lg:px-16 pt-16 pb-10">
        <SectionHead title="THE SYSTEM" tag="NO GUESSWORK" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-5">
          {SYSTEM_STEPS.map((s) => (
            <div
              key={s.n}
              className="bg-[var(--card)] border border-[var(--border)] p-6 flex flex-col gap-3"
            >
              <div className="font-display text-4xl text-[var(--wood)] leading-none">{s.n}</div>
              <div className="font-display text-2xl tracking-wide leading-none">{s.title}</div>
              <div className="text-[13px] leading-relaxed text-[var(--text-muted)]">{s.body}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center gap-4 px-6 lg:px-16 py-7 border-t border-[var(--border-soft)] bg-[var(--bg-deep)]">
        <BallLogo size={20} ink="#130d07" />
        <div className="text-xs text-[var(--text-faint)]">
          Updates hourly · Lines via Underdog Fantasy · Not financial advice
        </div>
        <a
          href="https://github.com/SidSub1041/NBA-Player-Prop-Model"
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-xs font-semibold tracking-wider text-[var(--text-muted)]"
        >
          GITHUB
        </a>
      </div>
    </main>
  );
}
