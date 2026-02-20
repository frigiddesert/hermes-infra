"""
core/topic_manager.py — Synchronous TopicManager

Used by scripts (session_relay, permission_relay) that run as plain Python
processes. The async version (ProjectRouter) is used by the Telegram bot.
"""

import requests
import psycopg2
from psycopg2.extras import RealDictCursor


class TopicManager:
    """
    Manages the mapping between project names and Telegram forum topic IDs.

    - In-memory cache (process lifetime)
    - PostgreSQL persistence (shared across machines)
    - Telegram API for topic creation
    """

    def __init__(self, bot_token: str, forum_id: int, database_url: str):
        self.bot_token = bot_token
        self.forum_id = forum_id
        self.database_url = database_url
        self._cache: dict[str, int] = {}
        self._ensure_table()

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _conn(self):
        return psycopg2.connect(self.database_url)

    def _ensure_table(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS project_topics (
                        project_name VARCHAR(100) PRIMARY KEY,
                        topic_id     INTEGER NOT NULL,
                        created_at   TIMESTAMP DEFAULT NOW()
                    )
                """)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_or_create(self, project_name: str) -> int:
        """Return topic_id, creating the forum topic if it doesn't exist."""
        if project_name in self._cache:
            return self._cache[project_name]

        topic_id = self.lookup(project_name)
        if topic_id is None:
            topic_id = self.create_topic(project_name)

        self._cache[project_name] = topic_id
        return topic_id

    def lookup(self, project_name: str) -> int | None:
        """DB lookup only — no topic creation."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT topic_id FROM project_topics WHERE project_name = %s",
                    (project_name,),
                )
                row = cur.fetchone()
                if row:
                    topic_id = row[0]
                    self._cache[project_name] = topic_id
                    return topic_id
        return None

    def create_topic(self, project_name: str) -> int:
        """Create a forum topic via Telegram API and save to DB."""
        url = f"https://api.telegram.org/bot{self.bot_token}/createForumTopic"
        resp = requests.post(
            url,
            json={"chat_id": self.forum_id, "name": f"🔧 {project_name}"},
            timeout=15,
        )
        resp.raise_for_status()
        topic_id: int = resp.json()["result"]["message_thread_id"]

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO project_topics (project_name, topic_id)
                    VALUES (%s, %s)
                    ON CONFLICT (project_name) DO UPDATE SET topic_id = EXCLUDED.topic_id
                    """,
                    (project_name, topic_id),
                )

        print(f"[topic_manager] created topic '{project_name}' → {topic_id}")
        return topic_id

    def send(self, topic_id: int, text: str) -> None:
        """Send a message to a forum topic, splitting if over 4096 chars."""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        for chunk in _split(text):
            try:
                requests.post(
                    url,
                    json={
                        "chat_id": self.forum_id,
                        "message_thread_id": topic_id,
                        "text": chunk,
                        "parse_mode": "Markdown",
                    },
                    timeout=15,
                )
            except Exception as exc:
                # Fall back to plain text if Markdown parse fails
                try:
                    requests.post(
                        url,
                        json={
                            "chat_id": self.forum_id,
                            "message_thread_id": topic_id,
                            "text": chunk,
                        },
                        timeout=15,
                    )
                except Exception:
                    print(f"[topic_manager] send failed: {exc}")


# ── helpers ───────────────────────────────────────────────────────────────────

def _split(text: str, max_len: int = 4096) -> list[str]:
    return [text[i : i + max_len] for i in range(0, max(len(text), 1), max_len)]
