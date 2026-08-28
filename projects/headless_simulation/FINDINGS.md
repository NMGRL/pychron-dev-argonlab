# Pychron simulation mode — design & requirements

Codebase surveyed: `~/Documents/codes/pychron/codebase` @ `6ccadb441`. All `path:line` refs are relative to that root.

## 1. Scope (settled)

**Not** headless in the no-GUI sense. This is stock `pyexperiment` with a normal window, driven by a human, with three substitutions:

1. **Hardware faked** — no instrument, no valves, no extraction line.
2. **Data replayed** — intensities sourced from a backup of production analyses.
3. **Writes isolated** — nothing touches production data.

Target: build and prove it on the local M5 Pro, then deploy to Hornblende (M3). A true no-GUI mode is a possible later add-on; see §9.

Because the Qt event loop is live, everything that made a no-GUI run hard is out of scope: no dialog patching, no `invoke_in_main_thread` shim, no fake plot panel, no `WaitControl` workaround, no splash/window/startup-test bypass, no new application flavor. Recorded in §8 in case the no-GUI variant is revived.

## 2. Verified environment facts

- Local venv: python 3.12.13, **arm64**, PyQt5 (no PySide6). traits 7.1.0 / traitsui 8.0.0 / chaco 6.1.1 / enable 6.1.0.
- `from pychron.experiment.experiment_executor import ExperimentExecutor` and `AutomatedRun` both import cleanly (~36 s cold).
- `ExperimentQueue().load(txt)` parses `test/data/experiment.txt` (11 runs incl. the blank `bu-FD-J`) standalone; the only failure is `self.application.get_service(...)` at `experiment/queue/base_queue.py:610`.
- `~/Pychron` on this machine is a bare skeleton — `initialization.xml` enables only ArArConstants/DVC/Pipeline/Entry/PyScript/LocalGit (**no Experiment, Spectrometer, or ExtractionLine plugin**), and `setupfiles/spectrometer/configurations`, `setupfiles/extractionline`, `setupfiles/devices`, and all of `scripts/` are empty. The sim root is built fresh, not from this.
- `~/.pychron.0/` already holds a real `users.yaml` (NMGRL user list) and `environments.yaml`.

## 3. Launcher — no code required

No new `KLASS_MAP` entry, no new `pychron/applications/*.py`. A shell wrapper in the shape of `aliases/pychron-jan-launchers.md`:

| Var | Value | Why |
|---|---|---|
| `PYCHRON_APPNAME` | `pyexperiment` | stock app |
| `PYCHRON_ENV` | `<sim root>` | selects the sim setup tree |
| `PYCHRON_USE_LOGIN` | `false` | skips the modal login (`envisage/user_login.py:238`); needs `login.user`/`login.environment` non-empty |
| `APPLICATION_ID` | distinct value | **containment** — see §7 |
| `PYCHRON_LOG_DIR` | inside sim root | **containment** — see §7 |
| `PYCHRON_TELEMETRY_ENABLED` | unset | **containment** — see §7 |

Launch chain for reference: `launchers/launcher.py:28` (`m3_diagnostics.install_early()`) → `launchers/helpers.py:398 entry_point()` → `initialize_version()` → `pychron/applications/pyexperiment.py:28` → `envisage/pychron_run.py:303 launch()`.

## 4. Sim root = production setupfiles + overlay

`paths.build(root)` derives **everything** from one directory — `setupfiles` (`paths.py:397`), `data` (`:420`, incl. `data/isotopes` at `:434`), `logs` (`:344`), `.appdata` (`:374`), `experiments` (`:361`), `preferences` (`:369`). Setup dir and write dir are the same root, which is why the sim root must be separate rather than reading production setupfiles in place.

Setupfiles are **not** version-controlled by pychron — `paths.py:57`'s `build_repo` (`~/.pychron.<id>/updates`) is the *code* updater repo. Syncing production setupfiles into the sim root is rsync today; putting them in a git repo first would make it a real sync step.

**Overlay** (the only hand-maintained part) spans two directories:

- `setupfiles/initialization.xml` — add `<globals><communication_simulation>True</communication_simulation></globals>`, and the plugin set (Experiment, a spectrometer plugin, an extraction-line plugin, DVC + a git host, PyScript, ArArConstants, Entry).
- `setupfiles/devices/*.cfg` — ports that don't resolve, or explicit simulated backends (§5).
- `preferences/*.ini` — DVC connection pointing at the scratch db, and `repository_root` / `meta_repo_dirname` kept **relative** (§7).

## 5. Simulated hardware

`<communication_simulation>True</communication_simulation>` is parsed by `Globals.build` (`globals.py:170`) from the `<globals>` block via `launchers/helpers.py:647`. That single flag gets most of the way:

- valve actuation succeeds with no hardware — `extraction_line/switch_manager.py:912` (`if result is None and (globalv.communication_simulation or globalv.experiment_debug): result, changed = True, True`)
- spectrometer `test_connection()` returns True — `spectrometer/base_spectrometer.py:141`
- `get_intensities` falls through to `_get_simulation_data()` — `base_spectrometer.py:711`
- `_check_intensity_no_change` is disabled — `base_spectrometer.py:735`
- actuator commands coerce `None` → `True` — `hardware/actuators/__init__.py:38,61`

Independently, communicators default to `simulation = True` (`hardware/core/communicators/communicator.py:90`) and a missing serial port silently degrades to simulation (`serial_communicator.py:376`), so unreachable device configs are safe.

Also available if a specific device needs byte-level realism: the transport-adapter framework, `[Communications] backend = simulator|replay` per `.cfg` (`hardware/core/simulation/`, parsed at `communicator.py:153`). Protocols exist only for **NGX** and **pychron-valve** (`simulation/protocols.py:18, :90`) — nothing for Thermo/Qtegra. Not needed for this design; noted because it's the layer that would exercise comms/parsing (see §9).

Ready-made fakes if wanted: `hardware/actuators/dummy_gp_actuator.py:5` (real in-memory valve state, name-resolvable from config), `hardware/actuators/dummy_controller.py:5`.

## 6. Replay spectrometer — the one genuinely new component

### 6.1 Where the source data lives

Per analysis, raw signal is one git-tracked file that ships with a repo backup:

```
<repo>/<uuid[:2]>/.data/<uuid[2:]>.dat.json
```

(`dvc_persister.py:897`, path built by `analysis_path()` → `_analysis_path()` → `subdirize()`, `pychron/dvc/__init__.py:147/178`, `core/helpers/filetools.py:31`.)

Contents (`dvc_persister.py:731-758`):
- `signals` — one entry per isotope: `{"isotope": "Ar40", "detector": "H1", "blob": <b64>}`
- `sniffs` — same shape
- `baselines` — **one entry per detector only**: `{"detector": "H1", "blob": <b64>}`
- top-level `"format": ">ff"`, `"encoding": "base64"`

Blobs are `struct.pack(">ff", x, y)` per point — float32 pairs, 8 bytes/point (`processing/isotope.py:109`, `core/helpers/binpack.py:28`).

Sibling files hold fitted scalars only, not raw data: `intercepts/`, `blanks/`, `baselines/`, `icfactors/`. There is also an HDF5 copy at `data/isotopes/<uu>/<id>.h5`, but it stays on the acquisition machine and does not travel with the repo — use the json.

### 6.2 Reading it without a database

`DVCAnalysis.__init__` (`dvc/dvc_analysis.py:96`) touches only the filesystem. Set `paths.repository_dataset_dir` to the backup's parent, then:

```
DVCAnalysis(uuid, record_id, repo).load_raw_data()
```

yields `iso.xs/ys`, `iso.baseline.xs/ys`, `iso.sniff.xs/ys` (`dvc_analysis.py:287`, `processing/isotope.py:119`). Lighter path, skipping pychron's analysis objects: `dvc_load(analysis_path(..., modifier=".data"))` then `binpack.unpack(format_blob(blob), fmt=">ff")`.

`DVC.find_record` (`dvc.py:421`) does uuid lookup straight off the json, including partial-prefix matching, with no `self.db`.

### 6.3 The injection contract

Hook: `_get_simulation_data()`, consumed at `base_spectrometer.py:711`. Stock implementations return constants — `thermo/spectrometer/base.py:530` (8 detectors), `isotopx/.../ngx.py:504`, `pfeiffer/.../quadera.py:363`.

Three constraints on the implementation:

1. **Return value is per-detector, not per-isotope** — `(keys, signals, t)` where keys are `H1`/`AX`/`CDD`. Source blobs carry both `isotope` and `detector`; key off `detector`. The isotope↔detector mapping for the sim run comes from its measurement script (`activate_detectors`), not from the source analysis, so the two have to line up.
2. **Baselines are per detector only**, shared by every isotope on that detector — unlike signals and sniffs.
3. **x is elapsed seconds within a segment, not wall clock** (`data_collector.py:263`). Sniff, signal, and baseline each restart near x≈0 because every `_measure` call sets its own `starttime` (`automated_run.py:3113`). Replay = interpolate `ys` at the requested elapsed x per segment, so the sim run's `ncounts`/`integration_time` need not match the source analysis.

Before writing it, check how the spectrometer class is selected by the plugin, so the subclass can be swapped in from sim-root config rather than by patching the Thermo class.

Source material: `Felix_blank260`, e.g. `bu-FC-F-57` / `bu-FC-F-58` from the 2026-07-13 incident.

**Not available from backups:** extraction streams (`measured_response`, `setpoint_stream`, `cryo_response`). `post_extraction_save` builds them at `dvc_persister.py:186` then overwrites `obj` at `:200` before writing, so they have never been persisted — even though `dvc_analysis.py:197` reads them.

## 7. Containment

### 7.1 Persistence flags are not a containment mechanism

`_persister_action` (`automated_run.py:2103`) calls the base persister **unconditionally**, and `self.persister` is `Instance(AutomatedRunPersister, ())` at `:178` — it can never be None. So with `use_db_persistence`, `use_dvc_persistence`, and `use_xls_persistence` all False you still get, per run:

| Write | Path | Call site |
|---|---|---|
| HDF5 frame | `<root>/data/isotopes/<uu>/<id>.h5` | `insure_run` `:1602`, `pre_measurement_save` `:1760`, `build_tables` `:542/:690`, `get_data_writer` `:554/:725`, `writer_ctx` `:3132` |
| `local_lab.db` row | `<root>/.appdata/local_lab.db` | `persistence.py:270` (ungated) |
| per-run log | `<root>/logs/<runid>.log` | `experiment_executor.py:1623` |
| backup-recovery append | `<root>/.appdata/backup_recovery` | `:2542`, from `_make_run` `:2340` |
| queue rem/ex files | `<root>/experiments/rem/*.txt` | `:1843`, after every run |
| valve actuation counters | `<root>/.appdata/actuation_tracker.json` | `switch_manager.py:933`, every actuation |
| mftable rewrite | `<root>/setupfiles/spectrometer/mftables/mftable.csv` | `field_table.py:264`; **`py_peak_center` defaults `save=True`** (`automated_run.py:944`) |

All root-derived, so a scratch root contains them. `globalv.experiment_savedb` is dead code — never read anywhere.

### 7.2 What escapes the root

1. **`~/.pychron.<APPLICATION_ID or 0>`** — created by `mkdir` at *import* of `pychron.paths` (`paths.py:51`), before `build()` can run. Holds `users.yaml`, `environments.yaml`, `<appname>.active_env`, and the updater repo. `APPLICATION_ID` is the only redirect.
2. **`~/Pychron/logs/m3_diagnostics.log`** — hard-coded candidate list (`core/helpers/m3_diagnostics.py:63`), installed unconditionally at `launcher.py:28`. Override with `PYCHRON_LOG_DIR`.
3. **`~/.pychron_telemetry/logs/telemetry_<pid>.jsonl`** — off `Path.home()`, ignores paths entirely (`state_machines/controller.py:155`). Gated by `globalv.telemetry_enabled` / `PYCHRON_TELEMETRY_ENABLED`; leave unset.
4. **`ETSConfig.application_home`** — not set by `paths.build`; defaults to `~/.enthought/<appname>` unless you go through `prepare_runtime_root(root, appname=...)` (`install_runtime.py:138`, `environment/util.py:50`).
5. **`paths.repository_dataset_dir` and `paths.meta_root` are reassigned *after* `build()`** from DVC connection preferences — an absolute or `~`-prefixed `repository_root` / `meta_repo_dirname` replaces the root-derived value (`dvc/dvc.py:2510` and `:2525`). **This is the real leak vector to production DVC repos.** Hornblende's production prefs plausibly use absolute paths; audit them in the overlay.
6. `/tmp/pychron_layout_debug.txt` (`envisage/tasks/base_tasks_application.py:237`) — debug noise, harmless.

### 7.3 Scratch database

Needed even with persistence off: `experiment_executor.py:751` does `dh.mainstore.precedence = 1` unconditionally, and `_check_first_aliquot` → `datahub.is_conflict` queries it for aliquots.

**Plan: a local MySQL instance, restored from a production dump, with the sim root's DVC connection pointing at it.** MySQL is the default kind (`dvc/dvc.py:2594`) and matches production, so no dialect divergence, and a `mysqldump` restores directly. Identifiers, PIs, repos, and irradiation positions all come along, so run IDs come out looking real. Hornblende gets its own local instance later if needed.

Sqlite also works if a zero-dependency option is ever wanted (`database/core/database_adapter.py:289`, connection UI at `database/tasks/connection_preferences.py:183`, wired at `dvc/dvc.py:2493`), but there is no schema bootstrap — you'd use the `core/test_helpers.py:30 dvc_db_factory` pattern (`create_all(dvc_orm.Base.metadata)`) and restoring a production dump would need conversion (`dvc_database.py:236` points at a mysql2sqlite gist).

If a db is ever built from scratch rather than restored, the minimum is a `MassSpectrometerTbl` row (the only hard FK on `AnalysisTbl`) and an `IrradiationPositionTbl` row for the blank identifier — without the latter, `dvc_persister.py:650` does `int(rs.identifier)` and raises an uncaught `ValueError` (not a `DatabaseError`, so the handler at `:322` misses it). `dvc/seed.py:34 SeedDatabase.seed()` creates `bu-CC-N` / `ba-01-N` / `a-01-N`; the menu action that calls it is broken (`dvc/tasks/actions.py:302` imports a nonexistent `seed_database`).

## 8. Dropped from scope (was the hard part of a no-GUI run)

Recorded in case the no-GUI variant is revived. With a live event loop none of it applies:

- ~28 dialog / `open_progress` / livemodal call sites in `experiment_executor.py` (`:859`, `:865`, `:949`, `:2069`, `:2852`, `:2950`, `:3218`, `:3512`, …), patched at `loggable.py:159/176/179` and `core/progress.py:30`.
- `invoke_in_main_thread` shim (`core/ui/gui.py:53`) — needed by the plot-panel handshake (`automated_run.py:2372`) and the collector (`data_collector.py:184/447/656`).
- Fake plot panel — `automated_run.py:717` and `data_collector.py:362` deref it unguarded, and the latter sits outside the try/except.
- `WaitControl` / `WaitGroup` main-thread round-trip (`experiment_executor.py:2506`, `core/wait/wait_group.py:134`).
- Startup-test results view and first-run wizard modal (`envisage/tasks/base_tasks_application.py:102/124`); zero-window self-shutdown (`:364`).
- `globalv.experiment_debug` as a blanket bypass of `_pre_execute_check` (`:2967`) and `_check_managers` (`:2765`).

## 9. Open items

1. **Spectrometer class selection** — how the plugin picks the concrete class, so the replay subclass loads from sim-root config instead of a patch.
2. **Measurement/extraction scripts** — the sim root needs the four scripts (extraction, measurement, post_measurement, post_equilibration) for a blank. Production copies should work; `analysis_type` is truncated at the first `_` in `spec.make_script_context()` (`spec.py:417`), so `blank_unknown` arrives as `"blank"`, which is what the NMGRL scripts branch on.
3. **Visual distinguishability** — it "looks exactly like normal pychron," so it needs to be unmistakable. Pychron already appends `" (Simulation)"` to editor names (`scan_manager.py:594`, `spectrometer_task.py:280`); extend to the window title and experiment task.
4. **Setupfiles sync** — rsync now; git repo for setupfiles would make it a real sync step.
5. **Later: comms-layer coverage.** A replay spectrometer at `_get_simulation_data()` bypasses `ethernet_communicator` and the Thermo parser, so it cannot catch bugs like `SPECTROMETER_GETDATA_MALFORMED_REPLY` (an 11-token `GetData` reply). Covering that needs the transport layer instead: `RecordingTransportAdapter` exists (`simulation/adapters.py:114`) but is **not reachable from config** — `build_transport_adapter` (`simulation/factory.py:15`) dispatches only `real`/`replay`/`simulator`, with no `backend = record`.

## 10. M5 Pro → M4 / M3 portability

Far easier than Intel → M3. That was an ISA change (x86_64 → arm64): every compiled wheel, Rosetta vs native, PyQt/Qt binaries, HDF5/pytables, numpy BLAS. M3/M4/M5 are all arm64 with the same macOS ABI — an arm64 wheel built here runs there. The local venv is already native arm64 with the full Qt/chaco/traits stack importing cleanly.

What actually differs, in descending order of risk:

1. **Timing-sensitive races — the real caveat.** The known Hornblende failures (`QT_TIMER_DISPATCH_FAULT`, `SYNC_CANVAS_TIMER_CRASH`, `ANALYSIS_VIEW_WIDGET_UAF`) are thread-affinity and use-after-free races. A faster machine with different core counts changes the race windows. **A sim run passing on the M5 does not prove those are fixed on the M3.** Config, code, and the sim substrate port cleanly; race-condition *findings* do not.
2. **macOS version, not chip.** Qt/pyface behavior varies more by OS release than by silicon — the recent native-menu-bar and layout-discard fixes are macOS-behavior issues, not chip issues. The M5 machine likely runs a newer macOS than Hornblende.
3. **Wheel/python provenance.** Building the sim env against newer wheels or a newer python than Hornblende has is the likely divergence. Pin via `uv.lock`.
4. **Codebase skew.** Hornblende runs its own checkout at `~/.pychron.0/pychron`, separate from the local repo — practically a bigger risk than the hardware.

## 11. Bring-up plan

Ordered work list with commands lives in `BUILD_TODO.md`. This is the strategy.

### 11.1 Build locally against Hornblende's real config

The sim root is production setupfiles + overlay. If the local build uses synthetic setupfiles, the port has to absorb "different machine" and "different config" simultaneously. Instead, pull Hornblende's `setupfiles/`, `preferences/`, `scripts/`, a DVC dump, and one repo backup (`Felix_blank260`) down first, and build the local sim root from those. The port then moves only the machine variable, and the overlay gets validated against the config it actually has to overlay — plugin set, device names, valve names, script names, which is where the surprises live.

### 11.2 What ports is the procedure, not the root

On Hornblende the production setupfiles are already there. Deployment is: run the same generator script against them, apply the same overlay, restore the same dump. Write Phase 1 as a script — it runs three times.

### 11.3 Use the M4 as the intermediate hop

Three machines, and Hornblende is the only one carrying production data. M5 → M4 first shakes out the whole "does this move to another machine" class of failure — wheel provenance, python version, macOS differences, path assumptions — with nothing at risk. By the time Hornblende is touched, the only untested variables are its OS version and its existing environment.

### 11.4 Deployment mechanics on Hornblende

Keep the sim additions **purely additive and config-gated**: a new spectrometer class instantiated only when the sim root's config names it, inert when `communication_simulation` is off. Then updating `~/.pychron.0/pychron` doesn't change production behavior, and sim mode stays a mode rather than a fork. Update that checkout via git to a known commit, not a Finder copy — that is how the 2026-07-13 DVC incident started. A distinct `APPLICATION_ID` gives sim mode its own `~/.pychron.<id>`, so no collision with production's `~/.pychron.0`.

### 11.5 Order, with a gate before the first run

1. Environment parity check before moving anything — python version, key wheel versions, macOS version, vs. local. Cheap, and predicts most of what breaks.
2. Codebase update via git.
3. Generate the sim root from Hornblende's own setupfiles + overlay.
4. Local MySQL, restore the dump.
5. Launch, run nothing. Confirm it comes up.
6. **Containment check** — mtime snapshot across the production root, `~/.pychron.0`, and the DVC repo directories; run one blank; diff. Worth proving empirically rather than by reading §7, because the leak vector (absolute `repository_root` / `meta_repo_dirname` in the production prefs, `dvc.py:2510`) is exactly what Hornblende's config is likely to have and the local one isn't.
7. One blank, then repeated/long runs for the race bugs.

### 11.6 The diffs are the deliverable

End goal is a stable build for both M3 and M4, so the differences hit during steps 1–3 on each machine *are* the modernization backlog. Log them as they come up rather than fixing in place and forgetting.
