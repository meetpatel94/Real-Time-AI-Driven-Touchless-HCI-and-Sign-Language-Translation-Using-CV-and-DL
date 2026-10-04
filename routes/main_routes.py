"""Top-level navigation and deployment health routes."""

from flask import Blueprint, jsonify, redirect, url_for

from services.runtime_capabilities import runtime_capabilities

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return redirect(url_for("overview.overview"))


# Friendly aliases keep common deployment checks and shared links from landing
# on a 404 while preserving the project's existing destination pages.
@main_bp.route("/dashboard")
def dashboard_alias():
    return redirect(url_for("overview.overview"))


@main_bp.route("/dataset")
@main_bp.route("/model-training")
def alphabet_alias():
    return redirect(url_for("alphabet.alphabet"))


@main_bp.route("/live-recognition")
def live_recognition_alias():
    return redirect(url_for("recognition.recognition"))


@main_bp.route("/history")
@main_bp.route("/analytics")
def analytics_alias():
    return redirect(url_for("custom_gestures.custom_gestures_page"))


@main_bp.route("/settings")
@main_bp.route("/about")
def overview_alias():
    return redirect(url_for("overview.overview"))


@main_bp.route("/api/health")
def health():
    """Small unauthenticated health check for Vercel and deployment checks."""
    return jsonify({
        "ok": True,
        "service": "gestureforge-ai",
        "deployment": runtime_capabilities.deployment_label,
        "runtime": runtime_capabilities.to_client_config(),
    })
