"""Lesson quarantine — LOCAL → PROVISIONAL → FLEET (Phase I T2-10).

Nothing promotes itself. Promotion to PROVISIONAL requires 2+ independent
instances of the same lesson. FLEET is a manual curator decision only.

Stages:
  local       — observed once; not yet validated
  provisional — seen on 2+ independent packets; worth watching
  fleet       — manually promoted by Josh/curator; system-wide guidance
"""

from __future__ import annotations

from datetime import datetime, timezone

from .store import cursor

STAGES = ("local", "provisional", "fleet")
PROVISIONAL_THRESHOLD = 2  # independent instances before auto-promoting to provisional


def record_lesson(db_path: str, summary: str,
                  source_packet_id: int | None = None) -> int:
    """Record a new lesson at LOCAL stage. Returns lesson id."""
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO lessons (summary, source_packet_id, stage, "
            "instance_count, created_at) VALUES (?, ?, 'local', 1, ?)",
            (summary.strip(), source_packet_id, now),
        )
        return cur.lastrowid


def observe_instance(db_path: str, lesson_id: int) -> str:
    """Record another independent instance of this lesson.

    If instance_count reaches PROVISIONAL_THRESHOLD, auto-promotes to
    provisional. Returns the new stage.
    """
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE lessons SET instance_count = instance_count + 1 WHERE id = ?",
            (lesson_id,),
        )
        row = cur.execute(
            "SELECT stage, instance_count FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        if row["stage"] == "local" and row["instance_count"] >= PROVISIONAL_THRESHOLD:
            cur.execute(
                "UPDATE lessons SET stage = 'provisional', promoted_at = ? WHERE id = ?",
                (now, lesson_id),
            )
            return "provisional"
        return row["stage"]


def promote_to_fleet(db_path: str, lesson_id: int) -> None:
    """Manual curator action only — promotes a provisional lesson to fleet."""
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT stage FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no lesson {lesson_id}")
        if row["stage"] != "provisional":
            raise ValueError(
                f"lesson {lesson_id} is {row['stage']!r} — only provisional lessons "
                "can be promoted to fleet"
            )
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(
            "UPDATE lessons SET stage = 'fleet', promoted_at = ? WHERE id = ?",
            (now, lesson_id),
        )


def lessons_by_stage(db_path: str, stage: str) -> list[dict]:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT * FROM lessons WHERE stage = ? ORDER BY created_at DESC", (stage,)
        ).fetchall()
    return [dict(r) for r in rows]
