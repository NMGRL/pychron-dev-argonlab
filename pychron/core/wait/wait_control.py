# ===============================================================================
# Copyright 2013 Jake Ross
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ===============================================================================
"""WaitControl: TraitsUI façade for a single wait.

Architecture (rewritten 2026):
    WaitState  -- pure-Python timing truth (no Qt). Lives in wait_state.py.
                  Experiment thread blocks on state.wait(); UI never blocks
                  experiment progress.
    WaitControl -- HasTraits façade. Owns a WaitState plus a Qt polling timer
                  that mirrors snapshots into Traits so all existing
                  ``object.wait_control.<trait>`` UI bindings keep working.

The polling timer is purely cosmetic. If the main Qt event loop is busy,
the displayed countdown lags briefly; the experiment thread is unaffected
because it is blocking on a threading.Event with timeout, not on Qt.
"""

# ============= standard library imports ========================
from threading import Thread, current_thread
from typing import Any, Callable, Optional

# ============= enthought library imports =======================
from pyface.qt.QtCore import QTimer
from pyface.qt.QtWidgets import QApplication
from pyface.ui_traits import PyfaceColor
from traits.api import Bool, Button, Event as TEvent, Float, Int, Property, Str

# ============= local library imports  ==========================
from pychron.core.helpers.ctx_managers import no_update
from pychron.core.ui.gui import invoke_in_main_thread
from pychron.core.wait.wait_state import (
    CANCELED,
    COMPLETED,
    CONTINUED,
    IDLE,
    RUNNING,
    WaitState,
    WaitSnapshot,
)
from pychron.loggable import Loggable

_POLL_INTERVAL_MS = 100


class WaitControl(Loggable):
    page_name = Str("Wait")
    message = Str("")
    message_color = PyfaceColor("black")
    message_bgcolor = PyfaceColor("#eaebbc")

    high = Int(auto_set=False, enter_set=True)
    duration = Float(10)

    current_time = Float
    current_display_time = Property(depends_on="current_time")

    auto_start = Bool(False)
    continue_button = Button("Continue")
    pause_button = TEvent
    pause_label = Property(depends_on="_paused")
    status = Str("idle")

    _paused = Bool
    _no_update = False
    _state: Optional[WaitState] = None
    _poll_timer: Optional[QTimer] = None
    _on_finished: Optional[Callable[[], None]] = None
    _wait_thread: Optional[Thread] = None

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self._state = WaitState(page_name=self.page_name)
        self.reset()
        if self.auto_start:
            self.start(block=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> WaitState:
        """Direct access to the underlying WaitState (for WaitGroup)."""
        assert self._state is not None
        return self._state

    def start(
        self,
        block: bool = True,
        duration: Optional[float] = None,
        message: Optional[str] = None,
        paused: bool = False,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        """Start a wait.

        block=True is no longer supported; callers must use
        WaitGroup.start_wait or pass on_finished and let the wait run
        asynchronously. Either is safe from any thread.
        """
        if block:
            raise RuntimeError(
                "WaitControl.start(block=True) is no longer supported; "
                "use WaitGroup.start_wait or pass on_finished"
            )

        state = self.state
        if duration is not None:
            self.duration = duration
        eff_duration = float(duration) if duration is not None else float(self.duration)
        eff_message = message if message is not None else self.message

        self.debug(
            "wait_control start page={} duration={} message={} paused={} thread={}".format(
                self.page_name,
                eff_duration,
                eff_message,
                paused,
                current_thread().name,
            )
        )

        state.page_name = self.page_name
        state.start(eff_duration, eff_message)
        if paused:
            state.request_pause(True)

        # Reset display traits and start polling on the main thread.
        invoke_in_main_thread(self._begin_view, eff_duration, eff_message, paused)

        # If a callback is requested, run the blocking wait on a helper
        # thread so this method returns immediately.
        self._on_finished = on_finished
        if on_finished is not None:
            self._wait_thread = Thread(
                target=self._await_and_callback,
                name="WaitControl-{}".format(self.page_name),
                daemon=True,
            )
            self._wait_thread.start()

    def stop(self) -> None:
        """Cancel any running wait. Safe from any thread."""
        state = self.state
        outcome_before = state.outcome
        self.debug(
            "wait_control stop page={} prior_status={} thread={}".format(
                self.page_name, outcome_before, current_thread().name
            )
        )
        state.request_cancel()
        # Stop the poll timer promptly instead of waiting for the next _poll
        # tick to notice cancellation. That tick never comes if the main thread
        # is starved -- the QTimerInfoList::activateTimers() livelock that hung
        # the app -- so relying on it left poll timers running forever.
        invoke_in_main_thread(self._stop_polling)
        # Synchronously mirror final state into traits so callers that
        # immediately read .status / .current_time see correct values.
        self._sync_from_state()
        if self.current_time > 1:
            self.set_message("Stopped", color="red")

    def continue_wait(self) -> None:
        """Resolve the wait as 'continued'. Safe from any thread."""
        self.debug(
            "wait_control continue page={} thread={}".format(
                self.page_name, current_thread().name
            )
        )
        self.state.request_continue()
        # Mirror remaining=0 / status="continued" synchronously so callers
        # don't have to wait for the polling timer to tick.
        self.current_time = 0
        self._sync_from_state()

    def reset(self) -> None:
        with no_update(self, fire_update_needed=False):
            self.trait_set(
                high=int(self.duration),
                current_time=self.duration,
                status="idle",
                _paused=False,
            )

    def pause(self) -> None:
        self.state.request_pause(True)
        self._paused = True

    def is_active(self) -> bool:
        return self.state.is_running()

    def is_canceled(self) -> bool:
        return self.state.outcome in (CANCELED, "stopped") or self.status in (
            "canceled",
            "stopped",
        )

    def is_continued(self) -> bool:
        return self.state.outcome == CONTINUED

    def set_message(
        self,
        message: str,
        *,
        color: Optional[str] = None,
        bgcolor: Optional[str] = None,
        wait: bool = True,
    ) -> None:
        traits: dict[str, Any] = {"message": message}
        if color is not None:
            traits["message_color"] = color
        if bgcolor is not None:
            traits["message_bgcolor"] = bgcolor
        self.trait_set(**traits)
        self.state.set_message(message)

    def set_remaining_time(self, remaining: float, *, wait: bool = False) -> None:
        self.state.set_remaining(remaining)
        self.trait_set(current_time=remaining)

    # ------------------------------------------------------------------
    # Polling timer (mirrors WaitState into display traits)
    # ------------------------------------------------------------------

    def _begin_view(self, duration: float, message: str, paused: bool) -> None:
        """Reset display traits and start polling. Main thread only.

        Called via invoke_in_main_thread; safe even if the main thread is
        slow because state.wait() does not depend on this completing.
        """
        with no_update(self, fire_update_needed=False):
            self.trait_set(
                high=int(duration),
                current_time=duration,
                status="running",
                _paused=paused,
                message=message,
                message_color="black",
                message_bgcolor="#eaebbc",
            )
        self._ensure_polling()

    def _ensure_polling(self) -> None:
        """Create (if needed) and start the polling QTimer. Main thread only."""
        timer = self._poll_timer
        if timer is None:
            app = QApplication.instance()
            timer = QTimer(app) if app is not None else QTimer()
            timer.setInterval(_POLL_INTERVAL_MS)
            timer.timeout.connect(self._poll)
            self._poll_timer = timer
        if not timer.isActive():
            timer.start()

    def _stop_polling(self) -> None:
        timer = self._poll_timer
        if timer is not None and timer.isActive():
            timer.stop()

    def dispose(self) -> None:
        """Stop and destroy the polling timer. Safe from any thread.

        Call when the control is discarded so its QTimer does not linger as a
        child of the QApplication. Without this, every WaitControl ever created
        leaks a 100ms QTimer into the app child list; enough of them saturate
        QTimerInfoList::activateTimers() on the main thread and starve the Qt
        event loop -- the observed spinning-wheel hang.
        """
        invoke_in_main_thread(self._dispose_polling)

    def _dispose_polling(self) -> None:
        """Stop, disconnect and delete the poll timer. Main thread only."""
        timer = self._poll_timer
        self._poll_timer = None
        if timer is not None:
            try:
                timer.stop()
                timer.timeout.disconnect(self._poll)
            except (TypeError, RuntimeError):
                # already disconnected or C++ side gone
                pass
            timer.deleteLater()

    def _poll(self) -> None:
        """Tick: read snapshot, update display traits, stop when resolved."""
        snap = self.state.snapshot()
        self._sync_from_snapshot(snap)
        if snap.outcome != RUNNING:
            self._stop_polling()

    def _sync_from_state(self) -> None:
        self._sync_from_snapshot(self.state.snapshot())

    def _sync_from_snapshot(self, snap: WaitSnapshot) -> None:
        # Map state outcomes back to legacy status strings for any callers
        # that still inspect .status directly.
        status_map = {
            IDLE: "idle",
            RUNNING: "running",
            COMPLETED: "completed",
            CONTINUED: "continued",
            CANCELED: "canceled",
        }
        traits: dict[str, Any] = {
            "current_time": snap.remaining,
            "_paused": snap.paused,
            "status": status_map.get(snap.outcome, snap.outcome),
        }
        if snap.message != self.message:
            traits["message"] = snap.message
        with no_update(self, fire_update_needed=False):
            self.trait_set(**traits)

    # ------------------------------------------------------------------
    # Background helpers
    # ------------------------------------------------------------------

    def _await_and_callback(self) -> None:
        try:
            self.state.wait()
        finally:
            cb = self._on_finished
            self._on_finished = None
            if cb is not None:
                try:
                    cb()
                except Exception:
                    self.debug("on_finished callback raised", exc_info=True)

    # ------------------------------------------------------------------
    # Display helpers / button handlers
    # ------------------------------------------------------------------

    def _get_current_display_time(self) -> str:
        return "{:03d}".format(int(self.current_time))

    def _get_pause_label(self) -> str:
        return "Unpause" if self._paused else "Pause"

    def _pause_button_fired(self) -> None:
        new = not self._paused
        self.state.request_pause(new)
        self._paused = new

    def _continue_button_fired(self) -> None:
        self.continue_wait()

    def _high_changed(self, v: int) -> None:
        if self._no_update:
            return
        self.duration = v
        self.set_remaining_time(v)


# ============= EOF =============================================
