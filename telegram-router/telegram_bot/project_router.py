"""
telegram_bot/project_router.py — Async ProjectRouter

Used by the bot process. Wraps asyncpg for non-blocking DB access.
"""

import asyncpg


class ProjectRouter:
    """
    Async lookup: thread_id → project_name (reverse of TopicManager).
    Also provides get_or_create for the bot to use when routing outbound messages.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool: asyncpg.Pool | None = None
        # thread_id → project_name
        self._cache: dict[int, str] = {}
        # project_name → thread_id
        self._reverse_cache: dict[str, int] = {}

    async def init(self) -> None:
        self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
        await self._warm_cache()

    async def _warm_cache(self) -> None:
        """Pre-load all mappings into memory at startup."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT project_name, topic_id FROM project_topics")
        for row in rows:
            self._cache[row["topic_id"]] = row["project_name"]
            self._reverse_cache[row["project_name"]] = row["topic_id"]
        print(f"[project_router] loaded {len(rows)} project→topic mappings")

    async def get_project_from_thread(self, thread_id: int) -> str | None:
        """thread_id → project_name"""
        if thread_id in self._cache:
            return self._cache[thread_id]

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT project_name FROM project_topics WHERE topic_id = $1",
                thread_id,
            )

        if row:
            name = row["project_name"]
            self._cache[thread_id] = name
            self._reverse_cache[name] = thread_id
            return name

        return None

    async def get_thread_from_project(self, project_name: str) -> int | None:
        """project_name → thread_id"""
        if project_name in self._reverse_cache:
            return self._reverse_cache[project_name]

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT topic_id FROM project_topics WHERE project_name = $1",
                project_name,
            )

        if row:
            topic_id = row["topic_id"]
            self._reverse_cache[project_name] = topic_id
            self._cache[topic_id] = project_name
            return topic_id

        return None

    async def save_mapping(self, project_name: str, topic_id: int) -> None:
        """Persist a new project→topic mapping."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO project_topics (project_name, topic_id)
                VALUES ($1, $2)
                ON CONFLICT (project_name) DO UPDATE SET topic_id = EXCLUDED.topic_id
                """,
                project_name,
                topic_id,
            )
        self._cache[topic_id] = project_name
        self._reverse_cache[project_name] = topic_id

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
