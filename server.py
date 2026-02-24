from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "mapp.db")))
load_dotenv(BASE_DIR.parent / ".env")

MONETAG_SDK_SRC = os.getenv("MONETAG_SDK_SRC", "").strip()
MONETAG_ZONE_ID = os.getenv("MONETAG_ZONE_ID", "").strip()
MONETAG_SHOW_FN = os.getenv("MONETAG_SHOW_FN", "").strip()
MONETAG_VIDEO_SHOW_FN = os.getenv("MONETAG_VIDEO_SHOW_FN", "").strip()
MONETAG_POSTBACK_TOKEN = os.getenv("MONETAG_POSTBACK_TOKEN", "").strip()
ADS_ALLOW_SIMULATE = os.getenv("ADS_ALLOW_SIMULATE", "true").lower() == "true"

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "10"))
MIN_WITHDRAW = float(os.getenv("MIN_WITHDRAW", "5.0"))
_macro_count = int(os.getenv("MONETAG_MACRO_TASKS_PER_DAY", "4"))
MONETAG_MACRO_TASKS_PER_DAY = max(1, min(_macro_count, 8))

MICRO_TASKS = [
    {"id": 1, "title": "Web Visit 15s", "reward": 0.10, "cooldown": 15, "kind": "web", "tier": "micro"},
    {"id": 2, "title": "Web Visit 30s", "reward": 0.10, "cooldown": 30, "kind": "web", "tier": "micro"},
    {"id": 3, "title": "Web Visit 50s", "reward": 0.10, "cooldown": 50, "kind": "web", "tier": "micro"},
    {"id": 4, "title": "Watch Short Video", "reward": 0.10, "cooldown": 45, "kind": "video", "tier": "micro"},
    {"id": 5, "title": "Watch Rewarded Clip", "reward": 0.12, "cooldown": 60, "kind": "video", "tier": "micro"},
]

MACRO_TEMPLATES = [
    {"title": "Watch Premium Video", "reward": 0.25, "cooldown": 120, "kind": "video", "tier": "macro"},
    {"title": "Complete Survey Offer", "reward": 0.35, "cooldown": 180, "kind": "web", "tier": "macro"},
    {"title": "Open Offer Wall Deal", "reward": 0.30, "cooldown": 150, "kind": "web", "tier": "macro"},
    {"title": "Watch Long Video", "reward": 0.28, "cooldown": 150, "kind": "video", "tier": "macro"},
    {"title": "Try Partner Landing Page", "reward": 0.22, "cooldown": 120, "kind": "web", "tier": "macro"},
    {"title": "Complete Video Challenge", "reward": 0.32, "cooldown": 180, "kind": "video", "tier": "macro"},
]


class StateRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    username: str = "user"
    device_id: str = Field(min_length=8, max_length=128)


class StartAdRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    task_id: int = Field(gt=0)


class WithdrawRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    method: str = Field(min_length=2)
    account: str = Field(min_length=3)
    amount: float = Field(gt=0)


app = FastAPI(title="momoney API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=BASE_DIR), name="static")


def now() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    return now().strftime("%Y-%m-%d")


def today_int() -> int:
    return int(now().strftime("%Y%m%d"))


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ad_sessions)").fetchall()}
    if "task_title" not in cols:
        conn.execute("ALTER TABLE ad_sessions ADD COLUMN task_title TEXT")
    if "task_kind" not in cols:
        conn.execute("ALTER TABLE ad_sessions ADD COLUMN task_kind TEXT")
    if "reward" not in cols:
        conn.execute("ALTER TABLE ad_sessions ADD COLUMN reward REAL")
    if "cooldown" not in cols:
        conn.execute("ALTER TABLE ad_sessions ADD COLUMN cooldown INTEGER")


def init_db() -> None:
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            ads_watched INTEGER NOT NULL DEFAULT 0,
            daily_ads INTEGER NOT NULL DEFAULT 0,
            daily_stamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_runs (
            telegram_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            next_available_at TEXT NOT NULL,
            PRIMARY KEY(telegram_id, task_id)
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            account TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS device_accounts (
            device_id TEXT NOT NULL,
            telegram_id INTEGER NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY(device_id, telegram_id)
        );

        CREATE TABLE IF NOT EXISTS ad_sessions (
            id TEXT PRIMARY KEY,
            ymid TEXT NOT NULL UNIQUE,
            telegram_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            task_title TEXT,
            task_kind TEXT,
            reward REAL,
            cooldown INTEGER,
            status TEXT NOT NULL,
            credited INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """
    )
    _migrate(conn)
    conn.commit()
    conn.close()


def build_task_catalog(telegram_id: int) -> list[dict]:
    tasks = [dict(t) for t in MICRO_TASKS]
    seed = today_int() + telegram_id
    daily_base = 2_000_000 + today_int() * 10
    for idx in range(MONETAG_MACRO_TASKS_PER_DAY):
        template = MACRO_TEMPLATES[(seed + idx) % len(MACRO_TEMPLATES)]
        task = dict(template)
        task["id"] = daily_base + idx + 1
        tasks.append(task)
    return tasks


def build_task_map(telegram_id: int) -> dict[int, dict]:
    return {int(t["id"]): t for t in build_task_catalog(telegram_id)}


def ensure_task_rows(conn: sqlite3.Connection, telegram_id: int, task_map: dict[int, dict]) -> None:
    for task_id in task_map:
        conn.execute(
            "INSERT OR IGNORE INTO task_runs (telegram_id, task_id, next_available_at) VALUES (?, ?, ?)",
            (telegram_id, int(task_id), "1970-01-01T00:00:00+00:00"),
        )


def ensure_user(conn: sqlite3.Connection, telegram_id: int, username: str, task_map: dict[int, dict]) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO users (telegram_id, username, balance, ads_watched, daily_ads, daily_stamp) VALUES (?, ?, ?, ?, ?, ?)",
            (telegram_id, username, 0, 0, 0, today_str()),
        )
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    ensure_task_rows(conn, telegram_id, task_map)
    conn.commit()
    return row


def refresh_daily(conn: sqlite3.Connection, telegram_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row["daily_stamp"] != today_str():
        conn.execute("UPDATE users SET daily_ads = 0, daily_stamp = ? WHERE telegram_id = ?", (today_str(), telegram_id))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return row


def task_payload(conn: sqlite3.Connection, telegram_id: int, task_map: dict[int, dict]) -> list[dict]:
    n = now()
    runs = conn.execute("SELECT task_id, next_available_at FROM task_runs WHERE telegram_id = ?", (telegram_id,)).fetchall()
    next_map = {int(r["task_id"]): datetime.fromisoformat(r["next_available_at"]) for r in runs}
    out = []
    for task_id, task in task_map.items():
        rem = max(0, int((next_map.get(task_id, datetime(1970, 1, 1, tzinfo=timezone.utc)) - n).total_seconds()))
        out.append(
            {
                "id": task_id,
                "title": task["title"],
                "reward": float(task["reward"]),
                "remaining_seconds": rem,
                "kind": task.get("kind", "web"),
                "tier": task.get("tier", "micro"),
            }
        )
    out.sort(key=lambda t: (0 if t["tier"] == "micro" else 1, t["id"]))
    return out


def show_fn_for_kind(kind: str) -> str:
    if kind == "video" and MONETAG_VIDEO_SHOW_FN:
        return MONETAG_VIDEO_SHOW_FN
    return MONETAG_SHOW_FN or (f"show_{MONETAG_ZONE_ID}" if MONETAG_ZONE_ID else "")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.post("/api/state")
def state(req: StateRequest):
    conn = db()
    task_map = build_task_map(req.telegram_id)
    ensure_user(conn, req.telegram_id, req.username, task_map)
    u = refresh_daily(conn, req.telegram_id)

    n = now().isoformat()
    conn.execute(
        "INSERT INTO device_accounts (device_id, telegram_id, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?) ON CONFLICT(device_id, telegram_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
        (req.device_id, req.telegram_id, n, n),
    )
    c = conn.execute("SELECT COUNT(DISTINCT telegram_id) AS c FROM device_accounts WHERE device_id = ?", (req.device_id,)).fetchone()
    conn.commit()

    all_tasks = task_payload(conn, req.telegram_id, task_map)
    micro_tasks = [t for t in all_tasks if t["tier"] == "micro"]
    macro_tasks = [t for t in all_tasks if t["tier"] == "macro"]
    payload = {
        "username": u["username"],
        "balance": round(float(u["balance"]), 3),
        "ads_watched": int(u["ads_watched"]),
        "daily_ads": int(u["daily_ads"]),
        "daily_limit": DAILY_LIMIT,
        "referrals": 0,
        "multiple_accounts": int(c["c"]) > 1,
        "tasks": all_tasks,
        "micro_tasks": micro_tasks,
        "macro_tasks": macro_tasks,
        "monetag": {
            "enabled": bool(MONETAG_SDK_SRC and MONETAG_ZONE_ID),
            "sdk_src": MONETAG_SDK_SRC,
            "show_fn": show_fn_for_kind("web"),
            "video_show_fn": show_fn_for_kind("video"),
        },
    }
    conn.close()
    return payload


@app.post("/api/ads/start")
def start_ad(req: StartAdRequest):
    conn = db()
    task_map = build_task_map(req.telegram_id)
    task = task_map.get(req.task_id)
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")

    ensure_task_rows(conn, req.telegram_id, task_map)
    u = refresh_daily(conn, req.telegram_id)
    if int(u["daily_ads"]) >= DAILY_LIMIT:
        conn.close()
        raise HTTPException(status_code=400, detail="Daily limit reached")

    run = conn.execute(
        "SELECT next_available_at FROM task_runs WHERE telegram_id = ? AND task_id = ?",
        (req.telegram_id, req.task_id),
    ).fetchone()
    if not run:
        conn.close()
        raise HTTPException(status_code=404, detail="Task run row missing")

    if datetime.fromisoformat(run["next_available_at"]) > now():
        conn.close()
        raise HTTPException(status_code=400, detail="Task cooling down")

    sid = str(uuid4())
    ymid = f"u{req.telegram_id}_t{req.task_id}_{uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO ad_sessions (id, ymid, telegram_id, task_id, task_title, task_kind, reward, cooldown, status, credited, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', 0, ?, ?)",
        (
            sid,
            ymid,
            req.telegram_id,
            req.task_id,
            task["title"],
            task["kind"],
            float(task["reward"]),
            int(task["cooldown"]),
            now().isoformat(),
            (now() + timedelta(minutes=20)).isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    return {
        "session_id": sid,
        "ymid": ymid,
        "show_fn": show_fn_for_kind(task["kind"]),
        "sdk_src": MONETAG_SDK_SRC,
        "allow_simulate": ADS_ALLOW_SIMULATE,
        "kind": task["kind"],
        "tier": task["tier"],
    }


def credit(conn: sqlite3.Connection, session_row: sqlite3.Row) -> bool:
    if int(session_row["credited"]) == 1:
        return False

    telegram_id = int(session_row["telegram_id"])
    u = refresh_daily(conn, telegram_id)
    if int(u["daily_ads"]) >= DAILY_LIMIT:
        return False

    reward = float(session_row["reward"] or 0)
    cooldown = int(session_row["cooldown"] or 30)
    task_id = int(session_row["task_id"])
    if reward <= 0:
        return False

    nb = round(float(u["balance"]) + reward, 3)
    na = int(u["ads_watched"]) + 1
    nd = int(u["daily_ads"]) + 1
    nx = now() + timedelta(seconds=cooldown)

    conn.execute("UPDATE users SET balance=?, ads_watched=?, daily_ads=? WHERE telegram_id=?", (nb, na, nd, telegram_id))
    conn.execute(
        "INSERT INTO task_runs (telegram_id, task_id, next_available_at) VALUES (?, ?, ?) ON CONFLICT(telegram_id, task_id) DO UPDATE SET next_available_at=excluded.next_available_at",
        (telegram_id, task_id, nx.isoformat()),
    )
    conn.execute("UPDATE ad_sessions SET credited=1, status='verified' WHERE id=?", (session_row["id"],))
    conn.commit()
    return True


@app.get("/api/ads/status/{session_id}")
def ad_status(session_id: str):
    conn = db()
    s = conn.execute("SELECT * FROM ad_sessions WHERE id = ?", (session_id,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(status_code=404, detail="Ad session not found")
    u = conn.execute("SELECT balance, ads_watched, daily_ads FROM users WHERE telegram_id=?", (s["telegram_id"],)).fetchone()
    out = {
        "credited": bool(s["credited"]),
        "status": s["status"],
        "balance": round(float(u["balance"]), 3),
        "ads_watched": int(u["ads_watched"]),
        "daily_ads": int(u["daily_ads"]),
        "daily_limit": DAILY_LIMIT,
    }
    conn.close()
    return out


@app.post("/api/ads/simulate/{session_id}")
def simulate(session_id: str):
    if not ADS_ALLOW_SIMULATE:
        raise HTTPException(status_code=403, detail="Simulate disabled")
    conn = db()
    s = conn.execute("SELECT * FROM ad_sessions WHERE id=?", (session_id,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(status_code=404, detail="Ad session not found")
    credited_now = credit(conn, s)
    conn.close()
    return {"ok": True, "credited_now": credited_now}


@app.get("/api/monetag/postback")
@app.post("/api/monetag/postback")
async def monetag_postback(request: Request):
    payload = dict(request.query_params)
    if "application/json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
            payload.update(body)
        except Exception:
            pass

    token = str(payload.get("token") or "")
    if MONETAG_POSTBACK_TOKEN and token != MONETAG_POSTBACK_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    ymid = str(payload.get("ymid") or "")
    reward_event = str(payload.get("reward_event_type") or "").lower()

    conn = db()
    credited_now = False
    if ymid and reward_event in {"valued", "rewarded", "completed"}:
        s = conn.execute("SELECT * FROM ad_sessions WHERE ymid = ?", (ymid,)).fetchone()
        if s:
            credited_now = credit(conn, s)
    conn.close()
    return {"ok": True, "credited_now": credited_now}


@app.post("/api/withdraw")
def withdraw(req: WithdrawRequest):
    conn = db()
    u = refresh_daily(conn, req.telegram_id)
    if req.amount < MIN_WITHDRAW:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Minimum withdrawal is ${MIN_WITHDRAW:.2f}")
    if req.amount > float(u["balance"]):
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient balance")

    nb = round(float(u["balance"]) - req.amount, 3)
    conn.execute("UPDATE users SET balance=? WHERE telegram_id=?", (nb, req.telegram_id))
    conn.execute(
        "INSERT INTO withdrawals (telegram_id, method, account, amount, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (req.telegram_id, req.method, req.account, req.amount, now().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Withdrawal request submitted", "balance": nb}
