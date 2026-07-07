# Audímetro

Radar de audiencias: una vez al día captura las estadísticas de tus vídeos y
shorts de YouTube, reels de Instagram y vídeos de TikTok, guarda el histórico
en SQLite y manda a Telegram un informe con totales por plataforma, piezas
nuevas y las que se han movido en las últimas 24 h.

Cuarto miembro de la familia de radares (Vigía/Ojeador/Tasador), construido
sobre `radar-core` (vendorizado: `make vendor-core`). Este repo **no es
auto-contenible** para terceros: el core es privado.

## Comandos del bot

`/stats [yt|ig|tt] [n]` detalle bajo demanda · `/hora <0-23>` hora UTC del
informe · `/informe` fuerza el informe ahora · más los comunes del core
(`/status /config /set /reset /pausa /reanuda /version /ayuda`).

## Setup (pasos humanos)

1. Bot en @BotFather → `AUDIMETRO_TELEGRAM_BOT_TOKEN` (+ tu chat id).
2. API key de YouTube en el proyecto GCP (YouTube Data API v3).
3. App de Instagram en developers.facebook.com → `uv run audimetro auth instagram`.
4. App sandbox de TikTok en developers.tiktok.com → `uv run audimetro auth tiktok`.
5. Opcional — YouTube Analytics (min vistos, duración media, subs por vídeo):
   cliente OAuth de escritorio en el mismo proyecto GCP, consentimiento
   publicado en producción → `uv run audimetro auth youtube`.
6. Copiar `.env.example` a `.env` y rellenar.

Detalle paso a paso: radar-ops/HUMAN-SETUP.md.

## Operación

```bash
make install     # uv sync (refresca vendor/)
make tick        # captura + informe AHORA (prueba end-to-end)
make dev         # daemon en primer plano
make test lint   # pytest · ruff + mypy strict
ssh gcp-apps 'mkdir -p ~/audimetro' && scp .env gcp-apps:~/audimetro/.env
make deploy AUDIMETRO_HOST=gcp-apps   # rsync + build + up en la VM
```
(El `.env` sube ANTES del primer deploy: así el contenedor nace con
credenciales; si no, quedaría corriendo pero mudo.)

El daemon tickea cada 15 min pero solo captura/envía una vez al día (primer
tick tras `AUDIMETRO_DIGEST_HOUR` UTC). El día 1 registra la línea base
("primera captura"); los deltas empiezan el día 2. Tokens: el de Instagram
(60 días) y el de TikTok (acceso 24 h) se renuevan solos y viven en la DB;
si un refresh falla de forma persistente, `/status` lo avisa.
