"""CLI de Audímetro: captura manual y autorización inicial de las APIs.

    audimetro tick                 # captura + informe AHORA (ignora la hora)
    audimetro list [plataforma]    # últimas stats por consola
    audimetro auth instagram       # flujo OAuth → imprime la línea para .env
    audimetro auth tiktok          # ídem (el refresh token de 365 días)
    audimetro auth youtube         # ídem (Analytics: min vistos y subs)

Los flujos de auth se hacen UNA vez desde el Mac: imprimen la URL de
autorización, el navegador redirige al redirect_uri configurado (dará error de
conexión: da igual) y se pega aquí la URL completa de la barra de direcciones.
Los tokens resultantes van al .env que se sube a la VM; en ejecución la app
los refresca sola y guarda los vigentes en su DB (meta).
"""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from radar_core.runtime import setup_logging

from audimetro.config import Settings
from audimetro.contracts import PLATFORMS
from audimetro.runtime import Runtime
from audimetro.store import open_store


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="audimetro")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("tick", help="captura y envía el informe ahora")
    lst = sub.add_parser("list", help="últimas stats por consola")
    lst.add_argument("platform", nargs="?", default=None, choices=PLATFORMS)
    auth = sub.add_parser("auth", help="autorización inicial de una API")
    auth.add_argument("provider", choices=("instagram", "tiktok", "youtube"))
    return p


async def _tick(cfg: Settings) -> int:
    async with open_store(cfg.db_path) as store:
        await store.init_schema()
        runtime = Runtime(cfg, store)
        try:
            stats = await runtime.run_tick(force=True)
        finally:
            await runtime.aclose()
        print(f"captura: {stats.captured} piezas · fuentes caídas: "
              f"{stats.sources_failed} · mensajes: {stats.messages_sent}")
        return 1 if stats.sources_failed else 0


async def _list(cfg: Settings, platform: str | None) -> int:
    async with open_store(cfg.db_path) as store:
        await store.init_schema()
        platforms = [platform] if platform else list(PLATFORMS)
        for name in platforms:
            rows = await store.latest_for_platform(name, limit=100)
            print(f"== {name} ({len(rows)} piezas)")
            for title, url, kind, snap in rows:
                print(f"  [{kind}] {title[:60]} — {snap.views} views · "
                      f"{snap.likes or 0} likes · {snap.comments or 0} comentarios")
                print(f"        {url}")
    return 0


def _paste_code(param: str = "code") -> str:
    pasted = input("Pega aquí la URL completa a la que te ha redirigido: ").strip()
    query = parse_qs(urlsplit(pasted).query)
    values = query.get(param)
    if not values:
        # Instagram añade '#_' al final del redirect; ya lo quita urlsplit.
        raise SystemExit(f"la URL pegada no trae ?{param}= — repite el flujo")
    return values[0]


def _auth_instagram(cfg: Settings) -> int:
    if not (cfg.ig_app_id and cfg.ig_app_secret):
        raise SystemExit(
            "faltan AUDIMETRO_IG_APP_ID / AUDIMETRO_IG_APP_SECRET en el .env\n"
            "(app de developers.facebook.com con el producto 'Instagram')"
        )
    authorize = "https://www.instagram.com/oauth/authorize?" + urlencode({
        "client_id": cfg.ig_app_id,
        "redirect_uri": cfg.ig_redirect_uri,
        "scope": "instagram_business_basic,instagram_business_manage_insights",
        "response_type": "code",
    })
    print("1) Abre esta URL y autoriza con tu cuenta profesional:\n\n"
          f"{authorize}\n")
    code = _paste_code()
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post("https://api.instagram.com/oauth/access_token", data={
                "client_id": cfg.ig_app_id,
                "client_secret": cfg.ig_app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": cfg.ig_redirect_uri,
                "code": code,
            })
            short = resp.json()
            if "access_token" not in short:
                raise SystemExit(
                    f"intercambio del code rechazado: {short.get('error_message', 'sin detalle')}"
                )
            # El canje a token largo solo acepta secret/token como query params
            # (excepción a la regla secretos-en-headers, forzada por la API).
            resp = client.get("https://graph.instagram.com/access_token", params={
                "grant_type": "ig_exchange_token",
                "client_secret": cfg.ig_app_secret,
                "access_token": short["access_token"],
            })
            long_lived = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Sin repr de la petición: la URL del canje lleva el secret.
        # ValueError cubre respuestas no-JSON (un 502 con HTML).
        raise SystemExit(f"fallo de red con la API de Instagram: {type(exc).__name__}") from None
    token = long_lived.get("access_token")
    if not token:
        raise SystemExit(
            "canje a token largo rechazado: "
            f"{(long_lived.get('error') or {}).get('message', 'sin detalle')}"
        )
    print("\n2) Añade esta línea al .env (y súbelo a la VM):\n")
    print(f"AUDIMETRO_IG_ACCESS_TOKEN={token}")
    print("\nEl token dura 60 días y la app lo renueva sola en cada captura.")
    return 0


def _auth_tiktok(cfg: Settings) -> int:
    if not (cfg.tt_client_key and cfg.tt_client_secret):
        raise SystemExit(
            "faltan AUDIMETRO_TT_CLIENT_KEY / AUDIMETRO_TT_CLIENT_SECRET en el .env\n"
            "(app de developers.tiktok.com con Login Kit + scope video.list)"
        )
    authorize = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key": cfg.tt_client_key,
        "response_type": "code",
        "scope": "user.info.basic,video.list",
        "redirect_uri": cfg.tt_redirect_uri,
        "state": "audimetro",
    })
    print("1) Abre esta URL y autoriza con tu cuenta de TikTok:\n\n"
          f"{authorize}\n")
    code = _paste_code()
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post("https://open.tiktokapis.com/v2/oauth/token/", data={
                "client_key": cfg.tt_client_key,
                "client_secret": cfg.tt_client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": cfg.tt_redirect_uri,
                "code": code,
            })
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SystemExit(f"fallo de red con la API de TikTok: {type(exc).__name__}") from None
    refresh = payload.get("refresh_token")
    if not refresh:
        raise SystemExit(
            "intercambio del code rechazado: "
            f"{payload.get('error', '?')} — {payload.get('error_description', '')}"
        )
    print("\n2) Añade esta línea al .env (y súbelo a la VM):\n")
    print(f"AUDIMETRO_TT_REFRESH_TOKEN={refresh}")
    print("\nEl refresh token dura 365 días; la app saca con él el token de "
          "acceso diario y guarda las rotaciones en su DB.")
    return 0


def _auth_youtube(cfg: Settings) -> int:
    if not (cfg.yt_oauth_client_id and cfg.yt_oauth_client_secret):
        raise SystemExit(
            "faltan AUDIMETRO_YT_OAUTH_CLIENT_ID / AUDIMETRO_YT_OAUTH_CLIENT_SECRET\n"
            "(cliente OAuth 'Aplicación de escritorio' del proyecto GCP, con la\n"
            "pantalla de consentimiento PUBLICADA en producción — si queda en\n"
            "Testing, el token caduca a los 7 días)"
        )
    authorize = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": cfg.yt_oauth_client_id,
        "redirect_uri": cfg.yt_redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/yt-analytics.readonly",
        "access_type": "offline",   # sin esto Google no da refresh token
        "prompt": "consent",
    })
    print("1) Abre esta URL, elige la cuenta del canal y autoriza (saldrá el\n"
          "   aviso 'Google no ha verificado esta aplicación': Configuración\n"
          "   avanzada → continuar — eres tú autorizando tu propia app):\n\n"
          f"{authorize}\n")
    code = _paste_code()
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post("https://oauth2.googleapis.com/token", data={
                "client_id": cfg.yt_oauth_client_id,
                "client_secret": cfg.yt_oauth_client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": cfg.yt_redirect_uri,
                "code": code,
            })
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SystemExit(f"fallo de red con OAuth de Google: {type(exc).__name__}") from None
    refresh = payload.get("refresh_token")
    if not refresh:
        raise SystemExit(
            "intercambio del code rechazado: "
            f"{payload.get('error', '?')} — {payload.get('error_description', '')}"
        )
    print("\n2) Añade esta línea al .env (y súbelo a la VM):\n")
    print(f"AUDIMETRO_YT_REFRESH_TOKEN={refresh}")
    print("\nCon la app OAuth en producción este token no caduca; la app saca "
          "con él un token de acceso fresco en cada captura.")
    return 0


_AUTH_HANDLERS = {
    "instagram": _auth_instagram,
    "tiktok": _auth_tiktok,
    "youtube": _auth_youtube,
}


def main() -> None:
    setup_logging()
    args = _parser().parse_args()
    cfg = Settings()
    if args.command == "tick":
        raise SystemExit(asyncio.run(_tick(cfg)))
    if args.command == "list":
        raise SystemExit(asyncio.run(_list(cfg, args.platform)))
    # args.command == "auth" (el subparser required=True ya validó el resto)
    raise SystemExit(_AUTH_HANDLERS[args.provider](cfg))


if __name__ == "__main__":
    main()
