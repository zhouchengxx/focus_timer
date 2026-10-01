import sys
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta

import tkinter as tk
from tkinter import ttk, messagebox


# ============================================================
# 基础配置
# ============================================================

APP_NAME = "Focus Timer"
DB_NAME = "focus_timer.db"

# 极简悬浮按钮大小
WINDOW_SIZE = 40

# 鼠标经过 / 离开后的透明度
ACTIVE_ALPHA = 0.96
INACTIVE_ALPHA = 0.60

# 拖动超过这个像素后，不再视为点击
DRAG_THRESHOLD = 4

DEFAULT_CATEGORIES = [
    "科研学术",
    "PWN学习",
    "英语学习",
    "临时事务",
    "生活/健身",
    "研会团总支",
]


# ============================================================
# 路径与工具函数
# ============================================================

def app_dir():
    """
    绿色版：
    - 直接运行 .py：数据库保存在 .py 同目录
    - PyInstaller 打包：数据库保存在 .exe 同目录
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = app_dir()
DB_PATH = APP_DIR / DB_NAME


def iso_now():
    return datetime.now().isoformat(timespec="seconds")


def fmt_clock(seconds):
    total = max(0, int(seconds))
    h, remain = divmod(total, 3600)
    m, s = divmod(remain, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_short(seconds):
    total = max(0, int(seconds))
    h, remain = divmod(total, 3600)
    m, s = divmod(remain, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"

class Database:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.init_database()

    def init_database(self):
        c = self.conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_sec INTEGER NOT NULL,
                note TEXT NOT NULL DEFAULT ''
            )
        """)

        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_started
            ON sessions(started_at)
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS categories(
                name TEXT PRIMARY KEY,
                sort_order INTEGER NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS active_state(
                id INTEGER PRIMARY KEY CHECK(id = 1),
                category TEXT NOT NULL DEFAULT '',
                session_started_at TEXT NOT NULL,
                elapsed_sec REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running', 'paused')),
                paused_at TEXT NOT NULL DEFAULT ''
            )
        """)

        columns = {
            row["name"]
            for row in c.execute("PRAGMA table_info(active_state)").fetchall()
        }
        if "paused_at" not in columns:
            c.execute("""
                ALTER TABLE active_state
                ADD COLUMN paused_at TEXT NOT NULL DEFAULT ''
            """)

        for i, name in enumerate(DEFAULT_CATEGORIES):
            c.execute(
                "INSERT OR IGNORE INTO categories(name, sort_order) VALUES(?, ?)",
                (name, i),
            )

        self.conn.commit()

    def get_setting(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.conn.execute("""
            INSERT INTO settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, str(value)))
        self.conn.commit()

    def get_categories(self):
        rows = self.conn.execute("""
            SELECT name FROM categories ORDER BY sort_order, name
        """).fetchall()
        return [r["name"] for r in rows]

    def add_category(self, category):
        category = category.strip()
        if not category:
            return
        row = self.conn.execute("""
            SELECT COALESCE(MAX(sort_order), -1) AS n FROM categories
        """).fetchone()
        self.conn.execute(
            "INSERT OR IGNORE INTO categories(name, sort_order) VALUES(?, ?)",
            (category, int(row["n"]) + 1),
        )
        self.conn.commit()

    def save_active(self, started_at, elapsed_sec, status, paused_at=""):
        self.conn.execute("""
            INSERT INTO active_state(
                id, category, session_started_at, elapsed_sec, status, paused_at
            )
            VALUES(1, '', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                category = '',
                session_started_at = excluded.session_started_at,
                elapsed_sec = excluded.elapsed_sec,
                status = excluded.status,
                paused_at = excluded.paused_at
        """, (started_at, float(elapsed_sec), status, paused_at))
        self.conn.commit()

    def get_active(self):
        return self.conn.execute(
            "SELECT * FROM active_state WHERE id = 1"
        ).fetchone()

    def clear_active(self):
        self.conn.execute("DELETE FROM active_state WHERE id = 1")
        self.conn.commit()

    def add_session(self, category, started_at, ended_at, duration_sec, note):
        self.conn.execute("""
            INSERT INTO sessions(
                category, started_at, ended_at, duration_sec, note
            ) VALUES(?, ?, ?, ?, ?)
        """, (
            category,
            started_at,
            ended_at,
            max(0, int(duration_sec)),
            note.strip(),
        ))
        self.conn.commit()

    def get_session(self, session_id):
        return self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

    def update_session(
        self,
        session_id,
        category,
        started_at,
        ended_at,
        duration_sec,
        note,
    ):
        self.conn.execute("""
            UPDATE sessions
            SET category = ?,
                started_at = ?,
                ended_at = ?,
                duration_sec = ?,
                note = ?
            WHERE id = ?
        """, (
            category,
            started_at,
            ended_at,
            max(0, int(duration_sec)),
            note.strip(),
            session_id,
        ))
        self.conn.commit()

    def sessions_between(self, start, end):
        return self.conn.execute("""
            SELECT * FROM sessions
            WHERE started_at >= ? AND started_at < ?
            ORDER BY started_at DESC
        """, (
            start.isoformat(timespec="seconds"),
            end.isoformat(timespec="seconds"),
        )).fetchall()

    def delete_session(self, session_id):
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.conn.commit()

    def close(self):
        self.conn.close()


class FinishDialog:
    def __init__(self, parent, db, duration):
        self.parent = parent
        self.db = db
        self.duration = duration
        self.result = None

        self.win = tk.Toplevel(parent)
        self.win.title("记录本次工作")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        self.category_var = tk.StringVar()
        self.note_var = tk.StringVar()
        self.build()
        self.center()

        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.grab_set()

        if self.category_var.get().strip():
            self.note_entry.focus_set()
        else:
            self.category_combo.focus_set()

        self.win.wait_window()

    def build(self):
        outer = ttk.Frame(self.win, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text=f"本次有效投入：{fmt_clock(self.duration)}",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 14))

        row1 = ttk.Frame(outer)
        row1.pack(fill="x", pady=(0, 10))
        ttk.Label(row1, text="类别", width=7).pack(side="left")

        self.category_combo = ttk.Combobox(
            row1,
            textvariable=self.category_var,
            values=self.db.get_categories(),
            width=31,
            state="normal",
        )
        self.category_combo.pack(side="left", fill="x", expand=True)

        last_category = self.db.get_setting("last_category", "")
        if last_category:
            self.category_var.set(last_category)

        row2 = ttk.Frame(outer)
        row2.pack(fill="x", pady=(0, 14))
        ttk.Label(row2, text="结果", width=7).pack(side="left")

        self.note_entry = ttk.Entry(
            row2,
            textvariable=self.note_var,
            width=38,
        )
        self.note_entry.pack(side="left", fill="x", expand=True)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")

        ttk.Button(
            buttons, text="取消", width=9, command=self.cancel
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            buttons, text="保存", width=9, command=self.save
        ).pack(side="right")

        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<Control-Return>", lambda e: self.save())

    def center(self):
        self.win.update_idletasks()
        width, height = 430, 175
        x = self.parent.winfo_rootx() + self.parent.winfo_width() // 2 - width // 2
        y = self.parent.winfo_rooty() + self.parent.winfo_height() // 2 - height // 2
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x = max(10, min(x, sw - width - 10))
        y = max(10, min(y, sh - height - 10))
        self.win.geometry(f"{width}x{height}+{x}+{y}")

    def save(self):
        category = self.category_var.get().strip()
        note = self.note_var.get().strip()
        if not category:
            messagebox.showwarning(
                "缺少类别", "请选择或输入一个事务类别。", parent=self.win
            )
            self.category_combo.focus_set()
            return
        self.result = (category, note)
        self.win.destroy()

    def cancel(self):
        self.result = None
        self.win.destroy()


class RecordDialog:
    """手动新增 / 编辑工作记录。日期表示开始日期。"""

    def __init__(self, parent, db, session=None):
        self.parent = parent
        self.db = db
        self.session = session
        self.result = None

        self.win = tk.Toplevel(parent)
        self.win.title("编辑记录" if session else "新增记录")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        self.date_var = tk.StringVar()
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.category_var = tk.StringVar()
        self.note_var = tk.StringVar()
        self.duration_var = tk.StringVar(value="有效时长：--")

        self._set_initial_values()
        self.build()
        self.center()

        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.grab_set()
        self.category_combo.focus_set()
        self.win.wait_window()

    def _set_initial_values(self):
        if self.session is not None:
            try:
                started = datetime.fromisoformat(self.session["started_at"])
                ended = datetime.fromisoformat(self.session["ended_at"])
            except Exception:
                started = datetime.now().replace(second=0, microsecond=0)
                ended = started + timedelta(hours=1)

            self.date_var.set(started.strftime("%Y-%m-%d"))
            self.start_var.set(started.strftime("%H:%M"))
            self.end_var.set(ended.strftime("%H:%M"))
            self.category_var.set(str(self.session["category"]))
            self.note_var.set(str(self.session["note"] or ""))
            return

        now = datetime.now().replace(second=0, microsecond=0)
        start = now - timedelta(hours=1)
        self.date_var.set(now.strftime("%Y-%m-%d"))
        self.start_var.set(start.strftime("%H:%M"))
        self.end_var.set(now.strftime("%H:%M"))

        last_category = self.db.get_setting("last_category", "")
        if last_category:
            self.category_var.set(last_category)

    def build(self):
        outer = ttk.Frame(self.win, padding=16)
        outer.pack(fill="both", expand=True)

        time_box = ttk.LabelFrame(outer, text="时间", padding=10)
        time_box.pack(fill="x")

        ttk.Label(time_box, text="日期", width=8).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        date_entry = ttk.Entry(time_box, textvariable=self.date_var, width=16)
        date_entry.grid(row=0, column=1, sticky="w", pady=(0, 8))
        ttk.Label(time_box, text="YYYY-MM-DD").grid(
            row=0, column=2, sticky="w", padx=(8, 0), pady=(0, 8)
        )

        ttk.Label(time_box, text="开始", width=8).grid(
            row=1, column=0, sticky="w", pady=(0, 8)
        )
        start_entry = ttk.Entry(time_box, textvariable=self.start_var, width=10)
        start_entry.grid(row=1, column=1, sticky="w", pady=(0, 8))
        ttk.Label(time_box, text="HH:MM").grid(
            row=1, column=2, sticky="w", padx=(8, 0), pady=(0, 8)
        )

        ttk.Label(time_box, text="结束", width=8).grid(
            row=2, column=0, sticky="w"
        )
        end_entry = ttk.Entry(time_box, textvariable=self.end_var, width=10)
        end_entry.grid(row=2, column=1, sticky="w")
        ttk.Label(time_box, text="早于开始则自动视为次日").grid(
            row=2, column=2, sticky="w", padx=(8, 0)
        )

        ttk.Label(
            outer,
            textvariable=self.duration_var,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(8, 12))

        category_row = ttk.Frame(outer)
        category_row.pack(fill="x", pady=(0, 10))
        ttk.Label(category_row, text="类别", width=8).pack(side="left")
        self.category_combo = ttk.Combobox(
            category_row,
            textvariable=self.category_var,
            values=self.db.get_categories(),
            state="normal",
            width=32,
        )
        self.category_combo.pack(side="left", fill="x", expand=True)

        note_row = ttk.Frame(outer)
        note_row.pack(fill="x", pady=(0, 14))
        ttk.Label(note_row, text="结果", width=8).pack(side="left")
        self.note_entry = ttk.Entry(
            note_row,
            textvariable=self.note_var,
            width=40,
        )
        self.note_entry.pack(side="left", fill="x", expand=True)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")
        ttk.Button(
            buttons,
            text="取消",
            width=9,
            command=self.cancel,
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            buttons,
            text="保存",
            width=9,
            command=self.save,
        ).pack(side="right")

        # 输入时间时即时显示计算结果；非法内容只显示 --，保存时再正式提示。
        for var in (self.date_var, self.start_var, self.end_var):
            var.trace_add("write", lambda *_: self.update_duration_preview())

        self.update_duration_preview()
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<Control-Return>", lambda e: self.save())

    def center(self):
        self.win.update_idletasks()
        width, height = 500, 315
        x = self.parent.winfo_rootx() + self.parent.winfo_width() // 2 - width // 2
        y = self.parent.winfo_rooty() + self.parent.winfo_height() // 2 - height // 2
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x = max(10, min(x, sw - width - 10))
        y = max(10, min(y, sh - height - 10))
        self.win.geometry(f"{width}x{height}+{x}+{y}")

    def parse_datetimes(self):
        date_text = self.date_var.get().strip()
        start_text = self.start_var.get().strip()
        end_text = self.end_var.get().strip()

        try:
            date_value = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("日期格式应为 YYYY-MM-DD，例如 2026-08-29。")

        try:
            start_time = datetime.strptime(start_text, "%H:%M").time()
        except ValueError:
            raise ValueError("开始时间格式应为 HH:MM，例如 09:30。")

        try:
            end_time = datetime.strptime(end_text, "%H:%M").time()
        except ValueError:
            raise ValueError("结束时间格式应为 HH:MM，例如 11:05。")

        started = datetime.combine(date_value, start_time)
        ended = datetime.combine(date_value, end_time)

        # 23:30 -> 00:40 自动理解为第二天 00:40。
        if ended < started:
            ended += timedelta(days=1)

        # 相同时间更可能是误输入，不自动解释为 24 小时。
        if ended == started:
            raise ValueError("开始时间和结束时间不能完全相同。")

        return started, ended

    def update_duration_preview(self):
        try:
            started, ended = self.parse_datetimes()
            seconds = int((ended - started).total_seconds())
            self.duration_var.set(f"有效时长：{fmt_clock(seconds)}")
        except ValueError:
            self.duration_var.set("有效时长：--")

    def save(self):
        category = self.category_var.get().strip()
        note = self.note_var.get().strip()

        if not category:
            messagebox.showwarning(
                "缺少类别",
                "请选择或输入一个事务类别。",
                parent=self.win,
            )
            self.category_combo.focus_set()
            return

        try:
            started, ended = self.parse_datetimes()
        except ValueError as exc:
            messagebox.showwarning(
                "时间格式有误",
                str(exc),
                parent=self.win,
            )
            return

        duration = int((ended - started).total_seconds())
        self.result = (
            category,
            started.isoformat(timespec="seconds"),
            ended.isoformat(timespec="seconds"),
            duration,
            note,
        )
        self.win.destroy()

    def cancel(self):
        self.result = None
        self.win.destroy()


class StatsWindow:
    def __init__(self, parent, db):
        self.db = db
        self.win = tk.Toplevel(parent)
        self.win.title("时间统计")
        self.win.geometry("990x735")
        self.win.minsize(800, 600)
        self.period = tk.StringVar(value="today")
        self.build()
        self.refresh()

    def build(self):
        outer = ttk.Frame(self.win, padding=12)
        outer.pack(fill="both", expand=True)

        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        for text, value in [("今天", "today"), ("本周", "week"), ("本月", "month")]:
            ttk.Radiobutton(
                bar,
                text=text,
                value=value,
                variable=self.period,
                command=self.refresh,
            ).pack(side="left", padx=(0, 12))
        ttk.Button(bar, text="刷新", command=self.refresh).pack(side="right")

        self.summary_var = tk.StringVar()
        ttk.Label(
            outer,
            textvariable=self.summary_var,
            justify="left",
            wraplength=950,
        ).pack(fill="x", pady=(12, 10))

        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("start", "end", "category", "duration", "note"),
            show="headings",
            selectmode="browse",
        )

        for col, title, width in [
            ("start", "开始时间", 145),
            ("end", "结束时间", 145),
            ("category", "类别", 120),
            ("duration", "有效时长", 90),
            ("note", "一句话结果", 420),
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(
                col,
                width=width,
                anchor="center" if col == "duration" else "w",
            )

        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 双击直接修改记录。
        self.tree.bind("<Double-1>", self.edit_selected)

        # ----------------------------------------------------
        # Markdown 预览
        # ----------------------------------------------------

        markdown_box = ttk.LabelFrame(
            outer,
            text="Markdown",
            padding=(8, 6),
        )
        markdown_box.pack(fill="both", pady=(10, 0))

        markdown_text_frame = ttk.Frame(markdown_box)
        markdown_text_frame.pack(fill="both", expand=True)

        self.markdown_text = tk.Text(
            markdown_text_frame,
            height=7,
            wrap="word",
            font=("Consolas", 9),
            relief="flat",
            borderwidth=0,
            padx=6,
            pady=6,
        )

        markdown_scrollbar = ttk.Scrollbar(
            markdown_text_frame,
            orient="vertical",
            command=self.markdown_text.yview,
        )
        self.markdown_text.configure(yscrollcommand=markdown_scrollbar.set)
        self.markdown_text.pack(side="left", fill="both", expand=True)
        markdown_scrollbar.pack(side="right", fill="y")

        markdown_actions = ttk.Frame(markdown_box)
        markdown_actions.pack(fill="x", pady=(6, 0))

        ttk.Label(
            markdown_actions,
            text="今天 / 本周会自动生成；可直接框选复制",
        ).pack(side="left")

        self.copy_markdown_button = ttk.Button(
            markdown_actions,
            text="复制 Markdown",
            command=self.copy_markdown,
        )
        self.copy_markdown_button.pack(side="right")

        # ----------------------------------------------------
        # 记录维护
        # ----------------------------------------------------

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))

        ttk.Button(
            bottom,
            text="新增记录",
            command=self.add_record,
        ).pack(side="left")

        ttk.Button(
            bottom,
            text="编辑选中",
            command=self.edit_selected,
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            bottom,
            text="删除选中",
            command=self.delete_selected,
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            bottom,
            text="双击记录也可以编辑",
        ).pack(side="right")

    def get_range(self):
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if self.period.get() == "today":
            return today, today + timedelta(days=1), "今天"

        if self.period.get() == "week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=7), "本周"

        start = today.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end, "本月"

    def refresh(self):
        start, end, title = self.get_range()
        rows = self.db.sessions_between(start, end)

        for item in self.tree.get_children():
            self.tree.delete(item)

        total = 0
        by_category = {}

        for row in rows:
            duration = int(row["duration_sec"])
            total += duration
            category = row["category"]
            by_category[category] = by_category.get(category, 0) + duration

            try:
                started = datetime.fromisoformat(row["started_at"]).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                started = row["started_at"]

            try:
                ended = datetime.fromisoformat(row["ended_at"]).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                ended = row["ended_at"]

            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    started,
                    ended,
                    category,
                    fmt_short(duration),
                    row["note"],
                ),
            )

        lines = [f"{title}累计：{fmt_clock(total)}    共 {len(rows)} 段"]
        if total:
            details = []
            for category, duration in sorted(
                by_category.items(),
                key=lambda item: item[1],
                reverse=True,
            ):
                percentage = duration / total * 100
                details.append(
                    f"{category} {fmt_short(duration)} ({percentage:.0f}%)"
                )
            lines.append("    ".join(details))

        self.summary_var.set("\n".join(lines))

        self.update_markdown_preview()

    def _markdown_time_range(self, started, ended):
        start_text = started.strftime("%H:%M")
        if ended.date() == started.date():
            end_text = ended.strftime("%H:%M")
        elif ended.date() == started.date() + timedelta(days=1):
            end_text = f"次日 {ended.strftime('%H:%M')}"
        else:
            end_text = ended.strftime("%Y-%m-%d %H:%M")
        return f"{start_text}–{end_text}"

    def _markdown_item(self, row):
        try:
            started = datetime.fromisoformat(row["started_at"])
            ended = datetime.fromisoformat(row["ended_at"])
        except Exception:
            return f"- {row['category']} · {fmt_short(int(row['duration_sec']))} · {row['note']}".rstrip(" ·")

        parts = [
            self._markdown_time_range(started, ended),
            str(row["category"]),
            fmt_short(int(row["duration_sec"])),
        ]

        note = str(row["note"] or "").replace("\r", " ").replace("\n", " ").strip()
        if note:
            parts.append(note)

        return "- " + " · ".join(parts)

    def build_markdown(self):
        period = self.period.get()
        if period not in ("today", "week"):
            raise ValueError("Markdown 预览只支持今天或本周。")

        start, end, _ = self.get_range()
        rows = self.db.sessions_between(start, end)

        if period == "today":
            lines = [f"# {start.strftime('%Y-%m-%d')}", ""]
            if rows:
                lines.extend(self._markdown_item(row) for row in rows)
            else:
                lines.append("- 无记录")
            return "\n".join(lines).rstrip() + "\n"

        # 周预览：按照统计界面的顺序（最新记录在前）按日期分组。
        week_end = end - timedelta(days=1)
        lines = [
            f"# {start.strftime('%Y-%m-%d')} ~ {week_end.strftime('%Y-%m-%d')}",
            "",
        ]

        grouped = {}
        for row in rows:
            try:
                day = datetime.fromisoformat(row["started_at"]).strftime("%Y-%m-%d")
            except Exception:
                day = str(row["started_at"])[:10] or "未知日期"
            grouped.setdefault(day, []).append(row)

        if not grouped:
            lines.append("- 无记录")
        else:
            first_group = True
            for day, day_rows in grouped.items():
                if not first_group:
                    lines.append("")
                first_group = False
                lines.append(f"## {day}")
                lines.append("")
                lines.extend(self._markdown_item(row) for row in day_rows)

        return "\n".join(lines).rstrip() + "\n"

    def _set_markdown_text(self, text):
        """更新预览文本，同时保持可以鼠标选择和 Ctrl+C。"""
        self.markdown_text.configure(state="normal")
        self.markdown_text.delete("1.0", "end")
        self.markdown_text.insert("1.0", text)
        self.markdown_text.configure(state="disabled")

    def update_markdown_preview(self):
        period = self.period.get()

        if period not in ("today", "week"):
            self._set_markdown_text("本月暂不生成 Markdown。\n")
            self.copy_markdown_button.configure(state="disabled")
            return

        try:
            markdown = self.build_markdown()
        except Exception as exc:
            self._set_markdown_text(f"生成 Markdown 失败：{exc}\n")
            self.copy_markdown_button.configure(state="disabled")
            return

        self._set_markdown_text(markdown)
        self.copy_markdown_button.configure(state="normal", text="复制 Markdown")

    def copy_markdown(self):
        if self.period.get() not in ("today", "week"):
            return

        try:
            markdown = self.build_markdown()
        except Exception as exc:
            messagebox.showerror(
                "复制失败",
                f"无法生成 Markdown：\n{exc}",
                parent=self.win,
            )
            return

        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(markdown)
            self.win.update_idletasks()
        except tk.TclError as exc:
            messagebox.showerror(
                "复制失败",
                f"无法写入剪贴板：\n{exc}",
                parent=self.win,
            )
            return

        self.copy_markdown_button.configure(text="已复制")
        self.win.after(1200, self._restore_copy_button)

    def _restore_copy_button(self):
        try:
            if self.copy_markdown_button.winfo_exists():
                self.copy_markdown_button.configure(text="复制 Markdown")
        except tk.TclError:
            pass

    def add_record(self):
        dialog = RecordDialog(self.win, self.db)
        if dialog.result is None:
            return

        category, started_at, ended_at, duration, note = dialog.result
        self.db.add_category(category)
        self.db.set_setting("last_category", category)
        self.db.add_session(
            category,
            started_at,
            ended_at,
            duration,
            note,
        )
        self.refresh()

    def _selected_session_id(self):
        selected = self.tree.selection()
        if not selected:
            return None
        try:
            return int(selected[0])
        except (TypeError, ValueError):
            return None

    def edit_selected(self, event=None):
        session_id = self._selected_session_id()
        if session_id is None:
            if event is None:
                messagebox.showinfo(
                    "提示",
                    "请先选择一条记录。",
                    parent=self.win,
                )
            return

        session = self.db.get_session(session_id)
        if session is None:
            messagebox.showwarning(
                "记录不存在",
                "这条记录可能已经被删除。",
                parent=self.win,
            )
            self.refresh()
            return

        dialog = RecordDialog(self.win, self.db, session=session)
        if dialog.result is None:
            return

        category, started_at, ended_at, duration, note = dialog.result
        self.db.add_category(category)
        self.db.set_setting("last_category", category)
        self.db.update_session(
            session_id,
            category,
            started_at,
            ended_at,
            duration,
            note,
        )
        self.refresh()

        # 记录仍在当前筛选范围时尽量重新选中。
        if self.tree.exists(str(session_id)):
            self.tree.selection_set(str(session_id))
            self.tree.focus(str(session_id))
            self.tree.see(str(session_id))

    def delete_selected(self):
        session_id = self._selected_session_id()
        if session_id is None:
            messagebox.showinfo(
                "提示",
                "请先选择一条记录。",
                parent=self.win,
            )
            return

        if not messagebox.askyesno(
            "删除",
            "确定删除这条记录吗？",
            parent=self.win,
        ):
            return

        self.db.delete_session(session_id)
        self.refresh()




# ============================================================
# 极简单按钮悬浮控制器
# ============================================================

class FocusTimer:
    def __init__(self, root):
        self.root = root
        self.db = Database(DB_PATH)

        # idle / running / paused
        self.state = "idle"
        self.started_at = ""
        self.paused_at = ""
        self.accumulated = 0.0
        self.run_started_mono = None
        self.last_snapshot_mono = 0.0

        # 拖动状态
        self.press_root_x = 0
        self.press_root_y = 0
        self.window_start_x = 0
        self.window_start_y = 0
        self.dragging = False

        self.topmost_var = tk.BooleanVar(value=True)

        self.configure_window()
        self.build_ui()
        self.build_menu()
        self.bind_events()
        self.draw_state()

        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)
        self.root.after(250, self.tick)
        self.root.after(250, self.recover_active)
        self.root.after(600, self.make_inactive)

    # --------------------------------------------------------
    # Window
    # --------------------------------------------------------

    def configure_window(self):
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", ACTIVE_ALPHA)

        position = self.db.get_setting("window_position", "")
        try:
            x_text, y_text = position.split(",")
            x, y = int(x_text), int(y_text)
        except Exception:
            x, y = 100, 100

        self.root.geometry(
            f"{WINDOW_SIZE}x{WINDOW_SIZE}+{x}+{y}"
        )
        self.root.resizable(False, False)

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):
        self.canvas = tk.Canvas(
            self.root,
            width=WINDOW_SIZE,
            height=WINDOW_SIZE,
            bg="#F3F3F3",
            highlightthickness=1,
            highlightbackground="#BEBEBE",
            bd=0,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)

    def draw_state(self):
        self.canvas.delete("all")

        cx = WINDOW_SIZE / 2
        cy = WINDOW_SIZE / 2
        icon_color = "#202020"

        # idle：点击后开始
        if self.state == "idle":
            self.canvas.create_polygon(
                cx - 6,
                cy - 9,
                cx - 6,
                cy + 9,
                cx + 9,
                cy,
                fill=icon_color,
                outline="",
            )
            self.root.title("Focus Timer - 未开始")

        # running：点击后暂停
        elif self.state == "running":
            self.canvas.create_rectangle(
                cx - 8,
                cy - 9,
                cx - 3,
                cy + 9,
                fill=icon_color,
                outline="",
            )
            self.canvas.create_rectangle(
                cx + 3,
                cy - 9,
                cx + 8,
                cy + 9,
                fill=icon_color,
                outline="",
            )
            self.root.title("Focus Timer - 正在计时")

        # paused：左键动作是“继续”，所以仍然显示播放图标。
        else:
            self.canvas.create_polygon(
                cx - 6,
                cy - 9,
                cx - 6,
                cy + 9,
                cx + 9,
                cy,
                fill=icon_color,
                outline="",
            )
            self.root.title("Focus Timer - 已暂停")

    # --------------------------------------------------------
    # Menu
    # --------------------------------------------------------

    def build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)

        self.menu.add_command(
            label="结束并记录...",
            command=self.finish_from_menu,
        )
        self.finish_menu_index = self.menu.index("end")

        self.menu.add_separator()
        self.menu.add_command(
            label="统计...",
            command=self.open_stats,
        )

        self.menu.add_separator()
        self.menu.add_checkbutton(
            label="始终置顶",
            variable=self.topmost_var,
            command=self.toggle_topmost,
        )

        self.menu.add_separator()
        self.menu.add_command(
            label="退出",
            command=self.on_exit,
        )

    def show_context_menu(self, event):
        self.make_active()

        self.menu.entryconfigure(
            self.finish_menu_index,
            state=(
                "normal"
                if self.state != "idle"
                else "disabled"
            ),
        )

        try:
            self.menu.tk_popup(
                event.x_root,
                event.y_root,
            )
        finally:
            self.menu.grab_release()

    def toggle_topmost(self):
        self.root.attributes(
            "-topmost",
            bool(self.topmost_var.get()),
        )

    # --------------------------------------------------------
    # Mouse / alpha
    # --------------------------------------------------------

    def bind_events(self):
        self.canvas.bind(
            "<Enter>",
            lambda e: self.make_active(),
        )
        self.canvas.bind(
            "<Leave>",
            lambda e: self.root.after(
                100,
                self.make_inactive,
            ),
        )
        self.canvas.bind(
            "<ButtonPress-1>",
            self.on_left_press,
        )
        self.canvas.bind(
            "<B1-Motion>",
            self.on_left_motion,
        )
        self.canvas.bind(
            "<ButtonRelease-1>",
            self.on_left_release,
        )
        self.canvas.bind(
            "<Button-3>",
            self.show_context_menu,
        )

        self.root.bind(
            "<FocusIn>",
            lambda e: self.make_active(),
        )
        self.root.bind(
            "<FocusOut>",
            lambda e: self.root.after(
                100,
                self.make_inactive,
            ),
        )

    def make_active(self):
        try:
            self.root.attributes(
                "-alpha",
                ACTIVE_ALPHA,
            )
        except tk.TclError:
            pass

    def make_inactive(self):
        try:
            px = self.root.winfo_pointerx()
            py = self.root.winfo_pointery()
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
            w = self.root.winfo_width()
            h = self.root.winfo_height()

            inside = (
                x <= px <= x + w
                and y <= py <= y + h
            )

            self.root.attributes(
                "-alpha",
                ACTIVE_ALPHA if inside else INACTIVE_ALPHA,
            )
        except tk.TclError:
            pass

    # --------------------------------------------------------
    # Click / drag
    # --------------------------------------------------------

    def on_left_press(self, event):
        self.make_active()

        self.press_root_x = event.x_root
        self.press_root_y = event.y_root
        self.window_start_x = self.root.winfo_x()
        self.window_start_y = self.root.winfo_y()
        self.dragging = False

    def on_left_motion(self, event):
        dx = event.x_root - self.press_root_x
        dy = event.y_root - self.press_root_y

        if (
            abs(dx) > DRAG_THRESHOLD
            or abs(dy) > DRAG_THRESHOLD
        ):
            self.dragging = True

        if self.dragging:
            self.root.geometry(
                f"+{self.window_start_x + dx}"
                f"+{self.window_start_y + dy}"
            )

    def on_left_release(self, event):
        if self.dragging:
            self.save_window_position()
            self.dragging = False
            return

        # 左键逻辑保持不变：开始 / 暂停 / 继续
        if self.state == "idle":
            self.start()
        elif self.state == "running":
            self.pause()
        elif self.state == "paused":
            self.resume()

    # --------------------------------------------------------
    # Timer
    # --------------------------------------------------------

    def elapsed(self):
        if self.state == "running":
            return (
                self.accumulated
                + (time.monotonic() - self.run_started_mono)
            )
        return self.accumulated

    def start(self):
        self.started_at = iso_now()
        self.paused_at = ""
        self.accumulated = 0.0
        self.run_started_mono = time.monotonic()
        self.last_snapshot_mono = time.monotonic()
        self.state = "running"

        self.db.save_active(
            self.started_at,
            0,
            "running",
            "",
        )
        self.draw_state()

    def pause(self):
        if self.state != "running":
            return

        self.accumulated = self.elapsed()
        self.run_started_mono = None
        self.paused_at = iso_now()
        self.state = "paused"

        self.db.save_active(
            self.started_at,
            self.accumulated,
            "paused",
            self.paused_at,
        )
        self.draw_state()

    def resume(self):
        if self.state != "paused":
            return

        self.paused_at = ""
        self.run_started_mono = time.monotonic()
        self.last_snapshot_mono = time.monotonic()
        self.state = "running"

        self.db.save_active(
            self.started_at,
            self.accumulated,
            "running",
            "",
        )
        self.draw_state()

    def finish_from_menu(self):
        if self.state == "idle":
            return

        original_state = self.state

        # 在选择“结束并记录”的这一刻冻结时间。
        if original_state == "running":
            frozen_duration = self.elapsed()
            ended_at = iso_now()
            self.accumulated = frozen_duration
            self.run_started_mono = None
            self.state = "paused"
            self.paused_at = ended_at
        else:
            frozen_duration = self.accumulated
            ended_at = self.paused_at or iso_now()

        # 打开填写窗口前先保存冻结状态。
        self.db.save_active(
            self.started_at,
            frozen_duration,
            "paused",
            ended_at,
        )
        self.draw_state()

        dialog = FinishDialog(
            self.root,
            self.db,
            frozen_duration,
        )

        # 取消结束：恢复进入菜单前的状态；填写窗口时间不计入。
        if dialog.result is None:
            if original_state == "running":
                self.state = "running"
                self.paused_at = ""
                self.run_started_mono = time.monotonic()
                self.last_snapshot_mono = time.monotonic()
                self.db.save_active(
                    self.started_at,
                    self.accumulated,
                    "running",
                    "",
                )
            else:
                self.state = "paused"
                self.db.save_active(
                    self.started_at,
                    self.accumulated,
                    "paused",
                    self.paused_at,
                )

            self.draw_state()
            return

        category, note = dialog.result

        self.db.add_category(category)
        self.db.set_setting(
            "last_category",
            category,
        )
        self.db.add_session(
            category,
            self.started_at,
            ended_at,
            round(frozen_duration),
            note,
        )
        self.db.clear_active()
        self.reset_timer()

    def reset_timer(self):
        self.state = "idle"
        self.started_at = ""
        self.paused_at = ""
        self.accumulated = 0.0
        self.run_started_mono = None
        self.draw_state()

    # --------------------------------------------------------
    # Persistence
    # --------------------------------------------------------

    def tick(self):
        if self.state == "running":
            now = time.monotonic()

            if (
                now - self.last_snapshot_mono
                >= 5
            ):
                self.db.save_active(
                    self.started_at,
                    self.elapsed(),
                    "running",
                    "",
                )
                self.last_snapshot_mono = now

        self.root.after(
            250,
            self.tick,
        )

    def recover_active(self):
        row = self.db.get_active()

        if not row:
            return

        elapsed = float(
            row["elapsed_sec"]
        )
        started_at = row[
            "session_started_at"
        ]
        status = row["status"]
        paused_at = (
            row["paused_at"]
            if "paused_at" in row.keys()
            else ""
        )

        ok = messagebox.askyesno(
            "恢复",
            (
                "发现上次有一条未完成记录。\n\n"
                f"已累计：{fmt_clock(elapsed)}\n\n"
                "是否恢复？\n\n"
                "程序关闭期间不会计入时间。"
            ),
            parent=self.root,
        )

        if not ok:
            self.db.clear_active()
            return

        self.started_at = started_at
        self.accumulated = elapsed

        if status == "paused":
            self.state = "paused"
            self.paused_at = paused_at or iso_now()
            self.run_started_mono = None
        else:
            self.state = "running"
            self.paused_at = ""
            self.run_started_mono = time.monotonic()
            self.last_snapshot_mono = time.monotonic()

        self.draw_state()

    # --------------------------------------------------------
    # Stats / exit
    # --------------------------------------------------------

    def open_stats(self):
        StatsWindow(
            self.root,
            self.db,
        )

    def save_window_position(self):
        self.db.set_setting(
            "window_position",
            (
                f"{self.root.winfo_x()},"
                f"{self.root.winfo_y()}"
            ),
        )

    def on_exit(self):
        if self.state != "idle":
            result = messagebox.askyesnocancel(
                "退出",
                (
                    "当前还有一条未完成记录。\n\n"
                    "是：结束并记录\n"
                    "否：保存现场并退出\n"
                    "取消：返回"
                ),
                parent=self.root,
            )

            if result is None:
                return

            if result is True:
                self.finish_from_menu()
                if self.state != "idle":
                    return
            else:
                self.db.save_active(
                    self.started_at,
                    self.elapsed(),
                    self.state,
                    self.paused_at,
                )

        self.save_window_position()
        self.db.close()
        self.root.destroy()


# ============================================================
# Main
# ============================================================

def main():
    root = tk.Tk()

    try:
        FocusTimer(root)
    except Exception as exc:
        root.withdraw()
        messagebox.showerror(
            "启动失败",
            str(exc),
            parent=root,
        )
        root.destroy()
        return

    root.mainloop()


if __name__ == "__main__":
    main()
