import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  audioUrl,
  fetchLabelSets,
  fetchPassageRuns,
  fetchPassageSample,
  formatDuration,
  saveLabelSet,
  type LabelSet,
  type Passage,
  type PassageRun,
} from "../lib/api";
import { usePlayer } from "../lib/player";

const GROUP_COLORS = [
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  "#8cd17d", "#b6992d", "#f1ce63", "#a0cbe8", "#ffbe7d",
  "#d4a6c8", "#86bcb6", "#d37295", "#fabfd2", "#b9ac8e",
];

export function LabelPassages() {
  const [runs, setRuns] = useState<PassageRun[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [passages, setPassages] = useState<Passage[]>([]);
  const [loading, setLoading] = useState(false);

  const [groups, setGroups] = useState<string[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [labelSetName, setLabelSetName] = useState("untitled");
  const [savedSets, setSavedSets] = useState<LabelSet[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  const [selectedPassageId, setSelectedPassageId] = useState<number | null>(null);
  const [newGroupName, setNewGroupName] = useState("");

  const player = usePlayer();
  const passageListRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchPassageRuns().then((r) => {
      setRuns(r);
      if (r.length > 0) setSelectedRun(r[r.length - 1].name);
    });
    fetchLabelSets().then(setSavedSets);
  }, []);

  useEffect(() => {
    if (!selectedRun) return;
    setLoading(true);
    setSelectedPassageId(null);
    fetchPassageSample(selectedRun, 100).then((p) => {
      setPassages(p);
      setLoading(false);
    });
  }, [selectedRun]);

  const handleLoadSet = useCallback((ls: LabelSet) => {
    setLabelSetName(ls.name);
    setSelectedRun(ls.run);
    setGroups(ls.groups);
    setLabels(ls.labels);
  }, []);

  const handleSave = useCallback(async () => {
    if (!labelSetName.trim()) return;
    setSaving(true);
    setSaveMsg("");
    try {
      await saveLabelSet({ name: labelSetName, run: selectedRun, groups, labels });
      setSaveMsg("Saved");
      fetchLabelSets().then(setSavedSets);
      setTimeout(() => setSaveMsg(""), 2000);
    } catch {
      setSaveMsg("Error saving");
    } finally {
      setSaving(false);
    }
  }, [labelSetName, selectedRun, groups, labels]);

  const handleAddGroup = useCallback(() => {
    const name = newGroupName.trim();
    if (!name || groups.includes(name)) return;
    setGroups((prev) => [...prev, name]);
    setNewGroupName("");
  }, [newGroupName, groups]);

  const handleRemoveGroup = useCallback((name: string) => {
    setGroups((prev) => prev.filter((g) => g !== name));
    setLabels((prev) => {
      const next = { ...prev };
      for (const [k, v] of Object.entries(next)) {
        if (v === name) delete next[k];
      }
      return next;
    });
  }, []);

  const handleAssign = useCallback((passageId: number, group: string) => {
    setLabels((prev) => {
      const key = String(passageId);
      if (prev[key] === group) {
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return { ...prev, [key]: group };
    });
  }, []);

  const handlePlayPassage = useCallback((p: Passage) => {
    setSelectedPassageId(p.passage_id);
    player.play({
      segment_id: p.passage_id,
      recording_id: p.recording_id,
      start_seconds: p.start_seconds,
      end_seconds: p.end_seconds,
      recording_title: p.recording_title,
      session_date: p.session_date,
      song_title: p.song_title,
    });
  }, [player]);

  const selectedIndex = useMemo(
    () => passages.findIndex((p) => p.passage_id === selectedPassageId),
    [passages, selectedPassageId]
  );

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;

      if (e.code === "Space") {
        e.preventDefault();
        if (player.isPlaying) {
          player.pause();
        } else if (selectedPassageId !== null) {
          const p = passages.find((x) => x.passage_id === selectedPassageId);
          if (p) handlePlayPassage(p);
        }
        return;
      }

      if (e.code === "ArrowDown" || e.code === "KeyJ") {
        e.preventDefault();
        const nextIdx = selectedIndex < passages.length - 1 ? selectedIndex + 1 : 0;
        handlePlayPassage(passages[nextIdx]);
        return;
      }

      if (e.code === "ArrowUp" || e.code === "KeyK") {
        e.preventDefault();
        const prevIdx = selectedIndex > 0 ? selectedIndex - 1 : passages.length - 1;
        handlePlayPassage(passages[prevIdx]);
        return;
      }

      const digit = parseInt(e.key, 10);
      if (digit >= 1 && digit <= 9 && selectedPassageId !== null) {
        const group = groups[digit - 1];
        if (group) {
          handleAssign(selectedPassageId, group);
          const nextIdx = selectedIndex < passages.length - 1 ? selectedIndex + 1 : selectedIndex;
          if (nextIdx !== selectedIndex) {
            handlePlayPassage(passages[nextIdx]);
          }
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [player, selectedPassageId, selectedIndex, passages, groups, handlePlayPassage, handleAssign]);

  const groupCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const g of groups) counts[g] = 0;
    for (const v of Object.values(labels)) {
      counts[v] = (counts[v] || 0) + 1;
    }
    return counts;
  }, [groups, labels]);

  const labeledCount = Object.keys(labels).length;

  return (
    <div className="flex h-full">
      {/* Left: passage list */}
      <div className="w-96 flex-shrink-0 flex flex-col overflow-hidden border-r border-warm-200">
        <div className="px-5 py-4 border-b border-warm-200 space-y-2">
          <h1 className="text-lg font-medium">Label Passages</h1>
          <div className="flex gap-2">
            <select
              value={selectedRun}
              onChange={(e) => setSelectedRun(e.target.value)}
              className="flex-1 px-2 py-1.5 text-sm bg-warm-50 border border-warm-200 rounded-sm"
            >
              {runs.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex gap-2 items-center">
            <input
              type="text"
              value={labelSetName}
              onChange={(e) => setLabelSetName(e.target.value)}
              placeholder="Label set name…"
              className="flex-1 px-2 py-1 text-sm bg-warm-50 border border-warm-200 rounded-sm"
            />
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-3 py-1 text-sm bg-warm-900 text-cream rounded-sm hover:bg-warm-800 disabled:opacity-50"
            >
              Save
            </button>
            {saveMsg && <span className="text-xs text-warm-400">{saveMsg}</span>}
          </div>
          {savedSets.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {savedSets.map((ls) => (
                <button
                  key={ls.name}
                  onClick={() => handleLoadSet(ls)}
                  className="text-[10px] px-2 py-0.5 bg-warm-100 rounded-sm text-warm-600 hover:bg-warm-200"
                >
                  {ls.name}
                </button>
              ))}
            </div>
          )}
          <div className="text-xs text-warm-400">
            {labeledCount}/{passages.length} labeled · {groups.length} groups
          </div>
        </div>

        <div ref={passageListRef} className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center h-32 text-warm-400 text-sm animate-pulse">
              Loading…
            </div>
          )}
          {!loading && passages.map((p) => {
            const isSelected = p.passage_id === selectedPassageId;
            const label = labels[String(p.passage_id)];
            const groupIdx = label ? groups.indexOf(label) : -1;
            const isPlaying =
              player.current?.segment_id === p.passage_id && player.isPlaying;

            return (
              <div
                key={p.passage_id}
                onClick={() => handlePlayPassage(p)}
                className={`px-5 py-2.5 cursor-pointer border-b border-warm-200/60 transition-colors ${
                  isSelected
                    ? "bg-warm-100 ring-1 ring-inset ring-warm-300"
                    : "hover:bg-warm-100/40"
                }`}
              >
                <div className="flex items-center gap-2">
                  <div className={`w-5 h-5 flex-shrink-0 flex items-center justify-center rounded-full ${
                    isPlaying ? "bg-warm-900" : "bg-warm-300"
                  }`}>
                    {isPlaying ? (
                      <svg viewBox="0 0 16 16" className="w-2.5 h-2.5 fill-cream">
                        <rect x="3" y="2" width="3.5" height="12" />
                        <rect x="9.5" y="2" width="3.5" height="12" />
                      </svg>
                    ) : (
                      <svg viewBox="0 0 16 16" className="w-2.5 h-2.5 fill-cream">
                        <polygon points="5,2 13,8 5,14" />
                      </svg>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">
                      {p.song_title ?? p.recording_title ?? `Rec ${p.recording_id}`}
                    </div>
                    <div className="text-[10px] text-warm-400">
                      {p.session_date && <span>{p.session_date} · </span>}
                      {formatDuration(p.start_seconds)}–{formatDuration(p.end_seconds)}
                      <span className="ml-1">({formatDuration(p.duration)})</span>
                    </div>
                  </div>
                  {label && (
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded-sm text-white flex-shrink-0"
                      style={{ backgroundColor: GROUP_COLORS[groupIdx % GROUP_COLORS.length] }}
                    >
                      {label}
                    </span>
                  )}
                </div>
                {isSelected && groups.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2 ml-7">
                    {groups.map((g, gi) => (
                      <button
                        key={g}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAssign(p.passage_id, g);
                        }}
                        className={`text-[10px] px-2 py-0.5 rounded-sm transition-colors ${
                          label === g
                            ? "text-white"
                            : "text-warm-600 bg-warm-100 hover:bg-warm-200"
                        }`}
                        style={label === g ? { backgroundColor: GROUP_COLORS[gi % GROUP_COLORS.length] } : undefined}
                      >
                        <span className="text-warm-400 mr-0.5">{gi + 1}</span> {g}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Right: groups panel */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="px-6 py-4 border-b border-warm-200">
          <h2 className="text-base font-medium mb-3">Groups</h2>
          <div className="flex gap-2">
            <input
              type="text"
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAddGroup()}
              placeholder="New group name…"
              className="flex-1 px-3 py-1.5 text-sm bg-warm-50 border border-warm-200 rounded-sm placeholder:text-warm-300"
            />
            <button
              onClick={handleAddGroup}
              disabled={!newGroupName.trim()}
              className="px-3 py-1.5 text-sm bg-warm-900 text-cream rounded-sm hover:bg-warm-800 disabled:opacity-50"
            >
              Add
            </button>
          </div>
          <p className="text-[10px] text-warm-400 mt-2">
            Keys 1-9 assign the selected passage to a group. Space = play/pause. Arrow keys = navigate.
          </p>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
          {groups.length === 0 && (
            <div className="flex items-center justify-center h-32 text-warm-400 text-sm">
              Create groups to start labeling
            </div>
          )}
          {groups.map((g, gi) => {
            const memberIds = Object.entries(labels)
              .filter(([, v]) => v === g)
              .map(([k]) => k);
            const members = passages.filter((p) =>
              memberIds.includes(String(p.passage_id))
            );
            const color = GROUP_COLORS[gi % GROUP_COLORS.length];

            return (
              <div
                key={g}
                className="border border-warm-200 rounded-lg overflow-hidden"
              >
                <div
                  className="flex items-center gap-2 px-4 py-2.5"
                  style={{ borderLeft: `4px solid ${color}` }}
                >
                  <span className="text-xs font-medium text-warm-400 w-4">{gi + 1}</span>
                  <span className="text-sm font-medium flex-1">{g}</span>
                  <span className="text-xs text-warm-400">{groupCounts[g] || 0}</span>
                  <button
                    onClick={() => handleRemoveGroup(g)}
                    className="text-warm-400 hover:text-warm-900 text-xs ml-2"
                  >
                    ✕
                  </button>
                </div>
                {members.length > 0 && (
                  <div className="border-t border-warm-200/60 divide-y divide-warm-200/40">
                    {members.map((p) => (
                      <div
                        key={p.passage_id}
                        onClick={() => handlePlayPassage(p)}
                        className="px-4 py-1.5 text-xs text-warm-600 hover:bg-warm-50 cursor-pointer flex items-center gap-2"
                      >
                        <span className="truncate flex-1">
                          {p.song_title ?? p.recording_title ?? `Rec ${p.recording_id}`}
                        </span>
                        <span className="text-warm-400 flex-shrink-0">
                          {formatDuration(p.start_seconds)}–{formatDuration(p.end_seconds)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
