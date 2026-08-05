# ===============================================================================
# Reentrant-timer probe (observation only; installs nothing by default).
#
# pyface BaseTimer.perform() runs the user callback and then calls stop(),
# which removes the timer from BaseTimer._active_timers -- often its only
# strong reference.  PyQt then releases the bound method, the refcount hits
# zero, and ~sipQTimer runs while Qt is still inside QTimer::timerEvent for
# that very QTimer.  notifyInternal2 writes into the freed block as it
# unwinds (observed 2026-08-04: str w8,[x21,#8] into r-x __OBJC_RO -> SIGBUS).
#
# This module logs which callback was running when a timer destroyed itself
# inside its own dispatch.  Hooks BaseTimer.perform only, so it sees every
# creation path: do_after, do_later, direct CallbackTimer.single_shot, and
# traitsui editors.  Opt-in via PYCHRON_TIMER_PROBE=1.
# ===============================================================================
from __future__ import annotations

import logging
import os
import threading
import weakref

_log = logging.getLogger("pychron.m3_diag")

_probe_state = threading.local()
_probe_attached = weakref.WeakSet()
_probe_clear_pending = False
_probe_qtimer = None
_PROBE_INSTALLED = False


def _clear_last_perform():
    global _probe_clear_pending
    _probe_state.last_id = None
    _probe_clear_pending = False


def _report_timer_death(name, own_id):
    try:
        if getattr(_probe_state, "last_id", None) == own_id:
            _log.error("REENTRANT-OWN timer death (THE BUG): %s", name)
        else:
            _log.debug("timer released benignly: %s", name)
    except Exception:
        pass


def install_timer_probe() -> None:
    global _PROBE_INSTALLED, _probe_qtimer
    if _PROBE_INSTALLED:
        return
    if os.environ.get("PYCHRON_TIMER_PROBE", "0") != "1":
        _log.info("timer probe disabled (set PYCHRON_TIMER_PROBE=1 to enable)")
        return
    try:
        from pyface.timer.i_timer import BaseTimer
        from pyface.qt.QtCore import QTimer
    except Exception as e:
        _log.error("timer probe: import failed: %s", e)
        return

    _probe_qtimer = QTimer
    _orig_perform = BaseTimer.perform

    def _patched_perform(self):
        global _probe_clear_pending
        try:
            tid = id(self._timer)
            _probe_state.last_id = tid
            if self not in _probe_attached:
                _probe_attached.add(self)
                cb = getattr(self, "callback", None)
                name = getattr(cb, "__qualname__", None)
                if not name:
                    name = "%s(no-callback)" % type(self).__name__
                weakref.finalize(self, _report_timer_death, name, tid)
        except Exception as e:
            _log.warning("timer probe attach failed: %s", e)
        try:
            return _orig_perform(self)
        finally:
            if not _probe_clear_pending:
                _probe_clear_pending = True
                try:
                    _probe_qtimer.singleShot(0, _clear_last_perform)
                except Exception:
                    _probe_clear_pending = False

    BaseTimer.perform = _patched_perform
    _PROBE_INSTALLED = True
    _log.info("timer probe installed on BaseTimer.perform")
PROBEEOF
