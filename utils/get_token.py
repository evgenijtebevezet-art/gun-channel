"""
utils/get_token.py — запускается ЛОКАЛЬНО для получения YouTube OAuth токена.
После запуска скопируй содержимое token.json в GitHub Secret YOUTUBE_TOKEN_JSON.

Использование:
  pip install google-auth-oauthlib
  python utils/get_token.py path/to/client_secret.json
"""

import sys
import json
import time

def get_token(client_secret_path: str):
    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/drive.file",
    ]

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
        "expires_at": time.time() + 3600,
    }

    with open("token.json", "w") as f:
        json.dump(token_data, f, indent=2)

    print("\n✅ Токен сохранён в token.json")
    print("   Скопируй содержимое в GitHub Secret: YOUTUBE_TOKEN_JSON")
    print(f"\n   Значение:\n{json.dumps(token_data, indent=2)}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
    get_token(path)
