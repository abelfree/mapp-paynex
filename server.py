from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "mapp.db"

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

TASKS = [
    {"id": 1, "title": "Web Visit 15s", "reward": 0.10, "cooldown": 15},
    {"id": 2, "title": "Web Visit 30s", "reward": 0.10, "cooldown": 30},
    {"id": 3, "title": "Visit Website 50s", "reward": 0.10, "cooldown": 50},
    {"id": 4, "title": "Watch Short Video", "reward": 0.10, "cooldown": 45},
    {"id": 5, "title": "Join Telegram Channel", "reward": 0.10, "cooldown": 60},
    {"id": 6, "title": "Visit Website 1 Min", "reward": 0.15, "cooldown": 60},
]
TASK_MAP = {task["id"]: task for task in TASKS}

DAILY_LIMIT = 15
MIN_WITHDRAW = 5.0
DEMO_USER_ID = 1
AD_SESSION_TTL_MINUTES = 20

MONETAG_SDK_SRC = os.getenv("MONETAG_SDK_SRC", "").strip()
MONETAG_MAIN_ZONE = os.getenv("MONETAG_MAIN_ZONE", "").strip()
MONETAG_SHOW_FN = os.getenv("MONETAG_SHOW_FN", "").strip()
MONETAG_POSTBACK_TOKEN = os.getenv("MONETAG_POSTBACK_TOKEN", "").strip()
ALLOW_SIMULATE_VALUED = os.getenv("ALLOW_SIMULATE_VALUED", "true").lower() == "true"


class WithdrawRequest(BaseModel):
    method: str = Field(min_length=2, max_length=40)
    account: str = Field(min_length=3, max_length=120)
    amount: float = Field(gt=0)


class WithdrawResponse(BaseModel):
    ok: bool
    message: str
    balance: float


class AccountCheckRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    device_id: str = Field(min_length=8, max_length=128)


class StartTaskResponse(BaseModel):
    session_id: str
    ad_url: str


app = FastAPI(title="Mapp Task Platform")
app.mount("/static", StaticFiles(directory=BASE_DIR), name="static")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def day_stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def task_by_id(task_id: int) -> dict[str, Any]:
    task = TASK_MAP.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def setup_db() -> None:
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            ads_watched INTEGER NOT NULL DEFAULT 0,
            daily_ads INTEGER NOT NULL DEFAULT 0,
            daily_stamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_runs (
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            next_available_at TEXT NOT NULL,
            PRIMARY KEY(user_id, task_id)
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            account TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ad_sessions (
            id TEXT PRIMARY KEY,
            ymid TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            credited INTEGER NOT NULL DEFAULT 0,
            request_var TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            completed_at TEXT,
            credited_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ad_postbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ymid TEXT,
            event_type TEXT,
            reward_event_type TEXT,
            zone_id TEXT,
            sub_zone_id TEXT,
            telegram_id TEXT,
            request_var TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS device_accounts (
            device_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY(device_id, user_id)
        );
        """
    )

    today = day_stamp(utcnow())
    exists = conn.execute("SELECT id FROM users WHERE id = ?", (DEMO_USER_ID,)).fetchone()
    if not exists:
        conn.execute(
            """
            INSERT INTO users (id, username, balance, ads_watched, daily_ads, daily_stamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (DEMO_USER_ID, "abel", 1.9, 10, 10, today),
        )

    for task in TASKS:
        present = conn.execute(
            "SELECT 1 FROM task_runs WHERE user_id = ? AND task_id = ?",
            (DEMO_USER_ID, task["id"]),
        ).fetchone()
        if not present:
            conn.execute(
                "INSERT INTO task_runs (user_id, task_id, next_available_at) VALUES (?, ?, ?)",
                (DEMO_USER_ID, task["id"], "1970-01-01T00:00:00+00:00"),
            )

    conn.commit()
    conn.close()


def refresh_daily(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = day_stamp(utcnow())
    if user["daily_stamp"] != today:
        conn.execute(
            "UPDATE users SET daily_ads = 0, daily_stamp = ? WHERE id = ?",
            (today, user_id),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    return user


def task_with_status(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    now = utcnow()
    rows = conn.execute(
        "SELECT task_id, next_available_at FROM task_runs WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    next_map = {row["task_id"]: datetime.fromisoformat(row["next_available_at"]) for row in rows}

    active_rows = conn.execute(
        """
        SELECT id, task_id FROM ad_sessions
        WHERE user_id = ?
          AND status IN ('created', 'client_done')
          AND expires_at > ?
        """,
        (user_id, now.isoformat()),
    ).fetchall()
    active_map = {row["task_id"]: row["id"] for row in active_rows}

    out: list[dict[str, Any]] = []
    for task in TASKS:
        next_at = next_map.get(task["id"], datetime(1970, 1, 1, tzinfo=timezone.utc))
        secs = max(0, int((next_at - now).total_seconds()))
        out.append(
            {
                "id": task["id"],
                "title": task["title"],
                "reward": task["reward"],
                "cooldown": task["cooldown"],
                "remaining_seconds": secs,
                "active_session_id": active_map.get(task["id"]),
            }
        )
    return out


def can_start_task(conn: sqlite3.Connection, user: sqlite3.Row, task_id: int) -> None:
    if int(user["daily_ads"]) >= DAILY_LIMIT:
        raise HTTPException(status_code=400, detail="Daily limit reached")

    row = conn.execute(
        "SELECT next_available_at FROM task_runs WHERE user_id = ? AND task_id = ?",
        (DEMO_USER_ID, task_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task tracking not found")

    now = utcnow()
    next_available_at = datetime.fromisoformat(row["next_available_at"])
    if next_available_at > now:
        seconds = int((next_available_at - now).total_seconds())
        raise HTTPException(status_code=400, detail=f"Task on cooldown: {seconds}s")


def credit_session(conn: sqlite3.Connection, session: sqlite3.Row) -> bool:
    if int(session["credited"]) == 1:
        return False

    now = utcnow()
    if datetime.fromisoformat(session["expires_at"]) < now:
        return False

    task = task_by_id(int(session["task_id"]))
    user = refresh_daily(conn, int(session["user_id"]))

    if int(user["daily_ads"]) >= DAILY_LIMIT:
        return False

    new_balance = round(float(user["balance"]) + float(task["reward"]), 3)
    new_ads = int(user["ads_watched"]) + 1
    new_daily = int(user["daily_ads"]) + 1
    next_at = now + timedelta(seconds=int(task["cooldown"]))

    conn.execute(
        "UPDATE users SET balance = ?, ads_watched = ?, daily_ads = ? WHERE id = ?",
        (new_balance, new_ads, new_daily, int(session["user_id"])),
    )
    conn.execute(
        "UPDATE task_runs SET next_available_at = ? WHERE user_id = ? AND task_id = ?",
        (next_at.isoformat(), int(session["user_id"]), int(session["task_id"])),
    )
    conn.execute(
        """
        UPDATE ad_sessions
        SET credited = 1, status = 'verified', credited_at = ?, completed_at = COALESCE(completed_at, ?)
        WHERE id = ?
        """,
        (now.isoformat(), now.isoformat(), session["id"]),
    )
    conn.commit()
    return True


def monetag_enabled() -> bool:
    return bool(MONETAG_SDK_SRC and MONETAG_MAIN_ZONE)


@app.on_event("startup")
def on_startup() -> None:
    setup_db()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/task")
def task_page() -> FileResponse:
    return FileResponse(BASE_DIR / "ad-task.html")


@app.get("/api/me")
def me() -> dict[str, Any]:
    conn = db()
    user = refresh_daily(conn, DEMO_USER_ID)
    payload = {
        "username": user["username"],
        "balance": round(float(user["balance"]), 3),
        "ads_watched": int(user["ads_watched"]),
        "daily_ads": int(user["daily_ads"]),
        "daily_limit": DAILY_LIMIT,
        "referrals": 0,
    }
    conn.close()
    return payload


@app.get("/api/tasks")
def list_tasks() -> list[dict[str, Any]]:
    conn = db()
    refresh_daily(conn, DEMO_USER_ID)
    payload = task_with_status(conn, DEMO_USER_ID)
    conn.close()
    return payload


@app.post("/api/account/check")
def account_check(req: AccountCheckRequest) -> dict[str, Any]:
    now = utcnow().isoformat()
    conn = db()
    conn.execute(
        """
        INSERT INTO device_accounts (device_id, user_id, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(device_id, user_id)
        DO UPDATE SET last_seen_at = excluded.last_seen_at
        """,
        (req.device_id, req.telegram_id, now, now),
    )
    row = conn.execute(
        "SELECT COUNT(DISTINCT user_id) AS c FROM device_accounts WHERE device_id = ?",
        (req.device_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    account_count = int(row["c"]) if row else 0
    return {"multiple_accounts": account_count > 1, "account_count": account_count}


@app.post("/api/tasks/{task_id}/start", response_model=StartTaskResponse)
def start_task(task_id: int) -> StartTaskResponse:
    task_by_id(task_id)

    conn = db()
    user = refresh_daily(conn, DEMO_USER_ID)
    can_start_task(conn, user, task_id)

    now = utcnow()
    existing = conn.execute(
        """
        SELECT id FROM ad_sessions
        WHERE user_id = ?
          AND task_id = ?
          AND status IN ('created', 'client_done')
          AND expires_at > ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (DEMO_USER_ID, task_id, now.isoformat()),
    ).fetchone()

    if existing:
        conn.close()
        session_id = existing["id"]
        return StartTaskResponse(session_id=session_id, ad_url=f"/task?sid={session_id}")

    session_id = str(uuid4())
    ymid = f"u{DEMO_USER_ID}_t{task_id}_{uuid4().hex[:12]}"
    expires_at = now + timedelta(minutes=AD_SESSION_TTL_MINUTES)

    conn.execute(
        """
        INSERT INTO ad_sessions (
          id, ymid, user_id, task_id, provider, status, credited, request_var, created_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            ymid,
            DEMO_USER_ID,
            task_id,
            "monetag",
            "created",
            0,
            f"task_{task_id}",
            now.isoformat(),
            expires_at.isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    return StartTaskResponse(session_id=session_id, ad_url=f"/task?sid={session_id}")


@app.get("/api/ad/sessions/{session_id}")
def ad_session(session_id: str) -> dict[str, Any]:
    conn = db()
    row = conn.execute(
        "SELECT * FROM ad_sessions WHERE id = ? AND user_id = ?",
        (session_id, DEMO_USER_ID),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ad session not found")

    task = task_by_id(int(row["task_id"]))
    payload = {
        "session_id": row["id"],
        "status": row["status"],
        "credited": bool(row["credited"]),
        "expires_at": row["expires_at"],
        "task": {"id": task["id"], "title": task["title"], "reward": task["reward"]},
        "provider": {
            "name": "monetag",
            "enabled": monetag_enabled(),
            "sdk_src": MONETAG_SDK_SRC,
            "zone_id": MONETAG_MAIN_ZONE,
            "show_fn": MONETAG_SHOW_FN or (f"show_{MONETAG_MAIN_ZONE}" if MONETAG_MAIN_ZONE else ""),
            "ymid": row["ymid"],
            "request_var": row["request_var"],
        },
        "allow_simulate": ALLOW_SIMULATE_VALUED,
    }
    conn.close()
    return payload


@app.post("/api/ad/sessions/{session_id}/client-done")
def ad_session_client_done(session_id: str) -> dict[str, Any]:
    now = utcnow().isoformat()
    conn = db()
    row = conn.execute(
        "SELECT * FROM ad_sessions WHERE id = ? AND user_id = ?",
        (session_id, DEMO_USER_ID),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ad session not found")

    if row["status"] == "created":
        conn.execute(
            "UPDATE ad_sessions SET status = 'client_done', completed_at = ? WHERE id = ?",
            (now, session_id),
        )
        conn.commit()

    out = conn.execute("SELECT status, credited FROM ad_sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    return {"status": out["status"], "credited": bool(out["credited"]) }


@app.get("/api/ad/sessions/{session_id}/status")
def ad_session_status(session_id: str) -> dict[str, Any]:
    conn = db()
    row = conn.execute(
        "SELECT status, credited FROM ad_sessions WHERE id = ? AND user_id = ?",
        (session_id, DEMO_USER_ID),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ad session not found")

    user = refresh_daily(conn, DEMO_USER_ID)
    payload = {
        "status": row["status"],
        "credited": bool(row["credited"]),
        "balance": round(float(user["balance"]), 3),
        "ads_watched": int(user["ads_watched"]),
        "daily_ads": int(user["daily_ads"]),
        "daily_limit": DAILY_LIMIT,
    }
    conn.close()
    return payload


@app.post("/api/ad/sessions/{session_id}/simulate-valued")
def simulate_valued(session_id: str) -> dict[str, Any]:
    if not ALLOW_SIMULATE_VALUED:
        raise HTTPException(status_code=403, detail="Simulation disabled")

    conn = db()
    row = conn.execute(
        "SELECT * FROM ad_sessions WHERE id = ? AND user_id = ?",
        (session_id, DEMO_USER_ID),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ad session not found")

    credited_now = credit_session(conn, row)
    out = conn.execute("SELECT status, credited FROM ad_sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    return {"status": out["status"], "credited": bool(out["credited"]), "credited_now": credited_now}


@app.post("/api/monetag/postback")
@app.get("/api/monetag/postback")
async def monetag_postback(request: Request) -> JSONResponse:
    query = dict(request.query_params)
    body: dict[str, Any] = {}
    ctype = request.headers.get("content-type", "")

    if "application/json" in ctype:
        try:
            body = await request.json()
        except Exception:
            body = {}

    token = query.get("token") or request.headers.get("x-postback-token", "")
    if MONETAG_POSTBACK_TOKEN and token != MONETAG_POSTBACK_TOKEN:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    payload = {**query, **body}
    ymid = str(payload.get("ymid", "")).strip()
    event_type = str(payload.get("event_type", "")).strip().lower()
    reward_event_type = str(payload.get("reward_event_type", "")).strip().lower()

    conn = db()
    conn.execute(
        """
        INSERT INTO ad_postbacks (
          ymid, event_type, reward_event_type, zone_id, sub_zone_id, telegram_id, request_var, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ymid,
            event_type,
            reward_event_type,
            str(payload.get("zone_id", "")),
            str(payload.get("sub_zone_id", "")),
            str(payload.get("telegram_id", "")),
            str(payload.get("request_var", "")),
            json.dumps(payload),
            utcnow().isoformat(),
        ),
    )

    credited_now = False
    if ymid:
        session = conn.execute("SELECT * FROM ad_sessions WHERE ymid = ?", (ymid,)).fetchone()
        if session and reward_event_type == "valued":
            credited_now = credit_session(conn, session)

    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "credited_now": credited_now})


@app.post("/api/withdraw", response_model=WithdrawResponse)
def withdraw(req: WithdrawRequest) -> WithdrawResponse:
    conn = db()
    user = refresh_daily(conn, DEMO_USER_ID)

    if req.amount < MIN_WITHDRAW:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Minimum withdrawal is ${MIN_WITHDRAW:.2f}")

    balance = float(user["balance"])
    if req.amount > balance:
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient balance")

    new_balance = round(balance - req.amount, 3)

    conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, DEMO_USER_ID))
    conn.execute(
        """
        INSERT INTO withdrawals (user_id, method, account, amount, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (DEMO_USER_ID, req.method, req.account, req.amount, "pending", utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    return WithdrawResponse(ok=True, message="Withdrawal request submitted", balance=new_balance)
