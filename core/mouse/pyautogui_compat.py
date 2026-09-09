"""Headless-safe pyautogui import bridge.

Air Mouse/click/scroll actions need the real ``pyautogui`` package plus a
desktop session (an X11/Wayland ``DISPLAY``).  On servers, preview sandboxes
and CI boxes there is no display, so importing pyautogui directly raises
``KeyError: 'DISPLAY'`` at import time and prevents the whole application
(including unrelated modules such as Connect) from booting.

This bridge keeps the real pyautogui when a desktop session exists — desktop
behavior is byte-for-byte unchanged — and otherwise installs a small inert
fallback so the application still boots.  The fallback reports screen size as
1920x1080 and makes OS-level mouse calls no-ops; they are only ever invoked
while the existing Air Gesture toggle is enabled on a real desktop anyway.

Module consumers keep their existing code shape::

    from core.mouse.pyautogui_compat import pyautogui
    pyautogui.PAUSE = 0.0
    pyautogui.moveTo(...)
"""

import logging

logger = logging.getLogger("GestureForge")

_IMPORT_ERROR: Exception | None = None

try:  # Real desktop session (or at least an importable pyautogui).
    import pyautogui as _real_pyautogui  # type: ignore
except Exception as exc:  # pragma: no cover - depends on host environment
    _real_pyautogui = None
    _IMPORT_ERROR = exc


class _InertPyAutoGUI:
    """Safe stand-in used only when no desktop/display is available."""

    PAUSE = 0.0
    FAILSAFE = False

    SCREEN_WIDTH = 1920
    SCREEN_HEIGHT = 1080

    def size(self):
        return (self.SCREEN_WIDTH, self.SCREEN_HEIGHT)

    def moveTo(self, *args, **kwargs):  # noqa: N802 - mirrors pyautogui API
        self._warn_once()

    def click(self, *args, **kwargs):
        self._warn_once()

    def scroll(self, *args, **kwargs):
        self._warn_once()

    def hotkey(self, *args, **kwargs):
        self._warn_once()

    _logged = False

    @classmethod
    def _warn_once(cls):
        if not cls._logged:
            cls._logged = True
            logger.warning(
                "Desktop mouse control is unavailable (no display/session); "
                "OS-level mouse actions are disabled. "
                "Gesture recognition and Connect remain fully functional."
            )


if _real_pyautogui is not None:
    pyautogui = _real_pyautogui
else:  # pragma: no cover - depends on host environment
    pyautogui = _InertPyAutoGUI()
    logger.warning(
        "pyautogui is unavailable in this environment (%s). "
        "Falling back to an inert desktop-control stub; only OS mouse/scroll "
        "actions are affected.",
        _IMPORT_ERROR,
    )
