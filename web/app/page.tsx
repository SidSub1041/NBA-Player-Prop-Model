"use client";

import { useEffect, useState } from "react";
import type { PropsData, PropPick } from "@/lib/types";

/* ── Grade badge ──────────────────────────────────────────────────── */
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
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${cls[grade] ?? "grade-d"}`}
    >
      {grade}
    </span>
  );
}

/* ── Edge badge ───────────────────────────────────────────────────── */
function EdgeBadge({ edge }: { edge: string }) {
  return edge === "over" ? (
    <span className="text-emerald-400 font-semibold text-xs uppercase">
      ▲ Over
    </span>
  ) : (
    <span className="text-red-400 font-semibold text-xs uppercase">
      ▼ Under
    </span>
  );
}

/* ── Picks table ──────────────────────────────────────────────────── */
function PicksTable({
  picks,
  title,
  icon,
}: {
  picks: PropPick[];
  title: string;
  icon: string;
}) {
  if (picks.length === 0) return null;
  return (
    <div className="bg-[var(--card)] rounded-xl border border-[var(--border)] overflow-hidden">
      <div className="px-6 py-4 border-b border-[var(--border)] flex items-center gap-2">
        <span className="text-xl">{icon}</span>
        <h2 className="text-lg font-bold">{title}</h2>
        <span className="ml-auto text-sm text-[var(--text-muted)]">
          {picks.length} pick{picks.length !== 1 ? "s" : ""}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[var(--text-muted)] text-xs uppercase tracking-wider border-b border-[var(--border)]">
              <th className="px-4 py-3 text-left">Player</th>
              <th className="px-3 py-3 text-left">Matchup</th>
              <th className="px-3 py-3 text-center">Pos</th>
              <th className="px-3 py-3 text-left">Stat</th>
              <th className="px-3 py-3 text-center">Edge</th>
              <th className="px-3 py-3 text-center">Line</th>
              <th className="px-3 py-3 text-center">Odds</th>
              <th className="px-3 py-3 text-center">Score</th>
              <th className="px-3 py-3 text-center">Hit Rate</th>
              <th className="px-3 py-3 text-center">Grade</th>
            </tr>
          </thead>
          <tbody>
            {picks.map((p, i) => {
              const hr =
                p.hitrate !== null ? `${(p.hitrate * 100).toFixed(0)}%` : "N/A";
              const odds =
                p.edge === "over" ? p.ud_over_odds : p.ud_under_odds;
              return (
                <tr
                  key={`${p.player_name}-${p.stat}-${i}`}
                  className="border-b border-[var(--border)] hover:bg-[var(--card-hover)] transition-colors"
                >
                  <td className="px-4 py-3 font-medium whitespace-nowrap">
                    {p.player_name}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap text-[var(--text-muted)]">
                    {p.team} vs {p.opponent}
                  </td>
                  <td className="px-3 py-3 text-center text-xs font-mono">
                    {p.position}
                  </td>
                  <td className="px-3 py-3 uppercase text-xs font-semibold">
                    {p.stat}
                  </td>
                  <td className="px-3 py-3 text-center">
                    <EdgeBadge edge={p.edge} />
                  </td>
                  <td className="px-3 py-3 text-center font-mono">
                    {p.ud_line ?? "—"}
                  </td>
                  <td className="px-3 py-3 text-center font-mono text-xs">
                    {odds ?? "—"}
                  </td>
                  <td className="px-3 py-3 text-center font-mono">
                    {p.total_points}/{p.total_conditions}
                  </td>
                  <td className="px-3 py-3 text-center">
                    <span
                      className={`font-mono ${
                        p.hitrate !== null && p.hitrate >= 0.56
                          ? "text-emerald-400"
                          : p.hitrate !== null && p.hitrate >= 0.5
                            ? "text-yellow-400"
                            : "text-red-400"
                      }`}
                    >
                      {hr}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-center">
                    <GradeBadge grade={p.grade} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Model Track Record section ───────────────────────────────────── */
function ModelTrackRecord({ data }: { data: PropsData }) {
  const results = data.model_results ?? [];
  const hasResults = results.length > 0;

  // Aggregate stats across all recorded days
  const totalPicks = results.reduce((s, r) => s + r.total_picks, 0);
  const totalHits = results.reduce((s, r) => s + r.hits, 0);
  const totalMisses = results.reduce((s, r) => s + r.misses, 0);
  const totalVoided = results.reduce((s, r) => s + (r.voided ?? 0), 0);
  const overallRate = totalPicks > 0 ? totalHits / totalPicks : 0;

  // Units calculations
  const allTimeUnits = results.reduce((s, r) => s + (r.units ?? 0), 0);

  const now = new Date();
  const currentYear = now.getFullYear();
  const currentMonth = now.getMonth(); // 0-indexed

  const ytdResults = results.filter((r) => {
    const d = new Date(r.date + "T00:00:00");
    return d.getFullYear() === currentYear;
  });
  const ytdUnits = ytdResults.reduce((s, r) => s + (r.units ?? 0), 0);

  const monthResults = results.filter((r) => {
    const d = new Date(r.date + "T00:00:00");
    return d.getFullYear() === currentYear && d.getMonth() === currentMonth;
  });
  const monthUnits = monthResults.reduce((s, r) => s + (r.units ?? 0), 0);

  const formatUnits = (u: number) => {
    const sign = u >= 0 ? "+" : "";
    return `${sign}${u.toFixed(2)}u`;
  };

  const unitsColor = (u: number) =>
    u > 0 ? "text-emerald-400" : u < 0 ? "text-red-400" : "text-gray-400";

  return (
    <div className="bg-[var(--card)] rounded-xl border border-[var(--border)] overflow-hidden">
      <div className="px-6 py-4 border-b border-[var(--border)] flex items-center gap-2">
        <span className="text-xl">📊</span>
        <h2 className="text-lg font-bold">Model Track Record</h2>
      </div>

      {!hasResults ? (
        <div className="p-8 text-center space-y-3">
          <div className="text-4xl">📭</div>
          <p className="text-[var(--text-muted)] text-sm">
            No results recorded yet. After today&apos;s games finish, picks will be
            graded and the model&apos;s success rate will appear here.
          </p>
          <p className="text-xs text-[var(--text-muted)]">
            A &ldquo;hit&rdquo; means the player went over/under their line as predicted.
          </p>
        </div>
      ) : (
        <div className="p-6 space-y-6">
          {/* Summary stats */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-emerald-400">
                {(overallRate * 100).toFixed(1)}%
              </div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                Overall Hit Rate
              </div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-400">
                {totalHits}
              </div>
              <div className="text-xs text-[var(--text-muted)] mt-1">Hits</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-red-400">
                {totalMisses}
              </div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                Misses
              </div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-gray-400">
                {totalVoided}
              </div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                Voided
              </div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-400">
                {results.length}
              </div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                Days Tracked
              </div>
            </div>
          </div>

          {/* Units P&L */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-gray-800/50 rounded-lg p-4 text-center">
              <div className={`text-xl font-bold font-mono ${unitsColor(allTimeUnits)}`}>
                {formatUnits(allTimeUnits)}
              </div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                All-Time
              </div>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-4 text-center">
              <div className={`text-xl font-bold font-mono ${unitsColor(monthUnits)}`}>
                {formatUnits(monthUnits)}
              </div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                This Month
              </div>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-4 text-center">
              <div className={`text-xl font-bold font-mono ${unitsColor(ytdUnits)}`}>
                {formatUnits(ytdUnits)}
              </div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                YTD
              </div>
            </div>
          </div>

          {/* Overall bar */}
          <div>
            <div className="flex justify-between text-xs text-[var(--text-muted)] mb-1">
              <span>{totalHits} hits</span>
              <span>{totalPicks} total picks</span>
            </div>
            <div className="h-4 bg-gray-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-500 rounded-full transition-all duration-500"
                style={{ width: `${overallRate * 100}%` }}
              />
            </div>
          </div>

          {/* Per-day breakdown */}
          <div>
            <h3 className="text-sm font-semibold text-[var(--text-muted)] mb-3 uppercase tracking-wider">
              Daily Results
            </h3>
            <div className="space-y-2">
              {results.map((r) => {
                const dayRate =
                  r.total_picks > 0 ? r.hits / r.total_picks : 0;
                const dayVoided = r.voided ?? 0;
                const dayUnits = r.units ?? 0;
                return (
                  <div
                    key={r.date}
                    className="flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-800/50"
                  >
                    <span className="text-xs text-[var(--text-muted)] w-24 font-mono">
                      {r.date}
                    </span>
                    <div className="flex-1 h-2 bg-gray-700 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          dayRate >= 0.6
                            ? "bg-emerald-500"
                            : dayRate >= 0.5
                              ? "bg-yellow-500"
                              : "bg-red-500"
                        }`}
                        style={{ width: `${dayRate * 100}%` }}
                      />
                    </div>
                    <span
                      className={`text-xs font-mono w-20 text-right ${
                        dayRate >= 0.6
                          ? "text-emerald-400"
                          : dayRate >= 0.5
                            ? "text-yellow-400"
                            : "text-red-400"
                      }`}
                    >
                      {r.hits}/{r.total_picks} ({(dayRate * 100).toFixed(0)}%)
                    </span>
                    <span
                      className={`text-xs font-mono w-16 text-right ${unitsColor(dayUnits)}`}
                    >
                      {formatUnits(dayUnits)}
                    </span>
                    {dayVoided > 0 && (
                      <span className="text-xs font-mono text-gray-500 w-8 text-right">
                        {dayVoided}V
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

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

/* ── Format date for display ─────────────────────────────────────── */
function formatDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

/* ── About / Methodology section ──────────────────────────────────── */
function AboutSection() {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-[var(--card)] rounded-xl border border-[var(--border)] overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-6 py-4 border-b border-[var(--border)] flex items-center gap-2 text-left hover:bg-[var(--card-hover)] transition-colors"
      >
        <span className="text-xl">🏀</span>
        <h2 className="text-lg font-bold">How the Model Works</h2>
        <span className="ml-auto text-[var(--text-muted)]">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="p-6 space-y-6 text-sm leading-relaxed text-[var(--text-muted)]">
          <p>
            This model identifies NBA player prop edges by combining{" "}
            <strong className="text-[var(--text)]">Defense vs. Position</strong>{" "}
            matchup analysis with advanced shooting zones, playtype metrics,
            adaptive weighting, and hit-rate verification.
          </p>

          <div className="space-y-3">
            <h3 className="text-[var(--text)] font-semibold">Methodology (10 Steps)</h3>
            <ol className="list-decimal list-inside space-y-2 pl-2">
              <li>
                <strong className="text-[var(--text)]">Game Schedule</strong> — Fetch
                today&apos;s NBA games via the official NBA API and identify all teams playing.
              </li>
              <li>
                <strong className="text-[var(--text)]">DvP Matchup Scan</strong> — Scrape
                FantasyPros Defense vs. Position rankings (FanDuel). Green cells = easy
                matchups (over signal), gold cells = tough matchups (under signal) across
                PTS, REB, AST for all 5 positions.
              </li>
              <li>
                <strong className="text-[var(--text)]">Player Identification</strong> — For
                each highlighted matchup, find the starter at that position on the opposing
                team using ESPN depth charts.
              </li>
              <li>
                <strong className="text-[var(--text)]">Injury Adjustments</strong> — Check
                the NBA injury report. If the starter is out, find the backup from the depth
                chart and evaluate them instead.
              </li>
              <li>
                <strong className="text-[var(--text)]">Minutes Filter</strong> — Pull
                per-game minutes from NBA Stats. Players averaging fewer than 20 MPG are
                excluded as unreliable prop candidates.
              </li>
              <li>
                <strong className="text-[var(--text)]">Shooting Zone Analysis</strong>{" "}
                (Points only) — Identify the player&apos;s primary shooting zones
                (≥20% of FGA). Check if the opponent ranks in the top/bottom 10 in FG%
                allowed in each zone.
              </li>
              <li>
                <strong className="text-[var(--text)]">Playtype Analysis</strong> (Points
                only) — Identify player playtypes with ≥15% frequency (isolation,
                pick &amp; roll, spot-up, etc.). Check if the opponent ranks in the
                top/bottom 10 in PPP allowed per playtype.
              </li>
              <li>
                <strong className="text-[var(--text)]">Adaptive Weighting</strong> — The
                model dynamically weights shooting zones vs. playtypes based on which
                signal has historically predicted outcomes better for each stat type.
              </li>
              <li>
                <strong className="text-[var(--text)]">Condition Scoring</strong> — Each
                favorable zone/playtype match = 1 point. If ≥80% of weighted conditions
                are met, the prop passes the primary filter.
              </li>
              <li>
                <strong className="text-[var(--text)]">Hit Rate Verification</strong> —
                Check the player&apos;s season hit rate on the specific line. ≥56% hit rate
                combined with ≥80% conditions = valid pick (A/A+). Lower thresholds
                produce B-tier picks.
              </li>
            </ol>
          </div>

          <div className="space-y-2">
            <h3 className="text-[var(--text)] font-semibold">Grading Scale</h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
              <div className="flex items-center gap-2">
                <GradeBadge grade="A+" /> ≥80% conditions + ≥56% HR
              </div>
              <div className="flex items-center gap-2">
                <GradeBadge grade="A" /> ≥80% conditions + ≥50% HR
              </div>
              <div className="flex items-center gap-2">
                <GradeBadge grade="B+" /> ≥60% conditions + ≥56% HR
              </div>
              <div className="flex items-center gap-2">
                <GradeBadge grade="B" /> ≥60% conditions
              </div>
              <div className="flex items-center gap-2">
                <GradeBadge grade="C" /> ≥40% conditions
              </div>
              <div className="flex items-center gap-2">
                <GradeBadge grade="D" /> &lt;40% conditions
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <h3 className="text-[var(--text)] font-semibold">How Results Are Graded</h3>
            <p>
              After all games finish each night, the model automatically pulls box-score
              stats from the NBA API and checks whether each pick hit or missed. A pick
              is a <strong className="text-emerald-400">hit</strong> if the player went
              over/under their line as predicted, a <strong className="text-red-400">miss</strong>{" "}
              if they didn&apos;t, or <strong className="text-gray-400">voided</strong> if the
              player didn&apos;t play. Unit tracking assumes a flat 1u bet at the Underdog
              payout odds for each pick.
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="text-[var(--text)] font-semibold">Data Sources</h3>
            <ul className="list-disc list-inside space-y-1 pl-2">
              <li>NBA.com Stats API — game schedules, player stats, box scores, shooting zones</li>
              <li>FantasyPros — Defense vs. Position matchup rankings</li>
              <li>ESPN — Depth charts for starter identification</li>
              <li>Underdog Fantasy — Lines and payout odds</li>
            </ul>
          </div>

          <div className="pt-4 border-t border-[var(--border)]">
            <a
              href="https://github.com/SidSub1041/NBA-Player-Prop-Model"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors text-sm font-medium"
            >
              <svg
                className="w-5 h-5"
                fill="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  fillRule="evenodd"
                  d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                  clipRule="evenodd"
                />
              </svg>
              View on GitHub
            </a>
          </div>
        </div>
      )}
    </div>
  );
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
        <div className="animate-pulse text-[var(--text-muted)]">
          Loading model data...
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center space-y-2">
          <p className="text-red-400 font-medium">
            {error ?? "No data available"}
          </p>
          <p className="text-sm text-[var(--text-muted)]">
            Run the model to generate picks: <code>python main.py</code>
          </p>
        </div>
      </div>
    );
  }

  return (
    <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
      {/* Header */}
      <header className="text-center space-y-2">
        <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight">
          🏀 NBA Player Prop Model
        </h1>
        <p className="text-[var(--text-muted)] text-sm">
          Last updated {data.run_at} — {data.candidates_analyzed}{" "}
          props analyzed
        </p>
      </header>

      {/* Date banner */}
      <div className="text-center">
        <div className="inline-block bg-[var(--card)] rounded-xl border border-[var(--border)] px-8 py-3">
          <div className="text-lg font-bold">{formatDate(data.date)}</div>
        </div>
      </div>

      {/* Quick stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {[
          {
            label: "Valid Picks",
            value: data.valid_count + (data.combo_valid_count ?? 0),
            color: "text-emerald-400",
          },
          {
            label: "Analyzed",
            value: data.candidates_analyzed,
            color: "text-blue-400",
          },
          {
            label: "Days Tracked",
            value: (data.model_results ?? []).length || "—",
            color: "text-cyan-400",
          },
          {
            label: "Model Success",
            value: (() => {
              const r = data.model_results ?? [];
              if (r.length === 0) return "—";
              const hits = r.reduce((s, d) => s + d.hits, 0);
              const total = r.reduce((s, d) => s + d.total_picks, 0);
              return total > 0 ? `${((hits / total) * 100).toFixed(0)}%` : "—";
            })(),
            color: "text-purple-400",
          },
        ].map((s) => (
          <div
            key={s.label}
            className="bg-[var(--card)] rounded-xl border border-[var(--border)] p-4 text-center"
          >
            <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
            <div className="text-xs text-[var(--text-muted)] mt-1">
              {s.label}
            </div>
          </div>
        ))}
      </div>

      {/* All picks (deduplicated, best grade per player+stat+edge) */}
      <PicksTable
        picks={deduplicatePicks([
          ...data.valid_picks,
          ...(data.valid_combos ?? []),
        ])}
        title="Today's Picks"
        icon="✅"
      />

      {/* Model track record */}
      <ModelTrackRecord data={data} />

      {/* About / methodology */}
      <AboutSection />

      {/* Footer */}
      <footer className="text-center text-xs text-[var(--text-muted)] py-4 border-t border-[var(--border)]">
        Model updates hourly. Lines and odds sourced from Underdog Fantasy.
        Not financial advice.
      </footer>
    </main>
  );
}
