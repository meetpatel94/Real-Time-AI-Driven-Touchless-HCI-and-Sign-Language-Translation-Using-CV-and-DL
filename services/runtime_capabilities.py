"""Runtime feature detection for local desktop and Vercel serverless deployments.

The original GestureForge runtime owns a physical camera, runs long-lived
MediaPipe/TensorFlow workers and can drive the desktop cursor.  Those are valid
local-desktop capabilities, but they are not available in a Vercel Python
function.  This module gives routes and the app factory one explicit, small
place to decide which capabilities may be started.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Dict


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Capabilities of the current process, not of a visitor's browser."""

    is_vercel: bool
    is_serverless: bool
    browser_camera_required: bool
    server_camera_available: bool
    server_ai_available: bool
    background_workers_available: bool
    system_control_available: bool
    websocket_available: bool
    persistent_memory_available: bool

    @property
    def deployment_label(self) -> str:
        return "Vercel serverless demo" if self.is_serverless else "Local desktop runtime"

    def to_client_config(self) -> Dict[str, object]:
        """Only non-sensitive feature flags intended for browser UI code."""
        return {
            "isServerless": self.is_serverless,
            "isVercel": self.is_vercel,
            "browserCameraRequired": self.browser_camera_required,
            "serverCameraAvailable": self.server_camera_available,
            "serverAiAvailable": self.server_ai_available,
            "backgroundWorkersAvailable": self.background_workers_available,
            "systemControlAvailable": self.system_control_available,
            "websocketAvailable": self.websocket_available,
            "persistentMemoryAvailable": self.persistent_memory_available,
            "deploymentLabel": self.deployment_label,
        }

    def to_template_config(self) -> Dict[str, object]:
        """Template-safe flags; no secrets or environment values are exposed."""
        return asdict(self) | {"deployment_label": self.deployment_label}


# Vercel sets VERCEL=1 for build and runtime.  DEPLOYMENT_TARGET is useful when
# reproducing the production-safe path locally (e.g. VERCEL=1 flask --app app).
_is_vercel = _truthy(os.getenv("VERCEL")) or os.getenv("DEPLOYMENT_TARGET", "").strip().lower() == "vercel"
_is_serverless = _is_vercel or _truthy(os.getenv("GESTUREFORGE_SERVERLESS"))

runtime_capabilities = RuntimeCapabilities(
    is_vercel=_is_vercel,
    is_serverless=_is_serverless,
    browser_camera_required=_is_serverless,
    server_camera_available=not _is_serverless,
    server_ai_available=not _is_serverless,
    background_workers_available=not _is_serverless,
    system_control_available=not _is_serverless,
    websocket_available=not _is_serverless,
    persistent_memory_available=not _is_serverless,
)
