"""Connect: Flask-Sock WebSocket endpoint registration.

The endpoint is intentionally registered from the app factory with the shared
``Sock`` instance; the room relay itself lives in
:mod:`services.connect_room_service` and stays transport-agnostic.
"""

from config import Config
from services.connect_room_service import connect_room_service
from services.logging_service import logger


def register_connect_socket(sock) -> None:
    """Attach the /ws/connect WebSocket route to the given Flask-Sock object."""

    @sock.route(Config.CONNECT_WS_PATH)
    def connect_websocket(ws):
        client_ref = id(ws)
        logger.info(f"Connect WebSocket opened ({client_ref}).")
        try:
            while True:
                raw = ws.receive()
                if raw is None:
                    break
                connect_room_service.handle_message(ws, raw)
        except Exception as exc:
            logger.warning(f"Connect WebSocket error ({client_ref}): {exc}")
        finally:
            connect_room_service.disconnect(ws)
            logger.info(f"Connect WebSocket closed ({client_ref}).")
