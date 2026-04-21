"""Sync module — requires [drive] and/or [telegram] optional extras."""

from pester.core.extras import make_optional_check

HAS_DRIVE, require_drive = make_optional_check(
    ["googleapiclient", "google_auth_oauthlib.flow"], "drive", label="Drive sync"
)
HAS_TELEGRAM, require_telegram = make_optional_check("telegram.ext", "telegram", label="Telegram")
