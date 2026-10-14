# Web UI design

The teacher console (`gatemoe serve`, source in `web/`) follows the "Google for Education" design
reference: a near-white canvas, charcoal ink, one blue accent for interaction, green only for
success, pill-shaped controls, 24–28 px card radii and hairline borders instead of shadows.
Its information architecture follows the "Redesign of Google Classroom" case study (sections and a
feedback saver with auto-grading), adapted to an offline node with no accounts.

## Tokens (web/src/index.css, Tailwind v4 `@theme`)

| Token | Value | Use |
|---|---|---|
| canvas-white | `#f8f9fa` | page background |
| charcoal-ink | `#202124` | headings, primary text, selected toggle pills |
| graphite / slate | `#3c4043` / `#5f6368` | body text / secondary text |
| mist | `#dadce0` | borders, dividers |
| google-blue / -deep | `#1a73e8` / `#1967d2` | the one filled button per view, links, focus |
| classroom-green | `#188038` | success only (correct answers, published, reviewed) |
| tints | `#e8f0fe` `#e6f4ea` `#fef7e0` `#fce8e6` `#f3e8fd` | stat cards, chips, highlights |

Type: Google Sans is not redistributable, so Inter (bundled with `@fontsource-variable/inter`,
works offline) stands in. Sizes are named (`text-caption` 12, `text-body-sm` 14, `text-body` 16,
`text-subheading` 18, `text-heading-sm` 22, `text-heading` 28, `text-heading-lg` 48) with positive
letter-spacing at small sizes. `cn()` (web/src/lib/utils.ts) teaches tailwind-merge these names.

Spacing steps `8 16 24 32 40 48 80 88 120 216` are px and replace Tailwind's numeric utilities
for exactly those numbers (`p-8` = 8 px, `size-32` = 32 px); other numbers keep Tailwind's 4 px
scale (`p-4` = 16 px). Use an arbitrary value (`h-[192px]`) when neither fits.

Rules: every button is a pill; one filled (blue) button per view, outlined pills for the rest;
cards are white on the canvas with a 1 px mist border and no shadow; no gradients; modals use a
50 % charcoal scrim.

## Components

The shadcn/ui, Kibo UI, audio and PDF component registries cannot be reached from the build
sandbox, so the components were written by hand in `web/src/components/ui/` with the same APIs
(Radix primitives, `class-variance-authority`, `cn`), restyled to the tokens:

| Component | Equivalent | Notes |
|---|---|---|
| button, badge, card, dialog, dropdown-menu, select, tabs, toggle-group, tooltip, checkbox, slider, progress, popover, scroll-area, input, textarea, label, separator, sonner | shadcn/ui | dialog also has a right-side sheet (`side="right"`) |
| kanban | Kibo UI Kanban | `KanbanProvider / Board / Header / Cards / Card` on dnd-kit, `canMove` veto |
| audio-player | audio player blocks | play/seek/±10 s/speed/download, `onTime` for transcript highlighting |
| pdf-viewer | PDF viewer blocks | react-pdf 10 with pdf.js' legacy build and bundled worker (older school browsers, offline), loaded lazily |

## Screens

| Route | Screen | Case study / reference |
|---|---|---|
| `#/` | Lessons: three tinted stat cards, Kanban (Queued → Generating → Ready for review → Published) or list, search, archive | reference screenshot (board + stat cards) |
| `#/lesson/<id>` | Lesson: live stages + router bars while generating; tabs for notes, flashcards (flip), quiz (try / answer key), podcast (player + transcript), video (player + subtitles + scene plan), code, handout (PDF), simulations, sources, "how it was made" (routing, conditional computation, stage times, files) | |
| `#/sections`, `#/sections/archived` | Sections: cards with students / lessons / average grade; archive, restore, delete; roster | feature 1: sections; post once to several sections |
| `#/grading[/<post>][/<section>]` | Grading: post picker, section filter, submission table, feedback bank; grading sheet with feedback chips carrying points, "save & apply", personal comment, answers, computed grade | feature 2: feedback saver + auto-grader |
| `#/take/<post>` | Student view (no teacher navigation): pick your name, study (key points, podcast, video, handout), quiz, auto-marked result with explanations. The answer key is never sent to this page. | |
| `#/knowledge` | Kiwix snapshot layer, KDD delta layer, update commands | |
| `#/system` | memory, model in RAM, queue, temperature, specialists, configuration | |

## Working on the UI

```bash
cd web && npm install
python scripts/demo_ui.py --port 8000 &       # sample lessons, sections, grades; no models needed
npm run dev                                   # Vite on :5173, proxies /api and /zim to :8000
npm run build                                 # type-check + build into gatemoe/server/static (committed)
```

The built files are committed, so nodes never need Node.js. `scripts/demo_ui.py --video <mp4>`
shows a rendered video in the sample lesson.
