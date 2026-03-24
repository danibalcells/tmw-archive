import {
  MOCK_JAMS,
  MOCK_SESSION_RECORDINGS,
  MOCK_SESSIONS,
  MOCK_SONGS,
} from "./mockData";

export interface Recording {
  id: number;
  title: string | null;
  origin: string;
  duration_seconds: number | null;
  session_date: string | null;
  song_id: number | null;
  song_title: string | null;
  audio_path: string | null;
}

export interface Song {
  id: number;
  title: string;
  slug: string;
  song_type: string;
  cover_of: string | null;
  recording_count: number;
}

export interface SongDetail {
  id: number;
  title: string;
  slug: string;
  song_type: string;
  cover_of: string | null;
  recordings: Recording[];
}

export interface Session {
  id: number;
  date: string;
  date_uncertain: boolean;
  notes: string | null;
  recording_count: number;
}

export interface SessionDetail {
  id: number;
  date: string;
  date_uncertain: boolean;
  notes: string | null;
  recordings: Recording[];
}

async function apiFetch<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(path);
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

export async function fetchSongs(): Promise<SongDetail[]> {
  const result = await apiFetch<SongDetail[]>("/api/songs", MOCK_SONGS);
  return result.map((s) => ({ ...s, recordings: s.recordings ?? [] }));
}

export async function fetchSong(id: number): Promise<SongDetail | null> {
  const found = MOCK_SONGS.find((s) => s.id === id) ?? null;
  return apiFetch(`/api/songs/${id}`, found);
}

export async function fetchJams(): Promise<SongDetail[]> {
  return apiFetch("/api/jams", MOCK_JAMS);
}

export async function fetchSessions(): Promise<Session[]> {
  return apiFetch("/api/sessions", MOCK_SESSIONS);
}

export async function fetchSessionRecordings(
  id: number
): Promise<Recording[]> {
  const fallback = MOCK_SESSION_RECORDINGS[id] ?? [];
  return apiFetch(`/api/sessions/${id}`, { recordings: fallback }).then(
    (d) => (d as SessionDetail).recordings ?? fallback
  );
}

export function audioUrl(recordingId: number): string {
  return `/api/audio/${recordingId}`;
}

export interface MoodMapPoint {
  segment_id: number;
  recording_id: number;
  x: number;
  y: number;
  start_seconds: number;
  end_seconds: number;
  recording_title: string | null;
  audio_path: string | null;
  origin: string;
  session_date: string | null;
  song_title: string | null;
  song_type: string | null;
  mean_rms: number | null;
  mean_spectral_centroid: number | null;
}

export interface MoodMapMeta {
  name: string;
  label: string;
  count: number;
  filters: {
    include_song_type: string[] | null;
    include_origin: string[] | null;
  };
}

export async function fetchMoodMapList(): Promise<MoodMapMeta[]> {
  return apiFetch<MoodMapMeta[]>("/api/mood-map", []);
}

export async function fetchMoodMap(name: string): Promise<MoodMapPoint[]> {
  return apiFetch<MoodMapPoint[]>(`/api/mood-map/${name}`, []);
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
