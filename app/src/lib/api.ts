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

export async function fetchAllSongs(): Promise<SongDetail[]> {
  const [songs, jams] = await Promise.all([fetchSongs(), fetchJams()]);
  return [...songs, ...jams].sort((a, b) => a.title.localeCompare(b.title));
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
  // segment view
  segment_id?: number;
  // passage view
  passage_id?: number;
  duration?: number;
  segment_count?: number;
  // common
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
  effective_type: string | null;
  mean_rms: number | null;
  mean_spectral_centroid: number | null;
}

export interface MoodMapMeta {
  name: string;
  label: string;
  count: number;
  filters: {
    include_type: string[] | null;
    include_origin: string[] | null;
  };
}

export async function fetchMoodMapList(kind: string): Promise<MoodMapMeta[]> {
  return apiFetch<MoodMapMeta[]>(`/api/mood-map/${kind}`, []);
}

export async function fetchMoodMap(kind: string, name: string): Promise<MoodMapPoint[]> {
  return apiFetch<MoodMapPoint[]>(`/api/mood-map/${kind}/${name}`, []);
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export interface Candidate {
  id: number;
  song_id: number;
  song_title: string;
  confidence: number;
  rank: number;
  status: string;
  nearest_recording_id: number;
  nearest_recording_audio_path: string | null;
  nearest_recording_session_date: string | null;
}

export interface ReviewRecording {
  id: number;
  title: string | null;
  origin: string;
  duration_seconds: number | null;
  session_date: string | null;
  audio_path: string | null;
  content_type: string | null;
  content_type_source: string | null;
  song_id: number | null;
  song_title: string | null;
  candidates: Candidate[];
  mean_rms: number | null;
  mean_spectral_centroid: number | null;
}

export interface ReviewStats {
  total_recordings: number;
  classified: number;
  unclassified: number;
  pending_candidates: number;
  by_type: Record<string, number>;
  by_source: Record<string, number>;
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
  return res.json() as Promise<T>;
}

export async function fetchReviewQueue(params: {
  status?: string;
  sort?: string;
  limit?: number;
  offset?: number;
}): Promise<ReviewRecording[]> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.sort) qs.set("sort", params.sort);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  return apiFetch<ReviewRecording[]>(`/api/review/queue?${qs}`, []);
}

export async function fetchReviewStats(): Promise<ReviewStats | null> {
  return apiFetch<ReviewStats | null>("/api/review/stats", null);
}

export async function acceptCandidate(id: number): Promise<ReviewRecording> {
  return apiPost<ReviewRecording>(`/api/review/candidates/${id}/accept`);
}

export async function rejectCandidate(id: number): Promise<Candidate[]> {
  return apiPost<Candidate[]>(`/api/review/candidates/${id}/reject`);
}

export async function classifyRecording(
  id: number,
  content_type: string,
  song_id?: number
): Promise<Recording> {
  return apiPost<Recording>(`/api/recordings/${id}/classify`, {
    content_type,
    ...(song_id !== undefined ? { song_id } : {}),
  });
}

export async function assignSong(
  recordingId: number,
  song_id: number
): Promise<Recording> {
  return apiPost<Recording>(`/api/recordings/${recordingId}/assign-song`, {
    song_id,
  });
}

export async function unassignSong(recordingId: number): Promise<Recording> {
  return apiPost<Recording>(`/api/recordings/${recordingId}/unassign-song`);
}

export async function batchClassify(
  recording_ids: number[],
  content_type: string
): Promise<{ updated: number }> {
  return apiPost<{ updated: number }>("/api/recordings/batch-classify", {
    recording_ids,
    content_type,
  });
}

export async function createSong(
  title: string,
  song_type: string
): Promise<Song> {
  return apiPost<Song>("/api/songs", { title, song_type });
}

export async function revertRecording(id: number): Promise<ReviewRecording> {
  return apiPost<ReviewRecording>(`/api/recordings/${id}/revert`);
}

export interface PassageRun {
  name: string;
  n_clusters: number;
  method: string;
  passage_count: number;
  type_count: number;
}

export interface PassageTypeSummary {
  type_id: number;
  count: number;
  n_recordings: number;
  mean_duration: number;
  mean_rms: number | null;
  mean_spectral_centroid: number | null;
  top_songs: { title: string; count: number }[];
}

export interface Passage {
  passage_id: number;
  passage_type: number;
  recording_id: number;
  segment_ids: number[];
  start_seconds: number;
  end_seconds: number;
  duration: number;
  segment_count: number;
  mean_rms: number | null;
  mean_spectral_centroid: number | null;
  recording_title: string | null;
  audio_path: string | null;
  session_date: string | null;
  song_title: string | null;
  effective_type: string | null;
}

export async function fetchPassageRuns(): Promise<PassageRun[]> {
  return apiFetch<PassageRun[]>("/api/passages/runs", []);
}

export async function fetchPassageTypes(run: string): Promise<Record<string, PassageTypeSummary>> {
  return apiFetch<Record<string, PassageTypeSummary>>(`/api/passages/${run}/types`, {});
}

export async function fetchPassagesByType(run: string, typeId: number): Promise<Passage[]> {
  return apiFetch<Passage[]>(`/api/passages/${run}/type/${typeId}`, []);
}

export async function fetchPassagesByRecording(run: string, recordingId: number): Promise<Passage[]> {
  return apiFetch<Passage[]>(`/api/passages/${run}/recording/${recordingId}`, []);
}

export async function fetchPassageSample(run: string, n: number = 100, seed: number = 42): Promise<Passage[]> {
  return apiFetch<Passage[]>(`/api/passages/${run}/sample?n=${n}&seed=${seed}`, []);
}

export interface LabelSet {
  name: string;
  run: string;
  groups: string[];
  labels: Record<string, string>;
  created_at?: string;
}

export async function fetchLabelSets(): Promise<LabelSet[]> {
  return apiFetch<LabelSet[]>("/api/label-sets", []);
}

export async function fetchLabelSet(name: string): Promise<LabelSet | null> {
  return apiFetch<LabelSet | null>(`/api/label-sets/${name}`, null);
}

export async function saveLabelSet(data: { name: string; run: string; groups: string[]; labels: Record<string, string> }): Promise<LabelSet> {
  return apiPost<LabelSet>("/api/label-sets", data);
}

export interface SplitResult {
  actual_split_at: number;
  recordings: [ReviewRecording, ReviewRecording];
}

export async function splitRecording(id: number, splitAt: number): Promise<SplitResult> {
  return apiPost<SplitResult>(`/api/recordings/${id}/split`, { split_at: splitAt });
}
