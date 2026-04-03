import { useCallback, useEffect, useRef, useState } from "react";
import {
  advanceERSession,
  audioUrl,
  fetchPassageRuns,
  formatDuration,
  startERSession,
  type ERCandidate,
  type ERSelection,
  type PassageRun,
} from "../lib/api";

const STATE_PALETTE = [
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  "#5778a4", "#e49444", "#d1615d", "#85b6b2", "#6a9f58",
  "#e7ca60", "#a87c9f", "#f6a4a9", "#b0926e", "#c8c1b9",
  "#8cd17d", "#b6992d", "#f1ce63", "#a0cbe8", "#ffbe7d",
  "#d4a6c8", "#86bcb6", "#d37295", "#fabfd2", "#b9ac8e",
];

function stateColor(typeId: number): string {
  return STATE_PALETTE[typeId % STATE_PALETTE.length];
}

function StateChip({ state, active }: { state: number; active?: boolean }) {
  const color = stateColor(state);
  return (
    <span
      title={`State ${state}`}
      style={{
        backgroundColor: active ? color : color + "99",
        borderColor: color,
        transform: active ? "scale(1.15)" : undefined,
      }}
      className={`inline-flex items-center justify-center w-7 h-7 rounded-sm text-xs font-mono font-bold text-white border flex-shrink-0 transition-transform ${active ? "shadow-md" : "opacity-80"}`}
    >
      {state}
    </span>
  );
}

function PassageAudioPlayer({
  selection,
  autoAdvance,
  onEnded,
}: {
  selection: ERSelection;
  autoAdvance: boolean;
  onEnded: () => void;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [elapsed, setElapsed] = useState(0);
  const passage = selection.passage;
  const duration = passage.duration;

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    setElapsed(0);

    const onLoaded = () => {
      audio.currentTime = passage.start_seconds;
      audio.play().catch(() => {});
    };

    const onTime = () => {
      const e = audio.currentTime - passage.start_seconds;
      setElapsed(Math.max(0, e));
      if (audio.currentTime >= passage.end_seconds - 0.3) {
        audio.pause();
        if (autoAdvance) onEnded();
      }
    };

    audio.src = audioUrl(passage.recording_id);
    audio.load();
    audio.addEventListener("loadedmetadata", onLoaded);
    audio.addEventListener("timeupdate", onTime);
    return () => {
      audio.removeEventListener("loadedmetadata", onLoaded);
      audio.removeEventListener("timeupdate", onTime);
      audio.pause();
    };
  }, [selection.session_id, selection.step]);

  const progress = duration > 0 ? Math.min(elapsed / duration, 1) : 0;

  const toggle = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      if (audio.currentTime < passage.start_seconds || audio.currentTime >= passage.end_seconds) {
        audio.currentTime = passage.start_seconds;
      }
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  };

  return (
    <div className="space-y-2">
      <audio ref={audioRef} />
      <div
        className="h-1.5 bg-warm-200 rounded-full cursor-pointer overflow-hidden"
        onClick={(e) => {
          const audio = audioRef.current;
          if (!audio) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const frac = (e.clientX - rect.left) / rect.width;
          audio.currentTime = passage.start_seconds + frac * duration;
        }}
      >
        <div
          className="h-full rounded-full bg-accent transition-all"
          style={{ width: `${progress * 100}%` }}
        />
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={toggle}
          className="text-xs font-mono px-3 py-1 border border-warm-300 rounded-sm hover:bg-warm-100 transition-colors"
        >
          ▶/‖
        </button>
        <span className="text-xs font-mono text-warm-500">
          {formatDuration(elapsed)} / {formatDuration(duration)}
        </span>
      </div>
    </div>
  );
}

function CandidateRow({ c, rank }: { c: ERCandidate; rank: number }) {
  const p = c.passage;
  return (
    <div className="flex items-center gap-3 py-1.5 border-b border-warm-100 last:border-0">
      <span className="text-xs font-mono text-warm-400 w-4 text-right">{rank}</span>
      <StateChip state={p.passage_type} />
      <div className="flex-1 min-w-0">
        <div className="text-xs font-mono truncate text-warm-800">{p.recording_title || "—"}</div>
        <div className="text-xs font-mono text-warm-400">
          {p.session_date} · {formatDuration(p.duration)}
        </div>
      </div>
      <div className="text-right">
        <div
          className="text-xs font-mono font-bold"
          style={{ color: stateColor(p.passage_type) }}
        >
          {(c.score * 100).toFixed(0)}
        </div>
        <div className="text-xs font-mono text-warm-300">{(c.boundary_similarity * 100).toFixed(0)}b</div>
      </div>
    </div>
  );
}

export function EndlessRehearsal() {
  const [runs, setRuns] = useState<PassageRun[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [selectedPersonality, setSelectedPersonality] = useState("explorer");
  const [autoAdvance, setAutoAdvance] = useState(true);
  const [selection, setSelection] = useState<ERSelection | null>(null);
  const [history, setHistory] = useState<ERSelection[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pathEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchPassageRuns().then((r) => {
      setRuns(r);
      if (r.length > 0) setSelectedRun(r[r.length - 1].name);
    });
  }, []);

  useEffect(() => {
    pathEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "end" });
  }, [history.length]);

  const start = useCallback(async () => {
    if (!selectedRun) return;
    setError(null);
    setLoading(true);
    try {
      const sel = await startERSession(selectedRun, selectedPersonality, true);
      setSelection(sel);
      setHistory([sel]);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [selectedRun, selectedPersonality]);

  const advance = useCallback(async () => {
    if (!selection) return;
    setLoading(true);
    try {
      const sel = await advanceERSession(selection.session_id);
      setSelection(sel);
      setHistory((h) => [...h, sel]);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [selection]);

  const passage = selection?.passage ?? null;

  return (
    <div className="h-full flex flex-col overflow-hidden font-mono">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-warm-200 px-6 py-3 flex items-center gap-4 flex-wrap">
        <span className="text-xs tracking-widest text-accent uppercase">Endless Rehearsal</span>

        <select
          value={selectedRun}
          onChange={(e) => setSelectedRun(e.target.value)}
          disabled={loading}
          className="text-xs border border-warm-200 bg-cream rounded-sm px-2 py-1 font-mono"
        >
          {runs.map((r) => (
            <option key={r.name} value={r.name}>
              {r.name}
            </option>
          ))}
        </select>

        <div className="flex gap-1">
          {["faithful", "explorer", "chill"].map((name) => (
            <button
              key={name}
              onClick={() => setSelectedPersonality(name)}
              disabled={loading}
              className={`text-xs px-3 py-1 rounded-sm border transition-colors ${
                selectedPersonality === name
                  ? "bg-warm-800 text-cream border-warm-800"
                  : "border-warm-300 text-warm-600 hover:bg-warm-100"
              }`}
            >
              {name}
            </button>
          ))}
        </div>

        <button
          onClick={start}
          disabled={loading || !selectedRun}
          className="text-xs px-4 py-1 border border-warm-400 rounded-sm hover:bg-warm-100 transition-colors disabled:opacity-40"
        >
          {selection ? "Restart" : "Start"}
        </button>

        <label className="flex items-center gap-1.5 text-xs text-warm-500 cursor-pointer ml-auto">
          <input
            type="checkbox"
            checked={autoAdvance}
            onChange={(e) => setAutoAdvance(e.target.checked)}
            className="accent-accent"
          />
          Auto-advance
        </label>
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-hidden flex">
        {/* Left: now playing + path */}
        <div className="flex-1 flex flex-col overflow-hidden border-r border-warm-200">
          {/* Now playing */}
          <div className="flex-shrink-0 p-6 border-b border-warm-100">
            {passage ? (
              <div className="space-y-4">
                <div className="flex items-start gap-3">
                  <StateChip state={passage.passage_type} active />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-bold text-warm-900 truncate">
                      {passage.recording_title || "—"}
                    </div>
                    <div className="text-xs text-warm-500 mt-0.5">
                      {passage.session_date}
                      {passage.song_title ? ` · ${passage.song_title}` : ""}
                      {" · "}
                      <span className="text-warm-400">{passage.effective_type}</span>
                    </div>
                    <div className="text-xs text-warm-400 mt-0.5">
                      state {passage.passage_type} · step {selection?.step}
                    </div>
                  </div>
                </div>

                <PassageAudioPlayer
                  selection={selection!}
                  autoAdvance={autoAdvance}
                  onEnded={advance}
                />

                <button
                  onClick={advance}
                  disabled={loading}
                  className="text-xs px-4 py-1.5 border border-warm-300 rounded-sm hover:bg-warm-100 transition-colors disabled:opacity-40"
                >
                  {loading ? "Loading…" : "→ Skip to next"}
                </button>
              </div>
            ) : (
              <div className="text-xs text-warm-400">
                {loading ? "Starting…" : "Select a run and press Start."}
              </div>
            )}

            {error && (
              <div className="mt-3 text-xs text-red-600 font-mono">{error}</div>
            )}
          </div>

          {/* Path strip */}
          {history.length > 0 && (
            <div className="flex-shrink-0 px-6 py-3 border-b border-warm-100">
              <div className="text-xs text-warm-400 mb-2 uppercase tracking-wider">Path</div>
              <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-thin">
                {history.map((sel, i) => (
                  <StateChip
                    key={`${sel.session_id}-${sel.step}`}
                    state={sel.passage.passage_type}
                    active={i === history.length - 1}
                  />
                ))}
                <div ref={pathEndRef} />
              </div>
            </div>
          )}

          {/* State distribution mini-bar */}
          {selection && (
            <div className="flex-shrink-0 px-6 py-3">
              <div className="text-xs text-warm-400 mb-2 uppercase tracking-wider">State probabilities</div>
              <div className="flex gap-px h-8 rounded-sm overflow-hidden">
                {selection.state_distribution.map((prob, i) => (
                  <div
                    key={i}
                    title={`State ${i}: ${(prob * 100).toFixed(1)}%`}
                    style={{ backgroundColor: stateColor(i), flex: prob }}
                    className="h-full"
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right: candidates */}
        <div className="w-80 flex-shrink-0 flex flex-col overflow-hidden">
          <div className="flex-shrink-0 px-4 pt-4 pb-2">
            <span className="text-xs text-warm-400 uppercase tracking-wider">Up next · candidates</span>
          </div>
          <div className="flex-1 overflow-y-auto px-4">
            {selection?.candidates.map((c, i) => (
              <CandidateRow key={c.passage.passage_id} c={c} rank={i + 1} />
            ))}
            {!selection && (
              <div className="text-xs text-warm-300 pt-2">Candidates appear here during playback.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
