import WaveSurfer from "wavesurfer.js";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

export interface WaveformPlayerHandle {
  seekAndPlay: (seconds: number) => void;
}

interface WaveformPlayerProps {
  url: string;
}

export const WaveformPlayer = forwardRef<WaveformPlayerHandle, WaveformPlayerProps>(
function WaveformPlayer({ url }, ref) {
  const containerRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const pendingSeekRef = useRef<number | null>(null);
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
      waveColor: "#21409A",
      progressColor: "#BE1E2D",
      cursorColor: "#BE1E2D",
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
      if (pendingSeekRef.current !== null) {
        ws.setTime(pendingSeekRef.current);
        ws.play();
        pendingSeekRef.current = null;
      }
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

  useImperativeHandle(ref, () => ({
    seekAndPlay(seconds: number) {
      const ws = wsRef.current;
      if (!ws) return;
      if (!ready) {
        pendingSeekRef.current = seconds;
        return;
      }
      ws.setTime(seconds);
      ws.play();
    },
  }), [ready]);

  const toggle = () => wsRef.current?.playPause();

  const fmt = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  if (error) {
    return (
      <div className="rounded-sm bg-warm-100 border border-warm-200 p-4 text-sm text-warm-400">
        Audio file not available
      </div>
    );
  }

  return (
    <div className="rounded-sm bg-warm-50 border border-warm-200 p-4 space-y-3">
      <div ref={containerRef} className={ready ? "" : "opacity-0 h-16"} />
      {!ready && (
        <div className="h-16 flex items-center justify-center">
          <div className="text-warm-400 text-sm animate-pulse">Loading…</div>
        </div>
      )}
      <div className="flex items-center gap-3">
        <button
          onClick={toggle}
          disabled={!ready}
          className="flex items-center justify-center w-9 h-9 rounded-full bg-accent hover:bg-accent-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {playing ? (
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4 text-white">
              <rect x="3" y="2" width="4" height="12" rx="1" />
              <rect x="9" y="2" width="4" height="12" rx="1" />
            </svg>
          ) : (
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4 text-white ml-0.5">
              <path d="M4 2.5l10 5.5-10 5.5V2.5z" />
            </svg>
          )}
        </button>
        <span className="text-xs text-warm-600 tabular-nums">
          {fmt(currentTime)} / {fmt(duration)}
        </span>
      </div>
    </div>
  );
});
