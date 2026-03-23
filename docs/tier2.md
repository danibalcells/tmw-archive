# Tier 2 Dev Subset

_Selected: March 2026. All paths relative to `ARCHIVE_ROOT` (`~/Music/TMW-Archive`)._

The Tier 2 subset validates the pipeline at moderate scale (~10 hours of audio) and
introduces the edge cases deferred from Tier 1. All Tier 1 pipeline branches are assumed
to be working; this subset focuses on breadth and scale rather than branch coverage.

---

## Assajos — raw rehearsal sessions (~5.5 hr)

### Session A: `2016-11-23/` — full Zoom session, LR-only (Pattern B, no stems)

Extends Tier 1's Pattern B coverage with a session that has a single `_LR.WAV` and no
instrument stem files alongside it.

```
Assajos/2016-11-23/
  ZOOM0001_LR.WAV   (1.3 GB — ~1.3 hr)
```

**Expected output:**
- 1 `Session(date='2016-11-23')`
- N `Recording` rows from `ZOOM0001_LR.WAV`, `origin='vad_segment'`

---

### Session B: `2016-12-21/` — FAT32 split pair (deferred from Tier 1)

The first full exercise of the FAT32 concatenation path: two `_LR-0001` / `_LR-0002`
files that must be joined before VAD. The second file is non-trivial (222 MB), so VAD
output will span both halves.

```
Assajos/2016-12-21/
  ZOOM0013_LR-0001.WAV   (2.0 GB)
  ZOOM0013_LR-0002.WAV   (222 MB)
```

**Expected output:**
- 1 `Session(date='2016-12-21')`
- N `Recording` rows, `origin='vad_segment'`, `source_paths` contains both files

---

### Session C: `2016-09-29.WAV` — single-file Pattern A

A second Pattern A session (Tier 1 already has `2016-01-17.wav`), from a different part
of 2016.

```
Assajos/2016-09-29.WAV   (1.1 GB — ~1.1 hr)
```

**Expected output:**
- 1 `Session(date='2016-09-29')`
- N `Recording` rows, `origin='vad_segment'`

---

### Session D: `2017-03-09.WAV` — single-file Pattern A, different era

Adds a 2017 session, broadening the date range exercised in the DB.

```
Assajos/2017-03-09.WAV   (919 MB — ~55 min)
```

**Expected output:**
- 1 `Session(date='2017-03-09')`
- N `Recording` rows, `origin='vad_segment'`

---

## Jams — pretrimmed (~2 hr)

Four additional jams spanning 2013–2018, including one WAV (rather than MP3).

```
Jams/2013-09-12/Jammin-In-The-Name-Of.mp3   (46 MB — ~49 min)
Jams/2015-10-11/Phryjiam.mp3                (36 MB — ~38 min)
Jams/2016-10-05/Gruf Natur.mp3              (49 MB — ~52 min)
Jams/2018-09-16/Liquid.wav                  (307 MB — ~17 min, WAV format)
```

`Liquid.wav` is the only WAV-format file in `Jams/`; it tests that the pretrimmed scanner
accepts `.wav` alongside `.mp3`.

**Expected output (per file):**
- 1 `Session` (new or shared if date overlaps another section)
- 1 `Song(song_type='jam')`
- 1 `Recording(origin='pretrimmed')`

---

## Temas — songs with multiple takes (3 complete folders)

Three complete song folders, chosen for manageable take counts and good chronological spread.

```
Temas/Backstage/   (14 takes, 194 MB)
Temas/Parabara/    (20 takes, 310 MB)
Temas/Rain/        (16 takes, 272 MB)
```

These exercise song-identity grouping (all takes under one `Song` row), the `Demo`
date-null edge case (Parabara has `Parabara_Demo.mp3`), and variant slugs within one
folder (`Jazzabara`, `Zappabara`, `Parabaract` — all under `Temas/Parabara/`).

**Expected output (per song folder):**
- 1 `Song(song_type='original')` — seeded by `seed_songs.py`
- N `Recording` rows linked to that Song, one per take file
- Sessions created or reused for each take date; Demo take has `session_id=NULL`

---

## Edge cases covered by Tier 2 (beyond Tier 1)

| Case | File(s) |
|---|---|
| FAT32 split `_LR-0001` + `_LR-0002` | `Assajos/2016-12-21/` |
| WAV format in `Jams/` section | `Jams/2018-09-16/Liquid.wav` |
| Dateless take (`_Demo`) | `Temas/Parabara/Parabara_Demo.mp3` |
| Variant slugs within one song folder | `Jazzabara`, `Zappabara`, `Parabaract` in `Temas/Parabara/` |
| Multiple songs with many takes | `Backstage` (14), `Parabara` (20), `Rain` (16) |

---

## TIER2_SOURCE_PATHS reference

```
Assajos/2016-11-23
Assajos/2016-12-21
Assajos/2016-09-29.WAV
Assajos/2017-03-09.WAV
Jams/2013-09-12/Jammin-In-The-Name-Of.mp3
Jams/2015-10-11/Phryjiam.mp3
Jams/2016-10-05/Gruf Natur.mp3
Jams/2018-09-16/Liquid.wav
Temas/Backstage
Temas/Parabara
Temas/Rain
```
