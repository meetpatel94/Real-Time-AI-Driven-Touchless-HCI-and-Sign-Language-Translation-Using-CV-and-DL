from flask import Blueprint, render_template
from services.state_service import global_state

overview_bp = Blueprint("overview", __name__)

@overview_bp.route("/overview")
def overview():
    global_state.update_state({"active_module": "overview"})
    return render_template("overview/index.html")