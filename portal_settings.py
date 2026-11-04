import os
import re


class Settings:
    def __init__(self):
        self.portal_login_enabled = True
        self.portal_username = os.getenv("PORTAL_USERNAME")
        self.portal_password_hash = os.getenv("PORTAL_PASSWORD_HASH")
        self.portal_session_secret = os.getenv("PORTAL_SESSION_SECRET")
        self.app_version = os.getenv("APP_VERSION", "recognition-uat-20261104-r1")
        self.local_http = os.getenv("ALLOW_INSECURE_LOCALHOST", "false").lower() == "true"
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self.app_version):
            raise RuntimeError("APP_VERSION must be a short ASCII version identifier")
        values = (self.portal_username, self.portal_password_hash, self.portal_session_secret)
        if any(values) and not all(values):
            raise RuntimeError("Configure all three PORTAL_USERNAME, PORTAL_PASSWORD_HASH and PORTAL_SESSION_SECRET")
        if self.portal_username and not re.fullmatch(r"[A-Za-z0-9._@-]{1,128}", self.portal_username):
            raise RuntimeError("PORTAL_USERNAME must be 1-128 ASCII letters, digits or ._@-")
        if self.portal_session_secret and len(self.portal_session_secret) < 32:
            raise RuntimeError("PORTAL_SESSION_SECRET must contain at least 32 characters")


settings = Settings()
