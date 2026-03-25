import { useEffect, useState } from "react";
import {
  fetchSessionRecordings,
  fetchSessions,
  type Recording,
  type Session,
} from "../lib/api";
import { RecordingPane } from "../components/RecordingPane";

export function Sessions() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [selectedRecordingId, setSelectedRecordingId] = useState<number | null>(null);

  useEffect(() => {
    fetchSessions().then(setSessions);
  }, []);

  const handleSelectSession = async (session: Session) => {
    setSelectedSession(session);
    setSelectedRecordingId(null);
    const recs = await fetchSessionRecordings(session.id);
    setRecordings(recs);
  };

  const handleClose = () => {
    setSelectedSession(null);
    setRecordings([]);
    setSelectedRecordingId(null);
  };

  return (
    <div className="flex h-full">
      <div className={`flex flex-col overflow-hidden transition-all ${selectedSession ? "w-1/2" : "w-full"}`}>
        <div className="px-6 py-5 border-b border-warm-200">
          <h1 className="text-lg font-medium">Sessions</h1>
          <p className="text-sm text-warm-400 mt-0.5">{sessions.length} rehearsal days</p>
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-cream border-b border-warm-200">
              <tr>
                <th className="text-left px-6 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Date</th>
                <th className="text-left px-4 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Notes</th>
                <th className="text-right px-6 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Recordings</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-warm-200/60">
              {sessions.map((session) => (
                <tr
                  key={session.id}
                  onClick={() => handleSelectSession(session)}
                  className={`cursor-pointer hover:bg-warm-100/40 transition-colors ${
                    selectedSession?.id === session.id ? "bg-warm-100/60" : ""
                  }`}
                >
                  <td className="px-6 py-3">
                    <div className="font-medium text-warm-900">
                      {session.date}
                      {session.date_uncertain && (
                        <span className="ml-1.5 text-xs text-warm-400">(?)</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-warm-600">
                    {session.notes ?? <span className="text-warm-300">—</span>}
                  </td>
                  <td className="px-6 py-3 text-warm-600 text-right tabular-nums">
                    {session.recording_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {selectedSession && (
        <div className="w-1/2 border-l border-warm-200 flex flex-col overflow-hidden">
          <RecordingPane
            title={selectedSession.date}
            subtitle={
              selectedSession.notes ??
              `${selectedSession.recording_count} recording${selectedSession.recording_count !== 1 ? "s" : ""}`
            }
            recordings={recordings}
            selectedId={selectedRecordingId}
            onSelect={setSelectedRecordingId}
            onClose={handleClose}
          />
        </div>
      )}
    </div>
  );
}
