# Tier 1 Dev Subset

_Selected: March 2026. All paths relative to `ARCHIVE_ROOT` (`~/Music/TMW-Archive`)._

The Tier 1 subset exercises every distinct pipeline branch with the smallest
reasonable file selection. Each entry documents which branch it covers, the
files chosen, and the expected DB output after ingest.

---

## Branch 1 — Assajos Pattern A: raw single-file WAV → VAD

**What it tests:** parsing a date-stamped single WAV session, running silence
detection, writing N `Recording` rows with `origin='vad_segment'` and
`start/end_offset_seconds` populated.

**File:**
```
Assajos/2016-01-17.wav   (669 MB)
```

**Expected output:**
- 1 `Session(date='2016-01-17')`
- N `Recording` rows, `origin='vad_segment'`, each with `source_path=["Assajos/2016-01-17.wav"]`
  and `start/end_offset_seconds` set, `audio_path` pointing to extracted MP3s

---

## Branch 2 — Assajos Pattern B: Zoom multi-track folder → pick `_LR` → VAD

**What it tests:** scanning a Zoom session folder, selecting only the stereo LR
file (ignoring any instrument stems), then running VAD on it. The folder also
contains `ZOOM0011_LR.WAV` (682 KB — a very short clip, likely a false start);
VAD should either produce one tiny segment or discard it as silence.

**Folder:**
```
Assajos/2016-09-22/
  ZOOM0010_LR.WAV   (964 MB — primary session)
  ZOOM0011_LR.WAV   (682 KB — short clip)
  160922-210136.hprj  (ignore)
```

**Expected output:**
- 1 `Session(date='2016-09-22')`
- N `Recording` rows from `ZOOM0010_LR.WAV`, `origin='vad_segment'`
- 0 or 1 very short `Recording` row from `ZOOM0011_LR.WAV`

---

## Branch 3 — Jams: pre-trimmed → direct Recording (1:1 with Song)

**What it tests:** ingesting a pre-trimmed jam file directly as a `Recording`
with `origin='pretrimmed'`. A `Song(song_type='jam')` is created (or looked up)
on first encounter. No VAD.

**File:**
```
Jams/2013-11-16/Same-Rain-New-Villages.mp3
```

**Expected output:**
- 1 `Session(date='2013-11-16')` — shared with Branch 5 below
- 1 `Song(song_type='jam', slug='Same-Rain-New-Villages', title='Same Rain New Villages')`
- 1 `Recording(origin='pretrimmed', song_id=<above>, session_id=<above>)`

---

## Branch 4 — Temas: pre-trimmed song takes linked to a seeded Song

**What it tests:** ingesting multiple dated takes of an original song, each
becoming its own `Recording` linked to a single `Song` row that was pre-seeded
by `seed_songs.py`. Also exercises the `.WAV-split-12.mp3` double-extension
edge case.

**Files:**
```
Temas/Lady-Monas-Experience/Lady-Monas-Experience_2013-11-16.mp3
Temas/Lady-Monas-Experience/Lady-Monas-Experience_2015-09-25.mp3
Temas/Lady-Monas-Experience/Lady-Monas-Experience_2015-06-12.WAV-split-12.mp3
Temas/Lady-Monas-Experience/Lady-Monas-Experience_Demo.mp3
```

**Expected output:**
- `Song(song_type='original', slug='Lady-Monas-Experience')` — seeded in advance
- 4 `Recording` rows, all linked to that Song
- 1 `Session(date='2013-11-16')` — shared with Branch 3 (see Branch 5)
- Sessions for 2015-09-25, 2015-06-12 created fresh; Demo recording has `session_id=NULL`

---

## Branch 5 — Multi-section date overlap: shared Session across sections

**What it tests:** date `2013-11-16` appears in both `Jams/2013-11-16/` (Branch 3)
and `Temas/Lady-Monas-Experience/` (Branch 4). The pipeline must reuse the
same `Session` row rather than creating a duplicate.

**No extra files needed** — this is verified by running Branches 3 and 4 in
sequence and confirming there is only one `Session(date='2013-11-16')` in the
DB with two `Recording` rows pointing to it.

---

## Branch 6 — `Old/`: URL-encoded paths, Catalan names, unique content

**What it tests:** decoding `%3a` → `:` in folder names, parsing Catalan month
names to extract an ISO date, Unicode filenames (accents, spaces), and ingesting
files that have no counterpart in `Jams/` (i.e. unique content, not duplicates).

The audit identified these four files as unique (not present in `Jams/`):

**Folder:**
```
Old/12%3a12 - 7 Desembre/   (decodes to Dec 7, 2012)
  La part més fosca.mp3
  Per tres acords no està mal.MP3
  Toqueu algo guapo o morireu.mp3
  Volia un tremolo.mp3
```

**Expected output:**
- 1 `Session(date='2012-12-07')`
- 4 `Recording` rows, `origin='pretrimmed'`, `song_id=NULL` (jams with no
  existing Song row; the pipeline creates Song rows on encounter or leaves
  song_id null pending a future matching pass)

---

## Output folder

Processed files land at:
```
~/Music/TMW-Archive-Processed/recordings/{recording_id}.mp3
```

Flat, keyed by DB primary key. The DB is the index — no metadata in the path.

---

## Deferred to Tier 2

| Case | Reason |
|---|---|
| FAT32-split `_LR-0001` + `_LR-0002` pairs | Concatenation preprocessing; same VAD path as Branch 2 after that |
| `Covers/` | Identical pipeline path to Temas; not a new branch |
| `Live at the patio/` | No-date handling + multi-file concat; low priority |
| Root-level loose files (`Blades master 1.*`) | Dedup with `Temas/Blades/`; minor |
