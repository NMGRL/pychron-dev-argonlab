#!/bin/bash
# pychron_sim.sh -- simulation launcher

SIM=$HOME/Documents/codes/pychron/codebase/projects/headless_simulation/Pychron_sim


export APPLICATION_ID=9
export PYCHRON_APPNAME=pyexperiment
export PYCHRON_ENV=$SIM 
export PYCHRON_USE_LOGIN=0
export PYCHRON_LOG_DIR=$SIM/logs
unset PYCHRON_TELEMETRY_ENABLED
export QT_API=pyqt5

ROOT=$HOME/Documents/codes/pychron/codebase
export PYTHONPATH=$ROOT
export PYCHRON_REPLAY_REPO_DIR=$HOME/Documents/codes/pychron/codebase/projects/headless_simulation/hornblende_snapshot/repos/Felix_blank260
export PYCHRON_REPLAY_UUID=6ba7b8e3-2e89-4425-900b-078c82a45837
cd $ROOT && uv run python $ROOT/launchers/launcher.py