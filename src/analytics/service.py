from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

DB_SCHEMA = os.getenv("DB_SCHEMA", "public")


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _fetchall_dict(db: Session, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = db.execute(text(sql), params)
    return [dict(r._mapping) for r in rows]


def get_overview(db: Session, days: int = 30) -> Dict[str, Any]:
    since = _since(days)
    summary = db.execute(
        text(
            f'''
            SELECT
                COUNT(*) AS total_queries,
                COUNT(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL) AS active_users,
                COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
                COUNT(*) FILTER (WHERE answer_status = 'no_hit') AS no_hit_queries,
                COUNT(*) FILTER (WHERE answer_status = 'error' OR has_error = true) AS error_queries,
                COUNT(*) FILTER (WHERE tool_name = 'space_booking') AS booking_queries,
                COUNT(*) FILTER (WHERE detected_intent = 'assessments') AS assessment_queries,
                COUNT(*) FILTER (WHERE detected_intent = 'timetable') AS timetable_queries
            FROM "{DB_SCHEMA}".assistant_query_analytics
            WHERE created_at >= :since
            '''
        ),
        {"since": since},
    ).mappings().first()

    top_intents = _fetchall_dict(
        db,
        f'''
        SELECT detected_intent AS label, COUNT(*)::int AS value
        FROM "{DB_SCHEMA}".assistant_query_analytics
        WHERE created_at >= :since
        GROUP BY detected_intent
        ORDER BY value DESC, detected_intent ASC
        LIMIT 6
        ''',
        {"since": since},
    )

    top_tools = _fetchall_dict(
        db,
        f'''
        SELECT COALESCE(tool_name, 'unknown') AS label, COUNT(*)::int AS value
        FROM "{DB_SCHEMA}".assistant_query_analytics
        WHERE created_at >= :since
        GROUP BY COALESCE(tool_name, 'unknown')
        ORDER BY value DESC, label ASC
        LIMIT 6
        ''',
        {"since": since},
    )

    daily_usage = _fetchall_dict(
        db,
        f'''
        SELECT
            DATE_TRUNC('day', created_at)::date AS day,
            COUNT(*)::int AS query_count,
            COUNT(DISTINCT user_id)::int AS active_users
        FROM "{DB_SCHEMA}".assistant_query_analytics
        WHERE created_at >= :since
        GROUP BY 1
        ORDER BY 1 ASC
        ''',
        {"since": since},
    )

    total_queries = int(summary["total_queries"] or 0)
    no_hit_queries = int(summary["no_hit_queries"] or 0)
    error_queries = int(summary["error_queries"] or 0)
    no_hit_rate = round((no_hit_queries / total_queries) * 100, 2) if total_queries else 0.0

    return {
        "kpis": [
            {"label": "Total queries", "value": total_queries},
            {"label": "Active users", "value": int(summary["active_users"] or 0)},
            {"label": "Average latency (ms)", "value": round(float(summary["avg_latency_ms"] or 0), 1)},
            {"label": "No-hit rate (%)", "value": no_hit_rate},
            {"label": "Errors", "value": error_queries},
            {"label": "Space-booking queries", "value": int(summary["booking_queries"] or 0)},
        ],
        "top_intents": top_intents,
        "top_tools": top_tools,
        "daily_usage": daily_usage,
    }


def get_intent_trend(db: Session, days: int = 30, bucket: str = "day") -> List[Dict[str, Any]]:
    since = _since(days)
    safe_bucket = bucket if bucket in {"day", "week"} else "day"
    return _fetchall_dict(
        db,
        f'''
        SELECT
            DATE_TRUNC('{safe_bucket}', created_at) AS period,
            detected_intent AS series,
            COUNT(*)::int AS value
        FROM "{DB_SCHEMA}".assistant_query_analytics
        WHERE created_at >= :since
        GROUP BY 1, 2
        ORDER BY 1 ASC, 2 ASC
        ''',
        {"since": since},
    )


def get_frequent_questions(db: Session, days: int = 30, limit: int = 10) -> List[Dict[str, Any]]:
    since = _since(days)
    return _fetchall_dict(
        db,
        f'''
        WITH grouped AS (
            SELECT
                COALESCE(normalized_question, LOWER(TRIM(question_text))) AS normalized_question,
                MIN(question_text) AS question,
                MIN(detected_intent) AS detected_intent,
                MIN(COALESCE(tool_name, 'unknown')) AS tool_name,
                COUNT(*)::int AS frequency,
                MAX(created_at) AS latest_seen
            FROM "{DB_SCHEMA}".assistant_query_analytics
            WHERE created_at >= :since
            GROUP BY COALESCE(normalized_question, LOWER(TRIM(question_text)))
        )
        SELECT *
        FROM grouped
        ORDER BY frequency DESC, latest_seen DESC
        LIMIT :limit
        ''',
        {"since": since, "limit": limit},
    )


def get_unanswered_questions(db: Session, days: int = 30, limit: int = 10) -> List[Dict[str, Any]]:
    since = _since(days)
    rows = _fetchall_dict(
        db,
        f'''
        WITH grouped AS (
            SELECT
                COALESCE(normalized_question, LOWER(TRIM(question_text))) AS normalized_question,
                MIN(question_text) AS question,
                MIN(detected_intent) AS detected_intent,
                MIN(COALESCE(tool_name, 'unknown')) AS tool_name,
                COUNT(*)::int AS frequency,
                MAX(created_at) AS latest_seen,
                BOOL_OR(answer_status = 'no_hit') AS any_no_hit,
                BOOL_OR(answer_status = 'error' OR has_error = true) AS any_error,
                BOOL_OR(answer_status = 'partial') AS any_partial
            FROM "{DB_SCHEMA}".assistant_query_analytics
            WHERE created_at >= :since
              AND (
                    answer_status IN ('no_hit', 'partial', 'error')
                    OR has_error = true
                    OR (tool_name = 'rag' AND COALESCE(rag_hit_count, 0) = 0)
                  )
            GROUP BY COALESCE(normalized_question, LOWER(TRIM(question_text)))
        )
        SELECT *
        FROM grouped
        ORDER BY frequency DESC, latest_seen DESC
        LIMIT :limit
        ''',
        {"since": since, "limit": limit},
    )

    for row in rows:
        if row.get("any_no_hit"):
            row["gap_type"] = "missing_knowledge"
        elif row.get("any_error"):
            row["gap_type"] = "tool_or_data_issue"
        else:
            row["gap_type"] = "multi_step_friction"
    return rows


def get_spaces_demand(db: Session, days: int = 60, limit: int = 10) -> Dict[str, Any]:
    since = _since(days)

    top_spaces = _fetchall_dict(
        db,
        f'''
        SELECT
            s.id::text AS space_id,
            s.name,
            s.type AS space_type,
            s.location,
            COUNT(*)::int AS bookings,
            COUNT(DISTINCT sb."userId")::int AS unique_users
        FROM "{DB_SCHEMA}".space_bookings sb
        JOIN "{DB_SCHEMA}".spaces s ON s.id = sb."spaceId"
        WHERE sb.status = 'CONFIRMED'
          AND sb.start_time >= :since
        GROUP BY s.id, s.name, s.type, s.location
        ORDER BY bookings DESC, s.name ASC
        LIMIT :limit
        ''',
        {"since": since, "limit": limit},
    )

    type_breakdown = _fetchall_dict(
        db,
        f'''
        SELECT
            s.type AS label,
            COUNT(*)::int AS value
        FROM "{DB_SCHEMA}".space_bookings sb
        JOIN "{DB_SCHEMA}".spaces s ON s.id = sb."spaceId"
        WHERE sb.status = 'CONFIRMED'
          AND sb.start_time >= :since
        GROUP BY s.type
        ORDER BY value DESC, label ASC
        ''',
        {"since": since},
    )

    heatmap = _fetchall_dict(
        db,
        f'''
        SELECT
            EXTRACT(ISODOW FROM sb.start_time)::int AS day_of_week,
            EXTRACT(HOUR FROM sb.start_time)::int AS hour_of_day,
            COUNT(*)::int AS value
        FROM "{DB_SCHEMA}".space_bookings sb
        WHERE sb.status = 'CONFIRMED'
          AND sb.start_time >= :since
        GROUP BY 1, 2
        ORDER BY 1 ASC, 2 ASC
        ''',
        {"since": since},
    )

    library_booking_count = db.execute(
        text(
            f'''
            SELECT COUNT(*)::int
            FROM "{DB_SCHEMA}".space_bookings sb
            JOIN "{DB_SCHEMA}".spaces s ON s.id = sb."spaceId"
            WHERE sb.status = 'CONFIRMED'
              AND sb.start_time >= :since
              AND LOWER(s.location) LIKE '%library%'
            '''
        ),
        {"since": since},
    ).scalar() or 0

    library_query_count = db.execute(
        text(
            f'''
            SELECT COUNT(*)::int
            FROM "{DB_SCHEMA}".assistant_query_analytics
            WHERE created_at >= :since
              AND (
                    LOWER(question_text) LIKE '%library%'
                    OR LOWER(COALESCE(tool_name, '')) IN ('spaces', 'space_booking')
                 )
            '''
        ),
        {"since": since},
    ).scalar() or 0

    return {
        "top_spaces": top_spaces,
        "space_type_breakdown": type_breakdown,
        "booking_heatmap": heatmap,
        "library_signals": [
            {"label": "Library space bookings", "value": int(library_booking_count)},
            {"label": "Library-related assistant queries", "value": int(library_query_count)},
        ],
    }


def get_assessment_pressure(db: Session, days_ahead: int = 30, limit: int = 10) -> Dict[str, Any]:
    end_dt = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    modules = _fetchall_dict(
        db,
        f'''
        SELECT
            m.id::text AS module_id,
            m.code AS module_code,
            m.name AS module_name,
            COUNT(a.id)::int AS upcoming_assessment_count,
            MIN(a.due_date) AS next_due_date,
            AVG(a.weight)::float AS average_weight,
            AVG(g.mark)::float AS average_grade
        FROM "{DB_SCHEMA}".assessments a
        JOIN "{DB_SCHEMA}".modules m ON m.id = a."moduleId"
        LEFT JOIN "{DB_SCHEMA}".grades g ON g."assessmentId" = a.id
        WHERE a.due_date >= NOW()
          AND a.due_date < :end_dt
        GROUP BY m.id, m.code, m.name
        ORDER BY upcoming_assessment_count DESC, next_due_date ASC
        LIMIT :limit
        ''',
        {"end_dt": end_dt, "limit": limit},
    )

    query_trend = _fetchall_dict(
        db,
        f'''
        SELECT
            DATE_TRUNC('day', created_at)::date AS day,
            COUNT(*)::int AS query_count
        FROM "{DB_SCHEMA}".assistant_query_analytics
        WHERE created_at >= NOW() - INTERVAL '30 days'
          AND detected_intent = 'assessments'
        GROUP BY 1
        ORDER BY 1 ASC
        ''',
        {},
    )

    return {
        "modules": modules,
        "assessment_query_trend": query_trend,
    }


def get_module_confusion(db: Session, days: int = 30, limit: int = 10) -> Dict[str, Any]:
    since = _since(days)
    rows = _fetchall_dict(
        db,
        f'''
        WITH base AS (
            SELECT
                COALESCE(
                    meta_information->>'primary_module_code',
                    meta_information->'module_codes'->>0
                ) AS module_code,
                COALESCE(tool_name, 'unknown') AS tool_name,
                answer_status,
                COUNT(*)::int AS query_count
            FROM "{DB_SCHEMA}".assistant_query_analytics
            WHERE created_at >= :since
              AND COALESCE(meta_information->>'primary_module_code', meta_information->'module_codes'->>0) IS NOT NULL
            GROUP BY 1, 2, 3
        )
        SELECT
            module_code,
            SUM(query_count)::int AS query_count,
            SUM(CASE WHEN answer_status = 'no_hit' THEN query_count ELSE 0 END)::int AS no_hit_count,
            SUM(CASE WHEN tool_name = 'timetable' THEN query_count ELSE 0 END)::int AS timetable_queries,
            SUM(CASE WHEN tool_name = 'assessments' THEN query_count ELSE 0 END)::int AS assessment_queries
        FROM base
        GROUP BY module_code
        ORDER BY query_count DESC, module_code ASC
        LIMIT :limit
        ''',
        {"since": since, "limit": limit},
    )

    for row in rows:
        row["confusion_score"] = round(
            float(row["query_count"])
            + (float(row["no_hit_count"]) * 2.0)
            + (float(row["timetable_queries"]) * 0.5)
            + (float(row["assessment_queries"]) * 0.75),
            2,
        )

    rows.sort(key=lambda x: (-x["confusion_score"], x["module_code"]))
    return {"modules": rows}


def get_timetable_friction(db: Session, days: int = 30, limit: int = 10) -> Dict[str, Any]:
    since = _since(days)
    summary_row = db.execute(
        text(
            f'''
            SELECT
                COUNT(*)::int AS total_timetable_queries,
                COUNT(*) FILTER (WHERE answer_status = 'no_hit')::int AS no_hit_queries,
                COUNT(*) FILTER (WHERE answer_status = 'partial')::int AS partial_queries,
                COUNT(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL)::int AS affected_users
            FROM "{DB_SCHEMA}".assistant_query_analytics
            WHERE created_at >= :since
              AND tool_name = 'timetable'
            '''
        ),
        {"since": since},
    ).mappings().first()

    top_questions = _fetchall_dict(
        db,
        f'''
        SELECT
            MIN(question_text) AS question,
            COUNT(*)::int AS frequency,
            COUNT(*) FILTER (WHERE answer_status = 'no_hit')::int AS no_hit_count,
            MAX(created_at) AS latest_seen
        FROM "{DB_SCHEMA}".assistant_query_analytics
        WHERE created_at >= :since
          AND tool_name = 'timetable'
        GROUP BY COALESCE(normalized_question, LOWER(TRIM(question_text)))
        ORDER BY frequency DESC, latest_seen DESC
        LIMIT :limit
        ''',
        {"since": since, "limit": limit},
    )

    trend = _fetchall_dict(
        db,
        f'''
        SELECT
            DATE_TRUNC('day', created_at)::date AS day,
            COUNT(*)::int AS query_count
        FROM "{DB_SCHEMA}".assistant_query_analytics
        WHERE created_at >= :since
          AND tool_name = 'timetable'
        GROUP BY 1
        ORDER BY 1 ASC
        ''',
        {"since": since},
    )

    return {
        "summary": [
            {"label": "Timetable queries", "value": int(summary_row["total_timetable_queries"] or 0)},
            {"label": "No-hit timetable queries", "value": int(summary_row["no_hit_queries"] or 0)},
            {"label": "Partial timetable queries", "value": int(summary_row["partial_queries"] or 0)},
            {"label": "Affected users", "value": int(summary_row["affected_users"] or 0)},
        ],
        "top_questions": top_questions,
        "trend": trend,
    }


def _compute_risk_level(score: float) -> str:
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def _compute_risk_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    score = 0.0
    reasons: List[Dict[str, Any]] = []

    overdue = int(row.get("overdue_assessment_count") or 0)
    upcoming = int(row.get("upcoming_assessment_count") or 0)
    avg_grade = row.get("average_grade")
    trend = row.get("grade_trend_delta")
    assessment_queries = int(row.get("assessment_query_count") or 0)
    timetable_queries = int(row.get("timetable_query_count") or 0)
    no_hit_queries = int(row.get("no_hit_query_count") or 0)
    unread_notifications = int(row.get("unread_notification_count") or 0)

    if overdue > 0:
        delta = min(40, overdue * 15)
        score += delta
        reasons.append({"factor": "overdue_assessments", "value": overdue, "impact": delta})

    if avg_grade is not None:
        avg_grade = float(avg_grade)
        if avg_grade < 40:
            score += 25
            reasons.append({"factor": "low_average_grade", "value": round(avg_grade, 1), "impact": 25})
        elif avg_grade < 50:
            score += 15
            reasons.append({"factor": "borderline_average_grade", "value": round(avg_grade, 1), "impact": 15})

    if trend is not None:
        trend = float(trend)
        if trend < 0:
            delta = min(20, abs(trend) * 1.5)
            score += delta
            reasons.append({"factor": "grade_decline", "value": round(trend, 1), "impact": round(delta, 1)})

    if assessment_queries >= 6:
        score += 10
        reasons.append({"factor": "high_assessment_query_volume", "value": assessment_queries, "impact": 10})

    if timetable_queries >= 6:
        score += 8
        reasons.append({"factor": "high_timetable_query_volume", "value": timetable_queries, "impact": 8})

    if no_hit_queries >= 3:
        score += 10
        reasons.append({"factor": "repeated_unanswered_queries", "value": no_hit_queries, "impact": 10})

    if unread_notifications >= 5:
        score += 7
        reasons.append({"factor": "unread_notifications", "value": unread_notifications, "impact": 7})

    if upcoming >= 3:
        score += 5
        reasons.append({"factor": "high_upcoming_deadline_load", "value": upcoming, "impact": 5})

    return {
        "overall_risk_score": round(score, 2),
        "risk_level": _compute_risk_level(score),
        "risk_reasons": {"reasons": reasons},
    }


def rebuild_student_risk_snapshots(db: Session, snapshot_date: Optional[date] = None) -> Dict[str, Any]:
    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()

    rows = _fetchall_dict(
        db,
        f'''
        WITH student_base AS (
            SELECT u.id, u.full_name, u.email, u.programme, u.year_of_study
            FROM "{DB_SCHEMA}".users u
            WHERE LOWER(COALESCE(u.role, '')) = 'student'
               OR u.programme IS NOT NULL
               OR u.year_of_study IS NOT NULL
        ),
        student_modules AS (
            SELECT DISTINCT g."studentId" AS student_id, a."moduleId" AS module_id
            FROM "{DB_SCHEMA}".grades g
            JOIN "{DB_SCHEMA}".assessments a ON a.id = g."assessmentId"
        ),
        overdue AS (
            SELECT
                sm.student_id,
                COUNT(*)::int AS overdue_assessment_count
            FROM student_modules sm
            JOIN "{DB_SCHEMA}".assessments a ON a."moduleId" = sm.module_id
            LEFT JOIN "{DB_SCHEMA}".grades g
              ON g."studentId" = sm.student_id
             AND g."assessmentId" = a.id
            WHERE a.due_date < NOW()
              AND g.id IS NULL
            GROUP BY sm.student_id
        ),
        upcoming AS (
            SELECT
                sm.student_id,
                COUNT(*)::int AS upcoming_assessment_count
            FROM student_modules sm
            JOIN "{DB_SCHEMA}".assessments a ON a."moduleId" = sm.module_id
            LEFT JOIN "{DB_SCHEMA}".grades g
              ON g."studentId" = sm.student_id
             AND g."assessmentId" = a.id
            WHERE a.due_date >= NOW()
              AND a.due_date < NOW() + INTERVAL '14 days'
              AND g.id IS NULL
            GROUP BY sm.student_id
        ),
        grades AS (
            SELECT
                g."studentId" AS student_id,
                AVG(g.mark)::float AS average_grade,
                AVG(CASE WHEN g.graded_at >= NOW() - INTERVAL '30 days' THEN g.mark END)::float AS recent_average_grade,
                AVG(CASE WHEN g.graded_at < NOW() - INTERVAL '30 days' THEN g.mark END)::float AS older_average_grade
            FROM "{DB_SCHEMA}".grades g
            GROUP BY g."studentId"
        ),
        queries AS (
            SELECT
                aqa.user_id AS student_id,
                COUNT(*) FILTER (WHERE aqa.detected_intent = 'assessments')::int AS assessment_query_count,
                COUNT(*) FILTER (WHERE aqa.detected_intent = 'timetable')::int AS timetable_query_count,
                COUNT(*) FILTER (WHERE aqa.answer_status = 'no_hit')::int AS no_hit_query_count
            FROM "{DB_SCHEMA}".assistant_query_analytics aqa
            WHERE aqa.user_id IS NOT NULL
              AND aqa.created_at >= NOW() - INTERVAL '30 days'
            GROUP BY aqa.user_id
        ),
        notifications AS (
            SELECT
                n.user_id AS student_id,
                COUNT(*) FILTER (WHERE n.is_read = false)::int AS unread_notification_count
            FROM "{DB_SCHEMA}".notifications n
            GROUP BY n.user_id
        )
        SELECT
            sb.id::text AS student_id,
            sb.full_name,
            sb.email,
            sb.programme,
            sb.year_of_study,
            COALESCE(o.overdue_assessment_count, 0) AS overdue_assessment_count,
            COALESCE(u.upcoming_assessment_count, 0) AS upcoming_assessment_count,
            g.average_grade,
            CASE
                WHEN g.recent_average_grade IS NOT NULL AND g.older_average_grade IS NOT NULL
                THEN (g.recent_average_grade - g.older_average_grade)
                ELSE NULL
            END AS grade_trend_delta,
            COALESCE(q.assessment_query_count, 0) AS assessment_query_count,
            COALESCE(q.timetable_query_count, 0) AS timetable_query_count,
            COALESCE(q.no_hit_query_count, 0) AS no_hit_query_count,
            COALESCE(n.unread_notification_count, 0) AS unread_notification_count
        FROM student_base sb
        LEFT JOIN overdue o ON o.student_id = sb.id
        LEFT JOIN upcoming u ON u.student_id = sb.id
        LEFT JOIN grades g ON g.student_id = sb.id
        LEFT JOIN queries q ON q.student_id = sb.id
        LEFT JOIN notifications n ON n.student_id = sb.id
        ORDER BY sb.full_name ASC
        ''',
        {},
    )

    counts = Counter()
    for row in rows:
        risk = _compute_risk_payload(row)
        counts[risk["risk_level"]] += 1
        db.execute(
            text(
                f'''
                INSERT INTO "{DB_SCHEMA}".student_risk_snapshots (
                    id,
                    student_id,
                    snapshot_date,
                    overall_risk_score,
                    risk_level,
                    overdue_assessment_count,
                    upcoming_assessment_count,
                    average_grade,
                    grade_trend_delta,
                    assessment_query_count,
                    timetable_query_count,
                    no_hit_query_count,
                    unread_notification_count,
                    risk_reasons,
                    created_at
                )
                VALUES (
                    uuid_generate_v4(),
                    :student_id,
                    :snapshot_date,
                    :overall_risk_score,
                    :risk_level,
                    :overdue_assessment_count,
                    :upcoming_assessment_count,
                    :average_grade,
                    :grade_trend_delta,
                    :assessment_query_count,
                    :timetable_query_count,
                    :no_hit_query_count,
                    :unread_notification_count,
                    CAST(:risk_reasons AS jsonb),
                    NOW()
                )
                ON CONFLICT (student_id, snapshot_date)
                DO UPDATE SET
                    overall_risk_score = EXCLUDED.overall_risk_score,
                    risk_level = EXCLUDED.risk_level,
                    overdue_assessment_count = EXCLUDED.overdue_assessment_count,
                    upcoming_assessment_count = EXCLUDED.upcoming_assessment_count,
                    average_grade = EXCLUDED.average_grade,
                    grade_trend_delta = EXCLUDED.grade_trend_delta,
                    assessment_query_count = EXCLUDED.assessment_query_count,
                    timetable_query_count = EXCLUDED.timetable_query_count,
                    no_hit_query_count = EXCLUDED.no_hit_query_count,
                    unread_notification_count = EXCLUDED.unread_notification_count,
                    risk_reasons = EXCLUDED.risk_reasons
                '''
            ),
            {
                "student_id": row["student_id"],
                "snapshot_date": snapshot_date,
                "overall_risk_score": risk["overall_risk_score"],
                "risk_level": risk["risk_level"],
                "overdue_assessment_count": int(row.get("overdue_assessment_count") or 0),
                "upcoming_assessment_count": int(row.get("upcoming_assessment_count") or 0),
                "average_grade": row.get("average_grade"),
                "grade_trend_delta": row.get("grade_trend_delta"),
                "assessment_query_count": int(row.get("assessment_query_count") or 0),
                "timetable_query_count": int(row.get("timetable_query_count") or 0),
                "no_hit_query_count": int(row.get("no_hit_query_count") or 0),
                "unread_notification_count": int(row.get("unread_notification_count") or 0),
                "risk_reasons": __import__("json").dumps(risk["risk_reasons"]),
            },
        )

    db.commit()
    return {
        "snapshot_date": snapshot_date,
        "students_processed": len(rows),
        "high_risk": int(counts["high"]),
        "medium_risk": int(counts["medium"]),
        "low_risk": int(counts["low"]),
    }


def get_students_at_risk(db: Session, risk_level: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    snapshot_date = db.execute(
        text(f'SELECT MAX(snapshot_date) FROM "{DB_SCHEMA}".student_risk_snapshots')
    ).scalar()
    if snapshot_date is None:
        return []

    params: Dict[str, Any] = {"snapshot_date": snapshot_date, "limit": limit}
    level_filter = ""
    if risk_level in {"low", "medium", "high"}:
        params["risk_level"] = risk_level
        level_filter = "AND s.risk_level = :risk_level"

    return _fetchall_dict(
        db,
        f'''
        SELECT
            s.student_id::text AS student_id,
            u.full_name,
            u.email,
            u.programme,
            u.year_of_study,
            s.snapshot_date,
            s.overall_risk_score,
            s.risk_level,
            s.overdue_assessment_count,
            s.upcoming_assessment_count,
            s.average_grade,
            s.grade_trend_delta,
            s.assessment_query_count,
            s.timetable_query_count,
            s.no_hit_query_count,
            s.unread_notification_count,
            s.risk_reasons
        FROM "{DB_SCHEMA}".student_risk_snapshots s
        JOIN "{DB_SCHEMA}".users u ON u.id = s.student_id
        WHERE s.snapshot_date = :snapshot_date
          {level_filter}
        ORDER BY s.overall_risk_score DESC, u.full_name ASC
        LIMIT :limit
        ''',
        params,
    )


def get_recommendations(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    overview = get_overview(db, days=days)
    unanswered = get_unanswered_questions(db, days=days, limit=5)
    spaces = get_spaces_demand(db, days=max(days, 30), limit=5)
    risks = get_students_at_risk(db, risk_level="high", limit=10)

    kpi_lookup = {item["label"]: item["value"] for item in overview["kpis"]}
    total_queries = float(kpi_lookup.get("Total queries", 0) or 0)
    no_hit_rate = float(kpi_lookup.get("No-hit rate (%)", 0) or 0)

    recs: List[Dict[str, Any]] = []

    if no_hit_rate >= 10:
        sample = unanswered[0]["question"] if unanswered else "high-frequency unanswered questions"
        recs.append({
            "title": "Expand the knowledge base for unanswered queries",
            "priority": "high",
            "rationale": f"The assistant no-hit rate is {no_hit_rate:.1f}% over the selected period, with gaps such as: {sample}.",
            "action": "Review the top unanswered themes and add or refresh source documents in the RAG knowledge base.",
        })

    if spaces.get("top_spaces"):
        top_space = spaces["top_spaces"][0]
        recs.append({
            "title": "Review space capacity around peak booking demand",
            "priority": "medium",
            "rationale": f"{top_space['name']} has the highest confirmed booking demand with {top_space['bookings']} bookings.",
            "action": "Consider increasing similar space availability, extending opening times, or redirecting students to alternative study spaces during peak hours.",
        })

    if risks:
        recs.append({
            "title": "Intervene early for high-risk students",
            "priority": "high",
            "rationale": f"{len(risks)} students currently appear in the high-risk segment in the latest snapshot.",
            "action": "Use the at-risk dashboard to identify students with falling grades, overdue work, or repeated unanswered queries and trigger targeted support.",
        })

    if total_queries and kpi_lookup.get("Space-booking queries", 0) > 0 and kpi_lookup.get("Average latency (ms)", 0) > 1500:
        recs.append({
            "title": "Optimise high-latency assistant journeys",
            "priority": "medium",
            "rationale": f"Average response latency is {kpi_lookup.get('Average latency (ms)', 0)} ms while multi-step booking flows are active.",
            "action": "Optimise slow tool paths first, especially booking and RAG retrieval, to reduce abandonment during multi-turn tasks.",
        })

    if not recs:
        recs.append({
            "title": "Continue monitoring baseline student demand",
            "priority": "low",
            "rationale": "Current analytics do not show a major service-risk hotspot in the selected period.",
            "action": "Keep collecting assistant telemetry and refresh the risk snapshots daily so trends remain visible to admins.",
        })

    return recs
