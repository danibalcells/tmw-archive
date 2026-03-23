import WaveSurfer from "wavesurfer.js";
import { useEffect, useRef, useState } from "react";

interface WaveformPlayerProps {
  url: string;
}

export function WaveformPlayer({ url }: WaveformPlayerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    if (!containerRef.current) return;
    let active = true;

    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor: "#4ade80",
      progressColor: "#16a34a",
      cursorColor: "#bbf7d0",
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      height: 64,
      normalize: true,
    });

    wsRef.current = ws;

    ws.on("ready", () => {
      if (!active) return;
      setReady(true);
      setDuration(ws.getDuration());
    });
    ws.on("play", () => active && setPlaying(true));
    ws.on("pause", () => active && setPlaying(false));
    ws.on("finish", () => active && setPlaying(false));
    ws.on("timeupdate", (t) => active && setCurrentTime(t));
    ws.on("error", (err) => {
      if (!active) return;
      console.error("WaveSurfer error:", err);
      setError(true);
    });

    const loadTimer = setTimeout(() => ws.load(url), 0);

    return () => {
      active = false;
      clearTimeout(loadTimer);
      ws.destroy();
      wsRef.current = null;
      setReady(false);
      setPlaying(false);
      setError(false);
      setCurrentTime(0);
      setDuration(0);
    };
  }, [url]);

  const toggle = () => wsRef.current?.playPause();

  const fmt = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  if (error) {
    return (
      <div className="rounded-lg bg-zinc-800 p-4 text-sm text-zinc-500">
        Audio file not available
      </div>
    );
  }

  return (
    <div className="rounded-lg bg-zinc-800 p-4 space-y-3">
      <div ref={containerRef} className={ready ? "" : "opacity-0 h-16"} />
      {!ready && (
        <div className="h-16 flex items-center justify-center">
          <div className="text-zinc-500 text-sm animate-pulse">Loading…</div>
        </div>
      )}
      <div className="flex items-center gap-3">
        <button
          onClick={toggle}
          disabled={!ready}
          className="flex items-center justify-center w-9 h-9 rounded-full bg-green-500 hover:bg-green-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {playing ? (
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4 text-black">
              <rect x="3" y="2" width="4" height="12" rx="1" />
              <rect x="9" y="2" width="4" height="12" rx="1" />
            </svg>
          ) : (
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4 text-black ml-0.5">
              <path d="M4 2.5l10 5.5-10 5.5V2.5z" />
            </svg>
          )}
        </button>
        <span className="text-xs text-zinc-400 tabular-nums">
          {fmt(currentTime)} / {fmt(duration)}
        </span>
      </div>
    </div>
  );
}
