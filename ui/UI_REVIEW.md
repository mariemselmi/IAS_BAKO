# UI review: what makes an interface look AI-generated, and how the station page avoids it

Written 2026-11-23 for `connectivity_check.html`. Part 1 is the checklist (from design
write-ups on "AI slop" and vibe-coded interfaces). Part 2 is what industrial operator
screens do instead (ISA-101 high-performance HMI practice). Part 3 is the audit of the
old page against Part 1 and what was changed.

The sources are practitioner opinion, not measured studies. A single item does not prove
a page was generated; the combination does. The aim is a page that looks designed for
its job, not "not AI" for its own sake.

---

## Part 1. The slop checklist

### Colour and surface
| # | Tell | Why it reads as generated | Do instead |
|---|---|---|---|
| S1 | Purple or blue-to-purple gradients, gradient text | The default "AI look", from library defaults | Solid colours |
| S2 | One decorative brand accent used on everything (buttons, headings, pills, borders) | Colour stops meaning anything | Colour carries state only |
| S3 | Glow, neon text shadow, glassmorphism, blurred translucent panels | Decoration that signals "premium" without purpose | Flat surfaces, 1 px borders |
| S4 | Tinted translucent badges (`rgba(colour,.12)` fill + matching border) everywhere | Stock component-library badge | Plain text labels; fill only for alarms |
| S5 | Coloured bar on one side of a rounded card for alerts | Named tell in several lists | Full-row state, or a labelled tag |
| S6 | Heavy drop shadows on flat cards | Cards that pretend to float | No shadow, or 1 px |
| S7 | Low-contrast grey on grey text | Fails WCAG AA | 4.5:1 for body text |

### Shape and layout
| # | Tell | Why | Do instead |
|---|---|---|---|
| S8 | The same 12 px radius on every element | Default token, no hierarchy | Radius by role; small (2 to 4 px) for tools |
| S9 | Everything wrapped in a rounded card | Card soup, no grouping logic | Panels only for real groups; tables and rules elsewhere |
| S10 | Row of three equal "KPI" tiles | The three-card template | Values laid out where they are read, in a table or readout grid |
| S11 | Equal-width column grid regardless of importance | Symmetry by default | Size by importance: the task gets the most space |
| S12 | Pills and chips for every status | Visual noise, everything looks clickable | Text; colour only when abnormal |
| S13 | Many buttons of equal weight | No obvious next step | One primary action that changes with the state |

### Type and copy
| # | Tell | Why | Do instead |
|---|---|---|---|
| S14 | Wide letter-spacing on uppercase headings | Template "tech" styling | Sentence case, normal tracking |
| S15 | Condensed display font for decoration | Picked for mood, not legibility | One readable UI face, weight for hierarchy |
| S16 | Two-tone headings ("Battery **live**", "Signal **health**") | Common generated heading trick | Plain headings |
| S17 | Middle-dot separators everywhere ("a · b · c") | Generated-copy habit | Columns, labels, or commas |
| S18 | Default font with no reason | Strongest single tell per one guide | A face chosen for the context (here: offline, native, tabular figures) |
| S19 | Proportional digits in live numbers | Numbers jitter as they change | Tabular figures, units aligned |
| S20 | Vague or chatty copy, filler verbs | Says nothing specific | Short, specific instructions and results |
| S21 | Internal jargon shown to the user (codes with no explanation) | Written for the builder, not the operator | Plain names first, codes secondary |

### Icons and motion
| # | Tell | Why | Do instead |
|---|---|---|---|
| S22 | Emoji or Unicode glyphs as icons (✓ ! ✕ in circles) | Cheapest icon, inconsistent rendering | Words (PASS, FAIL) or one drawn icon set |
| S23 | Coloured dots as the only status signal | Colour-only meaning, unreadable for colour-blind users | Dot plus word, or word alone |
| S24 | Endless pulsing, spinning, fading | Motion without meaning | Motion only for a state change (activity LED, progress) |
| S25 | Transitions on everything | Feels sluggish in a tool | Instant updates; short transitions only on state change |

### Structure
| # | Tell | Why | Do instead |
|---|---|---|---|
| S26 | Everything visible at once, no hierarchy | Dashboard dump | The verdict first, the fix second, details on demand (tabs) |
| S27 | Missing states (disabled, focus, empty) | Unfinished | Every control has rest, hover, focus, disabled |
| S28 | Decorative visuals that carry no data | Filler | Every graphic shows real data |

---

## Part 2. What industrial operator screens do (ISA-101 high-performance HMI)

| # | Principle | Applied as |
|---|---|---|
| H1 | Neutral grey base; colour reserved for abnormal states and results | Grey UI. Green = pass, red = fail, amber = warning, blue = test running. Nothing else is coloured |
| H2 | Normal values are not coloured | Live readings and cell bars are grey; only an out-of-range value turns amber or red |
| H3 | The most important information is the largest | Full-width verdict banner: PASS or FAIL in 44 px |
| H4 | One clear next action | One primary button: Connect gateway, Start test, Re-test after fix |
| H5 | Results in a test-step table | Step, test, component, limit, measured, result; the standard end-of-line tester layout |
| H6 | Status bar for system state | Connection, bus rate, frames, receive errors, events, at the bottom |
| H7 | Schematic overview of the plant | Connection map: battery, CAN link, gateway, and the parts not yet monitored |
| H8 | Trends instead of raw numbers where timing matters | Bus timing lanes: every frame drawn on a 10 s timeline, gaps shaded |
| H9 | Keyboard operation | Space starts the test |
| H10 | Works offline on the plant network | No web fonts or external files; system fonts only |

---

## Part 3. Audit of the old page

| Old page element | Tell | Change |
|---|---|---|
| Mint accent on logo, buttons, flags, badges, headings | S2 | Removed. No brand accent; colour only for results |
| Barlow Condensed, uppercase headings with 0.1 to 0.24 em tracking | S14, S15 | System UI face, sentence case, normal tracking |
| Google Fonts link | H10 | Removed (also fails offline at the plant) |
| "Battery **live**", "Signal **health**", "CAN **feed**" | S16 | Plain headings |
| 12 px rounded cards around every block, 20 px pills | S8, S9, S12 | 2 to 4 px radius, panels only for real groups |
| Three equal KPI tiles | S10 | Readout grid inside the battery panel |
| Three equal columns | S11 | Test area gets the main column; supporting views at the side and in tabs |
| Translucent tinted verdict and badges | S4 | Solid verdict banner; plain text elsewhere |
| Fault cards with a red left border | S5 | "What to fix" list with a FAIL tag per row |
| ✓ and ! glyphs in coloured circles | S22 | Words: PASS, FAIL, WARN |
| Coloured dots in the signal table | S23 | Timing lanes with gap shading and text ("gap 4.2 s") |
| Green cells in the heatmap for normal values | H2 | Grey bars; colour only for deviation or out of range |
| Six top-bar buttons of equal weight | S13 | One primary action in the banner; source controls in the status bar |
| "K01 · CAN-B" tags on every check | S21, S17 | Component name column; codes kept small |
| Middle dots in labels | S17 | Removed |
| Raw CAN feed always visible | S26 | Moved to a tab |
| Proportional digits | S19 | Tabular figures everywhere |

---

## Sources

- Anti AI slop ban list, Laith0003 ux-skill wiki: https://github.com/Laith0003/ux-skill/wiki/Anti-AI-slop-ban-list
- 7 Signs a UI Has Been Vibe Coded, The Fountain Institute: https://www.thefountaininstitute.com/blog/signs-vibe-coded-ui
- How to fix the AI-generated look in your frontend, DEV: https://dev.to/alanwest/how-to-fix-the-ai-generated-look-in-your-frontend-1ahh
- AI purple problem, DEV: https://dev.to/jaainil/ai-purple-problem-make-your-ui-unmistakable-3ono
- How to de-slopify your designs, UX Collective: https://uxdesign.cc/how-to-de-slopify-your-designs-4c40c57c1dc9
- ISA-101 high performance HMI colour strategy: https://industrialmonitordirect.com/blogs/knowledgebase/isa-101-high-performance-hmi-design-principles-color-strategy
- Going Gray: a new HMI standard, Control.com: https://control.com/technical-articles/going-gray/
- High-performance HMIs, Control Engineering: https://www.controleng.com/high-performance-hmis-designs-to-improve-operator-effectiveness/
