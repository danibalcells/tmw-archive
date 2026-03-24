# The Eternal Rehearsal: Design Document

## Vision

An endless generative radio that stitches together fragments from the TMW archive to produce a continuous, never-repeating stream of music that mirrors the natural dynamics of a real band rehearsal. Not a playlist, not a shuffle — a system that has learned the *grammar* of how rehearsals flow and can speak that language fluently using the band's own recorded material.

The archive contains years of rehearsals, jams, original songs, and covers. The raw material spans the full texture of a band's life together: the intense peaks, the exploratory jams, the atmospheric passages, the psychedelic interludes, the playful noodling between songs, the banter, the false starts, the moments where everything locks in. The eternal rehearsal captures all of this and recombines it into an idealized, infinite session.

---

## Core Architecture: Hierarchical Hidden Markov Model

The system is built on a two-level generative model.

### Meta-States (Level 1): The Regime

Two meta-states representing qualitatively different modes:

- **Musical** — the band is playing. All songs (originals and covers), jams, and the musical portions of rehearsals contribute to this regime. Sub-states within Musical are emergent (discovered via clustering) and capture structural moments like buildup, peak, exploration, drone, atmospheric, comedown, etc. Songs and jams are *not* distinguished at this level — a buildup is a buildup regardless of source. Many of the band's songs are 10+ minutes with extended instrumental sections, making them structurally similar to jams at the passage level.

- **Interstitial** — the connective tissue between performances. Banter, tuning, solo noodling, count-ins, silence. Sourced almost entirely from Assajos (rehearsal) recordings. Present in the radio but compressed — a little goes a long way. Used for breathing room, pacing, and human texture.

The meta-transition model (Musical ↔ Interstitial) is trained on Assajos recordings, which are the only data source that shows the natural rhythm of when bands start playing, stop, talk, and start again.

### Sub-States (Level 2): The Moment Within the Regime

Within each meta-state, a separate set of emergent sub-states with their own transition matrix.

**Musical sub-states** are discovered by clustering all musical passages across all recordings. They might converge to states like: intro, verse-like, buildup, peak/climax, bridge, solo, exploration, drone, atmospheric/ambient, psychedelic, comedown, outro. The exact number and character of states is emergent — determined by the data, not designed.

**Interstitial sub-states** are discovered from the non-musical portions of rehearsals. Might include: silence, speech/banter, solo noodling, tuning, count-in. These are sparser and serve mainly to control how the radio uses interstitial material.

---

## Data Model

### Segments (existing)

The atom. 20-second windows with 50% overlap within each recording. Each carries:
- CLAP embedding (512-dim float32) — semantic audio meaning
- mean/var RMS — energy
- mean/var spectral centroid — brightness/texture
- mean/var chroma (12-dim) — tonal center

### Passages (new concept)

The molecule. A passage is a maximal contiguous run of segments from the same recording that share the same sub-state assignment. Formed by walking through each recording's segments in temporal order and grouping consecutive same-state segments (with tolerance — one or two interloper segments don't break the passage; treated as median filtering the state sequence).

A passage is defined by:
- `recording_id` + `start_seconds` + `end_seconds` (where to find the audio)
- `state` (which sub-state cluster it belongs to)
- `meta_state` (Musical or Interstitial)
- `duration` (could be 20 seconds or several minutes)
- `boundary_features` (feature vectors of the first and last segments — used for crossfade/stitching)
- `position_in_recording` (beginning / middle / end)
- `successor_passage_id` (what actually came next in the real recording — the fidelity link)

The passage is the natural unit of playback. The system commits to a passage, plays it through, then transitions to the next. This avoids the uncanny effect of changing audio every 30 seconds.

### Labeling Interstitials

Interstitial passages can be identified with heuristics from VAD (voice activity detection / silence detection). Within Assajos recordings, segments that fall within VAD-detected chunks shorter than ~2 minutes are likely interstitial (brief talking, tuning, noodling between songs). Longer VAD chunks are likely musical performances. This provides an automatic way to label the meta-state (Musical vs. Interstitial) for rehearsal segments without manual annotation.

---

## Training Pipeline

### Step 0: Segment Clustering (offline, once)

**Input:** All segments, represented as feature vectors (CLAP embedding + RMS + spectral centroid + chroma, possibly CLAP-dominated since it encodes much of the rest implicitly).

**Method:** HDBSCAN — finds the number of clusters automatically and handles outliers (weird one-off moments that don't fit any state). Produces K sub-states where K is emergent (maybe 8, maybe 20).

**Output:** Every segment gets a state label (integer 0..K-1, or -1 for noise). After inspection, clusters can be given human-readable names for the visualization layer.

### Step 1: Passage Construction (offline, once)

Walk through each recording's segments in temporal order. Group consecutive same-state segments into passages (with interloper tolerance). Label each passage's meta-state:
- For Temas, Covers, Jams recordings: all passages are Musical.
- For Assajos recordings: use the VAD heuristic (segments within sub-2-minute VAD chunks → Interstitial, others → Musical).

### Step 2: Transition Matrix Training

**Meta-transition matrix** (Musical ↔ Interstitial): Trained exclusively on Assajos passage sequences, which show the natural rhythm of performance vs. breaks.

**Musical sub-state transitions:** Trained on passage sequences from all sources:
- Temas + Covers contribute many short, well-structured sequences (song arcs).
- Jams contribute fewer but longer freeform sequences.
- Musical portions of Assajos contribute additional sequences.
- Data balancing: within songs, weight by song (not by take count) to avoid over-representing frequently-recorded songs.

**Interstitial sub-state transitions:** Trained on interstitial passage sequences from Assajos.

**Initial state distributions:** How often each state starts a new Musical or Interstitial block.

### Alternative: The Infinite HMM

Instead of the two-stage approach (cluster then fit HMM), a Hierarchical Dirichlet Process HMM (the "infinite HMM") can simultaneously discover states, learn transitions, and learn emission distributions in one training pass. States would be defined by their *role in sequences* rather than by acoustic similarity — two acoustically different moments might share a state if they serve the same structural function. More theoretically elegant, harder to train and inspect. Worth trying alongside the two-stage approach.

---

## Generation (Runtime)

### One Tick of the Radio

1. Currently in meta-state **M** (Musical or Interstitial), sub-state **s**, playing passage **P**.
2. **P** finishes (or approaches its end, triggering crossfade).
3. **Meta-transition:** Sample from meta-transition distribution. Most of the time, stay in current regime (high self-transition). Occasionally switch.
4. **Sub-state transition:**
   - If staying in same regime: sample next sub-state from that regime's internal transition matrix.
   - If switching regime: sample from the new regime's initial state distribution.
5. **Passage selection:** Score all passages in the target sub-state's inventory. Sample from the resulting distribution.
6. **Crossfade** from P to the new passage, using boundary features to find the optimal overlap point.
7. **Emit metadata** for visualization: state labels, passage info, transition probability, arc position.
8. Loop.

### Passage Selection (the "Emission")

When the model decides "next sub-state is state 5," it must choose which specific passage from state 5's inventory to play. This is a scoring function with multiple soft factors:

| Factor | Description | Weight |
|---|---|---|
| **Boundary similarity** | Prefer passages whose opening features are close (CLAP/chroma/energy) to the closing features of the current passage. Enables smooth stitching. | High |
| **Recency penalty** | Passages played recently get heavy penalty. Passages from recently-heard recordings get lighter penalty. Prevents repetition. | High |
| **Duration fit** | If the current arc wants a long buildup, prefer longer passages. For transitional moments, prefer shorter ones. | Medium |
| **Fidelity bonus** | If the current passage has a successor_passage_id and that successor is in the target state, boost it. Preserves real rehearsal continuity sometimes. | Tunable |
| **Key matching** | Use mean_chroma to find passages sharing tonal centers with the outgoing passage. Smoother harmonic transitions. | Medium |
| **Era coherence** | Soft preference for passages from similar time periods, creating temporal neighborhoods. | Low |
| **Song gravity** | When near a known song's region in feature space, boost passages from actual takes of that song. Songs become attractor basins — landmarks in the landscape. | Situational |
| **Vocal density** | Score based on vocal/lyrical content (see below). | Tunable |

Scores are converted to a probability distribution (softmax with temperature) and sampled.

### The Vocal/Lyrics Question

Vocal fragments have a different phenomenology than instrumental ones. A decontextualized 20 seconds of singing calls attention to itself as a fragment — you feel the absence of the rest of the song. But occasional vocal fragments surfacing from instrumental texture can also be hauntingly beautiful, giving the radio a dreamy, half-remembered quality.

CLAP embeddings reliably separate vocal from instrumental content, providing a **vocal density parameter** for free:
- **0:** Pure instrumental radio. Vocal passages excluded from inventory.
- **Low:** Rare vocal appearances. Ghostly, occasional.
- **Medium:** Vocals appear naturally as clustering dictates.
- **High:** Lean into vocal content.

This is a runtime knob, not an architectural decision.

---

## DJ Personalities (Bias Layer)

The DJ personality doesn't replace the learned model — it *bends* it via two knobs.

### Knob A: Transition Bias

```
T_effective = (1 - α) × T_learned + α × T_mode
```

where α is personality strength (0 = pure data, 1 = pure personality).

| Personality | Meta-level bias | Sub-level bias |
|---|---|---|
| **Narrator** | Follows designed meta-arc: warmup → song → interstitial → song → long jam → wind down. Classic rehearsal shape. | Boosts fidelity bonus (follow real sequences), prefers Assajos-sourced passages. |
| **Deep Listener** | Suppresses meta-transitions. Stay in Musical for long stretches. Minimize interstitial. | High self-transition within sub-states. If in a drone, stay in the drone. Maximize boundary similarity for seamless flow. |
| **Provocateur** | Boosts meta-transitions. Cuts out of music mid-climax into banter. | Inversely proportional to learned transitions — whatever's least likely is most likely. Inverts boundary similarity for jarring cuts. Boosts outlier/noise passages. |
| **Archaeologist** | Doesn't modify transitions much. | Adds monotonic time prior — prefers passages from early dates, gradually moving forward. The radio becomes a time-lapse of the band's evolution. |
| **Dream Logic** | Ignores meta-transitions, lets regimes blur. | Replaces state-based inventory selection entirely. Picks from *all* passages ranked by CLAP similarity to the current moment, regardless of state. Associative rather than structural. Lets vocal fragments bleed in unexpectedly. |

### Knob B: Emission Bias

Each personality re-weights the passage selection scoring table (see above). Additionally:
- **Narrator:** Higher vocal density at climactic moments.
- **Deep Listener:** Zero or near-zero vocal density. Pure instrumental trance.
- **Dream Logic:** Vocal density follows similarity — if a vocal passage is the closest CLAP match, it plays.

---

## Visualization: The State Space as Landscape

### The Map

A 2D projection (UMAP or t-SNE) of all passages in feature space. Each dot is a passage, colored by its sub-state. Dense clusters form "territories." Known songs are labeled like cities on a map.

- A **glowing cursor** moves through the landscape in real time, showing where the radio currently is.
- A **fading trail** shows where it's been.
- **Lines of force** between clusters show learned transition probabilities — some paths are highways, others are faint trails.
- The **current state label** pulses: *"entering: high intensity"*, *"drifting: atmospheric"*.

### The Arc

A simple energy/intensity curve plotted over time:
- **Past:** rendered as solid line showing the trajectory so far.
- **Future:** sketched as a probability cloud based on likely transitions. "The model thinks we're heading toward a peak."
- State transitions marked along the timeline.
- Source session/recording info displayed for the current passage.

### Now Playing Metadata

- Current sub-state name and description
- Source recording, session date, song (if applicable)
- How the current moment was reached (transition probability that was realized)
- DJ personality mode and current bias settings

---

## Additional Ideas

### Selective Continuity (Fidelity)

After a buildup passage, there's a tunable probability of following the *actual* climax that came next in the real recording. High-affinity transitions (buildup → peak) honor the original sequence. Low-affinity transitions (peak → next thing) are free to jump anywhere. The fidelity parameter controls how often the radio follows real sequences vs. recombines freely.

### Song Gravity Wells

Passages belonging to known songs create attractor basins in the state space. As the radio drifts through feature space near a song's region, it gets pulled in — you hear a fragment or a full section of that song, then drift back into the sea of jams and fragments. Songs become landmarks.

### Crossfade Intelligence

Transitions use boundary features for smooth stitching:
- Key matching via chroma vectors
- Energy matching via per-second RMS timeseries (FeatureTimeseries table) to find moments where energy at the end of one passage matches the beginning of another
- CLAP similarity at edges for minimum perceptual continuity (unless deliberately doing a surprise transition)

### Temporal Awareness

The radio could have a sense of era. "Time travel" episodes stay within a narrow date range, letting you hear the texture of the band at a particular moment. Or deliberately juxtapose early and late material.

### Listener Interaction

Listeners don't control the radio but can influence it:
- "More intense" biases transition probabilities toward high-energy states
- "Take me somewhere I haven't been" boosts rarely-visited passages
- "Stay here" increases self-transition probability
- The system remains autonomous but responsive

### Rehearsal DNA

Each real rehearsal can be encoded as a sequence of states — its "genome." Visualize patterns across years. Are rehearsals getting more intense? More structured? More exploratory?

---

## Data Sources and Their Roles

| Source | Sections | Content | Contributes to |
|---|---|---|---|
| **Assajos** | Rehearsals (raw, VAD-segmented) | Full rehearsal sessions with music, banter, silence, tuning | Meta-transitions, musical passages, interstitial passages |
| **Temas** | Original songs (pretrimmed) | Individual song takes, often 10+ minutes | Musical passages, sub-state transition training |
| **Covers** | Cover songs (pretrimmed) | Individual cover takes | Musical passages, sub-state transition training |
| **Jams** | Freeform jams (pretrimmed) | Complete jam recordings | Musical passages, sub-state transition training |
| **Old** | Early recordings (pretrimmed) | Historical material | Musical passages |

### Data Balance Considerations

- Songs have many takes per song but shorter arcs → weight by song, not by take count, to avoid over-representing frequently-recorded songs.
- Jams are fewer but longer → probably fine as-is since each is unique.
- Assajos are very long but much is non-musical → VAD heuristic separates musical from interstitial content.
- Sub-state models are separate per regime, so no balancing needed across Musical vs. Interstitial.
