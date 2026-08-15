from flask import Blueprint, render_template
from services.state_service import global_state

mouse_bp = Blueprint("mouse", __name__)

@mouse_bp.route("/mouse")
def mouse():
    global_state.update_state({"active_module": "mouse"})
    return render_template("mouse/index.html")