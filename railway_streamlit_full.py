# railway_streamlit_mongo.py
"""
INDIAN RAILWAYS MAINTENANCE SYSTEM — Streamlit
MongoDB-backed drop-in adapter replacing SQLite with minimal UI/logic changes.

How it works:
- If MONGO_URI environment variable is set, app will use MongoDB Atlas (or any MongoDB) for persistence.
- A lightweight SQL-to-Mongo adapter (only for the limited SQL patterns used in the original single-file app)
  implements a minimal cursor.execute(...) / fetchall() / fetchone() surface so the rest of the app
  code requires minimal changes.

Notes:
- Install pymongo in your deployment environment: pip install pymongo
- Set MONGO_URI environment variable (example):
  mongodb+srv://<user>:<pw>@cluster0.mongodb.net/railways_db?retryWrites=true&w=majority
- If MONGO_URI is not set, falls back to the original SQLite file behaviour (railway.db)

This file preserves the Streamlit UI and business logic while replacing the DB backend.
"""

import os
import base64
import hashlib
from datetime import datetime
from contextlib import contextmanager
from dotenv import load_dotenv
load_dotenv()
import streamlit as st
import pandas as pd
from pymongo import MongoClient
import pickle  # if using saved ML model
import plotly.graph_objects as go  # for speedometer-style indicator
import time

# optional dependency
try:
    from pymongo import MongoClient
    PYMONGO_AVAILABLE = True
except Exception:
    MongoClient = None
    PYMONGO_AVAILABLE = False

# JWT is still used by the app
import jwt
st.set_page_config(
    page_title="Indian Railways Maintenance System",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>

/* ---------- APP BACKGROUND ---------- */
[data-testid="stAppViewContainer"] {
    background-color: #EAF3FF !important;
}

/* ---------- MAIN DASHBOARD CARD ---------- */
.block-container {
    background-color: #ffffff !important;
    padding: 1.5rem !important;
    padding-bottom: 4rem !important;
    border-radius: 10px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
}

/* ---------- FORCE LIGHT TEXT IN MAIN CONTENT ---------- */
.block-container * {
    color: #0f172a !important;
}

/* ---------- SIDEBAR ---------- */
section[data-testid="stSidebar"] {
    background-color: #dbeafe !important;
    border-right: 2px solid #93c5fd;
    z-index: 1200 !important;
}

section[data-testid="stSidebar"] * {
    color: #0f172a !important;
}

/* ---------- HEADER ---------- */
header {
    background: transparent !important;
    box-shadow: none !important;
}

/* ---------- METRICS ---------- */
[data-testid="stMetricLabel"],
[data-testid="stMetricValue"] {
    color: #0f172a !important;
}

/* ---------- BUTTONS ---------- */
button {
    background-color: #2563eb !important;
    color: white !important;
    border-radius: 6px;
}

/* ---------- TABLE ---------- */
[data-testid="stDataFrame"] {
    width: 100% !important;
}
/* ---------- FIX INPUT TEXT VISIBILITY ---------- */

/* Text typed inside inputs */
input, textarea {
    color: #0f172a !important;       /* text color */
    background-color: #ffffff !important;
}

/* Selectbox / dropdown selected value */
[data-testid="stSelectbox"] div {
    color: #0f172a !important;
    background-color: #ffffff !important;
}

/* Placeholder text */
input::placeholder {
    color: #64748b !important;       /* gray placeholder */
}

/* Number input arrows & text */
[data-testid="stNumberInput"] input {
    color: #0f172a !important;
    background-color: #ffffff !important;
}

/* ---------- RESPONSIVE ---------- */
@media (max-width: 768px) {
    .stColumns {
        flex-direction: column !important;
        gap: 1rem;
    }
    button {
        width: 100%;
    }
}

</style>
""", unsafe_allow_html=True)


# ---------------- CONFIG ----------------
DB_PATH = "railway.db"   # fallback (SQLite) file
KM_LIMIT = 5000
DAYS_LIMIT = 180
DAYS_SOON = 150

# JWT secret
SECRET_KEY = os.environ.get("JWT_SECRET", "soorya123")

# ----------------- MongoDB adapter -----------------
MONGO_URI = os.environ.get("MONGO_URI")  # set this in Render / environment to enable MongoDB
USE_MONGO = bool(MONGO_URI)

if USE_MONGO and not PYMONGO_AVAILABLE:
    raise RuntimeError("MONGO_URI is set but pymongo is not installed. Run: pip install pymongo")

class FakeCursor:
    """A tiny cursor that recognizes the limited SQL patterns used in the app and translates
    them into MongoDB operations. It returns Python dict-like rows so the rest of the app
    (which expects sqlite3.Row) continues to work.

    This is intentionally narrow and implemented only to support this specific app's queries.
    """
    def __init__(self, db):
        self.db = db
        self._last = None

    def execute(self, sql, params=None):
        # normalize
        s = sql.strip().lower()
        params = params or ()

        # CREATE TABLE / PRAGMA / other schema SQL - ignore when using Mongo
        if s.startswith('create table') or s.startswith('pragma') or s.startswith('insert or replace'):
            # No-op for Mongo (collections created on demand)
            self._last = None
            return self

        # SELECT count(*) as c FROM X
        if s.startswith('select count'):
            # crude parse to find collection name
            # example: select count(*) as c from system_users
            parts = s.split('from')
            coll = parts[1].strip().split()[0]
            c = getattr(self.db, coll).count_documents({})
            self._last = [{'c': c}]
            return self

        # SELECT * FROM trains ORDER BY train_no
        if s.startswith('select * from trains'):
            docs = list(self.db.trains.find({}, {'_id': 0}).sort('train_no', 1))
            self._last = docs
            return self

        # SELECT coach_id, type, last_maintenance, km_run, status FROM coaches ORDER BY coach_id
        if 'from coaches' in s and 'select' in s:
            docs = list(self.db.coaches.find({}, {'_id': 0}).sort('coach_id', 1))
            self._last = docs
            return self

        # SELECT coach_id FROM coaches WHERE status!='Removed' ORDER BY coach_id
        if "from coaches where status!='removed'" in s:
            docs = list(self.db.coaches.find({'status': {'$ne': 'Removed'}}, {'coach_id': 1, '_id': 0}).sort('coach_id', 1))
            self._last = docs
            return self

        # SELECT train_no, train_name FROM trains ORDER BY train_no
        if 'select train_no, train_name from trains' in s:
            docs = list(self.db.trains.find({}, {'train_no': 1, 'train_name': 1, '_id': 0}).sort('train_no', 1))
            self._last = docs
            return self

        # SELECT coach_id FROM coaches ORDER BY coach_id
        if s.startswith('select coach_id from coaches') and 'where' not in s:
            docs = list(self.db.coaches.find({}, {'coach_id': 1, '_id': 0}).sort('coach_id', 1))
            self._last = docs
            return self

        # SELECT train_no FROM trains ORDER BY train_no
        if s.startswith('select train_no from trains'):
            docs = list(self.db.trains.find({}, {'train_no': 1, '_id': 0}).sort('train_no', 1))
            self._last = docs
            return self

        # SELECT train_no FROM trains (?) other selects with WHERE train_no=?
        if s.startswith('select * from coaches where coach_id=') or 'where coach_id=?' in s:
            # param 0 is coach_id
            key = params[0] if params else None
            doc = self.db.coaches.find_one({'coach_id': key}, {'_id': 0})
            self._last = [doc] if doc else []
            return self

        # SELECT username FROM engineers ORDER BY username
        if s.startswith('select username from engineers'):
            docs = list(self.db.engineers.find({}, {'username': 1, '_id': 0}).sort('username', 1))
            self._last = docs
            return self

        # SELECT password_hash FROM engineers WHERE username=?
        if 'select password_hash from engineers where username' in s:
            key = params[0] if params else None
            doc = self.db.engineers.find_one({'username': key}, {'password_hash': 1, '_id': 0})
            self._last = [doc] if doc else []
            return self

        # SELECT password_hash FROM system_users WHERE username=?
        if 'select password_hash from system_users where username' in s:
            key = params[0] if params else None
            doc = self.db.system_users.find_one({'username': key}, {'password_hash': 1, '_id': 0})
            self._last = [doc] if doc else []
            return self

        # SELECT record_id, date, coach_id, train_no, maintenance_type, engineer, notes FROM maintenance_records ORDER BY date DESC LIMIT N
        if 'from maintenance_records' in s and 'order by date desc' in s:
            # find limit if present
            limit = None
            if 'limit' in s:
                try:
                    limit = int(s.split('limit')[-1].strip())
                except Exception:
                    limit = None
            cursor = self.db.maintenance_records.find({}, {'_id': 0}).sort('date', -1)
            if limit:
                cursor = cursor.limit(limit)
            docs = list(cursor)
            self._last = docs
            return self

        # SELECT date, train_no, coach_id, maintenance_type, engineer, notes FROM maintenance_records ORDER BY date DESC
        if s.startswith('select date, train_no, coach_id, maintenance_type, engineer, notes from maintenance_records'):
            docs = list(self.db.maintenance_records.find({}, {'_id': 0}).sort('date', -1))
            self._last = docs
            return self

        # SELECT tc.train_no, t.train_name, tc.coach_id FROM train_coaches tc LEFT JOIN trains t ON tc.train_no=t.train_no ORDER BY tc.train_no, tc.coach_id
        if 'from train_coaches tc' in s and 'left join trains t' in s:
            pipeline = [
                {'$lookup': {
                    'from': 'trains',
                    'localField': 'train_no',
                    'foreignField': 'train_no',
                    'as': 'train_docs'
                }},
                {'$unwind': {'path': '$train_docs', 'preserveNullAndEmptyArrays': True}},
                {'$project': {'_id': 0, 'train_no': 1, 'coach_id': 1, 'train_name': '$train_docs.train_name'}},
                {'$sort': {'train_no': 1, 'coach_id': 1}}
            ]
            docs = list(self.db.train_coaches.aggregate(pipeline))
            self._last = docs
            return self

        # SELECT coach_id FROM train_coaches WHERE train_no=? ORDER BY coach_id
        if 'select coach_id from train_coaches where train_no' in s:
            key = params[0] if params else None
            docs = list(self.db.train_coaches.find({'train_no': key}, {'coach_id': 1, '_id': 0}).sort('coach_id', 1))
            self._last = docs
            return self

        # Generic fallback: try returning empty
        self._last = []
        return self

    def fetchall(self):
        # convert mongodb documents to sqlite3.Row-like dicts
        if not self._last:
            return []
        # _last may contain dicts with missing fields - return as-is
        return [r for r in self._last]

    def fetchone(self):
        if not self._last:
            return None
        return self._last[0]


class FakeConn:
    """Connection-like object for the app. commit() is a no-op for Mongo (writes happen immediately).
    """
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        return None

    def close(self):
        return None


@contextmanager
def get_conn():
    """Context manager that yields either a real sqlite3 connection (fallback) or a fake Mongo-backed connection."""
    if USE_MONGO:
        client = MongoClient(MONGO_URI)
        # database name will be the path's last element or 'railways_db' default
        dbname = os.environ.get('MONGO_DBNAME', 'railways_db')
        db = client[dbname]
        try:
            yield FakeConn(db)
        finally:
            client.close()
    else:
        # fallback to sqlite for local dev/if MONGO_URI not set
        import sqlite3
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

# ----------------- init_db for Mongo -----------------

def init_db():
    with get_conn() as conn:
        if USE_MONGO:
            db = conn.db
            # ensure collections exist and create indexes akin to PRIMARY KEY behavior
            db.coaches.create_index('coach_id', unique=True)
            db.trains.create_index('train_no', unique=True)
            db.train_coaches.create_index([('train_no', 1), ('coach_id', 1)], unique=True)
            db.engineers.create_index('username', unique=True)
            db.system_users.create_index('username', unique=True)
            db.maintenance_records.create_index('record_id', unique=False)
            # ensure default system user exists
            if db.system_users.count_documents({}) == 0:
                default_user = 'admin'
                default_pw = hashlib.sha256('admin123'.encode()).hexdigest()
                db.system_users.insert_one({'username': default_user, 'password_hash': default_pw})
        else:
            # original sqlite behaviour
            cur = conn.cursor()
            cur.execute('''CREATE TABLE IF NOT EXISTS coaches (
                coach_id TEXT PRIMARY KEY,
                type TEXT,
                last_maintenance TEXT,
                km_run INTEGER,
                status TEXT
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS maintenance_records (
                record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                coach_id TEXT,
                train_no TEXT,
                date TEXT,
                maintenance_type TEXT,
                engineer TEXT,
                notes TEXT
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS trains (
                train_no TEXT PRIMARY KEY,
                train_name TEXT,
                source TEXT,
                destination TEXT
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS train_coaches (
                train_no TEXT,
                coach_id TEXT,
                PRIMARY KEY(train_no, coach_id)
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS engineers (
                username TEXT PRIMARY KEY,
                password_hash TEXT
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS system_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT
            )''')
            cur.execute("SELECT count(*) as c FROM system_users")
            r = cur.fetchone()
            if r is None or r['c'] == 0:
                default_user = 'admin'
                default_pw = hashlib.sha256('admin123'.encode()).hexdigest()
                cur.execute("INSERT OR REPLACE INTO system_users (username, password_hash) VALUES (?, ?)", (default_user, default_pw))
            conn.commit()

# ----------------- small helpers -----------------

def hash_password(pw: str):
    return hashlib.sha256(pw.encode()).hexdigest()


def parse_date_safely(date_str):
    if not date_str:
        return None
    formats = ("%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%d.%m.%Y")
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(date_str)
    except Exception:
        return None


def format_date_for_display(dt):
    if not dt:
        return ""
    if isinstance(dt, str):
        parsed = parse_date_safely(dt)
        if parsed:
            return parsed.strftime("%d-%m-%Y")
        else:
            return dt
    if isinstance(dt, datetime):
        return dt.strftime("%d-%m-%Y")
    return str(dt)


def coach_due_status(row):
    km = row.get('km_run', 0) or 0
    last = row.get('last_maintenance')
    days_passed = None
    if last:
        dt = parse_date_safely(last)
        if dt:
            days_passed = (datetime.now() - dt).days
    if km >= KM_LIMIT or (days_passed is not None and days_passed >= DAYS_LIMIT):
        return 'Overdue'
    if km >= (KM_LIMIT - 500) or (days_passed is not None and days_passed >= DAYS_SOON):
        return 'Due Soon'
    return 'OK'


def get_due_maintenance():
    with get_conn() as conn:
        if USE_MONGO:
            db = conn.db
            rows = list(db.coaches.find({'status': {'$ne': 'Removed'}}, {'_id': 0}).sort('coach_id', 1))
        else:
            cur = conn.cursor()
            cur.execute("SELECT coach_id, type, last_maintenance, km_run, status FROM coaches WHERE status!='Removed' ORDER BY coach_id")
            rows = cur.fetchall()

    alerts = []
    for r in rows:
        km = r.get('km_run', 0) if isinstance(r, dict) else (r['km_run'] or 0)
        last = r.get('last_maintenance') if isinstance(r, dict) else r['last_maintenance']
        days_passed = None
        if last:
            dt = parse_date_safely(last)
            if dt:
                days_passed = (datetime.now() - dt).days
        if km >= KM_LIMIT or (days_passed is not None and days_passed >= DAYS_LIMIT):
            alerts.append({
                'coach_id': r.get('coach_id') if isinstance(r, dict) else r['coach_id'],
                'type': r.get('type') if isinstance(r, dict) else r['type'],
                'last_maintenance': last,
                'km_run': km,
                'days_passed': days_passed
            })
    return alerts

# ---------------- Streamlit UI code (same as original) ----------------
# The UI code below is intentionally kept nearly identical to the original file. It uses
# the get_conn() abstraction so the underlying DB backend (SQLite or MongoDB) is transparent.



# Auth handling
query_params = st.query_params
token = query_params.get("token")
if isinstance(token, list):
    token = token[0]

if "authenticated_user" not in st.session_state:
    if not token:
        login_url = os.environ.get('LOGIN_URL', 'https://railways-r1m.onrender.com/login')
        st.markdown(f'<meta http-equiv="refresh" content="0; url={login_url}">', unsafe_allow_html=True)
        st.stop()
    try:
        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        username = decoded.get('user', {}).get('username', 'User')
        st.session_state.authenticated_user = username
        st.success(f"✅ Welcome {username}")
        if "url_cleaned" not in st.session_state:
            st.session_state.url_cleaned = True
    except Exception:
        st.error("🚫 Session expired or invalid. Please login again.")
        st.stop()

username = st.session_state.authenticated_user

# Streamlit compatibility
if not hasattr(st, "experimental_rerun"):
    st.experimental_rerun = lambda: st.stop()

# Session defaults
if "system_user" not in st.session_state:
    st.session_state["system_user"] = None
if "engineer" not in st.session_state:
    st.session_state["engineer"] = None
if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"

# initialize DB/tables
init_db()

# UI helpers

def show_success(msg):
    st.success(msg)

def show_error(msg):
    st.error(msg)


def dataframe_download_link(df: pd.DataFrame, filename: str):
    csv = df.to_csv(index=False).encode()
    b64 = base64.b64encode(csv).decode()
    href = f'<a href="data:file/csv;base64,{b64}" download="{filename}">Download CSV</a>'
    return href


def download_db_link(db_path=DB_PATH):
    if USE_MONGO:
        return None
    if not os.path.exists(db_path):
        return None
    with open(db_path, 'rb') as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    return f'<a href="data:application/octet-stream;base64,{b64}" download="{os.path.basename(db_path)}">Download DB file</a>'

# Page functions (kept same but they call get_conn() which now may be Mongo)

def page_top_bar():
    st.title("INDIAN RAILWAYS — MAINTENANCE SYSTEM (Web)                               -developed by RAKHI ABI")
    cols = st.columns([3,1,1])
    cols[0].markdown("#### Manage trains, coaches, maintenance and engineers — advanced UI")
    if st.session_state["system_user"]:
        cols[1].markdown(f"**SYSTEM:** {st.session_state['system_user']}")
    else:
        cols[1].markdown("**SYSTEM:** _Not logged in_")
    if st.session_state["engineer"]:
        cols[2].markdown(f"**Engineer:** {st.session_state['engineer']}")
    else:
        cols[2].markdown("**Engineer:** _Not logged in_")
    st.markdown("---")


def page_dashboard():
    page_top_bar()
    st.subheader("Dashboard")
    with get_conn() as conn:
        if USE_MONGO:
            db = conn.db
            coaches_count = db.coaches.count_documents({})
            trains_count = db.trains.count_documents({})
            eng_count = db.engineers.count_documents({})
            mr_count = db.maintenance_records.count_documents({})
        else:
            cur = conn.cursor()
            cur.execute("SELECT count(*) as c FROM coaches"); coaches_count = cur.fetchone()["c"]
            cur.execute("SELECT count(*) as c FROM trains"); trains_count = cur.fetchone()["c"]
            cur.execute("SELECT count(*) as c FROM engineers"); eng_count = cur.fetchone()["c"]
            cur.execute("SELECT count(*) as c FROM maintenance_records"); mr_count = cur.fetchone()["c"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("COACHES", coaches_count)
    c2.metric("TRAINS", trains_count)
    c3.metric("ENGINEERS", eng_count)
    c4.metric("MAINTENANCE LOGS", mr_count)

    st.markdown("### Maintenance Alerts")
    alerts = get_due_maintenance()
    if not alerts:
        st.success("No coaches currently overdue by KM or days.")
    else:
        st.warning(f"{len(alerts)} coach(es) due for maintenance")
        for a in alerts[:30]:
            days_text = f"{a['days_passed']} days ago" if a['days_passed'] is not None else "N/A"
            st.write(f"• **{a['coach_id']}** — Type: {a['type'] or 'N/A'} — KM: {a['km_run']} — Last: {a['last_maintenance'] or 'N/A'} ({days_text})")

    st.markdown("---")
    with st.expander("Recent Maintenance Logs"):
        with get_conn() as conn:
            if USE_MONGO:
                rows = list(conn.db.maintenance_records.find({}, {'_id': 0}).sort('date', -1).limit(20))
            else:
                cur = conn.cursor()
                cur.execute("SELECT record_id, date, coach_id, train_no, maintenance_type, engineer, notes FROM maintenance_records ORDER BY date DESC LIMIT 20")
                rows = cur.fetchall()
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, width="stretch")
            st.markdown(dataframe_download_link(df, "recent_maintenance.csv"), unsafe_allow_html=True)
        else:
            st.info("No maintenance recorded yet.")

    st.markdown("---")
    link = download_db_link(DB_PATH)
    if link:
        st.markdown(link, unsafe_allow_html=True)

# (Remaining page functions are identical in logic to the original file.)

# For brevity in this generated file we will include the remainder of the UI functions
# by reading them from the original implementation and only changing DB access points
# to use get_conn(). This preserves the UI and business logic.

# --- Coaches management ---

def page_coaches():
    page_top_bar()
    st.subheader("Coaches Management")

    # Load coaches
    with get_conn() as conn:
        if USE_MONGO:
            rows = list(conn.db.coaches.find({}, {'_id': 0}).sort('coach_id', 1))
        else:
            cur = conn.cursor()
            cur.execute("""
                SELECT coach_id, type, last_maintenance, km_run, status
                FROM coaches
                ORDER BY coach_id
            """)
            rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows, columns=["coach_id", "type", "last_maintenance", "km_run", "status"]) if not USE_MONGO else pd.DataFrame(rows)
    else:
        df = pd.DataFrame(columns=["coach_id", "type", "last_maintenance", "km_run", "status"]) 

    if not df.empty:
        df_display = df.copy()
        df_display["last_maintenance"] = df_display["last_maintenance"].apply(format_date_for_display)
        df_display["status_flag"] = df_display.apply(coach_due_status, axis=1)
        st.dataframe(df_display, width="stretch")
        st.markdown(dataframe_download_link(df_display, "coaches.csv"), unsafe_allow_html=True)
    else:
        st.info("No coaches found. Add new coach below.")

    # Add Coach
    st.markdown("### Add Coach")
    with st.form("add_coach", clear_on_submit=True):
        c1, c2 = st.columns(2)
        coach_id = c1.text_input("Coach ID")
        ctype = c2.text_input("Type (e.g., SLR, Sleeper, AC)")
        c3, c4 = st.columns(2)
        last = c3.text_input("Last maintenance (dd-mm-YYYY)")
        km_run = c4.text_input("KM Run", value="0")
        status = st.selectbox("Status", ["Active", "Inactive", "Removed"],key ="1")
        if st.form_submit_button("Save Coach"):
            if not coach_id.strip():
                show_error("Coach ID is required.")
            else:
                try:
                    kmv = int(km_run) if km_run else 0
                except:
                    show_error("KM Run must be an integer.")
                else:
                    if USE_MONGO:
                        try:
                            with get_conn() as conn:
                                conn.db.coaches.insert_one({
                                    'coach_id': coach_id.strip(),
                                    'type': ctype.strip() or None,
                                    'last_maintenance': last.strip() or None,
                                    'km_run': kmv,
                                    'status': status
                                })
                            show_success('Coach added.')
                            st.stop()
                        except Exception as e:
                            show_error(str(e))
                    else:
                        try:
                            with get_conn() as conn:
                                cur = conn.cursor()
                                cur.execute("""
                                    INSERT INTO coaches (coach_id, type, last_maintenance, km_run, status)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (coach_id.strip(), ctype.strip() or None, last.strip() or None, kmv, status))
                                conn.commit()
                            show_success("Coach added.")
                            st.stop()
                        except Exception:
                            show_error("Coach ID already exists.")

    # Edit/Delete Coach
    st.markdown("### Edit / Delete Coach")
    with get_conn() as conn:
        if USE_MONGO:
            options = [r['coach_id'] for r in list(conn.db.coaches.find({}, {'coach_id':1,'_id':0}).sort('coach_id',1))]
        else:
            cur = conn.cursor()
            cur.execute("SELECT coach_id FROM coaches ORDER BY coach_id")
            options = [r['coach_id'] for r in cur.fetchall()]

    sel = st.selectbox("Select coach to edit/delete", [""] + options,key="2")
    if sel:
        with get_conn() as conn:
            if USE_MONGO:
                r = conn.db.coaches.find_one({'coach_id': sel}, {'_id': 0})
            else:
                cur = conn.cursor()
                cur.execute("SELECT * FROM coaches WHERE coach_id=?", (sel,))
                r = cur.fetchone()

        if r:
            with st.form("edit_coach"):
                c1, c2 = st.columns(2)

                type_new = c1.text_input(
                    "Type",
                    value=r.get('type') if isinstance(r, dict) else (r['type'] or "")
                )

                km_new = c2.text_input(
                    "KM Run",
                    value=str((r.get('km_run') if isinstance(r, dict) else r['km_run']) or 0)
                )

                last_new = st.text_input(
                    "Last maintenance (dd-mm-YYYY)",
                    value=r.get('last_maintenance') if isinstance(r, dict) else (r['last_maintenance'] or "")
                )

                status_new = st.selectbox(
                    "Status",
                    ["Active", "Inactive", "Removed"],
                    index=0 if ((r.get('status') if isinstance(r, dict) else (r['status'] or 'Active')) == 'Active')
                    else (1 if ((r.get('status') if isinstance(r, dict) else r['status']) == 'Inactive') else 2),
                    key="3"
                )

                update_btn = st.form_submit_button("Save changes")


            # ---------------- UPDATE LOGIC ----------------
            if update_btn:
                try:
                    kmv = int(km_new) if km_new else 0
                except:
                    show_error("KM Run must be integer.")
                else:
                    with get_conn() as conn:
                        if USE_MONGO:
                            conn.db.coaches.update_one(
                                {'coach_id': sel},
                                {'$set': {
                                    'type': type_new.strip() or None,
                                    'last_maintenance': last_new.strip() or None,
                                    'km_run': kmv,
                                    'status': status_new
                                }}
                            )
                        else:
                            cur = conn.cursor()
                            cur.execute("""
                                UPDATE coaches
                                SET type=?, last_maintenance=?, km_run=?, status=?
                                WHERE coach_id=?
                            """, (type_new.strip() or None, last_new.strip() or None, kmv, status_new, sel))
                            conn.commit()

                    show_success("Coach updated.")
                    st.rerun()


            # ---------------- DELETE LOGIC ----------------
            st.markdown("---")

            if st.button("Delete coach (permanent)"):
                st.session_state["confirm_delete_coach"] = True


            if st.session_state.get("confirm_delete_coach"):

                st.warning("⚠️ This will permanently delete the coach and ALL related data!")

                col1, col2 = st.columns(2)

                with col1:
                    if st.button("✅ YES, DELETE"):
                        with get_conn() as conn:
                            if USE_MONGO:
                                conn.db.coaches.delete_one({'coach_id': sel})
                                conn.db.train_coaches.delete_many({'coach_id': sel})
                                conn.db.maintenance_records.delete_many({'coach_id': sel})
                            else:
                                cur = conn.cursor()
                                cur.execute("DELETE FROM coaches WHERE coach_id=?", (sel,))
                                cur.execute("DELETE FROM train_coaches WHERE coach_id=?", (sel,))
                                cur.execute("DELETE FROM maintenance_records WHERE coach_id=?", (sel,))
                                conn.commit()

                        del st.session_state["confirm_delete_coach"]

                        show_success("Coach and related data deleted.")
                        st.rerun()

                with col2:
                    if st.button("❌ Cancel"):
                        del st.session_state["confirm_delete_coach"]
                        st.rerun()


# --- Trains management ---

def page_trains():
    page_top_bar()
    st.subheader("Trains Management")

    with get_conn() as conn:
        if USE_MONGO:
            rows = list(conn.db.trains.find({}, {'_id': 0}).sort('train_no', 1))
        else:
            cur = conn.cursor()
            cur.execute("SELECT * FROM trains ORDER BY train_no")
            rows = cur.fetchall()
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["train_no", "train_name", "source", "destination"])
    if not df.empty:
        st.dataframe(df, width="stretch")
        st.markdown(dataframe_download_link(df, "trains.csv"), unsafe_allow_html=True)
    else:
        st.info("No trains registered. Add a train below.")

    st.markdown("### Add Train")
    with st.form("add_train", clear_on_submit=True):
        t1, t2 = st.columns(2)
        train_no = t1.text_input("Train No")
        train_name = t2.text_input("Train Name")
        s1, s2 = st.columns(2)
        source = s1.text_input("Source")
        dest = s2.text_input("Destination")
        if st.form_submit_button("Save Train"):
            if not train_no.strip():
                show_error("Train No required.")
            else:
                if USE_MONGO:
                    try:
                        with get_conn() as conn:
                            conn.db.trains.insert_one({
                                'train_no': train_no.strip(),
                                'train_name': train_name.strip(),
                                'source': source.strip(),
                                'destination': dest.strip()
                            })
                        show_success("Train added.")
                        st.stop()
                    except Exception:
                        show_error("Train No already exists.")
                else:
                    try:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute("INSERT INTO trains (train_no, train_name, source, destination) VALUES (?, ?, ?, ?)",
                                        (train_no.strip(), train_name.strip(), source.strip(), dest.strip()))
                            conn.commit()
                        show_success("Train added.")
                        st.stop()
                    except Exception:
                        show_error("Train No already exists.")

    st.markdown("### Edit / Delete Train")
    with get_conn() as conn:
        if USE_MONGO:
            options = [r['train_no'] for r in list(conn.db.trains.find({}, {'train_no':1,'_id':0}).sort('train_no',1))]
        else:
            cur = conn.cursor()
            cur.execute("SELECT train_no FROM trains ORDER BY train_no")
            options = [r['train_no'] for r in cur.fetchall()]
    sel = st.selectbox("Select train", [""] + options,key="4")
    if sel:
        with get_conn() as conn:
            if USE_MONGO:
                r = conn.db.trains.find_one({'train_no': sel}, {'_id':0})
            else:
                cur = conn.cursor()
                cur.execute("SELECT * FROM trains WHERE train_no=?", (sel,))
                r = cur.fetchone()
        if r:
            with st.form("edit_train"):
               train_name_new = st.text_input(
               "Train Name",
                  value=r.get('train_name') if isinstance(r, dict) else (r['train_name'] or "")
               )
               source_new = st.text_input(
                   "Source",
                   value=r.get('source') if isinstance(r, dict) else (r['source'] or "")
               )
               dest_new = st.text_input(
                   "Destination",
                    value=r.get('destination') if isinstance(r, dict) else (r['destination'] or "")
               )

               update_btn = st.form_submit_button("Save changes")

# ✅ UPDATE LOGIC (SAFE)
        if update_btn:
            with get_conn() as conn:
                if USE_MONGO:
                     conn.db.trains.update_one(
                         {'train_no': sel},
                         {'$set': {
                              'train_name': train_name_new.strip(),
                              'source': source_new.strip(),
                              'destination': dest_new.strip()
                        }}
                     )
                else:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE trains SET train_name=?, source=?, destination=? WHERE train_no=?",
                        (train_name_new.strip(), source_new.strip(), dest_new.strip(), sel)
                    )
                    conn.commit()

            show_success("Train updated.")
            st.rerun()


# ✅ ✅ DELETE BUTTON MUST BE OUTSIDE THE FORM
        st.markdown("---")
        st.markdown("---")

        # Step 1: First click → ask for confirmation
        if st.button("Delete train (remove assignments)"):
            st.session_state["confirm_delete_train"] = True


        # Step 2: Show confirm button only after first click
        if st.session_state.get("confirm_delete_train"):

            st.warning("⚠️ Are you sure you want to permanently delete this train and all assignments?")

            col1, col2 = st.columns(2)

            with col1:
                if st.button("✅ YES, DELETE"):
                    with get_conn() as conn:
                        if USE_MONGO:
                            conn.db.trains.delete_one({'train_no': sel})
                            conn.db.train_coaches.delete_many({'train_no': sel})
                        else:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM trains WHERE train_no=?", (sel,))
                            cur.execute("DELETE FROM train_coaches WHERE train_no=?", (sel,))
                            conn.commit()

                    # ✅ RESET STATE
                    del st.session_state["confirm_delete_train"]

                    show_success("Train and assignments removed successfully.")
                    st.rerun()

            with col2:
                if st.button("❌ Cancel"):
                    del st.session_state["confirm_delete_train"]
                    st.rerun()



# --- Assign coaches ---

def page_assign():
    page_top_bar()
    st.subheader("Assign Coaches to Trains")

    with get_conn() as conn:
        if USE_MONGO:
            trains = list(conn.db.trains.find({}, {'train_no':1,'train_name':1,'_id':0}).sort('train_no',1))
            coaches = list(conn.db.coaches.find({'status': {'$ne': 'Removed'}}, {'coach_id':1,'_id':0}).sort('coach_id',1))
        else:
            cur = conn.cursor()
            cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
            trains = cur.fetchall()
            cur.execute("SELECT coach_id FROM coaches WHERE status!='Removed' ORDER BY coach_id")
            coaches = cur.fetchall()

    train_options = [f"{r['train_no']} — {r.get('train_name','')}" for r in trains] if USE_MONGO else [f"{r['train_no']} — {r['train_name']}" for r in trains]
    coach_options = [r['coach_id'] for r in coaches]

    c1, c2 = st.columns(2)
    train_sel = c1.selectbox("Select Train", [""] + train_options,key="5")
    coach_sel = c2.selectbox("Select Coach", [""] + coach_options,key="6")

    if st.button("Assign"):
        if not train_sel:
            show_error("Select a train.")
        elif not coach_sel:
            show_error("Select a coach.")
        else:
            train_no = train_sel.split(" — ")[0].strip()
            coach_id = coach_sel.strip()
            if USE_MONGO:
                try:
                    with get_conn() as conn:
                        conn.db.train_coaches.insert_one({'train_no': train_no, 'coach_id': coach_id})
                    show_success(f"Assigned {coach_id} to {train_no}.")
                    st.stop()
                except Exception:
                    show_error("This coach is already assigned to this train.")
            else:
                try:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT INTO train_coaches (train_no, coach_id) VALUES (?, ?)", (train_no, coach_id))
                        conn.commit()
                    show_success(f"Assigned {coach_id} to {train_no}.")
                    st.stop()
                except Exception:
                    show_error("This coach is already assigned to this train.")

    st.markdown("---")
    st.subheader("Assigned Coaches (by train)")
    with get_conn() as conn:
        if USE_MONGO:
            pipeline = [
                {'$lookup': {'from': 'trains', 'localField': 'train_no', 'foreignField': 'train_no', 'as': 'train_docs'}},
                {'$unwind': {'path': '$train_docs', 'preserveNullAndEmptyArrays': True}},
                {'$project': {'_id': 0, 'train_no': 1, 'coach_id': 1, 'train_name': '$train_docs.train_name'}},
                {'$sort': {'train_no': 1, 'coach_id': 1}}
            ]
            rows = list(conn.db.train_coaches.aggregate(pipeline))
        else:
            cur = conn.cursor()
            cur.execute("""
                SELECT tc.train_no, t.train_name, tc.coach_id
                FROM train_coaches tc
                LEFT JOIN trains t ON tc.train_no=t.train_no
                ORDER BY tc.train_no, tc.coach_id
            """)
            rows = cur.fetchall()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch")
        st.markdown(dataframe_download_link(df, "assignments.csv"), unsafe_allow_html=True)
    else:
        st.info("No assignments yet.")

# --- View / Remove train coaches ---

def page_train_coaches():
    page_top_bar()
    st.subheader("View / Remove Train Coaches")

    with get_conn() as conn:
        if USE_MONGO:
            trains = list(conn.db.trains.find({}, {'train_no':1,'train_name':1,'_id':0}).sort('train_no',1))
        else:
            cur = conn.cursor()
            cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
            trains = cur.fetchall()
    train_options = [f"{r['train_no']} — {r.get('train_name','')}" for r in trains] if USE_MONGO else [f"{r['train_no']} — {r['train_name']}" for r in trains]
    sel = st.selectbox("Select train", [""] + train_options,key="7")

    if sel:
        train_no = sel.split(" — ")[0].strip()
        with get_conn() as conn:
            if USE_MONGO:
                rows = list(conn.db.train_coaches.find({'train_no': train_no}, {'coach_id':1,'_id':0}).sort('coach_id',1))
            else:
                cur = conn.cursor()
                cur.execute("SELECT coach_id FROM train_coaches WHERE train_no=? ORDER BY coach_id", (train_no,))
                rows = cur.fetchall()
        coaches = [r['coach_id'] for r in rows]

        if not coaches:
            st.info("No coaches assigned to this train.")
        else:
            choices = st.multiselect("Assigned coaches (select to remove)", coaches)
            if st.button("Remove selected"):
                if not choices:
                    show_error("Select at least one coach")
                else:
                    with get_conn() as conn:
                        if USE_MONGO:
                            for c in choices:
                                conn.db.train_coaches.delete_one({'train_no': train_no, 'coach_id': c})
                        else:
                            cur = conn.cursor()
                            for c in choices:
                                cur.execute("DELETE FROM train_coaches WHERE train_no=? AND coach_id=?", (train_no, c))
                            conn.commit()
                    show_success("Selected coaches removed.")
                    st.stop()

# --- Record maintenance ---

def page_record_maintenance():
    page_top_bar()
    st.subheader("Record Maintenance")

    if not st.session_state["engineer"]:
        st.info("Engineer login required to record maintenance.")
        if st.button("Go to Engineer Login"):
            st.session_state["page"] = "Engineer Login"
            st.stop()
        return

    with get_conn() as conn:
        if USE_MONGO:
            coaches = [r['coach_id'] for r in list(conn.db.coaches.find({}, {'coach_id':1,'_id':0}).sort('coach_id',1))]
            trains = [f"{r['train_no']} — {r.get('train_name','')}" for r in list(conn.db.trains.find({}, {'train_no':1,'train_name':1,'_id':0}).sort('train_no',1))]
        else:
            cur = conn.cursor()
            cur.execute("SELECT coach_id FROM coaches ORDER BY coach_id")
            coaches = [r['coach_id'] for r in cur.fetchall()]
            cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
            trains = [f"{r['train_no']} — {r['train_name']}" for r in cur.fetchall()]

    c1, c2 = st.columns(2)
    coach_sel = c1.selectbox("Select Coach", [""] + coaches,key="8")
    train_sel = c2.selectbox("Train (optional)", [""] + trains,key="9")
    mtype = st.text_input("Maintenance Type / Task")
    notes = st.text_area("Notes (optional)", height=120)
    date_val = st.text_input("Date (dd-mm-YYYY)", value=datetime.now().strftime("%d-%m-%Y"))

    if st.button("Save Maintenance"):
        if not coach_sel:
            show_error("Select a coach.")
        else:
            train_no = train_sel.split(" — ")[0].strip() if train_sel else None
            with get_conn() as conn:
                if USE_MONGO:
                    # auto-generate a simple record id
                    rec = {
                        'coach_id': coach_sel,
                        'train_no': train_no,
                        'date': date_val.strip(),
                        'maintenance_type': mtype.strip(),
                        'engineer': st.session_state['engineer'],
                        'notes': notes.strip() or None,
                        'created_at': datetime.utcnow()
                    }
                    conn.db.maintenance_records.insert_one(rec)
                    conn.db.coaches.update_one({'coach_id': coach_sel}, {'$set': {'last_maintenance': date_val.strip()}})
                else:
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT INTO maintenance_records
                        (coach_id, train_no, date, maintenance_type, engineer, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (coach_sel, train_no, date_val.strip(), mtype.strip(), st.session_state['engineer'], notes.strip() or None))
                    cur.execute("UPDATE coaches SET last_maintenance=? WHERE coach_id=?", (date_val.strip(), coach_sel))
                    conn.commit()
            show_success("Maintenance recorded.")
            st.stop()

# --- History ---

def page_history():
    page_top_bar()
    st.subheader("Maintenance History")

    with get_conn() as conn:
        if USE_MONGO:
            rows = list(conn.db.maintenance_records.find({}, {'_id':0}).sort('date', -1))
        else:
            cur = conn.cursor()
            cur.execute("""
                SELECT date, train_no, coach_id, maintenance_type, engineer, notes
                FROM maintenance_records
                ORDER BY date DESC
            """)
            rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows, columns=["date", "train_no", "coach_id", "maintenance_type", "engineer", "notes"]) if not USE_MONGO else pd.DataFrame(rows)
    else:
        df = pd.DataFrame(columns=["date", "train_no", "coach_id", "maintenance_type", "engineer", "notes"])

    if not df.empty and "date" in df.columns:
        df["date"] = df["date"].apply(format_date_for_display)

    c1, c2, c3 = st.columns(3)
    train_filter = c1.selectbox("Filter by Train", ["All"] + sorted(df["train_no"].dropna().astype(str).unique()) if not df.empty else ["All"],key="10")
    coach_filter = c2.selectbox("Filter by Coach", ["All"] + sorted(df["coach_id"].dropna().astype(str).unique()) if not df.empty else ["All"],key="11")
    eng_filter = c3.selectbox("Filter by Engineer", ["All"] + sorted(df["engineer"].dropna().astype(str).unique()) if not df.empty else ["All"],key="12")

    df_filt = df.copy()
    if train_filter != "All":
        df_filt = df_filt[df_filt["train_no"].astype(str) == train_filter]
    if coach_filter != "All":
        df_filt = df_filt[df_filt["coach_id"].astype(str) == coach_filter]
    if eng_filter != "All":
        df_filt = df_filt[df_filt["engineer"].astype(str) == eng_filter]

    st.dataframe(df_filt, width="stretch")
    st.markdown(dataframe_download_link(df_filt, "maintenance_history.csv"), unsafe_allow_html=True)

# --- System login / logout ---

def page_system_login():
    page_top_bar()
    st.subheader("System Login / Management")

    if st.session_state.get("system_user"):
        st.success(f"Logged in as SYSTEM: {st.session_state['system_user']}")
        if st.button("Logout System"):
            st.session_state["system_user"] = None
            st.stop()
        return

    with st.form("system_login"):
        username = st.text_input("SYSTEM Username")
        password = st.text_input("SYSTEM Password", type="password")
        if st.form_submit_button("Login"):
            if not username.strip() or not password:
                show_error("Username and password required.")
            else:
                with get_conn() as conn:
                    if USE_MONGO:
                        r = conn.db.system_users.find_one({'username': username.strip()}, {'password_hash':1,'_id':0})
                    else:
                        cur = conn.cursor()
                        cur.execute("SELECT password_hash FROM system_users WHERE username=?", (username.strip(),))
                        r = cur.fetchone()
                if r and hash_password(password) == (r['password_hash'] if isinstance(r, dict) else r['password_hash']):
                    st.session_state['system_user'] = username.strip()
                    show_success('SYSTEM login successful.')
                    st.stop()
                else:
                    show_error('Invalid username or password.')


def page_system_logout():
    page_top_bar()
    st.subheader('System Logout')
    if st.session_state.get('system_user') or st.session_state.get('engineer'):
        st.write(f"SYSTEM: {st.session_state.get('system_user')}, Engineer: {st.session_state.get('engineer')}")
        if st.button('Logout both'):
            st.session_state['system_user'] = None
            st.session_state['engineer'] = None
            show_success('Logged out.')
            st.stop()
    else:
        st.info('No user logged in.')

# --- Engineer login / management ---

def page_engineer_login():
    page_top_bar()
    st.subheader('Engineer Login / Management')

    if st.session_state.get('engineer'):
        st.success(f"Engineer logged in: {st.session_state['engineer']}")
        if st.button('Logout Engineer'):
            st.session_state['engineer'] = None
            st.stop()
        return

    with st.form('engineer_login'):
        username = st.text_input('Engineer Username')
        password = st.text_input('Password', type='password')
        if st.form_submit_button('Login'):
            if not username.strip() or not password:
                show_error('Username and password required.')
            else:
                with get_conn() as conn:
                    if USE_MONGO:
                        r = conn.db.engineers.find_one({'username': username.strip()}, {'password_hash':1,'_id':0})
                    else:
                        cur = conn.cursor()
                        cur.execute('SELECT password_hash FROM engineers WHERE username=?', (username.strip(),))
                        r = cur.fetchone()
                if not r:
                    show_error('Engineer not found.')
                elif hash_password(password) == (r['password_hash'] if isinstance(r, dict) else r['password_hash']):
                    st.session_state['engineer'] = username.strip()
                    show_success('Engineer logged in.')
                    st.stop()
                else:
                    show_error('Incorrect password.')

# --- Add / List engineers ---

def page_add_engineer():
    page_top_bar()
    st.subheader('Add Engineer (SYSTEM only)')

    if not st.session_state.get('system_user'):
        st.info('SYSTEM login is required to add engineers.')
        if st.button('Go to System Login'):
            st.session_state['page'] = 'System Login'
            st.stop()
        return

    with st.form('add_engineer', clear_on_submit=True):
        username = st.text_input('Username')
        password = st.text_input('Password', type='password')
        if st.form_submit_button('Save Engineer'):
            if not username.strip() or not password:
                show_error('Username and password required.')
            else:
                hashed = hash_password(password)
                try:
                    with get_conn() as conn:
                        if USE_MONGO:
                            conn.db.engineers.insert_one({'username': username.strip(), 'password_hash': hashed})
                        else:
                            cur = conn.cursor()
                            cur.execute('INSERT INTO engineers (username, password_hash) VALUES (?, ?)', (username.strip(), hashed))
                            conn.commit()
                    show_success('Engineer added.')
                    st.stop()
                except Exception:
                    show_error('Engineer username already exists.')


def page_engineer_list():
    page_top_bar()
    st.subheader('Engineers')
    with get_conn() as conn:
        if USE_MONGO:
            rows = list(conn.db.engineers.find({}, {'_id':0}).sort('username',1))
        else:
            cur = conn.cursor()
            cur.execute('SELECT username FROM engineers ORDER BY username')
            rows = cur.fetchall()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width='stretch')
    else:
        st.info('No engineers yet. Add via \"Add Engineer\" page.')


client = MongoClient(MONGO_URI)
db = client["railways_db"]  # Your database name

trains_collection = db["trains"]
coaches_collection = db["coaches"]
train_coaches_collection = db["train_coaches"]
maintenance_records_collection = db["maintenance_records"]
import pickle

@st.cache_resource
def load_rf_model():
    with open("maintenance_rf_model.pkl", "rb") as f:
        return pickle.load(f)

rf_model = load_rf_model()
       
def page_predictive_maintenance():
    st.title("Predictive Maintenance 🚆")

    # ---------------- Select Train ----------------
    train_nos = sorted(
        list(set(t["train_no"] for t in trains_collection.find()))
    )

    selected_train = st.selectbox(
        "Select Train Number",
        train_nos,
        key="pm_train"
    )

    # ---------------- Select Coach ----------------
    train_coach_docs = list(
        train_coaches_collection.find({"train_no": selected_train})
    )

    if not train_coach_docs:
        st.warning("No coaches mapped to this train")
        return

    coach_ids = sorted(tc["coach_id"] for tc in train_coach_docs)
    selected_coach = st.selectbox(
        "Select Coach ID",
        coach_ids,
        key="pm_coach"
    )

    # ---------------- Run Prediction ----------------
    if st.button("Run Maintenance Check", key="pm_run"):

        coach_data = coaches_collection.find_one(
            {"coach_id": selected_coach}
        )

        if not coach_data:
            st.error("Coach details not found")
            return

        # 1️⃣ Base features
        total_km, days_since_maintenance = calculate_features(coach_data)

        # 2️⃣ Derived ML features
        vibration_level = round(min(0.1 + total_km / 250000, 1.0), 2)
        brake_health = round(max(100 - (total_km / 2500), 20), 1)

        # 3️⃣ ML Prediction (ONLY ONE CALL ✅)
        risk_level, score = predict_maintenance_risk(
            total_km,
            vibration_level,
            brake_health
        )

        # ---------------- Output ----------------
        st.subheader(f"Train {selected_train} – Coach {selected_coach}")
        # ---------------- Horizontal Level Indicators with controlled width ----------------
        st.subheader("Condition Indicators")

        MAX_KM = 33000
        km_pct = int(min((total_km / MAX_KM) * 100, 100))
        vibration_pct = int(vibration_level * 100)
        brake_pct = int(brake_health)

        # Wrap each progress bar in columns to control width
        col1, col2, col3 = st.columns([1, 3, 1])  # middle column will contain the progress bar
        with col2:
            st.markdown(f"**Total KM Run:** {total_km} km / {MAX_KM} km ({km_pct}%)")
            st.progress(km_pct)

        col1, col2, col3 = st.columns([1, 3, 1])
        with col2:
            st.markdown(f"**Vibration Level:** {vibration_pct}%")
            st.progress(vibration_pct)

        col1, col2, col3 = st.columns([1, 3, 1])
        with col2:
            st.markdown(f"**Brake Health:** {brake_pct}%")
            st.progress(brake_pct)

        # Overall Maintenance Risk
        col1, col2, col3 = st.columns([1, 3, 1])
        with col2:
            st.markdown(f"**Maintenance Risk:** {risk_level} — {score}%")
            st.progress(score)



# ---------------- Calculate Features ----------------
def calculate_features(coach_data):
    today = datetime.today()

    # --- Parse KM correctly ---
    total_km = coach_data.get("km_run", 0)

    # --- Parse Date correctly ---
    last_maint_str = coach_data.get("last_maintenance")

    if last_maint_str:
        last_maint = datetime.strptime(last_maint_str, "%d.%m.%Y")
        days_since_maintenance = (today - last_maint).days
    else:
        days_since_maintenance = 0

    return total_km, days_since_maintenance


# ---------------- Dynamic Risk Scoring ----------------
def predict_maintenance_risk(total_km, vibration, brake_health):
    prediction = rf_model.predict(
        [[total_km, vibration, brake_health]]
    )[0]

    # ---- Proper risk scoring ----
    km_score = min((total_km / 33000) * 40, 40)
    vibration_score = vibration * 30
    brake_score = (100 - brake_health) * 0.3

    risk_score = int(min(km_score + vibration_score + brake_score, 100))

    if prediction == 2:
        return "High", risk_score
    elif prediction == 1:
        return "Medium", risk_score
    else:
        return "Low", risk_score




# ---------------- Animated Speedometer ----------------

# --- Router & sidebar ---
PAGES = {
    'Dashboard': page_dashboard,
    'Coaches': page_coaches,
    'Trains': page_trains,
    'Assign': page_assign,
    'Train Coaches': page_train_coaches,
    'Record Maintenance': page_record_maintenance,
    'History': page_history,
    'Engineer Login': page_engineer_login,
    'Add Engineer': page_add_engineer,
    'Engineers': page_engineer_list,
    'System Login': page_system_login,
    'System Logout': page_system_logout
}
PAGES["Predictive Maintenance"] = page_predictive_maintenance
st.set_page_config(page_title='Railway Maintenance System', layout='wide')
with st.sidebar:
    st.markdown('## Menu')
    page = st.radio('Go to', list(PAGES.keys()), index=list(PAGES.keys()).index(st.session_state.get('page', 'Dashboard')),key='102')
    st.markdown('---')
    st.write(f"Logged in (SYSTEM): {st.session_state.get('system_user')}\nEngineer: {st.session_state.get('engineer')}")
    st.markdown('---')
    st.caption('Advanced mode: status cards, data export. Use DB download to backup.')

st.session_state['page'] = page
func = PAGES.get(page)
if func:
    func()
else:
    st.write('Page not found.')
