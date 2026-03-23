# TMW Archive Audit

_Audited: March 2026. Source: `~/Music/TMW-Archive`._

---

## Summary counts

| Metric | Value |
|---|---|
| Total files | 496 |
| Audio files (MP3 + WAV + AIF) | 483 |
| Non-audio files | 13 |
| Date range (earliest to latest) | Dec 2012 → early 2019 |

### File types

| Extension | Count | Notes |
|---|---|---|
| `.mp3` | 368 | Most pre-trimmed content |
| `.wav` / `.WAV` | 114 | Raw session recordings + stems |
| `.hprj` | 12 | Zoom recorder project files (non-audio, discard) |
| `.mp4` | 1 | Single video in `tm9/` |
| `.aif` | 1 | One file in `Assajos/2016-08-17/` |

---

## Folder structure

The archive root has 11 named top-level entries (9 folders + 2 loose files):

```
TMW-Archive/
├── Assajos/          139 files  — raw rehearsal recordings (Catalan: "rehearsals")
├── Temas/            176 files  — pre-trimmed takes of original songs
├── Jams/              94 files  — pre-trimmed named jams, one folder per session date
├── Covers/            38 files  — cover song takes
├── Old/               15 files  — earliest recordings (Dec 2012–May 2013), pre-system
├── Live at the patio/  5 files  — one live performance, raw Zoom WAVs, no dates
├── chrysler/           3 files  — vocal stem recordings for a song called "Chrysler"
├── routine/           10 files  — individual stem tracks + premix for "Routine"
├── sanctum/            3 files  — premix recordings for "Sanctum"
├── tm9/                7 files  — TM9 album materials (predemo, stems, one video)
├── tm10/               4 files  — TM10 album materials (predemo, ideas)
├── Blades master 1.mp3         — loose file at root (duplicate of Temas/Blades content)
└── Blades master 1.wav         — loose file at root (duplicate of Temas/Blades content)
```

Max nesting depth is 5 levels below the archive root (e.g. `Assajos/2016-08-17/ZOOM0003/file.wav`). The vast majority of files sit at depth 2 (folder + date folder + file) or 3 (folder + date + subfolder + file).

---

## Naming conventions by section

### `Assajos/` — raw rehearsals (2016–2019)

Two distinct sub-patterns:

**Pattern A — single-file sessions (51 files):**
```
YYYY-MM-DD.WAV   (or .MP3 for older ones)
```
One file per rehearsal. These are the raw full-session recordings the pipeline must segment. Some have extra descriptors: `2017-02-21 SMC.WAV`, `2017-12-22 jakab.WAV`.

**Pattern B — multi-track Zoom sessions (19 subfolders):**
```
YYYY-MM-DD [optional descriptor]/
  ZOOM####_LR.WAV          — stereo mix ("Left+Right"), primary recording
  ZOOM####_Tr1.WAV         — individual instrument track 1
  ZOOM####_Tr2.WAV         — individual instrument track 2
  ZOOM####_Tr3.WAV         — individual instrument track 3
  ZOOM####_Tr4.WAV         — individual instrument track 4
  YYMMDD-HHMMSS.hprj       — Zoom project file (ignore)
  [optional .mp3 excerpts] — hand-picked moments, already labeled
```
The `_LR` file is the room-mic stereo mix; `_Tr1–4` are individual instrument channels. Several sessions also have hand-extracted `.mp3` clips coexisting alongside the raw WAVs (e.g. `2016-07-14/` has 3 named mp3 excerpts). A few sessions have multiple `_LR` files with `-0001`/`-0002` suffixes — these are the result of hitting the FAT32 2GB recording limit on the Zoom recorder; they are one continuous session split across files.

**Subfolder descriptors** (appear in 5 cases): `ft manaswi`, `ft ojeda`, `jam feat smc` — indicate guest musicians present.

### `Jams/` — pre-trimmed jams (Dec 2012–Sep 2018)

```
YYYY-MM-DD/
  Jam-Name-With-Dashes.mp3
```
Each session folder contains 1–6 individually named and trimmed jam recordings. These files are already tracks — no pipeline segmentation needed. Naming is generally `Title-Case-Hyphenated.mp3`, but there are a few inconsistencies (see Anomalies).

### `Temas/` — original song takes (2013–2016)

```
SongName/
  SongName_YYYY-MM-DD.mp3
  SongName_Demo.mp3        — undated demo takes
```
Organized by song name (hyphen-separated folder names), with dated individual takes inside. These are already trimmed. Some songs have many takes (`Rain/` has 15, `The-Smell-Of-Harmony/` has ~25). Some files have variant suffixes, e.g. `Lady-Monas-Experience_2015-06-12.WAV-split-12.mp3` (see Anomalies).

### `Covers/` — cover song takes (2013–2015)

```
SongName/
  SongName_YYYY-MM-DD.mp3
```
Same pattern as `Temas/`. 5 songs: Apolodjize, Bold-As-Love, Foxy-Lady, Under-The-Bridge, Windowpane. All dates are in the 2013–2015 range.

### `Old/` — earliest recordings (Dec 2012–May 2013)

```
MM%3aYY - DD Month/
  Song Name.mp3
```
These predate the current system. Folder names use URL-encoded colons (`%3a`) for `MM:YY`, and Catalan month/day names (`Gener`=Jan, `Març`=Mar, `Abril`=Apr, `Maig`=May, `Desembre`=Dec). Files use natural-language Catalan titles with spaces, accents, and special characters. Many of these songs were later re-recorded and appear in `Jams/` under translated/anglicised names.

### `Live at the patio/` — live performance

```
ZOOM0001.WAV … ZOOM0005.WAV
```
Raw Zoom recorder output files with no dates or descriptive names. Clearly one continuous performance split into 5 tracks (Zoom auto-splits on file size). `ZOOM0004.WAV` is only 416KB — likely a very short clip or accidental recording. No session date is encoded anywhere.

### Loose song folders (`chrysler/`, `routine/`, `sanctum/`, `tm9/`, `tm10/`)

These don't follow any of the above patterns. They are song-level production folders containing stem tracks, premixes, and work-in-progress files. `tm9` and `tm10` are individual song names (song nine and song ten in the band's catalogue), not album numbers. `tm9/Vox/` has 3 individual vocal WAV files. `routine/` has the same content in both `.mp3` and `.wav` formats (duplicated). **These folders are excluded from the archive pipeline.**

---

## Raw sessions vs. pre-trimmed tracks

| Category | Files | Status | Pipeline action needed |
|---|---|---|---|
| `Assajos/` Pattern A (single WAV) | 51 | Raw full sessions | Segment via VAD → Tracks |
| `Assajos/` Pattern B (LR + stems) | 19 sessions | Raw full sessions | Segment `_LR.WAV` via VAD → Tracks; discard `_Tr1–4` stems |
| `Jams/` | 94 | Already trimmed Tracks | Ingest directly as Tracks |
| `Temas/` | 176 | Already trimmed Tracks | Ingest directly as Tracks |
| `Covers/` | 38 | Already trimmed Tracks | Ingest directly as Tracks |
| `Old/` | 15 | Already trimmed Tracks | Dedup against `Jams/`; ingest unique content as Tracks |
| `Live at the patio/` | 5 | Raw Zoom split files | Concatenate → treat as 1 Session |
| Loose folders (`chrysler`, `routine`, `sanctum`, `tm9`, `tm10`) | 30 | Song production materials | **Excluded from pipeline** |
| Root-level loose files | 2 | Trimmed takes | Ingest as Tracks (dedup with `Temas/Blades/`) |

Roughly **70 raw session recordings** need VAD segmentation; **~360 files** are already individual tracks and can be ingested directly.

---

## File size distribution

- **Largest**: 2.0GB (6 files) — these are the Zoom FAT32-capped `_LR.WAV` session files. Several sessions are split into `LR-0001.WAV` / `LR-0002.WAV` pairs.
- **Typical WAV session**: 1.0–1.6GB
- **Typical MP3 track**: 5–30MB
- **Suspicious small files**:
  - `Live at the patio/ZOOM0004.WAV`: 416KB — probably a false start or very short clip.
  - `Temas/Idees-Nou-Tema/rlitoral3.mp3`: 988KB — unusually short; likely a quick idea fragment.
  - `Temas/proto Space Metal/Jimi's Bending into Psychedelic.mp3`: 2.2MB — short clip.
  - `Temas/proto Space Metal/sp0c0 m0t0l dj000nt 0utr0.mp3`: 2.4MB — short clip/joke naming.

---

## Anomalies and edge cases

| Issue | Location | Implication |
|---|---|---|
| **FAT32 split files** | `Assajos/` — at least 4 sessions have `_LR-0001.WAV` + `_LR-0002.WAV` | Must be concatenated before segmentation; treat as one Recording |
| **URL-encoded chars in folder names** | `Old/01%3a13 - 11 Gener/`, etc. | `%3a` = `:`. Path parsing must decode or handle percent-encoding |
| **Fake date `2015-01-01`** | `Jams/2015-01-01/` — files tagged `(Unknown-Date)` | Actual dates unknown; 3 files here. Schema needs nullable or unknown date handling |
| **`%3f` in filename** | `Jams/2015-02-15/Did-You-Just-Say-The-D-Word%3f.mp3` | `%3f` = `?`. Decode on ingest |
| **`.WAV-split-12` double extension** | `Temas/Lady-Monas-Experience/Lady-Monas-Experience_2015-06-12.WAV-split-12.mp3` | Non-standard extension; ensure extension parsing uses last segment or handles this |
| **Duplicate content across folders** | `Temas/proto Space Metal/` and `Assajos/2017-06-02/` share `Twisted Tale.mp3`, `looper guitarra + talls.mp3`, `pujada looper i riff djent.mp3` | Same files appear in both section-by-song and section-by-date trees. Ingest dedup required (by content hash or path logic) |
| **Root-level loose files** | `Blades master 1.mp3` + `.wav` | Probably master exports; same content as `Temas/Blades/`. Dedup or exclude |
| **`routine/` has mp3+wav duplicates** | `routine/routine-bass.mp3` and `routine/routine-bass.wav`, etc. | Moot — loose folders are excluded from pipeline |
| **No date in `Live at the patio/`** | 5 ZOOM WAVs with no date | Need manual annotation or infer from file metadata |
| **Catalan + special characters in `Old/`** | Accents, spaces, Catalan words | Full Unicode support required throughout the pipeline |
| **Mixed case extensions** | `.mp3` vs `.MP3`, `.wav` vs `.WAV` vs `.WAV` | Extension checks must be case-insensitive |
| **`hprj` files scattered in Assajos** | 12 files, all 12KB | Zoom project metadata files. Ignore during ingest |
| **1 video file** | `tm9/2017-11-18-VIDEO-00001723.mp4` | Skip; not audio |
| **Inconsistent Jams filename style** | Most are `Hyphenated-Title.mp3`; `Jams/2016-10-05/Gruf Natur.mp3` uses spaces | Minor; handle on ingest |
| **Extra nesting level in one Assajos subfolder** | `Assajos/2016-08-17/ZOOM0003/` | Zoom wrote its own subfolder. Recurse one extra level |

---

## Schema mapping: what `Session → Recording → Track` maps to

Given the real folder structure:

**Session** = one rehearsal day, identified by date. Sessions are **inferred from date**, not necessarily derived from a single audio file. A session row is created for every unique date that appears anywhere in the archive. All files from that date — whether a raw WAV in `Assajos/`, named jams in `Jams/`, song takes in `Temas/`, or cover takes in `Covers/` — map to the same Session.

Example: date `2014-11-03` yields 6 files across 3 sections:
- `Jams/2014-11-03/Blades.mp3`
- `Jams/2014-11-03/Change-Clothes.mp3`
- `Temas/Any-Given/Any-Given_2014-11-03.mp3`
- `Temas/Parabara/Parabara_2014-11-03.mp3`
- `Temas/Rain/Rain_2014-11-03.mp3`
- `Covers/Under-The-Bridge/Under-The-Bridge_2014-11-03.mp3`

All six become Tracks under the single `Session(date=2014-11-03)`. 35 dates in the archive have this multi-section overlap.

Special cases:
- `Live at the patio`: 1 Session (date unknown); needs manual annotation.
- `Jams/2015-01-01`: date is a placeholder for unknown-date files; these 3 tracks cannot be reliably assigned to a session.

**Recording** = one continuous audio capture (a file, or a set of FAT32-split files that must be concatenated).
- Assajos Pattern A: 1 Recording = 1 WAV file.
- Assajos Pattern B: The `_LR.WAV` file (stereo mix) is the Recording; `_Tr1–4.WAV` individual stems are discarded for now — only the LR mix is used.
- Jams/Temas/Covers/Old: each file is simultaneously a Recording and a Track (1:1). No raw session file exists for these; the Recording and Track entities collapse.
- FAT32-split pairs (`LR-0001` + `LR-0002`): treated as 1 logical Recording; concatenate before VAD segmentation.
- `Live at the patio` (5 ZOOM files): treated as 1 Recording spanning all 5 files concatenated.

**Track** = one musical segment within a Recording.
- For raw recordings (Assajos): produced by VAD/silence detection.
- For pre-trimmed files (Jams/Temas/Covers/Old): the file *is* the Track. No segmentation needed. Attach `song_id` if it's a Temas or Covers file.

**`Old/` deduplication:**

The `Old/` folder dates (`12:12 - 7 Desembre`, `01:13 - 11 Gener`, etc.) decode to specific dates in Dec 2012 – May 2013, which overlap exactly with early `Jams/` date folders. Many tracks appear in both — e.g. `arabic limbo`, `bara bara bara barà`, `drop`, `em perdo`, `experience`, `warmup`, `watermelon dance` all have counterparts in `Jams/`. Deduplication strategy:
1. Parse `Old/` folder names to extract ISO dates (decode `%3a` → `:`, translate Catalan month names).
2. For each `Old/` file, fuzzy-match its title against files in the corresponding `Jams/` date folder.
3. Confirmed duplicates: skip the `Old/` version (the `Jams/` version is the canonical copy with a clean hyphenated name).
4. Unmatched `Old/` files (e.g. `la part més fosca`, `per tres acords no està mal`, `sanity overdose`, `toqueu algo guapo o morireu`, `volia un tremolo`) are **unique content** not present in `Jams/`. Ingest these as Tracks under the inferred Session date.

**Open question for schema design:**

When a date has both a raw Assajos recording *and* pre-trimmed Jams/Temas/Covers files (e.g. `2016-12-28` has an Assajos subfolder and a Jams entry), the Tracks produced by VAD segmentation of the raw recording may overlap with the pre-trimmed Tracks. These are likely the same performances. Should they be deduplicated (e.g. by CoverHunter similarity), or kept as separate Recordings under the same Session?
