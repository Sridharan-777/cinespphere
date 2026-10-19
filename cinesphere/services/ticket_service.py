import base64
import io

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer


class TicketService:
    SALT = "cinesphere-admission-v1"

    @classmethod
    def _serializer(cls):
        return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=cls.SALT)

    @classmethod
    def token_for(cls, booking):
        return cls._serializer().dumps({"booking_id": str(booking["_id"]),
                                        "version": int(booking.get("admission_token_version", 1))})

    @classmethod
    def decode(cls, token):
        try:
            data = cls._serializer().loads(token)
            return data if data.get("booking_id") else None
        except BadSignature:
            return None

    @classmethod
    def qr_data_uri(cls, token):
        """Create the QR locally; never disclose ticket credentials to a third party."""
        try:
            import qrcode
            image = qrcode.make(token)
            output = io.BytesIO()
            image.save(output, format="PNG")
            return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        except Exception:
            return None
