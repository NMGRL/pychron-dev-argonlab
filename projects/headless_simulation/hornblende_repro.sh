#!/bin/bash
  # hornblende_repro.sh -- Hornblende crash-repro launcher (timer-storm instrumentation)

  SIM=$HOME/Documents/codes/pychron/codebase/projects/headless_simulation/Pychron_sim

  export APPLICATION_ID=8
  export PYCHRON_APPNAME=pyexperiment
  export PYCHRON_ENV=$SIM
  export PYCHRON_USE_LOGIN=0
  export PYCHRON_LOG_DIR=$SIM/logs
  unset PYCHRON_TELEMETRY_ENABLED
  export QT_API=pyqt5

  ROOT=$HOME/Documents/codes/pychron/codebase
  export PYTHONPATH=$ROOT
  export PYCHRON_REPLAY_REPO_DIR=$ROOT/projects/headless_simulation/hornblende_snapshot/repos/Felix_blank260
  export PYCHRON_REPLAY_UUID=6ba7b8e3-2e89-4425-900b-078c82a45837

  export PYCHRON_TIMER_PROBE=1
  export PYCHRON_M3_EVENT_TRACE=1
  export PYCHRON_HEAP_DEBUG=1
  export MallocScribble=1
  export MallocPreScribble=1

  cd $ROOT && uv run python $ROOT/launchers/launcher.py