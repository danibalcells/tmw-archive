import { useState } from "react";

export interface Passage {
  passage_id: number;
  passage_type: number;
  recording_id: number;
  segment_ids: number[];
  start_seconds: number;
  end_seconds: number;
  duration: number;
  segment_count: number;
  mean_rms: number | null;
  mean_spectral_centroid: number | null;
  recording_title: string | null;
  audio_path: string | null;
  session_date: string | null;
  song_title: string | null;
  effective_type: string | null;
}

interface PassageTimelineProps {
  passages: Passage[];
  totalDuration: number;
  colorScale: (typeId: number) => string;
  activePassageId: number | null;
  onPassageClick: (passage: Passage) => void;
}

function formatTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export function PassageTimeline({
  passages,
  totalDuration,
  colorScale,
  activePassageId,
  onPassageClick,
}: PassageTimelineProps) {
  const [hoveredId, setHoveredId] = useState<number | null>(null);

  if (!passages.length || totalDuration <= 0) return null;

  return (
    <div className="relative">
      <div className="flex h-8 rounded-sm overflow-hidden border border-warm-200">
        {passages.map((p) => {
          const widthPct = (p.duration / totalDuration) * 100;
          const isActive = p.passage_id === activePassageId;
          const isHovered = p.passage_id === hoveredId;

          return (
            <div
              key={p.passage_id}
              className="relative cursor-pointer transition-opacity"
              style={{
                width: `${widthPct}%`,
                backgroundColor: colorScale(p.passage_type),
                opacity: isActive || isHovered ? 1 : 0.75,
              }}
              onClick={() => onPassageClick(p)}
              onMouseEnter={() => setHoveredId(p.passage_id)}
              onMouseLeave={() => setHoveredId(null)}
            >
              {isActive && (
                <div className="absolute inset-0 ring-2 ring-warm-900 ring-inset rounded-[1px]" />
              )}
              {widthPct > 6 && (
                <span className="absolute inset-0 flex items-center justify-center text-[10px] font-medium text-white/90 mix-blend-difference select-none">
                  {p.passage_type}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {hoveredId !== null && (() => {
        const p = passages.find((x) => x.passage_id === hoveredId);
        if (!p) return null;
        const leftPct = (p.start_seconds / totalDuration) * 100;
        const clampedLeft = Math.min(Math.max(leftPct, 5), 85);
        return (
          <div
            className="absolute top-full mt-1 z-10 px-2 py-1 bg-warm-900 text-cream text-xs rounded shadow-md whitespace-nowrap pointer-events-none"
            style={{ left: `${clampedLeft}%`, transform: "translateX(-50%)" }}
          >
            Type {p.passage_type} · {formatTime(p.start_seconds)}–{formatTime(p.end_seconds)} · {p.segment_count} seg
          </div>
        );
      })()}
    </div>
  );
}
