import logging
from datetime import datetime, timezone
import mysql.connector
from mysql.connector import pooling
import config

logger = logging.getLogger(__name__)

_pool = None

def _get_portfolio_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="portfolio_analytics_pool",
            pool_size=5,
            pool_reset_session=True,
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            database="portfolio_db",
        )
        logger.info("Portfolio MySQL connection pool created")
    return _pool

def get_portfolio_db():
    global _pool
    try:
        return _get_portfolio_pool().get_connection()
    except Exception as e:
        logger.warning(f"Portfolio pool connection failed, recreating pool: {e}")
        _pool = None
        return _get_portfolio_pool().get_connection()

def record_visit(data: dict) -> bool:
    sql = """
        INSERT INTO visits (
            session_id, visitor_id, page_url, path, referrer,
            user_agent, device_type, screen_width, screen_height,
            language, ip_address
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s
        )
    """
    params = (
        data.get("session_id", "")[:64],
        data.get("visitor_id", "")[:64],
        data.get("page_url", "")[:512],
        data.get("path", "")[:255],
        (data.get("referrer") or None)[:512] if data.get("referrer") else None,
        (data.get("user_agent") or None)[:512] if data.get("user_agent") else None,
        (data.get("device_type") or None)[:32] if data.get("device_type") else None,
        data.get("screen_width"),
        data.get("screen_height"),
        (data.get("language") or None)[:32] if data.get("language") else None,
        (data.get("ip_address") or None)[:64] if data.get("ip_address") else None,
    )
    conn = None
    try:
        conn = get_portfolio_db()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error recording portfolio visit: {e}")
        return False
    finally:
        if conn:
            conn.close()

def update_scroll(session_id: str, scroll_percent: int, scroll_px: int, duration_sec: int) -> bool:
    sql = """
        UPDATE visits
        SET max_scroll_percent = GREATEST(COALESCE(max_scroll_percent, 0), %s),
            max_scroll_px = GREATEST(COALESCE(max_scroll_px, 0), %s),
            duration_seconds = GREATEST(COALESCE(duration_seconds, 0), %s)
        WHERE session_id = %s
    """
    conn = None
    try:
        conn = get_portfolio_db()
        cursor = conn.cursor()
        cursor.execute(sql, (scroll_percent, scroll_px, duration_sec, session_id[:64]))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating portfolio scroll: {e}")
        return False
    finally:
        if conn:
            conn.close()

def record_click(data: dict) -> bool:
    sql = """
        INSERT INTO clicks (
            session_id, visitor_id, element_tag, element_id,
            element_classes, element_text, target_url,
            scroll_depth_percent, x_coord, y_coord
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s
        )
    """
    params = (
        data.get("session_id", "")[:64],
        (data.get("visitor_id") or None)[:64] if data.get("visitor_id") else None,
        (data.get("element_tag") or None)[:32] if data.get("element_tag") else None,
        (data.get("element_id") or None)[:128] if data.get("element_id") else None,
        (data.get("element_classes") or None)[:255] if data.get("element_classes") else None,
        (data.get("element_text") or None)[:255] if data.get("element_text") else None,
        (data.get("target_url") or None)[:512] if data.get("target_url") else None,
        data.get("scroll_depth_percent", 0),
        data.get("x_coord"),
        data.get("y_coord"),
    )
    conn = None
    try:
        conn = get_portfolio_db()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error recording portfolio click: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_summary() -> dict:
    conn = None
    try:
        conn = get_portfolio_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                COUNT(*) as total_visits,
                COUNT(DISTINCT visitor_id) as unique_visitors,
                COALESCE(AVG(max_scroll_percent), 0) as avg_scroll_percent,
                COALESCE(MAX(max_scroll_percent), 0) as max_scroll_percent,
                COALESCE(AVG(duration_seconds), 0) as avg_duration_seconds
            FROM visits
        """)
        stats = cursor.fetchone() or {}

        cursor.execute("SELECT COUNT(*) as total_clicks FROM clicks")
        click_stats = cursor.fetchone() or {}
        stats["total_clicks"] = click_stats.get("total_clicks", 0)

        # Device breakdown
        cursor.execute("""
            SELECT COALESCE(device_type, 'unknown') as device, COUNT(*) as count
            FROM visits
            GROUP BY device
            ORDER BY count DESC
        """)
        devices = cursor.fetchall()

        # Scroll depth buckets
        cursor.execute("""
            SELECT
                SUM(CASE WHEN max_scroll_percent < 25 THEN 1 ELSE 0 END) as s_0_25,
                SUM(CASE WHEN max_scroll_percent >= 25 AND max_scroll_percent < 50 THEN 1 ELSE 0 END) as s_25_50,
                SUM(CASE WHEN max_scroll_percent >= 50 AND max_scroll_percent < 75 THEN 1 ELSE 0 END) as s_50_75,
                SUM(CASE WHEN max_scroll_percent >= 75 THEN 1 ELSE 0 END) as s_75_100
            FROM visits
        """)
        scroll_buckets = cursor.fetchone() or {}

        # Top clicks
        cursor.execute("""
            SELECT COALESCE(element_text, element_id, target_url, element_tag) as title,
                   element_tag, target_url, COUNT(*) as clicks_count
            FROM clicks
            GROUP BY title, element_tag, target_url
            ORDER BY clicks_count DESC
            LIMIT 10
        """)
        top_clicks = cursor.fetchall()

        # Top referrers
        cursor.execute("""
            SELECT COALESCE(NULLIF(referrer, ''), 'Direct / None') as referrer_source, COUNT(*) as count
            FROM visits
            GROUP BY referrer_source
            ORDER BY count DESC
            LIMIT 8
        """)
        top_referrers = cursor.fetchall()

        return {
            "total_visits": stats.get("total_visits", 0),
            "unique_visitors": stats.get("unique_visitors", 0),
            "total_clicks": stats.get("total_clicks", 0),
            "avg_scroll_percent": round(float(stats.get("avg_scroll_percent", 0)), 1),
            "max_scroll_percent": stats.get("max_scroll_percent", 0),
            "avg_duration_seconds": round(float(stats.get("avg_duration_seconds", 0)), 1),
            "devices": devices,
            "scroll_buckets": scroll_buckets,
            "top_clicks": top_clicks,
            "top_referrers": top_referrers,
        }
    except Exception as e:
        logger.error(f"Error fetching portfolio summary: {e}")
        return {}
    finally:
        if conn:
            conn.close()

def get_visits(page: int = 1, limit: int = 50, search: str = None, device: str = None) -> dict:
    conn = None
    try:
        conn = get_portfolio_db()
        cursor = conn.cursor(dictionary=True)

        where_clauses = []
        params = []

        if search:
            where_clauses.append("(v.session_id LIKE %s OR v.visitor_id LIKE %s OR v.ip_address LIKE %s OR v.referrer LIKE %s)")
            like_s = f"%{search}%"
            params.extend([like_s, like_s, like_s, like_s])

        if device and device != "all":
            where_clauses.append("v.device_type = %s")
            params.append(device)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Total count
        cursor.execute(f"SELECT COUNT(*) as total FROM visits v {where_sql}", tuple(params))
        total = (cursor.fetchone() or {}).get("total", 0)

        offset = max(0, (page - 1) * limit)
        query = f"""
            SELECT
                v.id, v.session_id, v.visitor_id, v.page_url, v.path, v.referrer,
                v.user_agent, v.device_type, v.screen_width, v.screen_height,
                v.language, v.ip_address, v.max_scroll_percent, v.max_scroll_px,
                v.duration_seconds, v.created_at, v.updated_at,
                COUNT(c.id) as clicks_count
            FROM visits v
            LEFT JOIN clicks c ON c.session_id = v.session_id
            {where_sql}
            GROUP BY v.id
            ORDER BY v.created_at DESC
            LIMIT %s OFFSET %s
        """
        exec_params = list(params) + [limit, offset]
        cursor.execute(query, tuple(exec_params))
        rows = cursor.fetchall()

        # Format datetimes
        for r in rows:
            if isinstance(r.get("created_at"), datetime):
                r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(r.get("updated_at"), datetime):
                r["updated_at"] = r["updated_at"].strftime("%Y-%m-%d %H:%M:%S")

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "items": rows,
        }
    except Exception as e:
        logger.error(f"Error fetching portfolio visits: {e}")
        return {"total": 0, "page": page, "limit": limit, "items": []}
    finally:
        if conn:
            conn.close()

def get_clicks(page: int = 1, limit: int = 50, session_id: str = None, search: str = None) -> dict:
    conn = None
    try:
        conn = get_portfolio_db()
        cursor = conn.cursor(dictionary=True)

        where_clauses = []
        params = []

        if session_id:
            where_clauses.append("c.session_id = %s")
            params.append(session_id)

        if search:
            where_clauses.append("(c.element_text LIKE %s OR c.target_url LIKE %s OR c.element_id LIKE %s OR c.session_id LIKE %s)")
            like_s = f"%{search}%"
            params.extend([like_s, like_s, like_s, like_s])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cursor.execute(f"SELECT COUNT(*) as total FROM clicks c {where_sql}", tuple(params))
        total = (cursor.fetchone() or {}).get("total", 0)

        offset = max(0, (page - 1) * limit)
        query = f"""
            SELECT
                c.id, c.session_id, c.visitor_id, c.element_tag, c.element_id,
                c.element_classes, c.element_text, c.target_url,
                c.scroll_depth_percent, c.x_coord, c.y_coord, c.created_at
            FROM clicks c
            {where_sql}
            ORDER BY c.created_at DESC
            LIMIT %s OFFSET %s
        """
        exec_params = list(params) + [limit, offset]
        cursor.execute(query, tuple(exec_params))
        rows = cursor.fetchall()

        for r in rows:
            if isinstance(r.get("created_at"), datetime):
                r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "items": rows,
        }
    except Exception as e:
        logger.error(f"Error fetching portfolio clicks: {e}")
        return {"total": 0, "page": page, "limit": limit, "items": []}
    finally:
        if conn:
            conn.close()

def get_session_details(session_id: str) -> dict:
    conn = None
    try:
        conn = get_portfolio_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM visits WHERE session_id = %s ORDER BY created_at DESC LIMIT 1", (session_id,))
        visit = cursor.fetchone()
        if visit:
            if isinstance(visit.get("created_at"), datetime):
                visit["created_at"] = visit["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(visit.get("updated_at"), datetime):
                visit["updated_at"] = visit["updated_at"].strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("SELECT * FROM clicks WHERE session_id = %s ORDER BY created_at ASC", (session_id,))
        clicks = cursor.fetchall()
        for c in clicks:
            if isinstance(c.get("created_at"), datetime):
                c["created_at"] = c["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        return {
            "visit": visit,
            "clicks": clicks,
        }
    except Exception as e:
        logger.error(f"Error fetching session details: {e}")
        return {"visit": None, "clicks": []}
    finally:
        if conn:
            conn.close()
