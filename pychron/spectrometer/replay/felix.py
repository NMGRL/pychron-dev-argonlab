import os 
import time

import numpy as np

from pychron.spectrometer.thermo.spectrometer.helix import HelixSpectrometer
from pychron.spectrometer.thermo.manager.helix import HelixSpectrometerManager
from pychron.spectrometer.replay.loader import load_dat 


SEGMENT_KEY = {"sniff": "sniffs", "baseline":"baselines", "signal":"signals"}

class ReplayHelixSpectrometer(HelixSpectrometer):
    replay_repo_dir = os.environ.get("PYCHRON_REPLAY_REPO_DIR", "")
    replay_uuid = os.environ.get("PYCHRON_REPLAY_UUID", "")

    _replay_kind = "signal"
    _replay_starttime = None 
    _replay_data = None 

    def set_replay_context(self, kind, starttime):
        self._replay_kind = kind 
        self._replay_starttime = starttime 

    def _get_replay_data(self):
        if self._replay_data is None:
            self._replay_data = load_dat(self.replay_repo_dir, self.replay_uuid)
        return self._replay_data 

    def _get_simulation_data(self):
        data = self._get_replay_data()
        segment = data.get(SEGMENT_KEY.get(self._replay_kind, "signals"), {})

        elapsed = 0.0
        if self._replay_starttime is not None: 
            elapsed = time.time() - self._replay_starttime 

        keys = []
        signals = []
        for det in self.detectors:
            entry = segment.get(det.name)
            if entry is None:
                continue 
            xs, ys = entry 
            if not xs:
                continue 
            keys.append(det.name)
            signals.append(float(np.interp(elapsed, xs, ys)))

        return keys, signals, None 

class ReplayHelixSpectrometerManager(HelixSpectrometerManager):
    spectrometer_klass = ReplayHelixSpectrometer
