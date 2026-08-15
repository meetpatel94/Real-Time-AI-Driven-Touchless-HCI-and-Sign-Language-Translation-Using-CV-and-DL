from flask import Blueprint, render_template
from services.state_service import global_state

sentence_bp = Blueprint("sentence", __name__)

@sentence_bp.route("/sentence")
def sentence():
    global_state.update_state({"active_module": "sentence"})
    return render_template("sentence/index.html")