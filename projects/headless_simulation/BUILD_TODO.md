# Local build todo — pychron simulation mode

Target machine: local M5 Pro. Goal of this phase: stock `pyexperiment` launching against a sim root built from **Hornblende's real config**, with faked hardware, replayed data, and provably no writes outside a scratch root.

Details and citations in `FINDINGS.md`. This file is the ordered work list. Snippets are starting points — paths and names marked `<...>` need filling in, and anything tagged **VERIFY** is an assumption I have not confirmed against a live system.

---

## Phase 0 — Pull Hornblende artifacts

Build locally against Hornblende's real configuration, not a synthetic stand-in, so the later port only moves the machine variable.

### Resolved 2026-08-18 — target is Felix

Hornblende hosts two mass spectrometers under three env roots:

| Env root | Spectrometer | Selected by | `logs/` mtime |
|---|---|---|---|
| `~/Pychron3` | **Felix** (HelixSpectrometer) | fallback `env:` in `.pychron.0/environments.yaml` | **Aug 17 2026** |
| `~/Pychron-Jan` | Jan (ArgusSpectrometer) | `PYCHRON_ENV` in the Desktop `.command` | Aug 13 2026 |
| `~/Pychron` | — | `.pychron.1` | Aug 3 2026 |

**Target: Felix / `~/Pychron3`.** Most recently used, and the available blank data
(`Felix_blank260`, `bu-FC-F-57/58`) is Felix's. Simulating Jan instead would mean pulling a
Jan blank repo to replay from.

`APPLICATION_ID` 0, 1, and 2 are all taken on Hornblende, each with its own `pychron`
checkout (Aug 7 / Jun 25 / Jun 22). Both production shortcuts use `.pychron.0`.

Details on how the two launchers select their environment: `../aliases/pychron-jan-launchers.md`.

```bash
HB=hornblende@192.168.2.73
HBROOT=/Users/hornblende/Pychron3
SNAP=~/Documents/codes/pychron/lab-requests/Hornblende/headless_simulation/hornblende_snapshot
mkdir -p $SNAP

rsync -av --exclude='*.pyc' $HB:$HBROOT/setupfiles/   $SNAP/setupfiles/
rsync -av                    $HB:$HBROOT/preferences/ $SNAP/preferences/
rsync -av                    $HB:$HBROOT/scripts/     $SNAP/scripts/
```

- [ ] Confirm the snapshot's `initialization.xml` enables `HelixSpectrometer` (that is the
      Felix discriminator; Jan's enables `ArgusSpectrometer`). This file is also the base
      for the Phase 1 overlay — the `<hardware>` plugin block gets copied from it verbatim.
- [ ] Grab **Felix's** Desktop `.command` so the sim launcher inherits Felix's env values
      rather than Jan's

```bash
ssh $HB 'ls ~/Desktop/*.command; cat ~/Desktop/*.command' > $SNAP/launchers.txt
```

- [ ] Parity baseline — record now, compare at port time

```bash
ssh $HB 'sw_vers; python3 -V; ls ~/.pychron.0/pychron' > $SNAP/parity_hornblende.txt
# and the interpreter pychron actually runs under (read it out of the launcher script)
ssh $HB 'cat ~/Desktop/*.sh 2>/dev/null; cat ~/*.sh 2>/dev/null' >> $SNAP/parity_hornblende.txt
sw_vers > $SNAP/parity_local.txt
(cd ~/Documents/codes/pychron/codebase && uv run python -V && uv pip freeze) >> $SNAP/parity_local.txt
```

- [ ] DVC database dump

```bash
ssh $HB 'mysqldump -u <user> -p <dvc_dbname> | gzip' > $SNAP/dvc_dump.sql.gz
```

- [ ] One repo backup with real blanks (`Felix_blank260`, e.g. `bu-FC-F-57` / `bu-FC-F-58`)

```bash
# repository_root comes from the DVC connection pref; check the snapshot's preferences/
rsync -av $HB:<repository_root>/Felix_blank260 $SNAP/repos/
```

**Deliverable:** `hornblende_snapshot/` — read-only reference, never edited.

---

## Phase 1 — Sim root generator

Write this as a **script**; it runs three times (local, M4, Hornblende).

```bash
#!/bin/bash
# build_sim_root.sh <source-setup-root> <sim-root>
set -euo pipefail
SRC=$1
SIM=$2

mkdir -p "$SIM"
rsync -a --delete "$SRC/setupfiles/" "$SIM/setupfiles/"
rsync -a --delete "$SRC/scripts/"    "$SIM/scripts/"
mkdir -p "$SIM/preferences" "$SIM/logs" "$SIM/data" "$SIM/experiments" "$SIM/.appdata"

# overlay: files that differ from production
cp overlay/initialization.xml "$SIM/setupfiles/initialization.xml"
cp overlay/devices/*.cfg      "$SIM/setupfiles/devices/"
cp overlay/preferences/*.ini  "$SIM/preferences/"
```

`paths.build(root)` derives the whole tree from that one directory — `setupfiles` `paths.py:397`, `data` `:420`, `logs` `:344`, `.appdata` `:374`, `experiments` `:361`, `preferences` `:369`.

### Overlay: `initialization.xml`

The `<globals>` block is parsed by `Globals.build` (`globals.py:170`) via `launchers/helpers.py:647`.

```xml
<root>
    <globals>
        <communication_simulation>True</communication_simulation>
        <use_startup_tests>False</use_startup_tests>
        <ignore_connection_warnings>True</ignore_connection_warnings>
    </globals>
    <plugins>
        <general>
            <plugin enabled="true">Experiment</plugin>
            <plugin enabled="true">PyScript</plugin>
            <plugin enabled="true">DVC</plugin>
            <plugin enabled="true">LocalGit</plugin>
            <plugin enabled="true">ArArConstants</plugin>
            <plugin enabled="true">Entry</plugin>
            <plugin enabled="true">Pipeline</plugin>
        </general>
        <hardware>
            <!-- copy the spectrometer + extraction line plugin names from
                 Hornblende's production initialization.xml verbatim -->
        </hardware>
        <social>
        </social>
    </plugins>
</root>
```

- [ ] Copy the `<hardware>` block from the Hornblende snapshot rather than inventing plugin names
- [ ] **VERIFY** the exact plugin name strings against `PACKAGE_DICT` (`envisage/pychron_run.py:36-120`)

### Overlay: `setupfiles/devices/*.cfg`

Ports that don't resolve. Communicators default to `simulation = True` (`communicator.py:90`) and a missing serial port degrades silently (`serial_communicator.py:376`).

```ini
[Communications]
type = serial
port = /dev/null.sim
baudrate = 9600
```

Optional, per device, for byte-level realism (only NGX and pychron-valve protocols exist, `simulation/protocols.py:18,:90` — no Thermo):

```ini
[Communications]
backend = simulator
simulator_protocol = pychron_valve
simulator_seed = 1
```

### Overlay: `preferences/*.ini`

Files live under `<root>/preferences/`, one per plugin; the section header is the plugin's `preferences_path`.

```ini
[pychron.dvc.connection]
# MUST stay relative — an absolute or ~-prefixed value replaces the root-derived
# path AFTER paths.build(), which is the one real leak vector to production repos
# (dvc/dvc.py:2510 and :2525)
repository_root = data/.dvc/repositories
meta_repo_name = <MetaData>

[pychron.dvc.experiment]
use_dvc_persistence = False

[pychron.experiment]
use_db_persistence = False
use_xls_persistence = False
```

- [ ] Diff the snapshot's production prefs against this — specifically whether `repository_root` is absolute there
- [ ] **VERIFY** exact ini filenames (derived from each plugin's `_make_preferences_path`, `envisage/tasks/base_plugin.py:54`)

Persistence flags are belt-and-braces only; they do **not** stop all writes (Phase 5).

---

## Phase 2 — Local MySQL

MySQL is pychron's default kind (`dvc/dvc.py:2594`), so this matches production and the dump restores directly.

```bash
brew install mysql && brew services start mysql

mysql -u root <<'SQL'
CREATE DATABASE pychronsim CHARACTER SET utf8mb4;
CREATE USER 'pychronsim'@'localhost' IDENTIFIED BY 'pychronsim';
GRANT ALL PRIVILEGES ON pychronsim.* TO 'pychronsim'@'localhost';
FLUSH PRIVILEGES;
SQL

gunzip -c $SNAP/dvc_dump.sql.gz | mysql -u pychronsim -p pychronsim
```

Sanity checks before launching:

```sql
-- the only hard FK on AnalysisTbl
SELECT name FROM MassSpectrometerTbl;

-- must exist for the blank identifier, or dvc_persister.py:650 does
-- int(rs.identifier) -> uncaught ValueError (not a DatabaseError, so the
-- handler at :322 misses it)
SELECT identifier FROM IrradiationPositionTbl WHERE identifier LIKE 'bu-%';

SELECT name FROM RepositoryTbl WHERE name LIKE '%blank%';
```

Point the sim root at it:

```ini
[pychron.database]
# or configure via the connection-favorites UI; see
# database/tasks/connection_preferences.py:183 for the kind/host/path fields
```

- [ ] **VERIFY** the connection-favorites ini shape against `connection_preferences.py:315-360` — it is a yaml-in-preference structure, not flat keys

A database is required even with persistence off: `experiment_executor.py:751` does `dh.mainstore.precedence = 1` unconditionally, and `_check_first_aliquot` → `datahub.is_conflict` queries it for aliquots.

---

## Phase 3 — Launcher script

Shell skeleton taken from Hornblende's `~/Desktop/Pychron-Jan-startup.command` (copy in
`../aliases/pychron-jan-launchers.md`) — that is the only worked example of a launcher that
pins an explicit env. **Values must come from Felix's shortcut, not Jan's** (see Phase 0).
No code changes.

```bash
#!/bin/bash
# pychron_sim.command
export GITHUB_ORGANIZATION=<from Felix's .command>
export MassSpecDBVersion=<from Felix's .command>
export APPLICATION_ID=9              # 0,1,2 are taken on hornblende — recheck at deploy
export PYCHRON_APPNAME=pyexperiment
export PYCHRON_DATABASE_UPDATE=<from Felix's .command>
export PYCHRON_ALEMBIC_URL=<from Felix's .command>
export PYCHRON_ENV=$HOME/Pychron_sim
export PYCHRON_LOG_DIR=$HOME/Pychron_sim/logs
unset  PYCHRON_TELEMETRY_ENABLED

ROOT=$HOME/Documents/codes/pychron/codebase   # on hornblende: ~/.pychron.<id>/pychron
export PYTHONPATH=$ROOT
$ROOT/.venv/bin/python $ROOT/launchers/launcher.py
```

**Login stays on**, matching whatever Felix's production shortcut does — sim mode has to
behave like real pychron, and skipping the login dialog is a visible behavioral difference.
Consequences:

- The login dialog runs, so `dump_environments_file` runs. It writes to
  `~/.pychron.<APPLICATION_ID>/environments.yaml`, so the distinct `APPLICATION_ID` keeps it
  off production's file.
- The sim root must be listed in the sim's own `environments.yaml` for the dialog to offer
  it. Seed `~/.pychron.9/environments.yaml` with `env:` and `envs:` pointing at the sim root,
  and `~/.pychron.9/users.yaml` with a user, before first launch.
- `PYCHRON_ENV` is still set as a belt-and-braces pin (`user_login.py:148`).

Hornblende's Jan launcher also sets `PYCHRON_TIMER_PROBE=1`; add it if you want the same
timer instrumentation in sim runs.

Why each one:

| Var | Reason |
|---|---|
| `APPLICATION_ID` | `~/.pychron.<id>` is `mkdir`'d at *import* of `pychron.paths` (`paths.py:51`), before `build()` runs. Only redirect available. |
| `PYCHRON_ENV` | read by `Login.__init__` (`envisage/user_login.py:148`) to pick the environment |
| `PYCHRON_LOG_DIR` | else `m3_diagnostics` writes `~/Pychron/logs/m3_diagnostics.log` from a hard-coded candidate list (`m3_diagnostics.py:63`), installed unconditionally at `launcher.py:28` |
| telemetry unset | that recorder writes to `~/.pychron_telemetry/` off `Path.home()`, ignoring paths (`state_machines/controller.py:155`) |

`prepare_runtime_root` needs no special handling — `helpers.py:540` already calls it with
the `PYCHRON_ENV` root, so `ETSConfig.application_home` follows the sim root automatically.

---

## Phase 4 — First launch, no run

```bash
./pychron_sim.sh 2>&1 | tee /tmp/sim_first_launch.log
```

- [ ] Window comes up
- [ ] All plugins load — watch for the plugin-load `warning()` at `pychron_run.py:195`
- [ ] Spectrometer and extraction line managers resolve as services
- [ ] Open a queue with one blank in it
- [ ] Note anything that hangs, prompts, or errors — **do not run yet**

```bash
grep -iE "warning|error|critical|traceback" /tmp/sim_first_launch.log | head -50
grep -i "simulation" ~/Pychron_sim/logs/pychron.current.log | head
```

Baseline already verified locally: py3.12.13 arm64, PyQt5, executor + `automated_run` import cleanly (~36 s cold), `ExperimentQueue().load()` parses a real queue file.

---

## Phase 5 — Containment verification

Do this **before** the first analysis run, and repeat it verbatim on Hornblende.

```bash
# snapshot everything the sim run must NOT touch
# (BSD find has no -printf, so use stat)
snap() {
  find ~/Pychron ~/.pychron.0 ~/.enthought "$SNAP" <production repository_root> -type f 2>/dev/null \
    | xargs stat -f '%m %N' 2>/dev/null | sort > "$1"
}

snap /tmp/before.txt
# ... run one blank in the GUI ...
snap /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt   # expect: empty
```

Also confirm the DVC repo directories were untouched, since that's the leak vector:

```bash
cd <production repository_root>/Felix_blank260 && git status --porcelain && git log -1 --format=%H
```

Expected *inside* the sim root regardless of persistence flags — `_persister_action` (`automated_run.py:2103`) calls the base persister unconditionally, and `self.persister` is `Instance(..., ())` at `:178` so it is never None:

```
~/Pychron_sim/data/isotopes/<uu>/<id>.h5
~/Pychron_sim/.appdata/local_lab.db
~/Pychron_sim/logs/<runid>.log
~/Pychron_sim/.appdata/backup_recovery
~/Pychron_sim/experiments/rem/*.txt
~/Pychron_sim/.appdata/actuation_tracker.json
~/Pychron_sim/setupfiles/spectrometer/mftables/mftable.csv   # if a script peak_centers
```

(`py_peak_center` defaults `save=True`, `automated_run.py:944`.) Known harmless escapee: `/tmp/pychron_layout_debug.txt` (`base_tasks_application.py:237`).

---

## Phase 6 — Replay spectrometer

The only genuinely new code.

- [ ] **Blocked on:** how the spectrometer plugin selects the concrete class. The replay class should load from sim-root config, not by patching the Thermo class.

Standalone reader — prove it works before wiring anything in:

```python
import json, base64, struct

def load_dat(repo_dir, uuid):
    p = f"{repo_dir}/{uuid[:2]}/.data/{uuid[2:]}.dat.json"
    obj = json.load(open(p))
    out = {"signals": {}, "sniffs": {}, "baselines": {}}
    for kind in out:
        for e in obj.get(kind, []):
            blob = base64.b64decode(e["blob"])
            pts = [struct.unpack(">ff", blob[i:i+8]) for i in range(0, len(blob), 8)]
            xs, ys = zip(*pts) if pts else ((), ())
            # signals/sniffs carry isotope + detector; baselines carry detector only
            out[kind][e["detector"]] = (xs, ys)
    return out
```

Or via pychron, no database needed — `DVCAnalysis.__init__` (`dvc_analysis.py:96`) is filesystem-only:

```python
from pychron.paths import paths
paths.repository_dataset_dir = "<parent of the repo dir>"
from pychron.dvc.dvc_analysis import DVCAnalysis
a = DVCAnalysis(uuid, record_id, "Felix_blank260")
a.load_raw_data()
iso = a.isotopes["Ar40"]
iso.xs, iso.ys, iso.baseline.xs, iso.sniff.xs
```

`DVC.find_record` (`dvc.py:421`) does uuid lookup off the json, partial prefixes included.

Injection point — override `_get_simulation_data()`, consumed at `base_spectrometer.py:711`:

```python
class ReplayThermoSpectrometer(<ThermoSpectrometerClass>):
    def _get_simulation_data(self):
        # returns (keys, signals, t) keyed by DETECTOR, not isotope
        elapsed = time.time() - self._segment_start
        keys, signals = [], []
        for det, (xs, ys) in self._segment_data.items():
            keys.append(det)
            signals.append(float(np.interp(elapsed, xs, ys)))
        return keys, np.array(signals), None
```

Three constraints:

1. Return is **per-detector** (`H1`/`AX`/`CDD`), not per-isotope. Source blobs carry both; key off `detector`.
2. **Baselines are per detector only**, shared by every isotope on that detector.
3. **x is elapsed seconds within a segment, not wall clock** (`data_collector.py:263`). Sniff / signal / baseline each restart near x≈0 because every `_measure` call sets its own `starttime` (`automated_run.py:3113`). So track which segment is active and interpolate against that one — `ncounts` and `integration_time` need not match the source analysis.

- [ ] Confirm the sim measurement script's `activate_detectors` matches the source analysis's detector set

Not available from backups: extraction streams (`measured_response`, `setpoint_stream`, `cryo_response`) — `post_extraction_save` builds them at `dvc_persister.py:186` then overwrites `obj` at `:200` before writing.

---

## Phase 7 — First simulated blank

- [ ] Run one blank end to end
- [ ] Replayed signal renders correctly in the plot panel
- [ ] Re-run the Phase 5 containment diff
- [ ] Run IDs look real (aliquots assigned off the restored db)

---

## Phase 8 — Visual distinguishability

It looks exactly like production pychron, so make it unmistakable.

- [ ] Extend the existing `" (Simulation)"` suffix (`scan_manager.py:594`, `spectrometer_task.py:280`) to the window title and the experiment task

---

## Phase 9 — Deferred cleanup

- [ ] Cleanup, deferred: ~18 `Key binding "<name>" not found` lines on every launch
      (`Save`, `Close`, `Restart`, `Logger`, `Confirm Valve Actuation`, ...). Cosmetic log
      noise from `.appdata/key_bindings` not listing these actions; does not affect behavior.

---

## Open questions to resolve during the build

1. Spectrometer class selection mechanism — blocks Phase 6. Target class is the **Helix**
   (Felix), under `pychron/spectrometer/thermo/`; `_get_simulation_data` for the Thermo
   family is at `thermo/spectrometer/base.py:530`
2. ~~Which Hornblende environment root is live~~ — resolved 2026-08-18: `~/Pychron3` (Felix)
3. Whether production scripts run unmodified in the sim root. `analysis_type` is truncated at the first `_` in `spec.make_script_context()` (`spec.py:417`), so `blank_unknown` arrives as `"blank"` — which is what the NMGRL scripts branch on
4. Whether setupfiles should live in a git repo, making the Phase 1 sync a real sync step instead of rsync
