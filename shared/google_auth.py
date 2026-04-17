import time

import httpx

_cached_token: str | None = None
_token_expires_at: float = 0.0


def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    global _cached_token, _token_expires_at

    if _cached_token and time.time() < _token_expires_at:
        return _cached_token

    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Google token refresh failed: {resp.status_code} — {resp.text}")

    data = resp.json()
    _cached_token = data["access_token"]
    _token_expires_at = time.time() + min(data.get("expires_in", 3600) - 600, 3000)
    return _cached_token
