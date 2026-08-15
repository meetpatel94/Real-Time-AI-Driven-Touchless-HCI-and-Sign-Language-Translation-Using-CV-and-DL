from flask import Blueprint, render_template
from services.state_service import global_state

drawing_bp = Blueprint("drawing", __name__)

@drawing_bp.route("/drawing")
def drawing():
    global_state.update_state({"active_module": "drawing"})
    return render_template("drawing/index.html")