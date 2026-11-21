# Brag Plan: FinGuard

## What is this app?
FinGuard is an autonomous CFO for a small business (Pizzeria Amalfi): it forecasts the bank balance day by day, warns before the money runs out, computes rescue options with their cost, explains them in plain language, and carries out (simulated) only what the owner approves.

## The angle
A 25-second story, not a feature list. One owner, one innocent plan ("we close for holidays"), one cash hole nobody saw coming, one approved fix. The film is bookended by the same entry frame: it opens on the FinGuard lockup with a dark traffic light and closes on the identical frame with the light green. The traffic light (a real UI element of the status card) is the story's through-line: dark, red, green.

The judges are bankers and engineers, so the closing line is the trust claim the README makes: "Warns you before the money runs out. AI explains. Code calculates. You decide."

## Hook (first 2-3 seconds)
The entry frame: FinGuard mark, "Autonomous CFO for Pizzeria Amalfi", a dark traffic light. On the beat the red lamp switches on. Something is wrong before a word of story is told.

## Key moments (the middle)
- "Vincenzo runs a pizzeria and has no time for finance." Then the yellow Assistant button, clicked.
- The owner types "We are closing for holidays from 1 to 21 June" and confirms the plan card ("Yes, include it").
- The forecast recalculates on the real chart: the lowest projected balance counts from −€325.55 down to −€15,728.42; the headline becomes "In about 7 weeks you will be about €16,000 short."
- Options A, B, C, E arrive, E recommended. One click on Approve. The status turns to "On track": "You are covered for the next 60 days."

## Outro / punchline
The green lamp grows into the frame and lands back in the entry frame, now lit. Final line: "Warns you before the money runs out. AI explains. Code calculates. You decide."

## User flow worth showing
Tell the assistant a plan and confirm it, see the forecast recalculated with the shortfall, approve the recommended option and see the status go green.

## Tone
- Preset: polished (with cinematic scale on the two entry frames)
- Creative direction (user-provided): "application pitch based on story telling, it should start and end with entry frame, it is a video presentation for a Hackaton aiming to win the first prize"
- Interpretation: a calm, confident product story in three acts (problem, fall, rescue). The UI does the acting; text is sparse and held long enough to read. No jokes, no hype words. "Entry frame" is read as the opening title frame, which returns at the end changed by the story (lamp green).

## Format: landscape — 1920x1080
## Duration: 25 seconds (explicit `--duration 25s`; the scenes below sum to 25.0)

## Visual identity (from the project)
- Background: #002e3c (petrol, entry frames and top bar); #f2f5f6 (app ground); #ffffff (cards)
- Accent: #ffcc33 (yellow)
- Text: #0b2a33 on light, #ffffff on petrol; muted #4d646c
- Status colours: red #b3261e / lamp #ff5a4f, green #17693f / lamp #45d483
- Display font: Open Sans 700 (the app uses Segoe UI / system-ui, which is not available at render; Open Sans is the closest bundled humanist sans)
- Body font: Open Sans 400/600
- Strongest visual element: the traffic light of the status card, and the balance forecast chart falling below zero

## Data and privacy
All figures are computed by the project's own backend code from the challenge bank statement (as of 10 May 2025, 60 days, €3,000 buffer); nothing is invented. The data set is the fictional Pizzeria Amalfi of the challenge. The payee name inside option B's title is left out; the README's wording "Postpone the transfer to Zurich" is used. No secrets, hostnames or account numbers appear.

## Share copy (draft)
Vincenzo planned three weeks of holidays. FinGuard saw the €15,728 hole seven weeks ahead, computed the options and waited for his approval. Warns you before the money runs out. AI explains. Code calculates. You decide.

## Audio direction
- Role: cinematic support under a product story
- Music: happy-beats-business-moves-vol-12-by-ende-dot-app.mp3 (steady and clean)
- Music treatment: starts at 0, volume 0.34, 0.4s fade-in, 1.4s fade-out under the final frame
- Music cue guidance: preset read (109.96 BPM). Strong cues used: 8.74 (message sent), 10.93 (plan confirmed, forecast falls), 17.47 (Approve click), 22.93 (final frame settles). Beat-grid window 15.29 to 16.93 for the four option cards.
- Audio-reactive treatment: subtle; the glow of the lit lamp on the two entry frames follows the music's energy. No waveform or equalizer visuals.
- SFX posture: sparse and motion-matched; key ticks on typing, a click on each of the three cursor clicks, one soft impact when the forecast falls, soft drops for the option cards, one bell when the status turns green.
- Audio-coupled moments: typed chat message, three clicks, count-down of the lowest balance, card sequence, green payoff.
- Restraint rule: nothing loud or bright; no effect without a visible cause.

## Storyboard

### Scene 1 — Entry frame — 3.0s (0.0 to 3.0)
Petrol ground. Traffic light (all lamps dark) beside the FinGuard mark and wordmark. Text: "FinGuard", "Autonomous CFO for Pizzeria Amalfi", "Team WorksOnMyMachine · Collabothon 2026". At about 2.2s the red lamp switches on; the lockup text blurs away while the traffic light stays.
Sequential/interaction: none
Audio intent: quiet opening; one low hit when the lamp turns red
Audio-coupled idea: lamp switch on a beat
Transition mood: soft → Scene 2 (same ground, the traffic light carries over)

### Scene 2 — The owner — 4.0s (3.0 to 7.0)
Same ground, traffic light red in the same place. The line arrives word by word and holds: "Vincenzo runs a pizzeria and has no time for finance." (10 words, about 3s settled). Under it the real yellow "Assistant" button appears; a cursor clicks it and the button grows until yellow fills the frame.
Sequential/interaction: yes, cursor clicks the Assistant button
Audio intent: calm; a click on the press
Audio-coupled idea: click
Transition mood: clean → Scene 3 (the button becomes the world)

### Scene 3 — The plan, the fall, the rescue — 13.75s (7.0 to 20.75)
The yellow contracts into the top bar's Assistant button and the real cockpit is there: top bar, status card (traffic light red, "Shortfall ahead", "In about 7 weeks you will be about €300 short."), the balance chart, the assistant panel on the right.
- 7.7 to 8.7: the owner types "We are closing for holidays from 1 to 21 June"; sent at 8.74.
- 9.29: plan card "Closure, 1 Jun – 21 Jun 2025" with "Yes, include it" / "Correct it".
- 10.93: cursor clicks "Yes, include it". The forecast line falls, the "Closed" band appears, the lowest projected balance counts from −€325.55 to −€15,728.42, the headline becomes "In about 7 weeks you will be about €16,000 short." "My plans" count goes to 1.
- 14.73: the chart gives way to the Options tab. Cards arrive one per beat and then hold as a set: E "Postpone payments + smaller credit line" (Recommended, −€7,061.74, On track, within the €15,000 credit line, cost €12.66), A "Credit line", B "Postpone the transfer to Zurich", C "Shorter closure".
- 17.47: cursor clicks "Approve" on E. Badges "Approved" and "SIMULATED". Lamp green, status "On track", headline "You are covered for the next 60 days." Lowest projected balance −€7,061.74, credit line €15,000.00.
- 20.3: the green lamp grows until green fills the frame.
Sequential/interaction: yes, typing, two clicks, four cards one by one (text held as a full set for about 3s)
Audio intent: the music opens up here; effects follow the hands
Audio-coupled idea: typing ticks, clicks, count-down, card drops, bell on green
Transition mood: clean → Scene 4 (the lamp becomes the world)

### Scene 4 — Entry frame again — 4.25s (20.75 to 25.0)
The green contracts into the green lamp of the same entry frame: same lockup, same positions, lamp now green. Text: "FinGuard", "Autonomous CFO for Pizzeria Amalfi", "Team WorksOnMyMachine · Collabothon 2026", plus the final line, held about 3s: "Warns you before the money runs out. AI explains. Code calculates. You decide."
Sequential/interaction: none
Audio intent: resolution; music fades under the held frame
Audio-coupled idea: final line settles near the 22.93 cue
Transition mood: end

**Music mood for this video:** cinematic, steady
**Audio summary:** a quiet entry, a click into the product, the track's stronger beats under the fall and the approval, one bell on green, and a fade under the returning entry frame.
