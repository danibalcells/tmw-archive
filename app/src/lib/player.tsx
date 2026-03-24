import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { audioUrl } from "./api";

export interface PlayerSegment {
  segment_id: number;
  recording_id: number;
  start_seconds: number;
  end_seconds: number;
  recording_title: string | null;
  session_date: string | null;
  song_title: string | null;
}

interface PlayerState {
  current: PlayerSegment | null;
  isPlaying: boolean;
  progress: number;
  play: (segment: PlayerSegment) => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
}

const PlayerContext = createContext<PlayerState | null>(null);

export function PlayerProvider({ children }: { children: React.ReactNode }) {
  const [current, setCurrent] = useState<PlayerSegment | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const segmentRef = useRef<PlayerSegment | null>(null);
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearStopTimer = () => {
    if (stopTimerRef.current !== null) {
      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
  };

  const stop = useCallback(() => {
    clearStopTimer();
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.onended = null;
      audioRef.current.ontimeupdate = null;
    }
    setIsPlaying(false);
    setProgress(0);
  }, []);

  const play = useCallback(
    (segment: PlayerSegment) => {
      stop();

      const audio = new Audio(audioUrl(segment.recording_id));
      audioRef.current = audio;
      segmentRef.current = segment;
      setCurrent(segment);
      setProgress(0);

      audio.currentTime = segment.start_seconds;

      const duration = segment.end_seconds - segment.start_seconds;

      audio.ontimeupdate = () => {
        const elapsed = audio.currentTime - segment.start_seconds;
        setProgress(Math.min(elapsed / duration, 1));
        if (audio.currentTime >= segment.end_seconds) {
          stop();
        }
      };

      audio.onended = () => stop();

      audio.play().then(() => {
        setIsPlaying(true);
        stopTimerRef.current = setTimeout(stop, duration * 1000 + 200);
      });
    },
    [stop]
  );

  const pause = useCallback(() => {
    clearStopTimer();
    audioRef.current?.pause();
    setIsPlaying(false);
  }, []);

  const resume = useCallback(() => {
    const audio = audioRef.current;
    const segment = segmentRef.current;
    if (!audio || !segment) return;
    audio.play().then(() => {
      setIsPlaying(true);
      const remaining =
        (segment.end_seconds - audio.currentTime) * 1000;
      stopTimerRef.current = setTimeout(stop, remaining + 200);
    });
  }, [stop]);

  useEffect(() => () => stop(), [stop]);

  return (
    <PlayerContext.Provider value={{ current, isPlaying, progress, play, pause, resume, stop }}>
      {children}
    </PlayerContext.Provider>
  );
}

export function usePlayer(): PlayerState {
  const ctx = useContext(PlayerContext);
  if (!ctx) throw new Error("usePlayer must be used within PlayerProvider");
  return ctx;
}
