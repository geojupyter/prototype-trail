# GeoJupyter compass: tasks and reading for Daniel

Compiled 2026-10-20 from `compass/` at `c8559b1`, pulled the same day. The newest notes are the
2026-10-15 collaboration café, the 2026-10-13 community meeting, and the JupyterGIS syncs of
2026-09-22 and 2026-09-29. Older notes are included only where they concern Trail. GitHub was
checked the same day to identify "Matt's work" and to see what is already done. Items marked
*(suggestion)* are mine, not from the notes.

## Tasks

### 1. Consume Matt's JupyterGIS event feed from Trail

The 10-15 café ends with "First priority: get clone of Matt's work, see if you can consume the
events" (`compass/meeting-notes/20261015-collaboration-cafe/index.md:211`). It is also the first
prototype item on the [Trail roadmap](https://geojupyter.github.io/trail/): a tool that listens to
JupyterGIS events and saves them.

"Matt's work" is draft PR
[prototype-jupytergis-eventfeed#4](https://github.com/geojupyter/prototype-jupytergis-eventfeed/pull/4),
"Emit events for GUI operations", on branch `prototyping`. Matt added `@hodczak` as a reviewer on
10-13, "as FYI -- you don't need to review anything right now". His last push was on 10-20.

- Clone the repo and check out `prototyping`. It is a JupyterGIS fork. Build it with the
  [JupyterGIS development setup](https://jupytergis.readthedocs.io/en/latest/contributor_guide/development_setup.html),
  using micromamba as Matt recommends
  (`compass/meeting-notes/20261015-collaboration-cafe/index.md:63-64`, `:143-157`).
- Run the reference consumer, `examples/event_feed_consumer/consumer.py`. It subscribes to the
  Jupyter Server WebSocket at `/api/events/subscribe` with the server token. Open and close a
  `.jGIS` file; `document/v1` events should print.
- Add the subscription to Trail. `src/trail` has no WebSocket or `/api/events` client.
  `JupyterEvent` (`src/trail/event.py:503`) is a stub that nothing else references.

What the feed provides today:

- Two schemas. `https://schema.jupytergis.org/events/document/v1` has the verbs `opened` and
  `closed`. `.../layer/v1` has `added`, `updated` and `removed`, with `old` and `new` layer objects.
- Each event has `subject` (a client ID), `verb` and `object`. `object.document_path` is always
  present. It is `null` for ephemeral documents, such as `GISDocument()` with no path.
- Events carry no timestamp. Matt opened
  [jupyter_server#1710](https://github.com/jupyter-server/jupyter_server/issues/1710) on 10-17 to
  expose event metadata to subscribers. It is still open. Until it lands, Trail must stamp each
  event when it receives it.
- The socket carries every server event. Filter on the `https://schema.jupytergis.org/` prefix.
- Delivery failures are silent. Check the stream for the expected events; a missing error proves
  nothing.
- For the eventual review: in `layer_v1.yaml`, `new` says "Absent for 'deleted' verb", but the verb
  is `removed`.

### 2. Get `hodczak` into `prototype-trail` main in small PRs

The 10-15 café sets the working rules: work starts in
[geojupyter/prototype-trail](https://github.com/geojupyter/prototype-trail), "Everything in a PR,
PRs should be small as possible", and self-merging is fine
(`compass/meeting-notes/20261015-collaboration-cafe/index.md:51-52`). Pari starts in that repo now.

`prototype-trail` main is still at `881423f`, merged 10-01. `hodczak` is 51 commits ahead: 53
files, +15,110/−1,684. About 3,400 of those lines are the two plan files in `notes/`. If Pari
branches from main, Pari builds on the 10-01 code.

- Split it into a few PRs by area *(suggestion)*: `Collection`, `Select` and `Event`; markers and
  `trail init`; dirs, dirassets and sessions.
- Decide whether `notes/` belongs in main.
- [prototype-trail#2](https://github.com/geojupyter/prototype-trail/pull/2), a pre-commit.ci
  autoupdate, is open (last updated 10-05). Merge or close it.

### 3. Do modules 1–3 of the JupyterCon 2025 extension tutorial by 2026-10-31

Matt suggests modules 1–3 before November. Modules 2 and 3 matter most. If you finish by then, Matt
can answer questions (`compass/meeting-notes/20261015-collaboration-cafe/index.md:60-62`,
`:106-108`).

- [Tutorial site](https://jupytercon.github.io/jupytercon2025-developingextensions/) and
  [recording](https://www.youtube.com/watch?v=z-KZ6CjZjbM).
- Modules 1–3 are `04-materials/01-exploring-extensions.md`, `02-anatomy-of-extensions.md` and
  `03-debugging.md` in the [repo](https://github.com/jupytercon/jupytercon2025-developingextensions).
- Trail needs this. The initiative plans a Trail observer on the JupyterLab side and asks whether
  Trail should have a sidebar (Q6).

### 4. Give Pari a real trailfile *(suggestion)*

Pari's starting task is a mock trailfile with a simple graph of steps, and a UI prototype built on
it (`compass/meeting-notes/20261015-collaboration-cafe/index.md:45-47`). A mock trailfile was first
asked for on 07-08 (`compass/meeting-notes/20260708-collaboration-cafe/index.md:119`). The Trail
architecture page still lists the trailfile format as open. Your prototype already writes a
`.trail/` directory with a JSON state file and a JSONL event log. A sample from a real project,
plus the PR #4 schemas, keeps Pari's mock from inventing a second format.

### 5. Help pick the new collaboration café time

"collaboration cafe meeting, rescheduled — need to figure a new time"
(`compass/meeting-notes/20261015-collaboration-cafe/index.md:103-104`). The notes do not say where
this is decided. Most of the work happens on
[Zulip](https://jupyter.zulipchat.com/#narrow/channel/471314-geojupyter) (`:101`). Meetings are on
the [community calendar](https://geojupyter.org/calendar) (`:59`).

### 6. Bring `prototype-trail` in line with the team conventions

The 10-15 café lists the conventions
(`compass/meeting-notes/20261015-collaboration-cafe/index.md:52-56`, `:68-91`). Compared with
`hodczak`:

- Dependencies belong in `pyproject.toml` (`:68`). It already lists all of them, so
  `requirements.txt` and `requirements-dev.txt` are redundant. Delete both, and the
  `requirements-txt-fixer` hook.
- CI runs the tests on the minimum and maximum supported Python versions (`:88-90`). `test.yml`
  runs Python 3.12 only, on three OSes.
- Type checking runs in CI, not in pre-commit (`:72`, `:75`). The notes say "Pypy". Line 75 names
  mypy, so it is probably mypy *(speculation)*. Neither CI nor pre-commit type-checks today.
- Docstrings are Google style with argument descriptions (`:55`). API and CLI docs are generated
  from them with Sphinx (`:54`, `:80`). `src/trail` has no `Args:` sections.
- Ruff does linting and formatting (`:71`). `pyproject.toml` deliberately leaves out `ruff format`
  to keep expanded comprehensions. Raise it with Matt, since the team convention says otherwise.
- [prek](https://github.com/j178/prek) is "highly suggested" in place of pre-commit (`:69`).
- Prefer kwargs to positional args, often required kwargs. Name booleans clearly. Use early returns
  (`:76-78`).

### 7. Record Trail decisions in `geojupyter/trail`

Decisions live in [geojupyter/trail](https://github.com/geojupyter/trail)
(`compass/meeting-notes/20261015-collaboration-cafe/index.md:53`). Its `decisions/` holds only a
placeholder and a template. The architecture page lists three open questions: the trailfile
format, the event data model, and push vs pull tracking. Your prototype has working answers to the
first two. PR #4 implements pull.

The prototype differs from the
[Trail initiative](https://geojupyter.github.io/initiatives/initiatives/trail/) in four places.
Each needs a decision *(suggestion)*:

- The record lives in `.trail/`. The initiative uses `.geojupyter/`.
- The prototype tracks "assets". The initiative registers "resources" with resource UIDs.
- Replacing or deleting an event rewrites the JSONL log (`Events.__setitem__`, `__delitem__` and
  `clear()`, `src/trail/event.py:801-839`). The initiative says the record is append-only.
- There is no IPython `post_run_cell` hook. The initiative uses it to trigger reconciliation after
  notebook cells.

Roadmap upkeep *(suggestion)*: "Initialize a CLI" and "Track filesystem changes within a directory"
are unchecked. Filesystem tracking merged in
[prototype-trail#1](https://github.com/geojupyter/prototype-trail/pull/1) on 10-01, and the CLI
exists on `hodczak`. The roadmap lists `prototype-trail-eventlistener` as work in progress; that
repo is empty.

### 8. Look for gaps in the Jupyter geospatial ecosystem

"look at the existing geospatial universe in jupyter, see where the gaps are and try to fill them"
(`compass/meeting-notes/20261015-collaboration-cafe/index.md:92-99`). The notes name reproducible
research artifacts, closed tools (ArcGIS StoryMaps, Google Earth Engine), and user interviews.
This one is open-ended.

## Reading

1. **PR #4's event feed doc.**
   [events.md on `prototyping`](https://github.com/geojupyter/prototype-jupytergis-eventfeed/blob/prototyping/docs/contributor_guide/explanation/events.md),
   also as a [rendered preview](https://prototype-jupytergis-eventfeed--4.org.readthedocs.build/).
   It covers the architecture, event anatomy, pitfalls, and the rejected alternatives: joining the
   Yjs room, and watching the document on the server. Task 1 depends on it. Its further-reading
   links cover the server side, starting with
   [Jupyter Events](https://jupyter-events.readthedocs.io/en/latest/).

2. **Trail initiative 001.**
   [Rendered page](https://geojupyter.github.io/initiatives/initiatives/trail/), by Maryam
   Hosseini, last revised 2026-08-26. The proposal issue,
   [initiatives#34](https://github.com/geojupyter/initiatives/issues/34), is open. This is the
   agreed design, with open questions Q1–Q7. PR #4 takes the second option of Q2: a small, stable
   semantic event interface. It also changes JupyterGIS itself. The initiative's "How Trail records
   without continuously watching" section has a Trail-side observer and no JupyterGIS changes. The
   architecture page's "pull" answer agrees with PR #4. Record which one holds (task 7).

3. **Trail roadmap and architecture.** [geojupyter.github.io/trail](https://geojupyter.github.io/trail/),
   two short pages. The roadmap's prototype list is the backlog after task 1: CLI, filesystem
   tracking, tool switching, web GUI, JupyterGIS event feed and GUI interactions, an agent skill,
   notebook execution, JupyterDataConnect, and citation export.

4. **The 2026-07-08 café notes.** `compass/meeting-notes/20260708-collaboration-cafe/index.md:69-122`.
   The reference Trail use case (MTC transit access equity), the events each step should produce,
   and open questions on message format, a mock trailfile, and the MVP. Matt's action items there,
   a sequence diagram and a design fiction blog post (`:63-65`), have no visible result. A GitHub
   code search of the geojupyter org finds "design fiction" only in this note.

5. **The onboarding Google Doc.** Linked at
   `compass/meeting-notes/20261015-collaboration-cafe/index.md:39`. It holds the code-quality
   guidelines (`:56`) behind tasks 2 and 6.

6. **Diátaxis.** [diataxis.fr](https://diataxis.fr/). "Diataxis highly recommended, read it"
   (`compass/meeting-notes/20261015-collaboration-cafe/index.md:82-87`). The Trail docs already use
   its `explanation/` category, and PR #4 puts its doc under `contributor_guide/explanation/`.

7. **JupyterLab extension points.**
   [Commands, command palette, launcher, menus](https://jupyterlab.readthedocs.io/en/latest/extension/extension_points.html).
   Matt pointed to the extension docs for defining commands and launchers
   (`compass/meeting-notes/20261015-collaboration-cafe/index.md:133-142`, `:168-178`). Every menu
   item and button runs a command, and LLMs can see commands. The initiative names command signals
   as a possible event surface for GUI tools. Read after tutorial module 2.

8. **CNG 2026 talk.** [Slides](https://geojupyter.github.io/presentation-cng2026/), given 10-06
   (`compass/meeting-notes/20260922-jupytergis-sync/index.md:58`). Its
   [Trail history diagram](https://geojupyter.github.io/presentation-cng2026/assets/images/jupyter-trail-history-diagram.png)
   is the example for Pari's mock (`compass/meeting-notes/20261015-collaboration-cafe/index.md:47`).

9. **JupyterGIS state models.** `compass/meeting-notes/20260811-community/index.md:39-47`. Model,
   sharedModel, awareness, stateDB, uiState and settings, and how each persists. Matt planned a doc
   on this (`:47`). Relevant when Trail tracks JupyterGIS interactions beyond PR #4's events, such
   as a settled viewport.

10. **JupyterGIS PRs with provenance effects.** From
    `compass/meeting-notes/20260929-jupytergis-sync/index.md:53-71`:
    - [jupytergis#1789](https://github.com/geojupyter/jupytergis/pull/1789) stores layers in
      PostGIS. "Fold" saves the working copy to the database. A filesystem watcher cannot see
      that, so Trail needs an event for it *(suggestion)*.
    - [jupytergis#1894](https://github.com/geojupyter/jupytergis/pull/1894) saves edited layers as
      `.geojson` on the server. If the file lands in a tracked directory, Trail's watcher sees it.

11. **Background from the 10-15 café.**
    `compass/meeting-notes/20261015-collaboration-cafe/index.md:179-210`: renderers (OpenLayers vs
    deck.gl and MapLibre, projection support), CRDTs and jupyter-collaboration, awareness,
    GeoParquet vs GeoJSON, PMTiles and overviews. [anywidget](https://anywidget.dev/) was suggested
    to Pari for the Trail UI (`:49`).

## No action for Daniel

- 2026-10-13 community (`compass/meeting-notes/20261013-community/index.md`): deck.gl custom
  projections, the projection UI PR (Greg picks it up), interactive story layers. Matt wants a GIF
  of the interactive layers for FOSS4G on November 3 or 4.
- 2026-09-22 JupyterGIS sync action items
  (`compass/meeting-notes/20260922-jupytergis-sync/index.md:42-47`) belong to Matt, Greg and
  Martin.
- 2026-08-18 and 2026-08-25 JupyterGIS syncs: nothing on Trail.
