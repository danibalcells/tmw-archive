import { useEffect, useState } from "react";
import { fetchSongs, formatDuration, type SongDetail } from "../lib/api";
import { RecordingPane } from "../components/RecordingPane";

export function Songs() {
  const [songs, setSongs] = useState<SongDetail[]>([]);
  const [selectedSong, setSelectedSong] = useState<SongDetail | null>(null);
  const [selectedRecordingId, setSelectedRecordingId] = useState<number | null>(null);

  useEffect(() => {
    fetchSongs().then(setSongs);
  }, []);

  const handleSelectSong = (song: SongDetail) => {
    setSelectedSong(song);
    setSelectedRecordingId(null);
  };

  const handleClose = () => {
    setSelectedSong(null);
    setSelectedRecordingId(null);
  };

  return (
    <div className="flex h-full">
      <div className={`flex flex-col overflow-hidden transition-all ${selectedSong ? "w-1/2" : "w-full"}`}>
        <div className="px-6 py-5 border-b border-warm-200">
          <h1 className="text-lg font-medium">Songs</h1>
          <p className="text-sm text-warm-400 mt-0.5">{songs.length} compositions</p>
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-cream border-b border-warm-200">
              <tr>
                <th className="text-left px-6 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Title</th>
                <th className="text-left px-4 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Type</th>
                <th className="text-left px-4 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Takes</th>
                <th className="text-left px-4 py-3 text-xs text-warm-400 font-medium uppercase tracking-widest">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-warm-200/60">
              {songs.map((song) => {
                const totalSecs = song.recordings.reduce(
                  (sum, r) => sum + (r.duration_seconds ?? 0),
                  0
                );
                return (
                  <tr
                    key={song.id}
                    onClick={() => handleSelectSong(song)}
                    className={`cursor-pointer hover:bg-warm-100/40 transition-colors ${
                      selectedSong?.id === song.id ? "bg-warm-100/60" : ""
                    }`}
                  >
                    <td className="px-6 py-3">
                      <div className="font-medium text-warm-900">{song.title}</div>
                      {song.cover_of && (
                        <div className="text-xs text-warm-400 mt-0.5">{song.cover_of}</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded-sm text-xs font-medium ${
                        song.song_type === "cover"
                          ? "bg-bauhaus-blue/15 text-bauhaus-blue"
                          : "bg-accent/15 text-accent"
                      }`}>
                        {song.song_type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-warm-600">
                      {song.recordings.length}
                    </td>
                    <td className="px-4 py-3 text-warm-600 tabular-nums">
                      {formatDuration(totalSecs)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {selectedSong && (
        <div className="w-1/2 border-l border-warm-200 flex flex-col overflow-hidden">
          <RecordingPane
            title={selectedSong.title}
            subtitle={
              selectedSong.cover_of
                ? `Cover · ${selectedSong.cover_of}`
                : `Original · ${selectedSong.recordings.length} take${selectedSong.recordings.length !== 1 ? "s" : ""}`
            }
            recordings={selectedSong.recordings}
            selectedId={selectedRecordingId}
            onSelect={setSelectedRecordingId}
            onClose={handleClose}
          />
        </div>
      )}
    </div>
  );
}
