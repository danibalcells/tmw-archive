import { useEffect, useRef, useState, useCallback } from "react";
import * as d3Scale from "d3-scale";
import { interpolateInferno, interpolateCool, interpolatePlasma } from "d3-scale-chromatic";
import * as d3Zoom from "d3-zoom";
import * as d3Selection from "d3-selection";
import { fetchMoodMap, fetchMoodMapList } from "../lib/api";
import type { MoodMapPoint, MoodMapMeta } from "../lib/api";
import { usePlayer } from "../lib/player";

type MoodMapKind = "segments" | "recording-passage";

type ColorMode = "energy" | "brightness" | "year" | "type" | "song";

// 20-color categorical palette for songs (Tableau 20-ish, hand-tuned for dark bg)
const SONG_PALETTE = [
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  "#86bcb6", "#f1ce63", "#d4a6c8", "#8cd17d", "#b6992d",
  "#499894", "#e15759", "#79706e", "#d37295", "#a0cbe8",
];

// Segment categories for visibility toggling (maps to MoodMapPoint.song_type or "unidentified")
const CATEGORIES = [
  { key: "original", label: "Originals", color: "#4ade80" },
  { key: "cover", label: "Covers", color: "#60a5fa" },
  { key: "jam", label: "Jams", color: "#f97316" },
  { key: "unidentified", label: "Unidentified", color: "#6b7280" },
] as const;

type CategoryKey = (typeof CATEGORIES)[number]["key"];

const CATEGORY_COLOR: Record<string, string> = Object.fromEntries(
  CATEGORIES.map((c) => [c.key, c.color])
);

const POINT_RADIUS = 2.5;
const HOVER_THRESHOLD = 5;
const PASSAGE_RADIUS_MIN = 2;
const PASSAGE_RADIUS_MAX = 10;

function getCategory(p: MoodMapPoint): CategoryKey {
  return (p.song_type as CategoryKey) ?? "unidentified";
}

function getYear(p: MoodMapPoint): number | null {
  if (!p.session_date) return null;
  return parseInt(p.session_date.slice(0, 4), 10);
}

function formatTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export function MoodMap({ kind }: { kind: MoodMapKind }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [mapList, setMapList] = useState<MoodMapMeta[]>([]);
  const [selectedMap, setSelectedMap] = useState<string | null>(null);
  const [points, setPoints] = useState<MoodMapPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [colorMode, setColorMode] = useState<ColorMode>("type");
  const [hiddenCategories, setHiddenCategories] = useState<Set<CategoryKey>>(new Set());
  const [showRecordingLines, setShowRecordingLines] = useState(false);

  const [hovered, setHovered] = useState<MoodMapPoint | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });

  const { play, current: playingSegment } = usePlayer();

  // Stable refs read by draw/hitTest
  const transformRef = useRef<d3Zoom.ZoomTransform>(d3Zoom.zoomIdentity);
  const pointsRef = useRef<MoodMapPoint[]>([]);
  const colorModeRef = useRef<ColorMode>(colorMode);
  const hiddenCategoriesRef = useRef<Set<CategoryKey>>(hiddenCategories);
  const showRecordingLinesRef = useRef(showRecordingLines);
  const kindRef = useRef<MoodMapKind>(kind);
  colorModeRef.current = colorMode;
  hiddenCategoriesRef.current = hiddenCategories;
  showRecordingLinesRef.current = showRecordingLines;
  kindRef.current = kind;
  pointsRef.current = points;

  // Precomputed structures rebuilt whenever points change
  const recordingGroupsRef = useRef<Map<number, MoodMapPoint[]>>(new Map());
  const songColorMapRef = useRef<Map<string, string>>(new Map());

  const scalesRef = useRef<{
    sx: d3Scale.ScaleLinear<number, number>;
    sy: d3Scale.ScaleLinear<number, number>;
  } | null>(null);

  const colorScalesRef = useRef<{
    rms: d3Scale.ScaleSequential<string>;
    centroid: d3Scale.ScaleSequential<string>;
    year: d3Scale.ScaleSequential<string>;
  } | null>(null);

  const durationScaleRef = useRef<d3Scale.ScalePower<number, number> | null>(null);

  // Rebuild recording groups and song color map whenever points change
  useEffect(() => {
    const groups = new Map<number, MoodMapPoint[]>();
    for (const p of points) {
      const arr = groups.get(p.recording_id) ?? [];
      arr.push(p);
      groups.set(p.recording_id, arr);
    }
    for (const arr of groups.values()) {
      arr.sort((a, b) => a.start_seconds - b.start_seconds);
    }
    recordingGroupsRef.current = groups;

    const colorMap = new Map<string, string>();
    let idx = 0;
    for (const p of points) {
      if (p.song_title && !colorMap.has(p.song_title)) {
        colorMap.set(p.song_title, SONG_PALETTE[idx % SONG_PALETTE.length]);
        idx++;
      }
    }
    songColorMapRef.current = colorMap;
  }, [points]);

  // Fetch the list of available UMAPs on mount (or when kind changes)
  useEffect(() => {
    setMapList([]);
    setSelectedMap(null);
    setPoints([]);
    setError(null);
    setLoading(true);
    fetchMoodMapList(kind)
      .then((list) => {
        setMapList(list);
        if (list.length > 0) setSelectedMap(list[0].name);
        else setError(
          kind === "segments"
            ? "No segment maps available — run build_segment_umap.py first."
            : "No passage maps available — run build_passage_umap.py first."
        );
      })
      .catch(() => setError("Failed to load mood map list."))
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);

  // Fetch points when selectedMap changes
  useEffect(() => {
    if (!selectedMap) return;
    setLoading(true);
    setPoints([]);
    setError(null);
    fetchMoodMap(kind, selectedMap)
      .then((data) => {
        if (data.length === 0) setError(`No data in "${selectedMap}".`);
        else setPoints(data);
      })
      .catch(() => setError("Failed to load mood map data."))
      .finally(() => setLoading(false));
  }, [kind, selectedMap]);

  const buildScales = useCallback((width: number, height: number, pts: MoodMapPoint[]) => {
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const pad = 0.05;
    const xRange = xMax - xMin || 1;
    const yRange = yMax - yMin || 1;

    scalesRef.current = {
      sx: d3Scale.scaleLinear()
        .domain([xMin - xRange * pad, xMax + xRange * pad])
        .range([0, width]),
      sy: d3Scale.scaleLinear()
        .domain([yMin - yRange * pad, yMax + yRange * pad])
        .range([height, 0]),
    };

    const rmsVals = pts.map((p) => p.mean_rms ?? 0);
    const centVals = pts.map((p) => p.mean_spectral_centroid ?? 0);
    const years = pts.map((p) => getYear(p) ?? 0).filter((y) => y > 0);

    colorScalesRef.current = {
      rms: d3Scale.scaleSequential(interpolateInferno)
        .domain([Math.min(...rmsVals), Math.max(...rmsVals)]),
      centroid: d3Scale.scaleSequential(interpolateCool)
        .domain([Math.min(...centVals), Math.max(...centVals)]),
      year: d3Scale.scaleSequential(interpolatePlasma)
        .domain([Math.min(...years), Math.max(...years)]),
    };

    const durations = pts.map((p) => p.duration ?? 0).filter((d) => d > 0);
    if (durations.length > 0) {
      durations.sort((a, b) => a - b);
      const p10 = durations[Math.floor(durations.length * 0.1)];
      const p90 = durations[Math.floor(durations.length * 0.9)];
      durationScaleRef.current = d3Scale.scalePow()
        .exponent(0.5)
        .domain([p10, p90])
        .range([PASSAGE_RADIUS_MIN, PASSAGE_RADIUS_MAX])
        .clamp(true);
    } else {
      durationScaleRef.current = null;
    }
  }, []);

  const getSongColor = useCallback((songTitle: string | null): string => {
    if (!songTitle) return "#6b7280";
    return songColorMapRef.current.get(songTitle) ?? "#6b7280";
  }, []);

  const getColor = useCallback((p: MoodMapPoint, mode: ColorMode): string => {
    const cs = colorScalesRef.current;
    if (!cs) return "#6b7280";
    switch (mode) {
      case "energy":
        return p.mean_rms !== null ? cs.rms(p.mean_rms) : "#6b7280";
      case "brightness":
        return p.mean_spectral_centroid !== null ? cs.centroid(p.mean_spectral_centroid) : "#6b7280";
      case "year": {
        const yr = getYear(p);
        return yr ? cs.year(yr) : "#6b7280";
      }
      case "type":
        return CATEGORY_COLOR[getCategory(p)] ?? "#6b7280";
      case "song":
        return getSongColor(p.song_title);
    }
  }, [getSongColor]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const scales = scalesRef.current;
    if (!canvas || !scales) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);

    const t = transformRef.current;
    const mode = colorModeRef.current;
    const pts = pointsRef.current;
    const hidden = hiddenCategoriesRef.current;

    ctx.save();
    ctx.translate(t.x, t.y);
    ctx.scale(t.k, t.k);

    // Recording polylines — drawn first so dots appear on top
    if (showRecordingLinesRef.current) {
      const groups = recordingGroupsRef.current;
      const songColorMap = songColorMapRef.current;
      ctx.lineWidth = 0.8 / t.k;
      ctx.globalAlpha = 0.25;
      for (const segs of groups.values()) {
        const visible = segs.filter((p) => !hidden.has(getCategory(p)));
        if (visible.length < 2) continue;
        const lineColor =
          mode === "song"
            ? (songColorMap.get(visible[0].song_title ?? "") ?? "#9ca3af")
            : "#9ca3af";
        ctx.beginPath();
        ctx.strokeStyle = lineColor;
        ctx.moveTo(scales.sx(visible[0].x), scales.sy(visible[0].y));
        for (let i = 1; i < visible.length; i++) {
          ctx.lineTo(scales.sx(visible[i].x), scales.sy(visible[i].y));
        }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    const isPassage = kindRef.current === "recording-passage";
    const durScale = durationScaleRef.current;

    for (const p of pts) {
      if (hidden.has(getCategory(p))) continue;
      const cx = scales.sx(p.x);
      const cy = scales.sy(p.y);
      const r = isPassage && durScale && p.duration != null
        ? durScale(p.duration) / t.k
        : POINT_RADIUS / t.k;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = getColor(p, mode);
      ctx.fill();
    }

    ctx.restore();
  }, [getColor]);

  // Set up zoom once (stable — doesn't depend on points)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const selection = d3Selection.select(canvas);
    const zoom = d3Zoom.zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.3, 40])
      .on("zoom", (event: d3Zoom.D3ZoomEvent<HTMLCanvasElement, unknown>) => {
        transformRef.current = event.transform;
        draw();
      });
    selection.call(zoom);
    return () => { selection.on(".zoom", null); };
  }, [draw]);

  // Resize canvas buffer and rebuild scales whenever the container changes size.
  // This runs on first load and again whenever the NowPlayingBar appears/disappears,
  // keeping canvas buffer coordinates in sync with CSS coordinates for correct hit testing.
  useEffect(() => {
    if (points.length === 0) return;
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const resize = () => {
      const { width, height } = container.getBoundingClientRect();
      if (width === 0 || height === 0) return;
      canvas.width = width;
      canvas.height = height;
      buildScales(width, height, points);
      draw();
    };

    resize();
    transformRef.current = d3Zoom.zoomIdentity;

    const ro = new ResizeObserver(resize);
    ro.observe(container);
    return () => ro.disconnect();
  }, [points, buildScales, draw]);

  // Redraw when color mode, hidden categories, or line visibility changes
  useEffect(() => { draw(); }, [colorMode, hiddenCategories, showRecordingLines, draw]);

  const hitTest = useCallback((canvasX: number, canvasY: number): MoodMapPoint | null => {
    const scales = scalesRef.current;
    if (!scales || pointsRef.current.length === 0) return null;
    const t = transformRef.current;
    const dataX = (canvasX - t.x) / t.k;
    const dataY = (canvasY - t.y) / t.k;
    const threshold = (HOVER_THRESHOLD * 2) / t.k;
    let best: MoodMapPoint | null = null;
    let bestDist = Infinity;
    const hidden = hiddenCategoriesRef.current;

    for (const p of pointsRef.current) {
      if (hidden.has(getCategory(p))) continue;
      const dx = scales.sx(p.x) - dataX;
      const dy = scales.sy(p.y) - dataY;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < threshold && dist < bestDist) {
        bestDist = dist;
        best = p;
      }
    }
    return best;
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    setMousePos({ x: e.clientX, y: e.clientY });
    setHovered(hitTest(e.clientX - rect.left, e.clientY - rect.top));
  }, [hitTest]);

  const handleMouseLeave = useCallback(() => setHovered(null), []);

  const handleClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const p = hitTest(e.clientX - rect.left, e.clientY - rect.top);
    if (!p || !p.audio_path) return;
    play({
      segment_id: p.passage_id ?? p.segment_id ?? -1,
      recording_id: p.recording_id,
      start_seconds: p.start_seconds,
      end_seconds: p.end_seconds,
      recording_title: p.recording_title,
      session_date: p.session_date,
      song_title: p.song_title,
    });
  }, [hitTest, play]);

  const toggleCategory = useCallback((key: CategoryKey) => {
    setHiddenCategories((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const visibleCount = points.filter((p) => !hiddenCategories.has(getCategory(p))).length;

  return (
    <div className="flex flex-col h-full bg-zinc-950 text-zinc-100 relative">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-2.5 border-b border-zinc-800 flex-shrink-0">

        {/* UMAP selector */}
        {mapList.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-500 flex-shrink-0">UMAP</span>
            <select
              value={selectedMap ?? ""}
              onChange={(e) => setSelectedMap(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 text-zinc-200 text-xs rounded px-2 py-1 focus:outline-none focus:border-zinc-500"
            >
              {mapList.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.label} ({m.count.toLocaleString()})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Visibility toggles */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-zinc-500 flex-shrink-0">Show</span>
          {CATEGORIES.map(({ key, label, color }) => {
            const hidden = hiddenCategories.has(key);
            return (
              <button
                key={key}
                onClick={() => toggleCategory(key)}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs transition-colors border ${
                  hidden
                    ? "border-zinc-700 text-zinc-600 bg-transparent"
                    : "border-zinc-600 text-zinc-200 bg-zinc-800"
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: hidden ? "#3f3f46" : color }}
                />
                {label}
              </button>
            );
          })}
        </div>

        {/* Recording lines toggle */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-zinc-500 flex-shrink-0">Lines</span>
          <button
            onClick={() => setShowRecordingLines((v) => !v)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs transition-colors border ${
              showRecordingLines
                ? "border-zinc-600 text-zinc-200 bg-zinc-800"
                : "border-zinc-700 text-zinc-600 bg-transparent"
            }`}
          >
            <span
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{ backgroundColor: showRecordingLines ? "#9ca3af" : "#3f3f46" }}
            />
            Recording lines
          </button>
        </div>

        {/* Color mode */}
        <div className="flex items-center gap-1.5 ml-auto">
          <span className="text-xs text-zinc-500">Color</span>
          {(["type", "song", "energy", "brightness", "year"] as ColorMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setColorMode(m)}
              className={`px-2.5 py-1 rounded text-xs transition-colors ${
                colorMode === m
                  ? "bg-zinc-700 text-white"
                  : "text-zinc-400 hover:text-white hover:bg-zinc-800"
              }`}
            >
              {m === "energy" ? "Energy" : m === "brightness" ? "Brightness" : m === "year" ? "Year" : m === "song" ? "Song" : "Type"}
            </button>
          ))}
        </div>

        {/* Point count */}
        {points.length > 0 && (
          <span className="text-xs text-zinc-600 flex-shrink-0">
            {visibleCount.toLocaleString()} / {points.length.toLocaleString()}
          </span>
        )}
      </div>

      {/* Canvas */}
      <div ref={containerRef} className="flex-1 relative overflow-hidden">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center text-zinc-500 text-sm">
            Loading…
          </div>
        )}
        {!loading && error && (
          <div className="absolute inset-0 flex items-center justify-center text-zinc-400 text-sm px-8 text-center">
            {error}
          </div>
        )}
        {!loading && !error && (
          <canvas
            ref={canvasRef}
            className="w-full h-full"
            style={{ cursor: hovered ? "pointer" : "crosshair" }}
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
            onClick={handleClick}
          />
        )}
      </div>

      {/* Tooltip and hint live outside overflow-hidden so they're never clipped */}
      {hovered && (
        <div
          className="fixed z-50 pointer-events-none bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs shadow-xl max-w-56"
          style={{ left: mousePos.x + 14, top: mousePos.y - 10 }}
        >
          <div className="font-medium text-zinc-100 truncate mb-1">
            {hovered.recording_title ?? hovered.audio_path ?? `Recording ${hovered.recording_id}`}
          </div>
          {hovered.session_date && (
            <div className="text-zinc-400">{hovered.session_date}</div>
          )}
          {hovered.song_title && (
            <div className="text-zinc-400 truncate">{hovered.song_title}</div>
          )}
          <div className="text-zinc-500 mt-1">
            {formatTime(hovered.start_seconds)} – {formatTime(hovered.end_seconds)}
          </div>
          {hovered.duration != null && (
            <div className="text-zinc-500">
              {Math.round(hovered.duration)}s passage · {hovered.segment_count} segments
            </div>
          )}
          {hovered.mean_rms !== null && (
            <div className="text-zinc-500">
              Energy {hovered.mean_rms.toFixed(4)} · Brightness {hovered.mean_spectral_centroid?.toFixed(0)}
            </div>
          )}
          {playingSegment?.segment_id === (hovered.passage_id ?? hovered.segment_id) && (
            <div className="text-green-400 mt-1">▶ playing</div>
          )}
        </div>
      )}

      {points.length > 0 && !loading && (
        <div className="absolute bottom-3 right-4 text-zinc-700 text-xs pointer-events-none">
          scroll to zoom · drag to pan · click to play
        </div>
      )}
    </div>
  );
}
