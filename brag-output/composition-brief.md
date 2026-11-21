# Hyperframes Composition Brief: FinGuard

## Objective
Create a short launch-style brag video for FinGuard: a storytelling pitch for a hackathon jury, bookended by the same entry frame.

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080, 30 fps
- Duration: 25 seconds (explicit `--duration 25s`)

## Source Material
- Project root: repository root (Autonomous CFO / FinGuard, team WorksOnMyMachine, Collabothon 2026)
- Primary files read: `README.md`, `frontend/index.html`, `frontend/src/styles.css`, `frontend/src/components/` (TopBar, StatusHero, OptionCard, ChatPanel, BalanceChart, WorkspaceTabs), `frontend/src/lib/format.ts`, `backend/texts.py`
- Figures: computed with the project's backend code on the challenge bank statement (as of 10 May 2025, 60 days, €3,000 buffer): baseline, with the closure plan, and after approving option E
- Product name: FinGuard
- Tagline / strongest claim: "Warns you before the money runs out. AI explains. Code calculates. You decide."
- Key UI or visual moment to recreate: the cockpit (top bar, status card with traffic light, balance chart, options, assistant panel) while the forecast falls and is rescued
- Copy that must appear verbatim:
  - Autonomous CFO (for) Pizzeria Amalfi
  - Vincenzo runs a pizzeria and has no time for finance.
  - We are closing for holidays from 1 to 21 June
  - In about 7 weeks you will be about €16,000 short.
  - Postpone payments + smaller credit line
  - You are covered for the next 60 days
  - Warns you before the money runs out.
  - AI explains. Code calculates. You decide.

## Creative Direction
- Tone preset: polished, with cinematic scale on the entry frames
- Creative direction: "application pitch based on story telling, it should start and end with entry frame, it is a video presentation for a Hackaton aiming to win the first prize"
- Interpretation: three acts (problem, fall, rescue); the UI acts, text is sparse and held; no hype language
- Angle: one owner, one innocent plan, one cash hole seven weeks ahead, one approved fix. The traffic light of the status card is the through-line: dark on the entry frame, red through the story, green on the same frame at the end.
- Hook: the entry frame; the red lamp switches on
- Outro / punchline: the same entry frame with the green lamp and the trust line
- Avoid:
  - Generic SaaS language
  - Abstract filler visuals
  - Unrelated visual redesign
  - Any number that the backend did not compute

## Visual Identity
- LOOK: grounds #002e3c (petrol) and #f2f5f6 (app); cards #ffffff with 1px #d9e2e5 lines and 12px radius scaled about 1.7x for video; accent #ffcc33; ink #0b2a33; muted #4d646c; status red #b3261e (lamp #ff5a4f), green #17693f (lamp #45d483); chart history #0a6c94, forecast #a81e1e dashed. Open Sans 400/600/700, entry wordmark 150px, story line 76px, UI body 22 to 38px.
- BANNED: gradients across the full frame, floating or pulsing titles, glassmorphism, drop-in stock icons, fade-and-slide-up on every element
- Display font: Open Sans 700 (closest bundled face to the app's Segoe UI / system-ui)
- Body font: Open Sans
- Visual references from the project: the triangle brand mark, the traffic light pill, the yellow Assistant button, the featured option card with the yellow left border

## Storyboard
Use the storyboard in `brag-output/brag-plan.md` as the creative contract.

Scene summary:
1. Entry frame — 3.0s — lockup, dark traffic light, red lamp switches on
2. The owner — 4.0s — "Vincenzo runs a pizzeria and has no time for finance."; Assistant button clicked, yellow fills the frame
3. Cockpit — 13.75s — plan typed and confirmed; forecast falls to −€15,728.42; options A, B, C, E; E approved; status "On track"; green fills the frame
4. Entry frame again — 4.25s — same lockup, green lamp, the trust line

## Audio
- Audio role: cinematic support
- Audio arc: quiet entry, click into the product, stronger beats under the fall and the approval, bell on green, fade under the last frame
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3`
- Music treatment: volume 0.34, short fade-in, 1.4s fade-out at the end
- Music cue guidance: bundled preset `assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json` (109.96 BPM); strong cues 8.74, 10.93, 17.47, 22.93; beat grid 15.29, 15.84, 16.38, 16.93 for the option cards
- Audio-reactive treatment: subtle; lamp glow on the entry frames follows RMS/bass
- Audio-coupled moments:
  - Scene 2 — click on the Assistant button
  - Scene 3 — typed message, "Yes, include it" click, count-down of the lowest balance, card sequence, Approve click, green payoff
  - Scene 4 — final line settles near 22.93
- SFX selection guidance: key ticks for typing, soft clicks for cursor presses, one soft impact for the fall, soft drops for cards, one bell for green
- SFX analysis guidance: `assets/sfx/sfx-analysis.md` of the brag skill; low high-frequency-risk files only
- Exact SFX choice: Hyperframes chooses filenames, timestamps, density and volume from the implemented animation
- Audio files: copied into `brag-output/composition/assets/`

## Hyperframes Instructions
Load the composition-building Hyperframes skills — `hyperframes` (contract, `references/format.md`, `references/motion.md`, `references/look.md`), `hyperframes-registry`, `media-use`, and `hyperframes-cli`. /brag is its own workflow: no storyline step, no `SPEC.md`, no usage, history, feedback or REVIEW.md steps; `check` is the gate. Prefer native Hyperframes conventions over anything in `/brag`.

Requirements:
- Show the real UI, copy and figures of the project.
- Keep all text readable in the final render.
- Total duration 25 seconds.
- Include the planned music and SFX layer.
- Treat music cue metadata as timing hints; readability wins.
- Use local assets for audio.
- Run `hyperframes check` before render.
- Keep creation and rendering local; no telemetry beacons, no publishing.
