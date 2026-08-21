# Visual Target Clicker

A configurable PySide6 desktop app that lets you visually select on-screen UI
elements (via drag-select screenshots), save them as reusable "targets", and
automatically click them whenever they appear — no hard-coded coordinates.

Built for **Windows**, using fixed-scale OpenCV template matching (see
*Known limitation* below).

## 1. Installation

```powershell
# from the project folder
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Notes on dependencies:
- `keyboard` (global hotkeys) sometimes needs the app to be run **as
  Administrator** on Windows to capture hotkeys reliably, especially if the
  target application (e.g. a game) is itself running elevated. If hotkeys
  silently don't fire, try running your terminal as Administrator.
- `pyautogui` needs no special setup on Windows.

## 2. Running

```powershell
python main.py
```

The app starts in a **safe state**: automation is disabled by default. You
must explicitly click **Start** (or press the configured hotkey) to begin
clicking.

## 3. Quick start

1. Click **Add Target**.
2. Drag a rectangle around the button/icon you want detected. Release to
   capture it; the overlay closes automatically.
3. In the configuration dialog that opens, name the target and adjust
   confidence threshold, click behavior, cooldown, etc. Click **OK**.
4. Repeat for as many targets as you need.
5. Click **▶ Start** to begin automated monitoring + clicking, or
   **👁 Preview Detection** to watch matches happen without any clicks (safe
   for tuning thresholds).
6. Use **🛑 EMERGENCY STOP** (button or global hotkey, default `F12`) to
   immediately halt everything.

Use **Test** on a selected target for a one-shot detection check with the
exact confidence score and matched coordinates — the safest way to tune a
threshold before turning on live clicking.

## 4. Known limitation — fixed-scale matching

Per project requirements, this app uses **fixed-scale template matching
only** (no multi-scale search). This means:

- A target will stop matching if Windows display scaling (DPI), monitor
  resolution, or the target application's window size/zoom changes after you
  captured the screenshot.
- If a target that used to work suddenly stops detecting, the fix is almost
  always to **re-capture** it ("Replace Screenshot…" in the target editor)
  under the current display conditions, rather than to hunt for a scaling
  bug.
- Keep display scaling/resolution consistent between capture time and run
  time for reliable results.

## 5. Project structure

```
visual_clicker/
├── main.py                    # entry point
├── requirements.txt
├── config/
│   ├── app_settings.json      # global app preferences (monitoring speed, hotkeys)
│   └── profiles/*.json        # one JSON file per profile, holds the target list
├── screenshots/                # template PNGs, one per target (named by target id)
├── logs/
│   └── app.log                 # rotating file log
├── models/
│   ├── target.py               # Target dataclass (detection/click/behavior settings + stats)
│   └── settings.py             # AppSettings dataclass
├── core/
│   ├── persistence.py          # ConfigManager: JSON load/save, screenshot mgmt, import/export
│   ├── template_matcher.py     # OpenCV matching + template cache
│   ├── detection_engine.py     # matching workflow: cooldown/priority/min-visible-duration logic
│   ├── click_controller.py     # PyAutoGUI click execution
│   └── automation_manager.py   # top-level orchestrator: owns targets, worker thread, stats
├── services/
│   ├── screen_capture.py       # mss wrapper, multi-monitor aware
│   ├── monitoring_worker.py    # QThread running the detect→click loop
│   ├── logging_service.py      # file logging + in-memory buffer for the GUI log widget
│   └── hotkey_service.py       # global hotkeys via the `keyboard` library
└── ui/
    ├── main_window.py          # top-level window, wires everything together
    ├── overlay.py               # fullscreen transparent drag-select capture overlay
    ├── target_editor.py         # per-target configuration dialog (3 tabs)
    ├── target_table.py          # target list table
    ├── test_dialog.py           # "Test Target" one-shot detection check
    ├── settings_dialog.py       # app-wide preferences dialog
    ├── log_widget.py            # color-coded live activity log
    └── preview_widget.py        # small screenshot thumbnail widget
```

### Architecture notes

- **GUI vs. automation logic are fully separated.** `MainWindow` only wires
  up widgets and delegates every action to `AutomationManager`.
  `DetectionEngine` and `TemplateMatcher` never import Qt.
- **The monitoring loop runs on its own `QThread`** (`MonitoringWorker`), so
  the GUI stays responsive. It communicates back to the GUI thread only via
  Qt signals (thread-safe by construction).
- **A `threading.Lock` guards the shared target list** since the GUI thread
  can add/edit/delete targets while the worker thread is iterating them for
  detection.
- **Per-target state** (cooldown, click count, min-visible-duration tracking,
  last confidence, etc.) lives directly on the `Target` dataclass and is
  persisted, so statistics and cooldowns survive an app restart correctly if
  you stop mid-session.
- **Screenshots are stored as separate PNG files**, named by target UUID, not
  embedded as base64 in the JSON — keeps config files small and diffable.

## 6. Import / Export

Use **Export Profile…** to bundle a profile's JSON config + all its
screenshot PNGs into a single `.zip`. **Import Profile…** unpacks that zip
into a new named profile, re-pointing screenshot paths to your local
`screenshots/` folder automatically.

## 8. Additional features (v2)

- **Multi-select editing**: select several rows in the table (Ctrl/Shift-click)
  and click **Edit** — a bulk editor opens where every field has its own
  "Apply" checkbox, so you can change just one setting (e.g. cooldown) across
  many targets without touching the rest. **Edit All…** does the same for
  every target regardless of table selection.
- **Multi-scale matching** (per-target toggle, Detection tab): tries the
  template at several sizes around 100% and keeps the best match. More
  forgiving of display-scaling/window-size drift than fixed-scale, at some
  extra CPU cost per cycle.
- **Multiple template samples per target** (Templates tab): add extra
  screenshots of the same UI element (different lighting/state); detection
  matches against all of them and uses whichever scores highest.
- **Exclusion zones**: mark regions where a match should never be
  acted on, even if the template matches there (useful for avoiding
  look-alike UI elsewhere on screen).
- **Condition system**: a target can be configured to only fire if another
  named target is *also* currently visible ("Only click if also visible" in
  the Detection tab) — enables simple "wait for both A and B" workflows.
- **Confidence history graph** (History tab): a sparkline of recent match
  confidence per target, with the threshold drawn as a reference line — an
  early warning that a target's detection is drifting before it fails
  outright.
- **On-screen match highlight**: a transparent, click-through overlay briefly
  flashes a box around each detected match, so you can see exactly where the
  engine is "looking" without digging through the log.
- **Undo Delete**: deleting a target soft-deletes it (screenshot kept) for a
  short trash window; click **Undo Delete** to restore the most recent one.
- **Record Mode**: capture several new targets back-to-back without
  reopening the picker each time — press Escape on the selection overlay to
  stop.
- **Adaptive interval** (Settings): after a configurable number of idle
  (no-detection) cycles, polling automatically backs off up to a max
  interval to save CPU, and snaps back to full speed the instant something
  is detected again.
- **Region-of-interest caching**: targets that share an identical search
  region (or all use full-screen search) are captured once per cycle and
  matched against, instead of once per target — cuts down redundant screen
  captures when you have many targets watching the same area.

## 9. Design decisions made without explicit spec

- **Priority/cooldown/eligibility resolution order**: enabled → not on
  cooldown → not at click limit → sorted by priority (desc) → configured
  order. This matches the spec's conflict-resolution list.
- **Minimum visible duration** is tracked as "first time seen this
  appearance" — if the target disappears even for one cycle, the timer
  resets, so a flickering target won't accidentally satisfy the duration
  requirement.
- **Emergency stop vs. Stop**: functionally identical (hard-stops the worker
  thread), but Emergency Stop always logs at `warning` level and is the only
  one wired to a global hotkey by default, per the "always accessible, never
  hidden" safety requirement.
- **`stop_after_click`** disables the target (rather than deleting it) so you
  can always re-enable it from the table without reconfiguring.
