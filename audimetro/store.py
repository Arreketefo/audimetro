"""Persistencia de dominio sobre la base SQLite de radar-core."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from radar_core.store import BaseStore, open_db

from audimetro.contracts import Snapshot, VideoStat

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "schema.sql"


@asynccontextmanager
async def open_store(db_path: str) -> AsyncIterator[Store]:
    async with open_db(db_path) as conn:
        yield Store(conn)


def _opt(row: Any, column: str) -> int | None:
    return None if row[column] is None else int(row[column])


def _snapshot(row: Any) -> Snapshot:
    return Snapshot(
        views=int(row["views"]),
        likes=_opt(row, "likes"),
        comments=_opt(row, "comments"),
        shares=_opt(row, "shares"),
        saves=_opt(row, "saves"),
        captured_at=str(row["captured_at"]),
        watch_min=_opt(row, "watch_min"),
        avg_view_s=_opt(row, "avg_view_s"),
        subs_gained=_opt(row, "subs_gained"),
    )


# Columnas añadidas tras la v1 del schema: CREATE TABLE IF NOT EXISTS no
# amplía tablas ya creadas, así que se migran aditivamente en init_schema.
_SNAPSHOT_ADDED_COLUMNS = ("watch_min", "avg_view_s", "subs_gained")


class Store(BaseStore):
    async def init_schema(self) -> None:
        await self._conn.executescript(_SCHEMA_PATH.read_text())
        cur = await self._conn.execute("PRAGMA table_info(snapshots)")
        existing = {str(r["name"]) for r in await cur.fetchall()}
        for column in _SNAPSHOT_ADDED_COLUMNS:
            if column not in existing:
                await self._conn.execute(
                    f"ALTER TABLE snapshots ADD COLUMN {column} INTEGER"
                )
        await self._conn.commit()

    async def known_kinds(self, platform: str) -> dict[str, str]:
        cur = await self._conn.execute(
            "SELECT video_id, kind FROM videos WHERE platform = ?", (platform,)
        )
        return {str(r["video_id"]): str(r["kind"]) for r in await cur.fetchall()}

    async def platforms_with_history(self, before: str | None = None) -> set[str]:
        """Plataformas con al menos una captura (para no tratar la primera
        pasada como una riada de piezas 'nuevas'). Con `before`, solo capturas
        anteriores a ese instante — debe usar el MISMO corte que
        latest_snapshots para que 'hay historia' implique 'hay previous'."""
        where = "WHERE captured_at < ?" if before else ""
        params = (before,) if before else ()
        cur = await self._conn.execute(
            f"SELECT DISTINCT platform FROM snapshots {where}", params)
        return {str(r["platform"]) for r in await cur.fetchall()}

    async def latest_snapshots(
        self, before: str | None = None
    ) -> dict[tuple[str, str], Snapshot]:
        """Último snapshot por pieza; con `before` (timestamp TIMESTAMP_FMT),
        el último ESTRICTAMENTE anterior a ese instante (referencia estable de
        deltas ante reintentos o informes forzados el mismo día)."""
        where = "WHERE captured_at < ?" if before else ""
        params = (before,) if before else ()
        # Bare column + MAX(): SQLite devuelve la fila del máximo.
        cur = await self._conn.execute(
            f"""
            SELECT platform, video_id, MAX(captured_at) AS captured_at,
                   views, likes, comments, shares, saves,
                   watch_min, avg_view_s, subs_gained
            FROM snapshots {where} GROUP BY platform, video_id
            """,
            params,
        )
        return {
            (str(r["platform"]), str(r["video_id"])): _snapshot(r)
            for r in await cur.fetchall()
        }

    async def record(self, stats: list[VideoStat], captured_at: str) -> None:
        """Persiste metadatos + snapshot de una captura (un commit por lote)."""
        await self._conn.executemany(
            """
            INSERT INTO videos (platform, video_id, kind, title, url,
                                published_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (platform, video_id) DO UPDATE SET
                kind = excluded.kind, title = excluded.title,
                url = excluded.url, published_at = excluded.published_at,
                last_seen = excluded.last_seen
            """,
            [(s.platform, s.video_id, s.kind, s.title, s.url,
              s.published_at, captured_at) for s in stats],
        )
        await self._conn.executemany(
            """
            INSERT OR REPLACE INTO snapshots
                (platform, video_id, captured_at, views, likes, comments,
                 shares, saves, watch_min, avg_view_s, subs_gained)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(s.platform, s.video_id, captured_at, s.views, s.likes,
              s.comments, s.shares, s.saves, s.watch_min, s.avg_view_s,
              s.subs_gained) for s in stats],
        )
        await self._conn.commit()

    async def latest_for_platform(
        self, platform: str, limit: int = 50
    ) -> list[tuple[str, str, str, Snapshot]]:
        """(title, url, kind, snapshot) por views desc — para /stats."""
        cur = await self._conn.execute(
            """
            SELECT v.title, v.url, v.kind, s.views, s.likes, s.comments,
                   s.shares, s.saves, s.watch_min, s.avg_view_s, s.subs_gained,
                   MAX(s.captured_at) AS captured_at
            FROM snapshots s JOIN videos v
                 ON v.platform = s.platform AND v.video_id = s.video_id
            WHERE s.platform = ?
            GROUP BY s.platform, s.video_id
            ORDER BY s.views DESC
            LIMIT ?
            """,
            (platform, limit),
        )
        return [
            (str(r["title"]), str(r["url"]), str(r["kind"]), _snapshot(r))
            for r in await cur.fetchall()
        ]

    async def piece_counts(self) -> dict[str, int]:
        cur = await self._conn.execute(
            "SELECT platform, COUNT(*) AS n FROM videos GROUP BY platform"
        )
        return {str(r["platform"]): int(r["n"]) for r in await cur.fetchall()}

    async def prune(self, max_age_days: int) -> int:
        return await self.prune_older_than("snapshots", "captured_at", max_age_days)
