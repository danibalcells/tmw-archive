import { audioUrl, formatDuration, type Recording } from "../lib/api";
import { WaveformPlayer } from "./WaveformPlayer";

interface RecordingPaneProps {
  title: string;
  subtitle?: string;
  recordings: Recording[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onClose: () => void;
}

export function RecordingPane({
  title,
  subtitle,
  recordings,
  selectedId,
  onSelect,
  onClose,
}: RecordingPaneProps) {
  const selected = recordings.find((r) => r.id === selectedId) ?? null;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-start justify-between gap-4 p-5 border-b border-warm-200">
        <div>
          <h2 className="text-base font-medium text-warm-900 leading-snug">{title}</h2>
          {subtitle && (
            <p className="text-sm text-warm-400 mt-0.5">{subtitle}</p>
          )}
        </div>
        <button
          onClick={onClose}
          className="mt-0.5 text-warm-400 hover:text-warm-900 transition-colors flex-shrink-0"
          aria-label="Close"
        >
          <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
            <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06z" />
          </svg>
        </button>
      </div>

      {selected && (
        <div className="p-5 border-b border-warm-200 space-y-3">
          <div>
            <div className="text-xs text-warm-400 uppercase tracking-widest mb-1">Now playing</div>
            <div className="text-sm text-warm-900 font-medium">
              {selected.title ?? "Untitled"}
            </div>
            {selected.session_date && (
              <div className="text-xs text-warm-400 mt-0.5">{selected.session_date}</div>
            )}
          </div>
          {selected.audio_path ? (
            <WaveformPlayer url={audioUrl(selected.id)} />
          ) : (
            <div className="rounded-sm bg-warm-100 border border-warm-200 p-4 text-sm text-warm-400">
              Audio not yet processed
            </div>
          )}
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {recordings.length === 0 ? (
          <p className="p-5 text-sm text-warm-400">No recordings</p>
        ) : (
          <ul>
            {recordings.map((rec) => (
              <li key={rec.id}>
                <button
                  onClick={() => onSelect(rec.id)}
                  className={`w-full text-left px-5 py-3 flex items-center justify-between gap-3 hover:bg-warm-100/60 transition-colors ${
                    selectedId === rec.id ? "bg-warm-100" : ""
                  }`}
                >
                  <div className="min-w-0">
                    <div className="text-sm text-warm-900 truncate">
                      {rec.title ?? "Untitled"}
                    </div>
                    <div className="text-xs text-warm-400 mt-0.5">
                      {rec.session_date ?? "Date unknown"}
                      {rec.song_title && rec.song_title !== rec.title && (
                        <> · {rec.song_title}</>
                      )}
                    </div>
                  </div>
                  <span className="text-xs text-warm-400 flex-shrink-0 tabular-nums">
                    {formatDuration(rec.duration_seconds)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
