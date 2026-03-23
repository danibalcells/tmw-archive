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
        <div className="px-6 py-5 border-b border-zinc-800">
          <h1 className="text-lg font-semibold">Songs</h1>
          <p className="text-sm text-zinc-500 mt-0.5">{songs.length} compositions</p>
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-zinc-950 border-b border-zinc-800">
              <tr>
                <th className="text-left px-6 py-3 text-xs text-zinc-500 font-medium uppercase tracking-wide">Title</th>
                <th className="text-left px-4 py-3 text-xs text-zinc-500 font-medium uppercase tracking-wide">Type</th>
                <th className="text-left px-4 py-3 text-xs text-zinc-500 font-medium uppercase tracking-wide">Takes</th>
                <th className="text-left px-4 py-3 text-xs text-zinc-500 font-medium uppercase tracking-wide">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/60">
              {songs.map((song) => {
                const totalSecs = song.recordings.reduce(
                  (sum, r) => sum + (r.duration_seconds ?? 0),
                  0
                );
                return (
                  <tr
                    key={song.id}
                    onClick={() => handleSelectSong(song)}
                    className={`cursor-pointer hover:bg-zinc-800/40 transition-colors ${
                      selectedSong?.id === song.id ? "bg-zinc-800/60" : ""
                    }`}
                  >
                    <td className="px-6 py-3">
                      <div className="font-medium text-white">{song.title}</div>
                      {song.cover_of && (
                        <div className="text-xs text-zinc-500 mt-0.5">{song.cover_of}</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                        song.song_type === "cover"
                          ? "bg-purple-900/50 text-purple-300"
                          : "bg-green-900/50 text-green-300"
                      }`}>
                        {song.song_type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-zinc-400">
                      {song.recordings.length}
                    </td>
                    <td className="px-4 py-3 text-zinc-400 tabular-nums">
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
        <div className="w-1/2 border-l border-zinc-800 flex flex-col overflow-hidden">
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
