from flask import Blueprint, render_template
from services.state_service import global_state

translation_bp = Blueprint("translation", __name__)

@translation_bp.route("/translation")
def translation():
    global_state.update_state({"active_module": "translation"})
    return render_template("translation/index.html")