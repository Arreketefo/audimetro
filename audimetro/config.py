from pydantic_settings import BaseSettings
from radar_core.config import radar_settings_config


class Settings(BaseSettings):
    model_config = radar_settings_config("AUDIMETRO_")

    db_path: str = "/data/audimetro.db"
    # El tick corre cada poll_interval_s pero solo captura/envía una vez al
    # día (>= digest_hour); el resto de ticks solo refrescan el healthcheck.
    poll_interval_s: int = 900
    digest_hour: int = 8              # hora UTC del envío diario
    min_delta_views: int = 10         # delta mínimo para salir en "movimiento"
    top_movers: int = 10              # tope de piezas en "movimiento"
    snapshot_retention_days: int = 180

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # YouTube: stats públicas del canal, solo API key (sin OAuth).
    youtube_api_key: str | None = None
    youtube_handle: str | None = None     # p.ej. @micanal
    youtube_max_videos: int = 300

    # YouTube Analytics (OAuth, opcional): min vistos, duración media y subs
    # por vídeo. El refresh token lo imprime `audimetro auth youtube` y no
    # rota (app OAuth en producción): vive solo en el .env.
    yt_oauth_client_id: str | None = None
    yt_oauth_client_secret: str | None = None
    yt_refresh_token: str | None = None
    yt_redirect_uri: str = "http://localhost:8400/callback"

    # Instagram (API with Instagram Login): el token largo de .env es la
    # SEMILLA; el vigente (auto-refrescado cada tick) vive en meta y gana.
    ig_access_token: str | None = None
    ig_app_id: str | None = None          # solo para `audimetro auth instagram`
    ig_app_secret: str | None = None
    ig_redirect_uri: str = "https://localhost:8443/callback"

    # TikTok (Display API): mismo esquema semilla/meta para el refresh token.
    tt_client_key: str | None = None
    tt_client_secret: str | None = None
    tt_refresh_token: str | None = None
    tt_redirect_uri: str = "https://localhost:8443/callback"
