import { useEffect, useState } from "react";
import { fetchJams, formatDuration, type SongDetail } from "../lib/api";
import { RecordingPane } from "../components/RecordingPane";

export function Jams() {
  const [jams, setJams] = useState<SongDetail[]>([]);
  const [selectedJam, setSelectedJam] = useState<SongDetail | null>(null);
  const [selectedRecordingId, setSelectedRecordingId] = useState<number | null>(null);

  useEffect(() => {
    fetchJams().then(setJams);
  }, []);

  const handleSelectJam = (jam: SongDetail) => {
    setSelectedJam(jam);
    const firstRecording = jam.recordings[0];
    setSelectedRecordingId(firstRecording?.id ?? null);
  };

  const handleClose = () => {
    setSelectedJam(null);
    setSelectedRecordingId(null);
  };

  return (
    <div className="flex h-full">
      <div className={`flex flex-col overflow-hidden transition-all ${selectedJam ? "w-1/2" : "w-full"}`}>
        <div className="px-6 py-5 border-b border-warm-200">
          <h1 className="text-lg font-medium">Jams</h1>
          <p className="text-sm text-warm-400 mt-0.5">{jams.length} recordings</p>
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-cream border-b border-warm-200">
              <tr>
                <th className="text-left px-6 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Title</th>
                <th className="text-left px-4 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Date</th>
                <th className="text-right px-6 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Duration</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-warm-200/60">
              {jams.map((jam) => {
                const rec = jam.recordings[0] ?? null;
                return (
                  <tr
                    key={jam.id}
                    onClick={() => handleSelectJam(jam)}
                    className={`cursor-pointer hover:bg-warm-100/40 transition-colors ${
                      selectedJam?.id === jam.id ? "bg-warm-100/60" : ""
                    }`}
                  >
                    <td className="px-6 py-3 font-medium text-warm-900">{jam.title}</td>
                    <td className="px-4 py-3 text-warm-600">
                      {rec?.session_date ?? "—"}
                    </td>
                    <td className="px-6 py-3 text-warm-600 tabular-nums text-right">
                      {formatDuration(rec?.duration_seconds ?? null)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {selectedJam && (
        <div className="w-1/2 border-l border-warm-200 flex flex-col overflow-hidden">
          <RecordingPane
            title={selectedJam.title}
            subtitle={selectedJam.recordings[0]?.session_date ?? undefined}
            recordings={selectedJam.recordings}
            selectedId={selectedRecordingId}
            onSelect={setSelectedRecordingId}
            onClose={handleClose}
          />
        </div>
      )}
    </div>
  );
}
