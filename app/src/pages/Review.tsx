import WaveSurfer from "wavesurfer.js";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Candidate, ReviewRecording, ReviewStats, Song } from "../lib/api";
import {
  acceptCandidate,
  assignSong,
  classifyRecording,
  createSong,
  fetchAllSongs,
  fetchReviewQueue,
  fetchReviewStats,
  formatDuration,
  rejectCandidate,
  revertRecording,
  splitRecording,
} from "../lib/api";

const CONTENT_TYPES = [
  { key: "jam", label: "Jam", shortcut: "j" },
  { key: "banter", label: "Banter", shortcut: "b" },
  { key: "tuning", label: "Tuning", shortcut: "t" },
  { key: "noodling", label: "Noodling", shortcut: "n" },
  { key: "silence", label: "Silence", shortcut: "x" },
  { key: "count_in", label: "Count-in", shortcut: "c" },
  { key: "other", label: "Other", shortcut: "o" },
] as const;

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.7
      ? "bg-green-500"
      : value >= 0.4
      ? "bg-yellow-500"
      : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-zinc-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-zinc-400 w-8 text-right">
        {pct}%
      </span>
    </div>
  );
}

interface WaveformRef {
  toggle: () => void;
  pause: () => void;
}

interface InlineWaveformProps {
  url: string;
  label?: string;
  waveRef?: React.MutableRefObject<WaveformRef | null>;
  waveColor?: string;
  progressColor?: string;
  onTimeUpdate?: (t: number) => void;
}

function InlineWaveform({
  url,
  label,
  waveRef,
  waveColor = "#4ade80",
  progressColor = "#16a34a",
  onTimeUpdate,
}: InlineWaveformProps) {
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
    setReady(false);
    setError(false);
    setPlaying(false);
    setCurrentTime(0);
    setDuration(0);

    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor,
      progressColor,
      cursorColor: "#bbf7d0",
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      height: 56,
      normalize: true,
    });

    wsRef.current = ws;
    if (waveRef) waveRef.current = { toggle: () => ws.playPause(), pause: () => ws.pause() };

    ws.on("ready", () => {
      if (!active) return;
      setReady(true);
      setDuration(ws.getDuration());
    });
    ws.on("play", () => active && setPlaying(true));
    ws.on("pause", () => active && setPlaying(false));
    ws.on("finish", () => active && setPlaying(false));
    ws.on("timeupdate", (t) => {
      if (!active) return;
      setCurrentTime(t);
      onTimeUpdate?.(t);
    });
    ws.on("error", () => active && setError(true));

    const t = setTimeout(() => ws.load(url), 0);

    return () => {
      active = false;
      clearTimeout(t);
      ws.destroy();
      wsRef.current = null;
      if (waveRef) waveRef.current = null;
    };
  }, [url, waveColor, progressColor]);

  const fmt = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  if (error) {
    return (
      <div className="rounded-lg bg-zinc-800/60 p-3 text-sm text-zinc-500">
        Audio unavailable
      </div>
    );
  }

  return (
    <div className="rounded-lg bg-zinc-800/60 p-3 space-y-2">
      {label && <div className="text-xs text-zinc-500 font-medium">{label}</div>}
      <div ref={containerRef} className={ready ? "" : "opacity-0 h-14"} />
      {!ready && (
        <div className="h-14 flex items-center justify-center">
          <span className="text-zinc-600 text-xs animate-pulse">Loading…</span>
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          onClick={() => wsRef.current?.playPause()}
          disabled={!ready}
          className="flex items-center justify-center w-7 h-7 rounded-full bg-green-500 hover:bg-green-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex-shrink-0"
        >
          {playing ? (
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3 text-black">
              <rect x="3" y="2" width="4" height="12" rx="1" />
              <rect x="9" y="2" width="4" height="12" rx="1" />
            </svg>
          ) : (
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3 text-black ml-0.5">
              <path d="M4 2.5l10 5.5-10 5.5V2.5z" />
            </svg>
          )}
        </button>
        <span className="text-xs text-zinc-500 tabular-nums">
          {fmt(currentTime)} / {fmt(duration)}
        </span>
      </div>
    </div>
  );
}

interface SongPickerProps {
  songs: Song[];
  onSelect: (song: Song) => void;
  onCreate: (title: string) => void;
  onClose: () => void;
}

function SongPicker({ songs, onSelect, onCreate, onClose }: SongPickerProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const trimmed = query.trim();
  const filtered = trimmed
    ? songs.filter((s) => s.title.toLowerCase().includes(trimmed.toLowerCase()))
    : songs;
  const exactMatch = songs.some(
    (s) => s.title.toLowerCase() === trimmed.toLowerCase()
  );
  const showCreate = trimmed.length > 0 && !exactMatch;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-full max-w-md flex flex-col overflow-hidden">
        <div className="px-4 pt-4 pb-3 border-b border-zinc-800">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search or type a new song name…"
            className="w-full bg-zinc-800 text-zinc-100 placeholder-zinc-500 rounded-lg px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-green-500"
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "Enter" && showCreate) onCreate(trimmed);
            }}
          />
        </div>
        <div className="overflow-y-auto max-h-72">
          {filtered.map((song) => (
            <button
              key={song.id}
              onClick={() => onSelect(song)}
              className="w-full text-left px-4 py-2.5 text-sm hover:bg-zinc-800 transition-colors flex items-center gap-2"
            >
              <span className="text-zinc-100">{song.title}</span>
              <span className="text-xs text-zinc-500 ml-auto">{song.song_type}</span>
            </button>
          ))}
          {filtered.length === 0 && !showCreate && (
            <div className="px-4 py-6 text-sm text-zinc-500 text-center">No songs found</div>
          )}
          {showCreate && (
            <button
              onClick={() => onCreate(trimmed)}
              className="w-full text-left px-4 py-2.5 text-sm hover:bg-zinc-800 transition-colors flex items-center gap-2 border-t border-zinc-800"
            >
              <span className="text-green-400">+ Create</span>
              <span className="text-zinc-100 ml-1">"{trimmed}"</span>
              <span className="text-xs text-zinc-500 ml-auto">original</span>
            </button>
          )}
        </div>
        <div className="px-4 py-2.5 border-t border-zinc-800 text-xs text-zinc-600 flex justify-between">
          <span>Click to assign · Enter to create new</span>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 transition-colors">
            Esc to close
          </button>
        </div>
      </div>
    </div>
  );
}

interface CandidatePanelProps {
  candidates: Candidate[];
  activeRefId: number | null;
  onAccept: (c: Candidate) => void;
  onPlayRef: (c: Candidate) => void;
}

function CandidatePanel({ candidates, activeRefId, onAccept, onPlayRef }: CandidatePanelProps) {
  const pending = candidates.filter((c) => c.status === "pending");
  if (pending.length === 0) return null;

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-zinc-400 uppercase tracking-wide">
        Song Match Candidates
      </div>
      {pending.map((c) => (
        <div key={c.id} className="rounded-lg bg-zinc-800/80 p-3 space-y-2">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-500 font-mono">#{c.rank}</span>
                <span className="text-sm font-medium text-zinc-100 truncate">{c.song_title}</span>
              </div>
              {c.nearest_recording_session_date && (
                <div className="text-xs text-zinc-500 mt-0.5">
                  ref: {c.nearest_recording_session_date}
                </div>
              )}
            </div>
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <button
                onClick={() => onPlayRef(c)}
                className={`px-2 py-1 rounded text-xs transition-colors ${
                  activeRefId === c.id
                    ? "bg-green-600 text-white"
                    : "bg-zinc-700 hover:bg-zinc-600 text-zinc-300"
                }`}
                title={`r — play ref for #${c.rank}`}
              >
                ▶ Ref
              </button>
              <button
                onClick={() => onAccept(c)}
                className="px-2 py-1 rounded text-xs bg-green-600 hover:bg-green-500 text-white transition-colors"
                title={`${c.rank} — accept candidate #${c.rank}`}
              >
                ✓ Accept
              </button>
            </div>
          </div>
          <ConfidenceBar value={c.confidence} />
        </div>
      ))}
    </div>
  );
}

function JamNamer({
  onConfirm,
  onClose,
}: {
  onConfirm: (name: string) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-full max-w-sm p-5 space-y-4">
        <div className="text-sm font-medium text-zinc-200">Name this jam</div>
        <input
          ref={inputRef}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Leave blank to classify without naming…"
          className="w-full bg-zinc-800 text-zinc-100 placeholder-zinc-500 rounded-lg px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-green-500"
          onKeyDown={(e) => {
            if (e.key === "Enter") onConfirm(name);
            if (e.key === "Escape") onClose();
          }}
        />
        <div className="flex gap-2 justify-end">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg text-sm text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm(name)}
            className="px-3 py-1.5 rounded-lg text-sm bg-zinc-700 hover:bg-zinc-600 text-zinc-100 transition-colors"
          >
            {name.trim() ? "Create & assign" : "Classify as jam"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function Review() {
  const [queue, setQueue] = useState<ReviewRecording[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [sort, setSort] = useState("confidence");
  const [filter, setFilter] = useState("unreviewed");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [songs, setSongs] = useState<Song[]>([]);
  const [showSongPicker, setShowSongPicker] = useState(false);
  const [showJamNamer, setShowJamNamer] = useState(false);
  const [undoStack, setUndoStack] = useState<Array<{ index: number; recordingId: number }>>([]);
  const [activeRefCandidateId, setActiveRefCandidateId] = useState<number | null>(null);
  const [refUrl, setRefUrl] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const [mainCurrentTime, setMainCurrentTime] = useState(0);

  const mainWaveRef = useRef<WaveformRef | null>(null);
  const refWaveRef = useRef<WaveformRef | null>(null);

  const current = queue[currentIndex] ?? null;

  const refreshStats = useCallback(async () => {
    try {
      const s = await fetchReviewStats();
      setStats(s);
    } catch {
      // noop
    }
  }, []);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [q, s] = await Promise.all([
        fetchReviewQueue({ status: filter, sort, limit: 50 }),
        fetchReviewStats(),
      ]);
      setQueue(q);
      setStats(s);
      setCurrentIndex(0);
      setActiveRefCandidateId(null);
      setRefUrl(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [filter, sort]);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  useEffect(() => {
    fetchAllSongs().then(setSongs);
  }, []);

  const goNext = useCallback(() => {
    setActiveRefCandidateId(null);
    setRefUrl(null);
    setMainCurrentTime(0);
    mainWaveRef.current?.pause();
    refWaveRef.current?.pause();
    setCurrentIndex((i) => Math.min(i + 1, queue.length - 1));
  }, [queue.length]);

  const goPrev = useCallback(() => {
    setActiveRefCandidateId(null);
    setRefUrl(null);
    setMainCurrentTime(0);
    mainWaveRef.current?.pause();
    refWaveRef.current?.pause();
    setCurrentIndex((i) => Math.max(i - 1, 0));
  }, []);

  const handleAccept = useCallback(
    async (candidate: Candidate) => {
      if (!current || actionPending) return;
      setActionPending(true);
      try {
        const updated = await acceptCandidate(candidate.id);
        setQueue((q) => q.map((r) => (r.id === updated.id ? updated : r)));
        setUndoStack((s) => [...s, { index: currentIndex, recordingId: current.id }]);
        goNext();
        refreshStats();
      } catch {
        // noop
      } finally {
        setActionPending(false);
      }
    },
    [current, currentIndex, actionPending, goNext, refreshStats]
  );

  const handleClassify = useCallback(
    async (content_type: string) => {
      if (!current || actionPending) return;
      setActionPending(true);
      try {
        await classifyRecording(current.id, content_type);
        setQueue((q) =>
          q.map((r) =>
            r.id === current.id ? { ...r, content_type, content_type_source: "human" } : r
          )
        );
        setUndoStack((s) => [...s, { index: currentIndex, recordingId: current.id }]);
        goNext();
        refreshStats();
      } catch {
        // noop
      } finally {
        setActionPending(false);
      }
    },
    [current, currentIndex, actionPending, goNext, refreshStats]
  );

  const handleAssignSong = useCallback(
    async (song: Song) => {
      if (!current || actionPending) return;
      setShowSongPicker(false);
      setActionPending(true);
      try {
        await assignSong(current.id, song.id);
        setQueue((q) =>
          q.map((r) =>
            r.id === current.id
              ? { ...r, song_id: song.id, song_title: song.title, content_type: "song_take" }
              : r
          )
        );
        setUndoStack((s) => [...s, { index: currentIndex, recordingId: current.id }]);
        goNext();
        refreshStats();
      } catch {
        // noop
      } finally {
        setActionPending(false);
      }
    },
    [current, currentIndex, actionPending, goNext, refreshStats]
  );

  const handleCreateAndAssign = useCallback(
    async (title: string) => {
      if (!current || actionPending) return;
      setShowSongPicker(false);
      setActionPending(true);
      try {
        const newSong = await createSong(title, "original");
        setSongs((prev) => [...prev, newSong]);
        await assignSong(current.id, newSong.id);
        setQueue((q) =>
          q.map((r) =>
            r.id === current.id
              ? { ...r, song_id: newSong.id, song_title: newSong.title, content_type: "song_take" }
              : r
          )
        );
        setUndoStack((s) => [...s, { index: currentIndex, recordingId: current.id }]);
        goNext();
        refreshStats();
      } catch {
        // noop
      } finally {
        setActionPending(false);
      }
    },
    [current, currentIndex, actionPending, goNext, refreshStats]
  );

  const handleJamConfirm = useCallback(
    async (name: string) => {
      if (!current || actionPending) return;
      setShowJamNamer(false);
      setActionPending(true);
      try {
        if (name.trim()) {
          const newSong = await createSong(name.trim(), "jam");
          setSongs((prev) => [...prev, newSong]);
          await assignSong(current.id, newSong.id);
          setQueue((q) =>
            q.map((r) =>
              r.id === current.id
                ? { ...r, song_id: newSong.id, song_title: newSong.title, content_type: "jam" }
                : r
            )
          );
        } else {
          await classifyRecording(current.id, "jam");
          setQueue((q) =>
            q.map((r) =>
              r.id === current.id ? { ...r, content_type: "jam", content_type_source: "human" } : r
            )
          );
        }
        setUndoStack((s) => [...s, { index: currentIndex, recordingId: current.id }]);
        goNext();
        refreshStats();
      } catch {
        // noop
      } finally {
        setActionPending(false);
      }
    },
    [current, currentIndex, actionPending, goNext, refreshStats]
  );

  const handleUndo = useCallback(async () => {
    if (undoStack.length === 0 || actionPending) return;
    const last = undoStack[undoStack.length - 1];
    setActionPending(true);
    try {
      const reverted = await revertRecording(last.recordingId);
      setQueue((q) => q.map((r) => (r.id === reverted.id ? reverted : r)));
      setUndoStack((s) => s.slice(0, -1));
      setCurrentIndex(last.index);
      mainWaveRef.current?.pause();
      refWaveRef.current?.pause();
      setActiveRefCandidateId(null);
      setRefUrl(null);
      refreshStats();
    } catch {
      // noop
    } finally {
      setActionPending(false);
    }
  }, [undoStack, actionPending, refreshStats]);

  const handlePlayRef = useCallback(
    (candidate: Candidate) => {
      if (!candidate.nearest_recording_audio_path) return;
      if (activeRefCandidateId === candidate.id) {
        refWaveRef.current?.toggle();
        return;
      }
      mainWaveRef.current?.pause();
      setActiveRefCandidateId(candidate.id);
      setRefUrl(`/api/audio-path/${encodeURIComponent(candidate.nearest_recording_audio_path)}`);
    },
    [activeRefCandidateId]
  );

  const handleReject = useCallback(
    async (candidate: Candidate) => {
      if (!current || actionPending) return;
      setActionPending(true);
      try {
        const remaining = await rejectCandidate(candidate.id);
        setQueue((q) =>
          q.map((r) => (r.id === current.id ? { ...r, candidates: remaining } : r))
        );
      } catch {
        // noop
      } finally {
        setActionPending(false);
      }
    },
    [current, actionPending]
  );

  const handleSplit = useCallback(async () => {
    if (!current || actionPending || mainCurrentTime <= 0) return;
    setActionPending(true);
    mainWaveRef.current?.pause();
    try {
      const result = await splitRecording(current.id, mainCurrentTime);
      setQueue((q) => {
        const before = q.slice(0, currentIndex);
        const after = q.slice(currentIndex + 1);
        return [...before, ...result.recordings, ...after];
      });
      setMainCurrentTime(0);
      setActiveRefCandidateId(null);
      setRefUrl(null);
    } catch {
      // noop
    } finally {
      setActionPending(false);
    }
  }, [current, currentIndex, actionPending, mainCurrentTime]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (showSongPicker || showJamNamer) return;
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      switch (e.key) {
        case " ":
          e.preventDefault();
          mainWaveRef.current?.toggle();
          break;
        case "r":
        case "R": {
          const top = current?.candidates.find((c) => c.status === "pending" && c.rank === 1);
          if (top) handlePlayRef(top);
          break;
        }
        case "1":
        case "2":
        case "3": {
          const rank = parseInt(e.key, 10);
          const c = current?.candidates.find(
            (x) => x.status === "pending" && x.rank === rank
          );
          if (c) handleAccept(c);
          break;
        }
        case "j":
        case "J":
          e.preventDefault();
          setShowJamNamer(true);
          break;
        case "b":
        case "B":
          handleClassify("banter");
          break;
        case "t":
        case "T":
          handleClassify("tuning");
          break;
        case "n":
        case "N":
          handleClassify("noodling");
          break;
        case "x":
        case "X":
          handleClassify("silence");
          break;
        case "c":
        case "C":
          handleClassify("count_in");
          break;
        case "o":
        case "O":
          handleClassify("other");
          break;
        case "s":
        case "S":
          e.preventDefault();
          setShowSongPicker(true);
          break;
        case "u":
        case "U":
          handleUndo();
          break;
        case "ArrowRight":
        case "]":
          goNext();
          break;
        case "ArrowLeft":
        case "[":
          goPrev();
          break;
        case "Escape":
          setShowSongPicker(false);
          break;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    showSongPicker,
    showJamNamer,
    current,
    handlePlayRef,
    handleAccept,
    handleClassify,
    handleUndo,
    goNext,
    goPrev,
  ]);

  const progressPct =
    stats && stats.total_recordings > 0
      ? Math.round((stats.classified / stats.total_recordings) * 100)
      : 0;

  const mainAudioUrl = current?.audio_path
    ? `/api/audio/${current.id}`
    : null;

  return (
    <div className="flex flex-col h-full overflow-hidden bg-zinc-950">
      {showSongPicker && (
        <SongPicker
          songs={songs}
          onSelect={handleAssignSong}
          onCreate={handleCreateAndAssign}
          onClose={() => setShowSongPicker(false)}
        />
      )}

      {showJamNamer && (
        <JamNamer
          onConfirm={handleJamConfirm}
          onClose={() => setShowJamNamer(false)}
        />
      )}

      <div className="flex-shrink-0 border-b border-zinc-800 px-6 py-4 space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h1 className="text-lg font-semibold">Review</h1>
          {stats && (
            <span className="text-sm text-zinc-400">
              {stats.classified} / {stats.total_recordings} classified ({progressPct}%)
            </span>
          )}
        </div>

        {stats && (
          <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-green-500 rounded-full transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        )}

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            Sort:
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 text-zinc-100 rounded px-2 py-1 text-sm outline-none focus:border-green-500"
            >
              <option value="confidence">Confidence</option>
              <option value="duration">Duration</option>
              <option value="date">Date</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            Filter:
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 text-zinc-100 rounded px-2 py-1 text-sm outline-none focus:border-green-500"
            >
              <option value="unreviewed">Unreviewed</option>
              <option value="auto">Auto-classified</option>
              <option value="all">All</option>
            </select>
          </label>
          <span className="ml-auto text-xs text-zinc-600">
            {currentIndex + 1} / {queue.length}
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
        {loading && (
          <div className="flex items-center justify-center h-40 text-zinc-500 text-sm animate-pulse">
            Loading queue…
          </div>
        )}

        {!loading && error && (
          <div className="rounded-lg bg-red-900/30 border border-red-800 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {!loading && !error && queue.length === 0 && (
          <div className="flex items-center justify-center h-40 text-zinc-500 text-sm">
            Nothing to review.
          </div>
        )}

        {!loading && current && (
          <>
            <div className="rounded-xl bg-zinc-900 border border-zinc-800 p-4 space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <div className="text-base font-semibold text-zinc-100">
                    {current.title ?? `Recording #${current.id}`}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-zinc-500">
                    <span>ID: {current.id}</span>
                    {current.session_date && <span>{current.session_date}</span>}
                    {current.duration_seconds !== null && (
                      <span>{formatDuration(current.duration_seconds)}</span>
                    )}
                    <span className="text-zinc-600">{current.origin}</span>
                  </div>
                  {current.content_type && (
                    <div className="flex items-center gap-1.5 mt-1">
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                          current.content_type === "song"
                            ? "bg-green-900/50 text-green-300"
                            : "bg-zinc-700 text-zinc-400"
                        }`}
                      >
                        {current.content_type}
                      </span>
                      {current.content_type_source === "auto" && (
                        <span className="text-xs text-yellow-500">auto-suggested</span>
                      )}
                      {current.song_title && (
                        <span className="text-xs text-zinc-400">→ {current.song_title}</span>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {mainAudioUrl ? (
                <InlineWaveform
                  url={mainAudioUrl}
                  label="Current recording"
                  waveRef={mainWaveRef}
                  onTimeUpdate={setMainCurrentTime}
                />
              ) : (
                <div className="rounded-lg bg-zinc-800/60 p-3 text-sm text-zinc-500">
                  No audio available
                </div>
              )}

              {refUrl && (
                <InlineWaveform
                  key={refUrl}
                  url={refUrl}
                  label="Reference recording"
                  waveRef={refWaveRef}
                  waveColor="#60a5fa"
                  progressColor="#2563eb"
                />
              )}
            </div>

            {current.candidates.some((c) => c.status === "pending") && (
              <CandidatePanel
                candidates={current.candidates}
                activeRefId={activeRefCandidateId}
                onAccept={handleAccept}
                onPlayRef={handlePlayRef}
              />
            )}

            <div className="space-y-2">
              <div className="text-xs font-medium text-zinc-400 uppercase tracking-wide">
                Classify as
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => setShowSongPicker(true)}
                  disabled={actionPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm bg-green-700 hover:bg-green-600 text-white transition-colors disabled:opacity-40"
                >
                  Song…
                  <kbd className="ml-1 text-xs bg-green-900/60 px-1 rounded font-mono">s</kbd>
                </button>
                {CONTENT_TYPES.map(({ key, label, shortcut }) => (
                  <button
                    key={key}
                    onClick={() => key === "jam" ? setShowJamNamer(true) : handleClassify(key)}
                    disabled={actionPending}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm bg-zinc-700 hover:bg-zinc-600 text-zinc-200 transition-colors disabled:opacity-40"
                  >
                    {label}
                    <kbd className="ml-1 text-xs bg-zinc-900/60 px-1 rounded font-mono">
                      {shortcut}
                    </kbd>
                  </button>
                ))}
              </div>
            </div>

            {mainAudioUrl && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-zinc-400 uppercase tracking-wide">
                  Split recording
                </div>
                <div className="flex items-center gap-3">
                  <button
                    onClick={handleSplit}
                    disabled={
                      actionPending ||
                      mainCurrentTime <= 1 ||
                      (current.duration_seconds !== null &&
                        mainCurrentTime >= current.duration_seconds - 1)
                    }
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm bg-zinc-700 hover:bg-zinc-600 text-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    title="Split recording at current playhead position (snaps to nearest segment boundary)"
                  >
                    ✂ Split at {formatDuration(mainCurrentTime)}
                  </button>
                  <span className="text-xs text-zinc-600">
                    Snaps to nearest segment boundary · segments &amp; features remapped
                  </span>
                </div>
              </div>
            )}

            <div className="text-xs text-zinc-600 space-y-0.5">
              <div>
                Shortcuts: <kbd className="font-mono">Space</kbd> play/pause ·{" "}
                <kbd className="font-mono">1/2/3</kbd> accept candidate ·{" "}
                <kbd className="font-mono">r</kbd> play ref · <kbd className="font-mono">→/]</kbd>{" "}
                next · <kbd className="font-mono">←/[</kbd> prev
              </div>
            </div>
          </>
        )}
      </div>

      {!loading && queue.length > 0 && (
        <div className="flex-shrink-0 border-t border-zinc-800 px-6 py-3 flex items-center justify-between">
          <button
            onClick={goPrev}
            disabled={currentIndex === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <span>←</span> Prev
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={handleUndo}
              disabled={undoStack.length === 0 || actionPending}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              title="Undo last label (u)"
            >
              ↩ Undo
              <kbd className="ml-1 text-xs bg-zinc-900/60 px-1 rounded font-mono">u</kbd>
            </button>
            <span className="text-xs text-zinc-600">
              {currentIndex + 1} of {queue.length}
            </span>
          </div>
          <button
            onClick={goNext}
            disabled={currentIndex === queue.length - 1}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next <span>→</span>
          </button>
        </div>
      )}
    </div>
  );
}
