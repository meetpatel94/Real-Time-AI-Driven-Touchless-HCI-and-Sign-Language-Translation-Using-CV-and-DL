from flask import Blueprint, render_template
from services.state_service import global_state

studio_bp = Blueprint("studio", __name__)

@studio_bp.route("/sign-language-studio")
def sign_language_studio():
    global_state.update_state({"active_module": "studio"})
    return render_template("studio/index.html")