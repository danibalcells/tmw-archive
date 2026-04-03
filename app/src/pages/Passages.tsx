import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  audioUrl,
  fetchPassageRuns,
  fetchPassagesByRecording,
  fetchPassagesByType,
  fetchPassageTypes,
  formatDuration,
  type Passage,
  type PassageRun,
  type PassageTypeSummary,
} from "../lib/api";
import { usePlayer } from "../lib/player";
import { PassageTimeline } from "../components/PassageTimeline";
import { WaveformPlayer, type WaveformPlayerHandle } from "../components/WaveformPlayer";

function makeColorScale(nClusters: number): (typeId: number) => string {
  const palette = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
    "#5778a4", "#e49444", "#d1615d", "#85b6b2", "#6a9f58",
    "#e7ca60", "#a87c9f", "#f6a4a9", "#b0926e", "#c8c1b9",
    "#8cd17d", "#b6992d", "#f1ce63", "#a0cbe8", "#ffbe7d",
    "#d4a6c8", "#86bcb6", "#d37295", "#fabfd2", "#b9ac8e",
  ];
  return (typeId: number) => palette[typeId % palette.length];
}

type RightPanelMode =
  | { kind: "type"; typeId: number }
  | { kind: "recording"; recordingId: number; highlightPassageType?: number };

export function Passages() {
  const [runs, setRuns] = useState<PassageRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<string>("");
  const [types, setTypes] = useState<Record<string, PassageTypeSummary>>({});
  const [rightPanel, setRightPanel] = useState<RightPanelMode | null>(null);

  const player = usePlayer();

  useEffect(() => {
    fetchPassageRuns().then((r) => {
      setRuns(r);
      if (r.length > 0) setSelectedRun(r[r.length - 1].name);
    });
  }, []);

  useEffect(() => {
    if (!selectedRun) return;
    fetchPassageTypes(selectedRun).then(setTypes);
    setRightPanel(null);
  }, [selectedRun]);

  const currentRun = runs.find((r) => r.name === selectedRun);
  const colorScale = useMemo(
    () => makeColorScale(currentRun?.n_clusters ?? 30),
    [currentRun?.n_clusters]
  );

  const sortedTypes = useMemo(() => {
    return Object.values(types).sort((a, b) => b.count - a.count);
  }, [types]);

  const handleSelectType = useCallback((typeId: number) => {
    setRightPanel({ kind: "type", typeId });
  }, []);

  const handleViewRecording = useCallback((recordingId: number, passageType?: number) => {
    setRightPanel({ kind: "recording", recordingId, highlightPassageType: passageType });
  }, []);

  return (
    <div className="flex h-full">
      <div className="w-80 flex-shrink-0 flex flex-col overflow-hidden border-r border-warm-200">
        <div className="px-5 py-5 border-b border-warm-200">
          <h1 className="text-lg font-medium">Passages</h1>
          <select
            value={selectedRun}
            onChange={(e) => setSelectedRun(e.target.value)}
            className="mt-2 w-full px-2 py-1.5 text-sm bg-warm-50 border border-warm-200 rounded-sm"
          >
            {runs.map((r) => (
              <option key={r.name} value={r.name}>
                {r.name} — {r.passage_count} passages, K={r.n_clusters}
              </option>
            ))}
          </select>
          {currentRun && (
            <p className="text-xs text-warm-400 mt-1">
              {currentRun.type_count} types · {currentRun.passage_count} passages
            </p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {sortedTypes.map((t) => {
            const isActive = rightPanel?.kind === "type" && rightPanel.typeId === t.type_id;
            return (
              <div
                key={t.type_id}
                onClick={() => handleSelectType(t.type_id)}
                className={`px-5 py-3 cursor-pointer border-b border-warm-200/60 transition-colors ${
                  isActive ? "bg-warm-100" : "hover:bg-warm-100/40"
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <div
                    className="w-4 h-4 rounded-sm flex-shrink-0"
                    style={{ backgroundColor: colorScale(t.type_id) }}
                  />
                  <span className="text-sm font-medium">Type {t.type_id}</span>
                  <span className="text-xs text-warm-400 ml-auto">{t.count} passages</span>
                </div>
                <div className="text-xs text-warm-400">
                  {t.n_recordings} recordings · avg {formatDuration(t.mean_duration)}
                </div>
                <div className="flex gap-2 mt-1.5">
                  <AcousticBar
                    label="RMS"
                    value={t.mean_rms}
                    max={0.2}
                    color={colorScale(t.type_id)}
                  />
                  <AcousticBar
                    label="Cent"
                    value={t.mean_spectral_centroid}
                    max={4000}
                    color={colorScale(t.type_id)}
                  />
                </div>
                {t.top_songs.length > 0 && (
                  <div className="text-[10px] text-warm-400 mt-1 truncate">
                    {t.top_songs.slice(0, 3).map((s) => s.title).join(", ")}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        {rightPanel === null && (
          <div className="flex items-center justify-center h-full text-warm-400 text-sm">
            Select a passage type to see examples
          </div>
        )}
        {rightPanel?.kind === "type" && (
          <TypeDetailPanel
            run={selectedRun}
            typeId={rightPanel.typeId}
            typeSummary={types[String(rightPanel.typeId)]}
            colorScale={colorScale}
            player={player}
            onViewRecording={handleViewRecording}
          />
        )}
        {rightPanel?.kind === "recording" && (
          <RecordingDetailPanel
            run={selectedRun}
            recordingId={rightPanel.recordingId}
            highlightPassageType={rightPanel.highlightPassageType}
            colorScale={colorScale}
            player={player}
            onSelectType={handleSelectType}
            onViewRecording={handleViewRecording}
          />
        )}
      </div>
    </div>
  );
}

function AcousticBar({
  label,
  value,
  max,
  color,
}: {
  label: string;
  value: number | null;
  max: number;
  color: string;
}) {
  if (value === null) return null;
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div className="flex-1">
      <div className="flex justify-between text-[10px] text-warm-400 mb-0.5">
        <span>{label}</span>
      </div>
      <div className="h-1.5 bg-warm-200 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, backgroundColor: color, opacity: 0.7 }}
        />
      </div>
    </div>
  );
}

function TypeDetailPanel({
  run,
  typeId,
  typeSummary,
  colorScale,
  player,
  onViewRecording,
}: {
  run: string;
  typeId: number;
  typeSummary?: PassageTypeSummary;
  colorScale: (typeId: number) => string;
  player: ReturnType<typeof usePlayer>;
  onViewRecording: (recordingId: number, passageType?: number) => void;
}) {
  const [passages, setPassages] = useState<Passage[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchPassagesByType(run, typeId).then((p) => {
      setPassages(p);
      setLoading(false);
    });
  }, [run, typeId]);

  const samples = useMemo(() => {
    const byRecording = new Map<number, Passage[]>();
    for (const p of passages) {
      const arr = byRecording.get(p.recording_id) ?? [];
      arr.push(p);
      byRecording.set(p.recording_id, arr);
    }
    const picked: Passage[] = [];
    const entries = [...byRecording.entries()];
    entries.sort(() => Math.random() - 0.5);
    for (const [, arr] of entries) {
      if (picked.length >= 15) break;
      picked.push(arr[Math.floor(Math.random() * arr.length)]);
    }
    return picked;
  }, [passages]);

  const color = colorScale(typeId);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-6 py-5 border-b border-warm-200">
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 rounded-sm" style={{ backgroundColor: color }} />
          <div>
            <h2 className="text-lg font-medium">Type {typeId}</h2>
            {typeSummary && (
              <p className="text-xs text-warm-400">
                {typeSummary.count} passages across {typeSummary.n_recordings} recordings ·
                avg {formatDuration(typeSummary.mean_duration)}
              </p>
            )}
          </div>
        </div>
        {typeSummary && typeSummary.top_songs.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {typeSummary.top_songs.map((s) => (
              <span
                key={s.title}
                className="inline-block px-2 py-0.5 rounded-sm text-xs bg-warm-100 text-warm-600"
              >
                {s.title} ({s.count})
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="flex items-center justify-center h-32 text-warm-400 text-sm animate-pulse">
            Loading…
          </div>
        )}
        {!loading && samples.length === 0 && (
          <div className="flex items-center justify-center h-32 text-warm-400 text-sm">
            No passages found
          </div>
        )}
        {!loading && samples.map((p) => (
          <PassageSampleRow
            key={p.passage_id}
            passage={p}
            color={color}
            player={player}
            onViewRecording={onViewRecording}
          />
        ))}
      </div>
    </div>
  );
}

function PassageSampleRow({
  passage,
  color,
  player,
  onViewRecording,
}: {
  passage: Passage;
  color: string;
  player: ReturnType<typeof usePlayer>;
  onViewRecording: (recordingId: number, passageType?: number) => void;
}) {
  const isPlaying =
    player.current?.recording_id === passage.recording_id &&
    player.current?.start_seconds === passage.start_seconds &&
    player.isPlaying;

  const handlePlay = () => {
    player.play({
      segment_id: passage.passage_id,
      recording_id: passage.recording_id,
      start_seconds: passage.start_seconds,
      end_seconds: passage.end_seconds,
      recording_title: passage.recording_title,
      session_date: passage.session_date,
      song_title: passage.song_title,
    });
  };

  return (
    <div className="px-6 py-3 border-b border-warm-200/60 flex items-start gap-3">
      <button
        onClick={isPlaying ? () => player.pause() : handlePlay}
        className="mt-0.5 w-7 h-7 flex-shrink-0 flex items-center justify-center rounded-full transition-colors"
        style={{ backgroundColor: color }}
      >
        {isPlaying ? (
          <svg viewBox="0 0 16 16" className="w-3 h-3 fill-white">
            <rect x="3" y="2" width="3.5" height="12" />
            <rect x="9.5" y="2" width="3.5" height="12" />
          </svg>
        ) : (
          <svg viewBox="0 0 16 16" className="w-3 h-3 fill-white">
            <polygon points="4,1 13,8 4,15" />
          </svg>
        )}
      </button>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">
          {passage.song_title ?? passage.recording_title ?? `Recording ${passage.recording_id}`}
        </div>
        <div className="text-xs text-warm-400 mt-0.5">
          {passage.session_date && <span>{passage.session_date} · </span>}
          {formatDuration(passage.start_seconds)}–{formatDuration(passage.end_seconds)}
          <span className="ml-1">({formatDuration(passage.duration)})</span>
        </div>
        {passage.effective_type && (
          <span className="inline-block mt-1 px-1.5 py-0.5 rounded-sm text-[10px] bg-warm-100 text-warm-400">
            {passage.effective_type}
          </span>
        )}
      </div>
      <button
        onClick={() => onViewRecording(passage.recording_id, passage.passage_type)}
        className="text-xs text-warm-400 hover:text-warm-900 transition-colors whitespace-nowrap mt-1"
      >
        View in recording →
      </button>
    </div>
  );
}

function RecordingDetailPanel({
  run,
  recordingId,
  highlightPassageType,
  colorScale,
  player,
  onSelectType,
  onViewRecording,
}: {
  run: string;
  recordingId: number;
  highlightPassageType?: number;
  colorScale: (typeId: number) => string;
  player: ReturnType<typeof usePlayer>;
  onSelectType: (typeId: number) => void;
  onViewRecording: (recordingId: number, passageType?: number) => void;
}) {
  const [passages, setPassages] = useState<Passage[]>([]);
  const [loading, setLoading] = useState(true);
  const [activePassage, setActivePassage] = useState<Passage | null>(null);
  const [similarPassages, setSimilarPassages] = useState<Passage[]>([]);
  const [loadingSimilar, setLoadingSimilar] = useState(false);
  const waveformRef = useRef<WaveformPlayerHandle>(null);

  useEffect(() => {
    setLoading(true);
    setActivePassage(null);
    setSimilarPassages([]);
    fetchPassagesByRecording(run, recordingId).then((p) => {
      setPassages(p);
      setLoading(false);
    });
  }, [run, recordingId]);

  const handlePassageClick = useCallback(
    (passage: Passage) => {
      setActivePassage(passage);
      player.stop();
      waveformRef.current?.seekAndPlay(passage.start_seconds);
      setLoadingSimilar(true);
      fetchPassagesByType(run, passage.passage_type).then((all) => {
        setSimilarPassages(
          all
            .filter((p) => p.recording_id !== recordingId)
            .sort(() => Math.random() - 0.5)
            .slice(0, 10)
        );
        setLoadingSimilar(false);
      });
    },
    [run, recordingId, player]
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-warm-400 text-sm animate-pulse">
        Loading…
      </div>
    );
  }

  const first = passages[0];
  const totalDuration = passages.length
    ? passages[passages.length - 1].end_seconds
    : 0;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-6 py-5 border-b border-warm-200">
        <h2 className="text-lg font-medium truncate">
          {first?.song_title ?? first?.recording_title ?? `Recording ${recordingId}`}
        </h2>
        {first?.session_date && (
          <p className="text-xs text-warm-400 mt-0.5">{first.session_date}</p>
        )}
        {first?.effective_type && (
          <span className="inline-block mt-1 px-2 py-0.5 rounded-sm text-xs bg-warm-100 text-warm-600">
            {first.effective_type}
          </span>
        )}
        <button
          onClick={() => {
            setActivePassage(null);
            setSimilarPassages([]);
          }}
          className="text-xs text-warm-400 hover:text-warm-900 ml-3 transition-colors"
        >
          ← Back
        </button>
      </div>

      <div className="px-6 py-4 border-b border-warm-200 space-y-3">
        <WaveformPlayer ref={waveformRef} url={audioUrl(recordingId)} />
        <PassageTimeline
          passages={passages}
          totalDuration={totalDuration}
          colorScale={colorScale}
          activePassageId={activePassage?.passage_id ?? null}
          onPassageClick={handlePassageClick}
        />
        <div className="flex flex-wrap gap-1">
          {passages.map((p) => (
            <button
              key={p.passage_id}
              onClick={() => handlePassageClick(p)}
              className={`text-[10px] px-1.5 py-0.5 rounded-sm transition-colors ${
                activePassage?.passage_id === p.passage_id
                  ? "ring-1 ring-warm-900"
                  : "hover:opacity-80"
              }`}
              style={{
                backgroundColor: colorScale(p.passage_type),
                color: "white",
              }}
            >
              {formatDuration(p.start_seconds)} T{p.passage_type}
            </button>
          ))}
        </div>
      </div>

      {activePassage && (
        <div className="flex-1 overflow-y-auto">
          <div className="px-6 py-3 border-b border-warm-200 bg-warm-50">
            <div className="flex items-center gap-2">
              <div
                className="w-4 h-4 rounded-sm"
                style={{ backgroundColor: colorScale(activePassage.passage_type) }}
              />
              <span className="text-sm font-medium">
                Similar passages (Type {activePassage.passage_type})
              </span>
              <button
                onClick={() => onSelectType(activePassage.passage_type)}
                className="text-xs text-warm-400 hover:text-warm-900 ml-auto transition-colors"
              >
                Browse all →
              </button>
            </div>
          </div>
          {loadingSimilar && (
            <div className="flex items-center justify-center h-20 text-warm-400 text-sm animate-pulse">
              Loading…
            </div>
          )}
          {!loadingSimilar && similarPassages.length === 0 && (
            <div className="flex items-center justify-center h-20 text-warm-400 text-sm">
              No similar passages from other recordings
            </div>
          )}
          {!loadingSimilar &&
            similarPassages.map((p) => (
              <PassageSampleRow
                key={p.passage_id}
                passage={p}
                color={colorScale(p.passage_type)}
                player={player}
                onViewRecording={onViewRecording}
              />
            ))}
        </div>
      )}

      {!activePassage && (
        <div className="flex items-center justify-center flex-1 text-warm-400 text-sm">
          Click a passage in the timeline to see similar passages
        </div>
      )}
    </div>
  );
}
