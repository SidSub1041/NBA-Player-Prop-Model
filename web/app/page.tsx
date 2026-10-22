"use client";

import { useEffect, useState } from "react";
import type { PropsData, PropPick, DayResult } from "@/lib/types";

const DASHBOARD_URL =
  "https://public.tableau.com/app/profile/sidharth.subramanian7031/viz/NBAPlayerPropModel/Dashboard1";
const GITHUB_URL = "https://github.com/SidSub1041/NBA-Player-Prop-Model";
const DEFAULT_HITRATE_THRESHOLD = 0.56;

/* ── Season + date helpers ────────────────────────────────────────── */

/** 2026-10-20 -> "2026-27". Seasons start in October (same rule as src/config.py). */
function seasonForDate(iso: string): string {
  const [y, m] = iso.split("-").map(Number);
  const start = m >= 10 ? y : y - 1;
  return `${start}-${String((start + 1) % 100).padStart(2, "0")}`;
}

function previousSeason(season: string): string {
  const start = parseInt(season.slice(0, 4), 10) - 1;
  return `${start}-${String((start + 1) % 100).padStart(2, "0")}`;
}

function localISODate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtDate(iso: string): string {
  return new Date(iso + "T00:00:00")
    .toLocaleDateString("en-US", { month: "short", day: "numeric" })
    .toUpperCase();
}

function fmtDayDate(iso: string): string {
  return new Date(iso + "T00:00:00")
    .toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
    .toUpperCase()
    .replace(",", " ·");
}

function fmtLongDate(iso: string): string {
  return new Date(iso + "T00:00:00")
    .toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })
    .toUpperCase();
}

/** Run timestamp in the viewer's own timezone; null if the payload's ISO string is unusable. */
function fmtRunTime(iso: string | undefined): string | null {
  if (!iso) return null;
  // Payloads from before the timezone fix carried a naive timestamp that was
  // actually UTC (the CI runner's clock); pin it so it isn't read as local time.
  const hasOffset = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasOffset ? iso : iso + "Z");
  if (Number.isNaN(d.getTime())) return null;
  return d
    .toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZoneName: "short" })
    .toUpperCase();
}

/* ── Formatting helpers ───────────────────────────────────────────── */

const STAT_LABELS: Record<string, string> = {
  points: "PTS",
  rebounds: "REB",
  assists: "AST",
  "pts+ast": "PTS+AST",
  "pts+reb": "PTS+REB",
  "reb+ast": "REB+AST",
  pra: "PRA",
};

function statLabel(stat: string): string {
  return STAT_LABELS[stat] ?? stat.toUpperCase();
}

function fmtUnits(u: number): string {
  return `${u >= 0 ? "+" : ""}${u.toFixed(2)}u`;
}

function unitsColor(u: number): string {
  return u > 0 ? "var(--over)" : u < 0 ? "#f87171" : "var(--text)";
}

/** Colors the raw hit rate against the model's own A+ threshold (never the rounded display value). */
function hitrateColor(hitrate: number, threshold: number): string {
  return hitrate >= threshold ? "var(--over)" : hitrate >= 0.5 ? "var(--under)" : "#f87171";
}

interface ResultsSummary {
  totalPicks: number;
  hits: number;
  misses: number;
  voided: number;
  gradedDays: number;
  rate: number;
  allUnits: number;
  lastGraded: DayResult | null;
}

function aggregateResults(results: DayResult[]): ResultsSummary {
  const decided = results.filter((r) => r.total_picks > 0);
  const totalPicks = decided.reduce((s, r) => s + r.total_picks, 0);
  const hits = decided.reduce((s, r) => s + r.hits, 0);
  const misses = decided.reduce((s, r) => s + r.misses, 0);
  const voided = results.reduce((s, r) => s + (r.voided ?? 0), 0);
  return {
    totalPicks,
    hits,
    misses,
    voided,
    gradedDays: results.filter((r) => r.total_picks > 0 || (r.voided ?? 0) > 0).length,
    rate: totalPicks > 0 ? Math.round((hits / totalPicks) * 100) : 0,
    allUnits: results.reduce((s, r) => s + (r.units ?? 0), 0),
    lastGraded: decided.length > 0 ? decided[decided.length - 1] : null,
  };
}

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

function ArrowIcon({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
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
  );
}

function GradeBadge({ grade, small }: { grade: string; small?: boolean }) {
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
      className={`inline-block font-bold ${small ? "px-2 py-0.5 text-xs" : "px-3 py-1.5 text-sm"} ${cls[grade] ?? "grade-d"}`}
    >
      {grade}
    </span>
  );
}

function EdgeTag({ edge }: { edge: string }) {
  const known = edge === "over" || edge === "under";
  const over = edge === "over";
  return (
    <span
      className="font-display inline-flex items-center gap-1.5 px-4 py-2 text-xl tracking-widest"
      style={{
        background: known ? (over ? "var(--over)" : "var(--under)") : "var(--card-hover)",
        color: known ? (over ? "var(--over-ink)" : "var(--under-ink)") : "var(--text)",
      }}
    >
      {known && (
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
      )}
      {known ? (over ? "OVER" : "UNDER") : edge.toUpperCase()}
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

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] p-12 text-center flex flex-col gap-2">
      <div className="font-display text-4xl tracking-wide">{title}</div>
      <div className="text-sm text-[var(--text-muted)]">{body}</div>
    </div>
  );
}

/* ── Vegas (sportsbook) odds — display only ───────────────────────── */

function VegasOdds({ pick, edge }: { pick: PropPick; edge: string }) {
  const price = edge === "under" ? pick.vegas_under_odds : pick.vegas_over_odds;
  if (pick.vegas_line == null || !price) return null;
  const sameLine = pick.ud_line != null && pick.vegas_line === pick.ud_line;
  return (
    <div className="text-right">
      <div className="text-[13px] font-bold text-[var(--text)]">
        {sameLine ? "" : `${edge === "under" ? "U" : "O"} ${pick.vegas_line} `}
        {price}
      </div>
      <div className="text-[11px] text-[var(--text-faint)] whitespace-nowrap">
        vegas{pick.vegas_book ? ` · ${pick.vegas_book}` : ""}
      </div>
    </div>
  );
}

/* ── On hold: conditions met, waiting on a line ─────────────────────── */

function OnHoldPanel({ picks }: { picks: PropPick[] }) {
  if (picks.length === 0) return null;
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] p-5 flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <div className="font-display text-2xl tracking-wide whitespace-nowrap">ON HOLD</div>
        <div className="text-[12px] font-semibold tracking-wider text-[var(--text-muted)]">
          CONDITIONS MET · WAITING ON A LINE · RE-CHECKED EVERY RUN
        </div>
      </div>
      <div className="flex flex-col divide-y divide-[var(--border-soft)]">
        {picks.map((p) => (
          <div
            key={`hold-${p.player_name}-${p.stat}-${p.edge.toLowerCase()}`}
            className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm"
          >
            <span className="font-semibold">{p.player_name}</span>
            <span className="text-[var(--text-muted)]">
              {p.team} vs {p.opponent}
            </span>
            <span className="font-display tracking-wide">
              {statLabel(p.stat)} {p.edge.toUpperCase()}
            </span>
            {p.vegas_line != null && (
              <span className="text-[var(--text-muted)]">
                vegas {p.vegas_line} · O {p.vegas_over_odds ?? "—"} / U {p.vegas_under_odds ?? "—"}
              </span>
            )}
            <span className="sm:ml-auto text-[12px] text-[var(--text-faint)]">{p.hold_reason ?? "Awaiting line"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Pick card ────────────────────────────────────────────────────── */

function PickCard({ pick, hrThreshold }: { pick: PropPick; hrThreshold: number }) {
  const edge = pick.edge.toLowerCase();
  const hr = pick.hitrate !== null ? Math.round(pick.hitrate * 100) : null;
  const odds = edge === "over" ? pick.ud_over_odds : edge === "under" ? pick.ud_under_odds : null;
  const oddsCaption = pick.ud_line === null ? "no line" : odds ? "payout" : "no odds";
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
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] font-semibold text-[var(--text-muted)]">
            <span className="bg-[var(--card-hover)] border border-[var(--border-strong)] px-2 py-0.5 tracking-wider">
              {pick.team}
            </span>
            <span>vs</span>
            <span className="bg-[var(--card-hover)] border border-[var(--border-strong)] px-2 py-0.5 tracking-wider">
              {pick.opponent}
            </span>
            <span className="whitespace-nowrap">· {pick.position}</span>
            {pick.injury && (
              <span className="text-[11px] font-bold tracking-wider px-1.5 py-0.5 border border-[var(--under)] text-[var(--under)] whitespace-nowrap">
                {pick.injury.toUpperCase()}
              </span>
            )}
          </div>
        </div>
        <GradeBadge grade={pick.grade} />
      </div>
      <div className="flex items-center gap-4 bg-[var(--card-inset)] px-5 py-4">
        <div className="font-display text-4xl tracking-wide leading-none">
          {statLabel(pick.stat)} {pick.ud_line ?? "—"}
        </div>
        <EdgeTag edge={edge} />
        <div className="ml-auto flex flex-col items-end gap-1">
          <div className="text-right">
            <div className="text-[13px] font-bold text-[var(--text-muted)]">{odds ?? "—"}</div>
            <div className="text-[11px] text-[var(--text-faint)]">underdog {oddsCaption}</div>
          </div>
          <VegasOdds pick={pick} edge={edge} />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="text-[11px] font-bold tracking-widest text-[var(--text-muted)] w-20 shrink-0">
          HIT RATE
        </div>
        {hr !== null && pick.hitrate !== null ? (
          <>
            <div className="flex-grow h-2 bg-[var(--card-hover)] overflow-hidden">
              <div className="h-2 bg-[var(--accent)]" style={{ width: `${hr}%` }} />
            </div>
            <div className="text-sm font-bold" style={{ color: hitrateColor(pick.hitrate, hrThreshold) }}>
              {hr}%
            </div>
          </>
        ) : (
          <div className="flex-grow text-sm font-bold text-[var(--text-faint)]">N/A</div>
        )}
        {pick.hitrate_source === "gamelog_vs_line" && pick.games_hit != null && pick.games_played ? (
          <div className="text-xs text-[var(--text-faint)] whitespace-nowrap">
            · {pick.games_hit}/{pick.games_played} vs line
          </div>
        ) : pick.hitrate_source === "season_avg_estimate" ? (
          <div className="text-xs text-[var(--text-faint)] whitespace-nowrap">· est. (no line)</div>
        ) : (
          pick.season_avg !== null && (
            <div className="text-xs text-[var(--text-faint)]">· avg {pick.season_avg}</div>
          )
        )}
      </div>
    </div>
  );
}

/* ── Archived (last season) board — compact, off to the side ──────── */

function ArchivedBoard({ picks, date, season }: { picks: PropPick[]; date: string; season: string }) {
  return (
    <aside className="bg-[var(--card)] border border-[var(--border)] p-5 flex flex-col gap-3 opacity-80">
      <div className="flex items-baseline gap-2">
        <div className="text-[11px] font-bold tracking-widest text-[var(--text-muted)]">
          LAST SEASON&apos;S FINAL BOARD
        </div>
        <div className="ml-auto text-[11px] text-[var(--text-faint)]">
          {season} · {fmtDate(date)}
        </div>
      </div>
      {picks.length === 0 ? (
        <div className="text-sm text-[var(--text-faint)]">No plays on the final slate.</div>
      ) : (
        <div className="flex flex-col divide-y divide-[var(--border-soft)]">
          {picks.map((p) => {
            const edge = p.edge.toLowerCase();
            return (
              <div key={`${p.player_name}-${p.stat}-${edge}`} className="flex items-center gap-3 py-2 text-sm">
                <div className="font-semibold min-w-0 flex-grow overflow-hidden whitespace-nowrap text-ellipsis">
                  {p.player_name}
                </div>
                <div className="font-display tracking-wide text-base text-[var(--text-muted)]">
                  {statLabel(p.stat)} {p.ud_line ?? "—"}
                </div>
                <div
                  className="text-[11px] font-bold tracking-widest"
                  style={{ color: edge === "over" ? "var(--over)" : "var(--under)" }}
                >
                  {edge.toUpperCase()}
                </div>
                <GradeBadge grade={p.grade} small />
              </div>
            );
          })}
        </div>
      )}
    </aside>
  );
}

/* ── The run: cumulative units line chart ─────────────────────────── */

function cumulative(results: DayResult[]) {
  let cum = 0;
  return results.map((r) => {
    cum += r.units ?? 0;
    return { date: r.date, cum };
  });
}

function UnitsChart({ results }: { results: DayResult[] }) {
  const W = 840;
  const H = 240;
  const PAD_L = 56;
  const PAD_R = 64;
  const PAD_T = 24;
  const PAD_B = 28;

  const pts = cumulative(results);
  if (pts.length === 0) return null;

  const lo = Math.min(0, ...pts.map((p) => p.cum));
  const hi = Math.max(0, ...pts.map((p) => p.cum));
  const span = Math.max(hi - lo, 1);
  const y = (v: number) => PAD_T + ((hi - v) / span) * (H - PAD_T - PAD_B);
  const x = (i: number) =>
    pts.length === 1 ? W / 2 : PAD_L + (i / (pts.length - 1)) * (W - PAD_L - PAD_R);

  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)} ${y(p.cum)}`).join(" ");
  const area = `${line} L${x(pts.length - 1)} ${y(0)} L${x(0)} ${y(0)} Z`;
  const last = pts[pts.length - 1];
  const midIdx = Math.floor((pts.length - 1) / 2);
  const gridVals = hi === lo ? [hi] : [hi, (hi + lo) / 2, lo];
  const labelX = pts.length === 1 ? W / 2 + 10 : W - PAD_R + 8;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" aria-label="Cumulative units by day">
      {gridVals.map((v) => (
        <g key={v}>
          <path d={`M${PAD_L} ${y(v)} H${W - PAD_R}`} stroke="var(--border-soft)" strokeWidth="1" />
          <text x={PAD_L - 8} y={y(v) + 4} fill="var(--text-faint)" fontSize="11" textAnchor="end">
            {fmtUnits(v)}
          </text>
        </g>
      ))}
      <path d={`M${PAD_L} ${y(0)} H${W - PAD_R}`} stroke="#4e3d28" strokeWidth="1" />
      <path d={area} fill="var(--accent)" opacity="0.1" />
      <path d={line} stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" fill="none" />
      {pts.map((p, i) => (
        <circle key={p.date} cx={x(i)} cy={y(p.cum)} r={i === pts.length - 1 ? 5 : 4} fill="var(--accent)" stroke="var(--card)" strokeWidth="2" />
      ))}
      <text x={labelX} y={y(last.cum) + 4} fill="var(--text)" fontSize="13" fontWeight="700" textAnchor="start">
        {fmtUnits(last.cum)}
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

function MiniRun({ results }: { results: DayResult[] }) {
  const W = 300;
  const H = 64;
  const pts = cumulative(results);
  if (pts.length < 2) return null;
  const lo = Math.min(0, ...pts.map((p) => p.cum));
  const hi = Math.max(0, ...pts.map((p) => p.cum));
  const span = Math.max(hi - lo, 1);
  const y = (v: number) => 6 + ((hi - v) / span) * (H - 12);
  const x = (i: number) => 6 + (i / (pts.length - 1)) * (W - 12);
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)} ${y(p.cum)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" aria-label="Last season cumulative units">
      <path d={`M6 ${y(0)} H${W - 6}`} stroke="var(--border)" strokeWidth="1" />
      <path d={line} stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" fill="none" />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1].cum)} r="4" fill="var(--accent)" stroke="var(--card)" strokeWidth="2" />
    </svg>
  );
}

/* ── Shot chart: one ball per graded pick ─────────────────────────── */

const SHOT_CHART_DAYS = 20;

function ShotChart({ results }: { results: DayResult[] }) {
  const allDays = results.filter((r) => r.total_picks > 0 || (r.voided ?? 0) > 0);
  const days = allDays.slice(-SHOT_CHART_DAYS);
  const truncated = allDays.length > days.length;
  if (days.length === 0) return null;
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] p-7 flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <div className="text-[15px] font-bold">Shot chart</div>
        <div className="text-xs text-[var(--text-faint)]">
          one ball per graded pick, by day
          {truncated ? ` · last ${SHOT_CHART_DAYS} graded days` : ""}
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
          <span className="inline-flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full bg-[var(--border)] inline-block" />
            Void
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
            {Array.from({ length: d.voided ?? 0 }).map((_, i) => (
              <div key={`v${i}`} className="w-[18px] h-[18px] rounded-full bg-[var(--border)]" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── The Receipts (this season) ───────────────────────────────────── */

function Receipts({ summary, results }: { summary: ResultsSummary; results: DayResult[] }) {
  const { totalPicks, hits, misses, voided, rate, allUnits, lastGraded } = summary;

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
              <span>
                {totalPicks} decided · {voided} voided
              </span>
            </div>
          </div>
          <div className="border-t border-[var(--border)] pt-4 flex gap-6">
            <div className="flex flex-col gap-0.5">
              <div className="text-xl font-bold" style={{ color: unitsColor(allUnits) }}>
                {fmtUnits(allUnits)}
              </div>
              <div className="text-[11px] tracking-wider text-[var(--text-faint)]">SEASON</div>
            </div>
            {lastGraded && (
              <div className="flex flex-col gap-0.5">
                <div className="text-xl font-bold" style={{ color: unitsColor(lastGraded.units ?? 0) }}>
                  {fmtUnits(lastGraded.units ?? 0)}
                </div>
                <div className="text-[11px] tracking-wider text-[var(--text-faint)]">
                  {fmtDate(lastGraded.date)}
                </div>
              </div>
            )}
            <div className="flex flex-col gap-0.5">
              <div className="text-xl font-bold">{voided}</div>
              <div className="text-[11px] tracking-wider text-[var(--text-faint)]">VOIDED</div>
            </div>
          </div>
        </div>
        <div className="bg-[var(--card)] border border-[var(--border)] p-7 flex flex-col gap-3">
          <div className="flex items-baseline gap-3">
            <div className="text-[15px] font-bold">The run</div>
            <div className="text-xs text-[var(--text-faint)]">cumulative units, flat 1u per pick</div>
          </div>
          <UnitsChart results={results} />
        </div>
      </div>
      <ShotChart results={results} />
    </div>
  );
}

/* ── Last season — compact side panel, never center stage ─────────── */

function LastSeasonPanel({ season, summary, results }: { season: string; summary: ResultsSummary; results: DayResult[] }) {
  return (
    <aside className="bg-[var(--card)] border border-[var(--border)] p-6 flex flex-col gap-4 opacity-80 self-start">
      <div className="text-[11px] font-bold tracking-widest text-[var(--text-muted)]">
        LAST SEASON · {season}
      </div>
      <div className="flex items-baseline gap-3">
        <div className="text-5xl font-bold leading-none">{summary.rate}%</div>
        <div className="text-xs text-[var(--text-faint)]">
          {summary.hits}–{summary.misses} · {summary.totalPicks} decided
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="flex flex-col gap-0.5">
          <div className="text-lg font-bold" style={{ color: unitsColor(summary.allUnits) }}>
            {fmtUnits(summary.allUnits)}
          </div>
          <div className="text-[10px] tracking-wider text-[var(--text-faint)]">UNITS</div>
        </div>
        <div className="flex flex-col gap-0.5">
          <div className="text-lg font-bold">{summary.gradedDays}</div>
          <div className="text-[10px] tracking-wider text-[var(--text-faint)]">DAYS GRADED</div>
        </div>
        <div className="flex flex-col gap-0.5">
          <div className="text-lg font-bold">{summary.voided}</div>
          <div className="text-[10px] tracking-wider text-[var(--text-faint)]">VOIDED</div>
        </div>
      </div>
      <MiniRun results={results} />
      <a
        href={DASHBOARD_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="text-[11px] font-bold tracking-widest text-[var(--accent)]"
      >
        FULL HISTORY ON TABLEAU →
      </a>
    </aside>
  );
}

/* ── The System ───────────────────────────────────────────────────── */

const SYSTEM_STEPS = [
  { n: "01", title: "SCAN MATCHUPS", body: "Defense vs. position, every game tonight." },
  { n: "02", title: "FIND STARTERS", body: "Depth charts + injury report, live." },
  { n: "03", title: "MAP ZONES", body: "Shot zones and playtypes vs. the defense." },
  { n: "04", title: "SCORE IT", body: "80% of conditions must pass. No exceptions." },
  { n: "05", title: "VERIFY HITS", body: "Season hit rate confirms it. __HR__% seals an A+." },
];

/* ── Grade ranking for deduplication ──────────────────────────────── */

const GRADE_RANK: Record<string, number> = {
  "A+": 6, A: 5, "B+": 4, B: 3, C: 2, D: 1, F: 0,
};

function deduplicatePicks(picks: PropPick[]): PropPick[] {
  const best = new Map<string, PropPick>();
  for (const p of picks) {
    const key = `${p.player_name}::${p.stat}::${p.edge.toLowerCase()}`;
    const existing = best.get(key);
    if (!existing || (GRADE_RANK[p.grade] ?? 0) > (GRADE_RANK[existing.grade] ?? 0)) {
      best.set(key, p);
    }
  }
  return Array.from(best.values());
}

/* ── Main page ────────────────────────────────────────────────────── */

type BoardState = "live" | "stale" | "last-season";

export default function Home() {
  const [data, setData] = useState<PropsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [today, setToday] = useState<string>("");

  useEffect(() => {
    setToday(localISODate(new Date()));
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

  if (loading || !today) {
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

  // ── Season framing ─────────────────────────────────────────────────
  const currentSeason = seasonForDate(today);
  const lastSeason = previousSeason(currentSeason);
  const boardSeason = data.season ?? seasonForDate(data.date);
  const boardState: BoardState =
    boardSeason !== currentSeason ? "last-season" : data.date === today ? "live" : "stale";

  const allPicks = deduplicatePicks([...data.valid_picks, ...(data.valid_combos ?? [])]);
  const boardPicks = boardState === "last-season" ? [] : allPicks;
  const archivedPicks = boardState === "last-season" ? allPicks : [];

  const results = data.model_results ?? [];
  const seasonOf = (r: DayResult) => r.season ?? seasonForDate(r.date);
  const thisSeasonResults = results.filter((r) => seasonOf(r) === currentSeason);
  const lastSeasonResults = results.filter((r) => seasonOf(r) === lastSeason);
  const summary = aggregateResults(thisSeasonResults);
  const lastSummary = aggregateResults(lastSeasonResults);
  const hasThisSeason = summary.totalPicks > 0 || summary.voided > 0;
  const hasLastSeason = lastSeasonResults.length > 0;
  const hrThreshold = data.thresholds?.hitrate ?? DEFAULT_HITRATE_THRESHOLD;
  const runTime = fmtRunTime(data.run_at_iso) ?? data.run_at;

  const heroBadge =
    boardState === "live"
      ? { text: "LIVE", bg: "var(--over)", ink: "var(--over-ink)" }
      : boardState === "stale"
        ? { text: `LAST RUN · ${fmtDayDate(data.date)}`, bg: "var(--under)", ink: "var(--under-ink)" }
        : { text: `SEASON ${currentSeason}`, bg: "var(--accent)", ink: "var(--bg)" };

  const kpis = [
    {
      label: "PLAYS",
      value: boardState === "last-season" ? "—" : String(boardPicks.length),
      sub: boardState === "last-season" ? "no board yet" : "graded A or better",
      color: "var(--text)",
    },
    {
      label: "HIT RATE",
      value: summary.totalPicks > 0 ? `${summary.rate}%` : "—",
      sub: hasThisSeason ? "this season" : "no grades yet",
      color: "var(--text)",
    },
    {
      label: "UNITS",
      value: hasThisSeason ? fmtUnits(summary.allUnits) : "—",
      sub: "this season, flat 1u",
      color: hasThisSeason ? unitsColor(summary.allUnits) : "var(--text)",
    },
    {
      label: "DAYS",
      value: summary.gradedDays > 0 ? String(summary.gradedDays) : "—",
      sub: "graded this season",
      color: "var(--text)",
    },
  ];

  return (
    <main className="flex flex-col">
      {/* Nav */}
      <div className="flex items-center gap-4 px-6 lg:px-16 py-5 border-b border-[var(--border-soft)]">
        <BallLogo size={30} ink="var(--bg)" />
        <div className="font-display text-2xl tracking-widest hidden sm:block">PROP MODEL</div>
        <span className="font-display text-base tracking-widest bg-[var(--card)] border border-[var(--border)] px-3 py-1">
          {currentSeason}
        </span>
        <div className="ml-auto flex items-center gap-4 sm:gap-7">
          <a
            href={DASHBOARD_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[13px] font-semibold tracking-wider text-[var(--accent)]"
          >
            DASHBOARD
          </a>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[13px] font-semibold tracking-wider text-[var(--accent)]"
          >
            CODE
          </a>
        </div>
      </div>

      {/* Verification strip: board date, generation time, viewer date */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-6 lg:px-16 py-2 bg-[var(--bg-deep)] border-b border-[var(--border-soft)] font-mono text-[11px] tracking-wider text-[var(--text-faint)]">
        <span>
          BOARD <span className="text-[var(--text-muted)]">{fmtLongDate(data.date)}</span> · SEASON{" "}
          <span className="text-[var(--text-muted)]">{boardSeason}</span>
        </span>
        {data.stats_season && data.stats_season !== boardSeason && (
          <span title={data.stats_season_reason ?? ""}>
            STATS <span className="text-[var(--text-muted)]">{data.stats_season}</span> · EARLY SEASON
          </span>
        )}
        <span>
          GENERATED <span className="text-[var(--text-muted)]">{runTime}</span>
        </span>
        <span>
          VIEWED <span className="text-[var(--text-muted)]">{fmtLongDate(today)}</span>
        </span>
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
            <span
              className="text-xs font-bold tracking-[3px] px-3.5 py-1.5"
              style={{ background: heroBadge.bg, color: heroBadge.ink }}
            >
              {heroBadge.text}
            </span>
          </div>
          <h1
            className="font-display text-7xl sm:text-8xl lg:text-9xl leading-[0.9] tracking-wide text-[var(--text-bright)]"
            style={{ textShadow: "0 4px 0 rgba(23,16,9,0.35)" }}
          >
            {boardState === "last-season" ? "NEW SEASON." : "DAILY PICKS:"}
          </h1>
          <div className="text-lg sm:text-xl font-semibold" style={{ textShadow: "0 1px 0 rgba(23,16,9,0.4)" }}>
            {boardState === "live" && `${data.candidates_analyzed} props scanned. ${boardPicks.length} on the board.`}
            {boardState === "stale" &&
              `Board from ${fmtDayDate(data.date)}. Today's run posts in the morning (about 9 AM ET) on game days.`}
            {boardState === "last-season" &&
              `${currentSeason} picks post the morning of each game day. Last season's record is archived below.`}
          </div>
          <div className="flex items-center gap-4 mt-2">
            <a
              href="#board"
              className="font-display inline-flex items-center gap-2.5 bg-[var(--bg)] text-[var(--text)] text-xl tracking-widest px-7 py-3.5"
            >
              SEE THE BOARD
              <ArrowIcon size={18} />
            </a>
            <div className="hidden sm:block text-[13px] font-semibold tracking-wider opacity-85">
              FREE. EVERY GAME DAY.
            </div>
          </div>
        </div>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 px-6 lg:px-16 pt-10 pb-2">
        {kpis.map((t) => (
          <div
            key={t.label}
            className="bg-[var(--card)] border border-[var(--border)] border-t-[3px] border-t-[var(--accent)] px-6 py-5 flex flex-col gap-1.5"
          >
            <div className="text-xs font-bold tracking-[3px] text-[var(--text-muted)]">{t.label}</div>
            <div className="text-4xl font-bold leading-none" style={{ color: t.color }}>
              {t.value}
            </div>
            <div className="text-xs text-[var(--text-faint)]">{t.sub}</div>
          </div>
        ))}
      </div>

      {/* The Board */}
      <div id="board" className="flex flex-col gap-7 px-6 lg:px-16 pt-16 pb-6">
        <SectionHead
          title="THE BOARD"
          tag={
            boardState === "last-season"
              ? `SEASON ${currentSeason} · NOT STARTED`
              : `${fmtDayDate(data.date)} · ${currentSeason}`
          }
        />
        {boardState === "last-season" ? (
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_380px] gap-6">
            <EmptyState
              title="FIRST TIP-OFF PENDING."
              body={`The ${currentSeason} board fills in the morning of the first game day.`}
            />
            <ArchivedBoard picks={archivedPicks} date={data.date} season={boardSeason} />
          </div>
        ) : boardPicks.length === 0 ? (
          <EmptyState
            title="NO PLAYS TONIGHT."
            body="The model found no A-grade edges today. Passing is a position."
          />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {boardPicks.map((p) => (
              <PickCard key={`${p.player_name}-${p.stat}-${p.edge.toLowerCase()}`} pick={p} hrThreshold={hrThreshold} />
            ))}
          </div>
        )}
        {boardState !== "last-season" && <OnHoldPanel picks={deduplicatePicks(data.on_hold ?? [])} />}
      </div>

      {/* The Receipts */}
      <div className="flex flex-col gap-7 px-6 lg:px-16 pt-16 pb-6">
        <SectionHead title="THE RECEIPTS" tag={`SEASON ${currentSeason} · EVERY PICK. GRADED.`} />
        <div className={`grid grid-cols-1 gap-6 ${hasLastSeason ? "lg:grid-cols-[minmax(0,1fr)_340px]" : ""}`}>
          <div className="min-w-0">
            {hasThisSeason ? (
              <Receipts summary={summary} results={thisSeasonResults} />
            ) : (
              <EmptyState
                title="NO GRADES YET THIS SEASON."
                body="Picks are graded the morning after each game day. The first receipts land after opening night."
              />
            )}
          </div>
          {hasLastSeason && (
            <LastSeasonPanel season={lastSeason} summary={lastSummary} results={lastSeasonResults} />
          )}
        </div>
        <a
          href={DASHBOARD_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="self-start font-display inline-flex items-center gap-2.5 bg-[var(--card)] border border-[var(--border)] text-[var(--text)] text-lg tracking-widest px-6 py-3"
        >
          FULL DASHBOARD ON TABLEAU
          <ArrowIcon size={16} />
        </a>
      </div>

      {/* The System */}
      <div className="flex flex-col gap-7 px-6 lg:px-16 pt-16 pb-10">
        <SectionHead title="THE SYSTEM" tag="NO GUESSWORK" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-5">
          {SYSTEM_STEPS.map((s) => (
            <div key={s.n} className="bg-[var(--card)] border border-[var(--border)] p-6 flex flex-col gap-3">
              <div className="font-display text-4xl text-[var(--wood)] leading-none">{s.n}</div>
              <div className="font-display text-2xl tracking-wide leading-none">{s.title}</div>
              <div className="text-[13px] leading-relaxed text-[var(--text-muted)]">
                {s.body.replace("__HR__", String(Math.round(hrThreshold * 100)))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center gap-4 px-6 lg:px-16 py-7 border-t border-[var(--border-soft)] bg-[var(--bg-deep)]">
        <BallLogo size={20} ink="var(--bg-deep)" />
        <div className="text-xs text-[var(--text-faint)]">
          Runs every game day · Lines via Underdog Fantasy · Not financial advice
        </div>
        <a
          href={GITHUB_URL}
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
