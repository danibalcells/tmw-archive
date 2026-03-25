import { usePlayer } from "../lib/player";

function formatTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export function NowPlayingBar() {
  const { current, isPlaying, progress, pause, resume, stop } = usePlayer();

  if (!current) return null;

  const duration = current.end_seconds - current.start_seconds;
  const elapsed = progress * duration;

  const label =
    current.song_title ??
    current.recording_title ??
    `Recording ${current.recording_id}`;

  return (
    <div className="flex-shrink-0 border-t border-warm-200 bg-warm-50 px-5 py-3 flex items-center gap-4">
      <button
        onClick={isPlaying ? pause : resume}
        className="w-8 h-8 flex items-center justify-center rounded-full bg-accent hover:bg-accent-dark transition-colors flex-shrink-0"
        aria-label={isPlaying ? "Pause" : "Resume"}
      >
        {isPlaying ? (
          <svg viewBox="0 0 16 16" className="w-3.5 h-3.5 fill-white">
            <rect x="3" y="2" width="3.5" height="12" />
            <rect x="9.5" y="2" width="3.5" height="12" />
          </svg>
        ) : (
          <svg viewBox="0 0 16 16" className="w-3.5 h-3.5 fill-white">
            <polygon points="3,1 13,8 3,15" />
          </svg>
        )}
      </button>

      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 mb-1.5">
          <span className="text-sm font-medium text-warm-900 truncate">{label}</span>
          {current.session_date && (
            <span className="text-xs text-warm-400 flex-shrink-0">{current.session_date}</span>
          )}
          <span className="text-xs text-warm-400 flex-shrink-0 ml-auto">
            {formatTime(elapsed)} / {formatTime(duration)}
          </span>
        </div>
        <div className="h-1 bg-warm-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-none"
            style={{ width: `${progress * 100}%` }}
          />
        </div>
      </div>

      <button
        onClick={stop}
        className="w-7 h-7 flex items-center justify-center rounded-sm text-warm-400 hover:text-warm-900 hover:bg-warm-100 transition-colors flex-shrink-0"
        aria-label="Stop"
      >
        <svg viewBox="0 0 16 16" className="w-3.5 h-3.5 fill-current">
          <rect x="2" y="2" width="12" height="12" />
        </svg>
      </button>
    </div>
  );
}
