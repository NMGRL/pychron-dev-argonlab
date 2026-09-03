from pychron.spectrometer.tasks.thermo.base import ThermoSpectrometerPlugin
from pychron.spectrometer.replay.felix import ReplayHelixSpectrometerManager


class ReplayHelixSpectrometerPlugin(ThermoSpectrometerPlugin):
    id = "pychron.spectrometer.helix"
    spectrometer_manager_klass = ReplayHelixSpectrometerManager
    manager_name = "helix_spectrometer_manager"
    name = "ReplayHelixSpectrometer"