## The Missing Watermelon Archive

### What it is

A private web app for The Missing Watermelon's full rehearsal archive — hundreds of hours of room-mic recordings spanning the band's full history, made browsable, searchable, and listenable in new ways. Shareable with the band via browser. No app installs.

---

### Processing pipeline

Runs once, unattended, on Apple Silicon (~3–7 days for full archive). Never needs to touch raw audio again for new features. All downstream experiences are built on the outputs of this single pass.

**Steps in dependency order:**

1. **FFmpeg silence detect + Silero VAD** → segment long rehearsal WAVs into tracks (song takes, jams, banter, silence)
2. **Demucs MLX** → source separation per recording; keep per-stem energy curves, discard stem audio
3. **All-in-One MLX** → beats, tempo, structural labels (intro/solo/break/etc.) per track
4. **librosa** → per-second RMS, spectral centroid, chroma; stored as compressed timeseries per track
5. **Essentia** → per-second arousal/valence curves + mood/theme tags (energetic, dark, atmospheric, etc.)
6. **LAION-CLAP** → 512-dim embedding per 30-second segment; primary vector for search, similarity, Mood Map
7. **CoverHunter** → song identity embeddings per track; used to auto-cluster takes of the same composition
8. **Whisper** → transcript of speech segments only; used for session summaries and auto song-name extraction
9. **Post-hoc batch** (requires all embeddings) → novelty scores (kNN density in CLAP space), UMAP 2D projection

**Dev subset strategy:**
- *Tier 1* (~1hr audio, 3–5 recordings): validate full pipeline end to end in ~1 hour. One long jam, one trimmed song take, one banter-heavy session.
- *Tier 2* (~10hrs, one full session + handful of jams + 3–4 songs with multiple takes): build and test all UI against real data.
- *Tier 3* (full archive): run as background job while developing against Tier 2. Schema identical across tiers — just more rows.

---

### Data model

Five entities in hierarchy, one SQLite database:

**Session** `id · date · location · notes · whisper_summary`
One row per rehearsal day. Calendar view rolls up here.

**Recording** `id · session_id · filename · duration_sec · waveform_peaks_path · type`
One per WAV file. Waveform peaks are a path to pre-generated bbc/audiowaveform JSON.

**Track** `id · recording_id · start_sec · end_sec · type · song_id (nullable) · label · structural_label · quality_flag`
Primary browsing atom. Song takes link to a Song; jams do not. Quality flag is auto-derived from energy variance, arousal range, duration.

**Song** `id · name · earliest_take_date · canonical_take_id · coverhunter_cluster_id`
One per composition. Auto-populated by CoverHunter, manually correctable. Powers the song list and take history views.

**Segment** `id · track_id · start_sec · end_sec · clap_embedding · chroma_mean (12-dim) · rms_mean · rms_variance · centroid_mean · arousal_mean · valence_mean · tempo_bpm · mood_tags (JSON) · structural_label · novelty_score · umap_x · umap_y`
30-second windows at 50% overlap (~24K segments for 200hrs). The engine of every smart feature. CLAP embedding stored as BLOB here and mirrored in a FAISS flat index keyed by segment ID.

**Feature timeseries** `id · track_id · feature (rms/centroid/arousal/valence) · resolution_sec · values (compressed float BLOB)`
Dense per-second curves that span an entire track continuously. Separate table so Track rows stay lean. Loaded on demand when rendering a Heatmap. *Belongs to Track, not Segment — segments are slices cut from this ribbon.*

**Sidecar:** FAISS flat index (~50MB) for embedding similarity queries. Everything else is SQLite.

---

### Storage & serving

| Asset | Size | Where |
|---|---|---|
| Source WAVs (24-bit/48kHz stereo) | ~200GB | Local only, never served |
| Transcoded Opus (128kbps) | ~20GB | Cloudflare R2 (~$0.20/mo) |
| Waveform peaks JSON | ~50MB | R2, served as static files |
| SQLite + FAISS | ~250MB | API server |
| UMAP coords, transcripts, misc | ~10MB | SQLite |

**Architecture:** FastAPI backend (SQLite + FAISS) on a $5/month Fly.io or Railway instance handling only metadata queries and embedding lookups. Audio streams directly from R2 to the browser — the API never touches audio bytes. React + wavesurfer.js v7 frontend, deployable as static files. No Postgres, no Redis, no vector database — one SQLite file, one FAISS index, one object storage bucket.

---

### Experiences

Ranked by **priority** (value to users, foundational dependency for other features), annotated with **complexity**.

---

**1. Browser** `priority: foundational` `complexity: low`
The spine of the whole product. Song list with take counts and date range; jam list; per-song take history in chronological order; session calendar. Filter sidebar operates on pre-aggregated scalar fields in Track and Segment (date, duration, arousal_mean, mood_tags) — pure SQL, no embedding lookups. Click a song → see all takes. Click a take → open the smart waveform. Without this, nothing else has a home.

---

**2. Jam Heatmap** `priority: high` `complexity: low-medium`
Smart waveform: energy, brightness, arousal, and valence rendered as a colored ribbon above the wavesurfer.js waveform. Replaces blind scrubbing on 30–60 minute jams — you can see at a glance where the interesting passages are. Loads the track-level feature timeseries on demand. Renders entirely in the browser from pre-computed curves; no ML at playback time. Unlocks the jams as actually navigable objects.

---

**3. Mood Map** `priority: high` `complexity: low-medium`
UMAP scatter plot of all segments (~24K dots), colored by mood tag or year. The band's entire musical vocabulary made spatial — click any dot to play that segment. Clusters reveal the archive's natural regions: heavy riff territory, spacy atmospherics, uptempo rock. Powered by pre-computed umap_x/y coordinates in the Segment table. The 2D projection regenerates as a batch job when new audio is added; no per-request ML inference. Also serves as the visual index that conceptually underlies Watermelon Slices.

---

**4. Ghost Tracks** `priority: medium` `complexity: low`
A curated feed of under-listened segments that score high on novelty (outliers in CLAP embedding space — unlike anything else in the archive). Surfaces the forgotten late-era jams. Implementation is a single SQL query ordered by novelty_score DESC, filtered to jam-type tracks. The novelty score is computed once in the post-hoc batch pass. Zero additional infrastructure.

---

**5. Watermelon Slices** `priority: high` `complexity: high`
Endless stochastic radio that walks a steerable energy/mood arc, crossfading segments from across the archive. High-level controls: energy level (chill ↔ chaotic), mood flavor (atmospheric ↔ heavy), era filter (by year or session range). The system selects the next segment via weighted sampling in CLAP space constrained by the current arc position, finds a crossfade-friendly endpoint (low-energy tail in source, low-energy head in destination), and fades between them. Displays a live history of played fragments with links back to their source jam. Complexity is in the arc navigation logic and crossfade UX — the underlying data (embeddings, energy curves) is already there. Depends on Heatmap and Mood Map being solid first, since those validate that the energy/mood signals are meaningful.

---

**6. Calendar** `priority: medium` `complexity: low`
Timeline view of sessions by date. Each session card shows: recordings present, songs worked on, jams, rough energy arc of the session (derived from mean arousal across tracks), Whisper-extracted banter snippets. A time machine into band history. Low complexity because it's mostly an aggregation view on data that already exists — the main extra ingredient is Whisper transcripts, which are cheap to generate on speech-only segments.

---

**7. Divergence Browser** `priority: low-medium` `complexity: medium-high`
Pick a song, get all takes aligned chronologically. Visual diff of structural sections across takes: which sections were stable, which evolved, which appeared and disappeared. Powered by All-in-One structural labels aligned across takes via CoverHunter clustering. Complexity is in the alignment and diff visualization logic — musically meaningful section comparison is non-trivial when takes vary in length and arrangement. Best built after the Browser and song grouping are validated.

---

### The core bet

SQLite + FAISS + R2 is sufficient for the entire project at this data scale. The pipeline runs once and produces a self-contained artifact — database + index + static audio files — from which every experience is built. Adding a new feature means adding a column and a processing pass, never reprocessing from scratch.