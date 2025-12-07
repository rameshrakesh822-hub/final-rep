
# railway_streamlit_full.py
"""
INDIAN RAILWAYS MAINTENANCE SYSTEM — Streamlit (Single file, advanced UI)
Features:
- Auto-create SQLite tables if missing (uses existing railway.db)
- System login (SHA-256)
- Engineer login (SHA-256)
- Add/list engineers (SYSTEM only)
- Trains: add/edit/delete/list
- Coaches: add/edit/delete/list, KM, status, last_maintenance
- Assign coaches to trains, view & remove assignments
- Record maintenance (engineer required), notes, update coach last_maintenance
- Maintenance history with filters (date range, engineer, train, coach)
- Dashboard: stat cards, alerts (KM & days), recent maintenance
- Export CSV / download DB
- Modern UI with columns, expanders, and informative messages
"""
import jwt
import sqlite3
from contextlib import contextmanager
import hashlib
from datetime import datetime, timedelta
import streamlit as st
import pandas as pd
import io
import os
import base64
SECRET_KEY = "SOORYA123"   # ⚠️ SAME as Node.js JWT secret

query_params = st.query_params
token = query_params.get("token")

if not token:
    st.error("🚫 Unauthorized access. Please login from the website.")
    st.stop()

try:
    decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    username = decoded.get("user", {}).get("username", "User")
    st.success(f"✅ Welcome {username}")
except:
    st.error("🚫 Session expired or invalid. Please login again.")
    st.stop()
    

# ---------------- Streamlit Compatibility ----------------
# For Streamlit >=1.52.1 compatibility
if not hasattr(st, "experimental_rerun"):
    st.experimental_rerun = lambda: st.stop()

# ---------------- CONFIG ----------------
DB_PATH = "railway.db"   # uses local DB file by default
KM_LIMIT = 5000
DAYS_LIMIT = 180
DAYS_SOON = 150

# ---------------- DB HELPERS ----------------
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        # Create tables if missing
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
            notes TEXT,
            FOREIGN KEY(coach_id) REFERENCES coaches(coach_id),
            FOREIGN KEY(train_no) REFERENCES trains(train_no)
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
            PRIMARY KEY(train_no, coach_id),
            FOREIGN KEY(train_no) REFERENCES trains(train_no),
            FOREIGN KEY(coach_id) REFERENCES coaches(coach_id)
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS engineers (
            username TEXT PRIMARY KEY,
            password_hash TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS system_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT
        )''')
        # Insert default system user if table empty
        cur.execute("SELECT count(*) as c FROM system_users")
        r = cur.fetchone()
        if r is None or r["c"] == 0:
            default_user = "admin"
            default_pw = hashlib.sha256("admin123".encode()).hexdigest()
            cur.execute("INSERT OR REPLACE INTO system_users (username, password_hash) VALUES (?, ?)", (default_user, default_pw))
        conn.commit()

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
    # try ISO parse fallback
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
    km = row["km_run"] or 0
    last = row["last_maintenance"]
    days_passed = None
    if last:
        dt = parse_date_safely(last)
        if dt:
            days_passed = (datetime.now() - dt).days
    if km >= KM_LIMIT or (days_passed is not None and days_passed >= DAYS_LIMIT):
        return "Overdue"
    if km >= (KM_LIMIT - 500) or (days_passed is not None and days_passed >= DAYS_SOON):
        return "Due Soon"
    return "OK"

def get_due_maintenance():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT coach_id, type, last_maintenance, km_run, status FROM coaches WHERE status!='Removed' ORDER BY coach_id")
        rows = cur.fetchall()
    alerts = []
    for r in rows:
        km = r["km_run"] or 0
        last = r["last_maintenance"]
        days_passed = None
        if last:
            dt = parse_date_safely(last)
            if dt:
                days_passed = (datetime.now() - dt).days
        if km >= KM_LIMIT or (days_passed is not None and days_passed >= DAYS_LIMIT):
            alerts.append({
                "coach_id": r["coach_id"],
                "type": r["type"],
                "last_maintenance": last,
                "km_run": km,
                "days_passed": days_passed
            })
    return alerts

# ---------------- Session state defaults ----------------
if "system_user" not in st.session_state:
    st.session_state["system_user"] = None
if "engineer" not in st.session_state:
    st.session_state["engineer"] = None
if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"

# initialize DB/tables
init_db()

# ---------------- UI Helpers ----------------
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
    if not os.path.exists(db_path):
        return None
    with open(db_path, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    return f'<a href="data:application/octet-stream;base64,{b64}" download="{os.path.basename(db_path)}">Download DB file</a>'

# ---------------- Pages ----------------

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

# ---------------- DASHBOARD ----------------
def page_dashboard():
    page_top_bar()
    st.subheader("Dashboard")
    with get_conn() as conn:
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

# ---------------- Coaches Management ----------------
def page_coaches():
    page_top_bar()
    st.subheader("Coaches Management")

    # Load coaches
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT coach_id, type, last_maintenance, km_run, status
            FROM coaches
            ORDER BY coach_id
        """)
        rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows, columns=["coach_id", "type", "last_maintenance", "km_run", "status"])
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
        status = st.selectbox("Status", ["Active", "Inactive", "Removed"])
        if st.form_submit_button("Save Coach"):
            if not coach_id.strip():
                show_error("Coach ID is required.")
            else:
                try:
                    kmv = int(km_run) if km_run else 0
                except:
                    show_error("KM Run must be an integer.")
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
                    except sqlite3.IntegrityError:
                        show_error("Coach ID already exists.")

    # Edit/Delete Coach
    st.markdown("### Edit / Delete Coach")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT coach_id FROM coaches ORDER BY coach_id")
        options = [r["coach_id"] for r in cur.fetchall()]

    sel = st.selectbox("Select coach to edit/delete", [""] + options)
    if sel:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM coaches WHERE coach_id=?", (sel,))
            r = cur.fetchone()

        if r:
            with st.form("edit_coach"):
                c1, c2 = st.columns(2)
                type_new = c1.text_input("Type", value=r["type"] or "")
                km_new = c2.text_input("KM Run", value=str(r["km_run"] or 0))
                last_new = st.text_input("Last maintenance (dd-mm-YYYY)", value=r["last_maintenance"] or "")
                status_new = st.selectbox(
                    "Status",
                    ["Active", "Inactive", "Removed"],
                    index=0 if (r["status"] or "Active") == "Active"
                    else (1 if (r["status"] or "") == "Inactive" else 2)
                )
                if st.form_submit_button("Save changes"):
                    try:
                        kmv = int(km_new) if km_new else 0
                    except:
                        show_error("KM Run must be integer.")
                    else:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute("""
                                UPDATE coaches
                                SET type=?, last_maintenance=?, km_run=?, status=?
                                WHERE coach_id=?
                            """, (type_new.strip() or None, last_new.strip() or None, kmv, status_new, sel))
                            conn.commit()
                        show_success("Coach updated.")
                        st.stop()
                if st.button("Delete coach (permanent)"):
                    confirm = st.checkbox("Confirm permanent delete of coach and related data")
                    if confirm:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM coaches WHERE coach_id=?", (sel,))
                            cur.execute("DELETE FROM train_coaches WHERE coach_id=?", (sel,))
                            cur.execute("DELETE FROM maintenance_records WHERE coach_id=?", (sel,))
                            conn.commit()
                        show_success("Coach and related data deleted.")
                        st.stop()

# ---------------- Trains Management ----------------
def page_trains():
    page_top_bar()
    st.subheader("Trains Management")

    # Load trains
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM trains ORDER BY train_no")
        rows = cur.fetchall()
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["train_no", "train_name", "source", "destination"])
    if not df.empty:
        st.dataframe(df, width="stretch")
        st.markdown(dataframe_download_link(df, "trains.csv"), unsafe_allow_html=True)
    else:
        st.info("No trains registered. Add a train below.")

    # Add Train
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
                try:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT INTO trains (train_no, train_name, source, destination) VALUES (?, ?, ?, ?)",
                                    (train_no.strip(), train_name.strip(), source.strip(), dest.strip()))
                        conn.commit()
                    show_success("Train added.")
                    st.stop()
                except sqlite3.IntegrityError:
                    show_error("Train No already exists.")

    # Edit/Delete Train
    st.markdown("### Edit / Delete Train")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT train_no FROM trains ORDER BY train_no")
        options = [r["train_no"] for r in cur.fetchall()]
    sel = st.selectbox("Select train", [""] + options)
    if sel:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM trains WHERE train_no=?", (sel,))
            r = cur.fetchone()
        if r:
            with st.form("edit_train"):
                train_name_new = st.text_input("Train Name", value=r["train_name"] or "")
                source_new = st.text_input("Source", value=r["source"] or "")
                dest_new = st.text_input("Destination", value=r["destination"] or "")
                if st.form_submit_button("Save changes"):
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("UPDATE trains SET train_name=?, source=?, destination=? WHERE train_no=?",
                                    (train_name_new.strip(), source_new.strip(), dest_new.strip(), sel))
                        conn.commit()
                    show_success("Train updated.")
                    st.stop()
                if st.button("Delete train (remove assignments)"):
                    confirm = st.checkbox("Confirm delete train and its assignments")
                    if confirm:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM trains WHERE train_no=?", (sel,))
                            cur.execute("DELETE FROM train_coaches WHERE train_no=?", (sel,))
                            conn.commit()
                        show_success("Train and assignments removed.")
                        st.stop()


# ---------------- Assign Coaches to Trains ----------------
def page_assign():
    page_top_bar()
    st.subheader("Assign Coaches to Trains")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
        trains = cur.fetchall()
        cur.execute("SELECT coach_id FROM coaches WHERE status!='Removed' ORDER BY coach_id")
        coaches = cur.fetchall()

    train_options = [f"{r['train_no']} — {r['train_name']}" for r in trains]
    coach_options = [r['coach_id'] for r in coaches]

    c1, c2 = st.columns(2)
    train_sel = c1.selectbox("Select Train", [""] + train_options)
    coach_sel = c2.selectbox("Select Coach", [""] + coach_options)

    if st.button("Assign"):
        if not train_sel:
            show_error("Select a train.")
        elif not coach_sel:
            show_error("Select a coach.")
        else:
            train_no = train_sel.split(" — ")[0].strip()
            coach_id = coach_sel.strip()
            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO train_coaches (train_no, coach_id) VALUES (?, ?)", (train_no, coach_id))
                    conn.commit()
                show_success(f"Assigned {coach_id} to {train_no}.")
                st.stop()
            except sqlite3.IntegrityError:
                show_error("This coach is already assigned to this train.")

    st.markdown("---")
    st.subheader("Assigned Coaches (by train)")
    with get_conn() as conn:
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


# ---------------- View / Remove Train Coaches ----------------
def page_train_coaches():
    page_top_bar()
    st.subheader("View / Remove Train Coaches")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
        trains = cur.fetchall()
    train_options = [f"{r['train_no']} — {r['train_name']}" for r in trains]
    sel = st.selectbox("Select train", [""] + train_options)

    if sel:
        train_no = sel.split(" — ")[0].strip()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT coach_id FROM train_coaches WHERE train_no=? ORDER BY coach_id", (train_no,))
            rows = cur.fetchall()
        coaches = [r["coach_id"] for r in rows]

        if not coaches:
            st.info("No coaches assigned to this train.")
        else:
            choices = st.multiselect("Assigned coaches (select to remove)", coaches)
            if st.button("Remove selected"):
                if not choices:
                    show_error("Select at least one coach")
                else:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        for c in choices:
                            cur.execute("DELETE FROM train_coaches WHERE train_no=? AND coach_id=?", (train_no, c))
                        conn.commit()
                    show_success("Selected coaches removed.")
                    st.stop()


# ---------------- Record Maintenance ----------------
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
        cur = conn.cursor()
        cur.execute("SELECT coach_id FROM coaches ORDER BY coach_id")
        coaches = [r["coach_id"] for r in cur.fetchall()]
        cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
        trains = [f"{r['train_no']} — {r['train_name']}" for r in cur.fetchall()]

    c1, c2 = st.columns(2)
    coach_sel = c1.selectbox("Select Coach", [""] + coaches)
    train_sel = c2.selectbox("Train (optional)", [""] + trains)
    mtype = st.text_input("Maintenance Type / Task")
    notes = st.text_area("Notes (optional)", height=120)
    date_val = st.text_input("Date (dd-mm-YYYY)", value=datetime.now().strftime("%d-%m-%Y"))

    if st.button("Save Maintenance"):
        if not coach_sel:
            show_error("Select a coach.")
        else:
            train_no = train_sel.split(" — ")[0].strip() if train_sel else None
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO maintenance_records
                    (coach_id, train_no, date, maintenance_type, engineer, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (coach_sel, train_no, date_val.strip(), mtype.strip(), st.session_state["engineer"], notes.strip() or None))
                cur.execute("UPDATE coaches SET last_maintenance=? WHERE coach_id=?", (date_val.strip(), coach_sel))
                conn.commit()
            show_success("Maintenance recorded.")
            st.stop()


# ---------------- Maintenance History ----------------
def page_history():
    page_top_bar()
    st.subheader("Maintenance History")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, train_no, coach_id, maintenance_type, engineer, notes
            FROM maintenance_records
            ORDER BY date DESC
        """)
        rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows, columns=["date", "train_no", "coach_id", "maintenance_type", "engineer", "notes"])
    else:
        df = pd.DataFrame(columns=["date", "train_no", "coach_id", "maintenance_type", "engineer", "notes"])

    # Format date safely
    if not df.empty and "date" in df.columns:
        df["date"] = df["date"].apply(format_date_for_display)

    # Filters
    c1, c2, c3 = st.columns(3)
    train_filter = c1.selectbox("Filter by Train", ["All"] + sorted(df["train_no"].dropna().astype(str).unique()))
    coach_filter = c2.selectbox("Filter by Coach", ["All"] + sorted(df["coach_id"].dropna().astype(str).unique()))
    eng_filter = c3.selectbox("Filter by Engineer", ["All"] + sorted(df["engineer"].dropna().astype(str).unique()))

    df_filt = df.copy()
    if train_filter != "All":
        df_filt = df_filt[df_filt["train_no"].astype(str) == train_filter]
    if coach_filter != "All":
        df_filt = df_filt[df_filt["coach_id"].astype(str) == coach_filter]
    if eng_filter != "All":
        df_filt = df_filt[df_filt["engineer"].astype(str) == eng_filter]

    st.dataframe(df_filt, width="stretch")
    st.markdown(dataframe_download_link(df_filt, "maintenance_history.csv"), unsafe_allow_html=True)

# ---------------- System Login / Logout ----------------
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
                    cur = conn.cursor()
                    cur.execute("SELECT password_hash FROM system_users WHERE username=?", (username.strip(),))
                    r = cur.fetchone()
                if r and hash_password(password) == r["password_hash"]:
                    st.session_state["system_user"] = username.strip()
                    show_success("SYSTEM login successful.")
                    st.stop()
                else:
                    show_error("Invalid username or password.")


def page_system_logout():
    page_top_bar()
    st.subheader("System Logout")
    if st.session_state.get("system_user") or st.session_state.get("engineer"):
        st.write(f"SYSTEM: {st.session_state.get('system_user')}, Engineer: {st.session_state.get('engineer')}")
        if st.button("Logout both"):
            st.session_state["system_user"] = None
            st.session_state["engineer"] = None
            show_success("Logged out.")
            st.stop()
    else:
        st.info("No user logged in.")


# ---------------- Engineer Login / Management ----------------
def page_engineer_login():
    page_top_bar()
    st.subheader("Engineer Login / Management")

    if st.session_state.get("engineer"):
        st.success(f"Engineer logged in: {st.session_state['engineer']}")
        if st.button("Logout Engineer"):
            st.session_state["engineer"] = None
            st.stop()
        return

    with st.form("engineer_login"):
        username = st.text_input("Engineer Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            if not username.strip() or not password:
                show_error("Username and password required.")
            else:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT password_hash FROM engineers WHERE username=?", (username.strip(),))
                    r = cur.fetchone()
                if not r:
                    show_error("Engineer not found.")
                elif hash_password(password) == r["password_hash"]:
                    st.session_state["engineer"] = username.strip()
                    show_success("Engineer logged in.")
                    st.stop()
                else:
                    show_error("Incorrect password.")


# ---------------- Add / List Engineers ----------------
def page_add_engineer():
    page_top_bar()
    st.subheader("Add Engineer (SYSTEM only)")

    if not st.session_state.get("system_user"):
        st.info("SYSTEM login is required to add engineers.")
        if st.button("Go to System Login"):
            st.session_state["page"] = "System Login"
            st.stop()
        return

    with st.form("add_engineer", clear_on_submit=True):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Save Engineer"):
            if not username.strip() or not password:
                show_error("Username and password required.")
            else:
                hashed = hash_password(password)
                try:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT INTO engineers (username, password_hash) VALUES (?, ?)", (username.strip(), hashed))
                        conn.commit()
                    show_success("Engineer added.")
                    st.stop()
                except sqlite3.IntegrityError:
                    show_error("Engineer username already exists.")


def page_engineer_list():
    page_top_bar()
    st.subheader("Engineers")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT username FROM engineers ORDER BY username")
        rows = cur.fetchall()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch")
    else:
        st.info("No engineers yet. Add via 'Add Engineer' page.")


# ---------------- Page router & Sidebar ----------------
PAGES = {
    "Dashboard": page_dashboard,
    "Coaches": page_coaches,
    "Trains": page_trains,
    "Assign": page_assign,
    "Train Coaches": page_train_coaches,
    "Record Maintenance": page_record_maintenance,
    "History": page_history,
    "Engineer Login": page_engineer_login,
    "Add Engineer": page_add_engineer,
    "Engineers": page_engineer_list,
    "System Login": page_system_login,
    "System Logout": page_system_logout
}

# Sidebar & layout
st.set_page_config(page_title="Railway Maintenance System", layout="wide")
with st.sidebar:
    st.markdown("## Menu")
    page = st.radio("Go to", list(PAGES.keys()), index=list(PAGES.keys()).index(st.session_state.get("page", "Dashboard")))
    st.markdown("---")
    st.write(f"Logged in (SYSTEM): {st.session_state.get('system_user')}\nEngineer: {st.session_state.get('engineer')}")
    st.markdown("---")
    st.caption("Advanced mode: status cards, data export. Use DB download to backup.")

# Set current page
st.session_state["page"] = page
func = PAGES.get(page)
if func:
    func()
else:
    st.write("Page not found.")

# railway_streamlit_full.py
"""
INDIAN RAILWAYS MAINTENANCE SYSTEM — Streamlit (Single file, advanced UI)
Features:
- Auto-create SQLite tables if missing (uses existing railway.db)
- System login (SHA-256)
- Engineer login (SHA-256)
- Add/list engineers (SYSTEM only)
- Trains: add/edit/delete/list
- Coaches: add/edit/delete/list, KM, status, last_maintenance
- Assign coaches to trains, view & remove assignments
- Record maintenance (engineer required), notes, update coach last_maintenance
- Maintenance history with filters (date range, engineer, train, coach)
- Dashboard: stat cards, alerts (KM & days), recent maintenance
- Export CSV / download DB
- Modern UI with columns, expanders, and informative messages
"""
import sqlite3
from contextlib import contextmanager
import hashlib
from datetime import datetime, timedelta
import streamlit as st
import pandas as pd
import io
import os
import base64

# ---------------- Streamlit Compatibility ----------------
# For Streamlit >=1.52.1 compatibility
if not hasattr(st, "experimental_rerun"):
    st.experimental_rerun = lambda: st.stop()

# ---------------- CONFIG ----------------
DB_PATH = "railway.db"   # uses local DB file by default
KM_LIMIT = 5000
DAYS_LIMIT = 180
DAYS_SOON = 150

# ---------------- DB HELPERS ----------------
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        # Create tables if missing
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
            notes TEXT,
            FOREIGN KEY(coach_id) REFERENCES coaches(coach_id),
            FOREIGN KEY(train_no) REFERENCES trains(train_no)
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
            PRIMARY KEY(train_no, coach_id),
            FOREIGN KEY(train_no) REFERENCES trains(train_no),
            FOREIGN KEY(coach_id) REFERENCES coaches(coach_id)
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS engineers (
            username TEXT PRIMARY KEY,
            password_hash TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS system_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT
        )''')
        # Insert default system user if table empty
        cur.execute("SELECT count(*) as c FROM system_users")
        r = cur.fetchone()
        if r is None or r["c"] == 0:
            default_user = "admin"
            default_pw = hashlib.sha256("admin123".encode()).hexdigest()
            cur.execute("INSERT OR REPLACE INTO system_users (username, password_hash) VALUES (?, ?)", (default_user, default_pw))
        conn.commit()

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
    # try ISO parse fallback
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
    km = row["km_run"] or 0
    last = row["last_maintenance"]
    days_passed = None
    if last:
        dt = parse_date_safely(last)
        if dt:
            days_passed = (datetime.now() - dt).days
    if km >= KM_LIMIT or (days_passed is not None and days_passed >= DAYS_LIMIT):
        return "Overdue"
    if km >= (KM_LIMIT - 500) or (days_passed is not None and days_passed >= DAYS_SOON):
        return "Due Soon"
    return "OK"

def get_due_maintenance():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT coach_id, type, last_maintenance, km_run, status FROM coaches WHERE status!='Removed' ORDER BY coach_id")
        rows = cur.fetchall()
    alerts = []
    for r in rows:
        km = r["km_run"] or 0
        last = r["last_maintenance"]
        days_passed = None
        if last:
            dt = parse_date_safely(last)
            if dt:
                days_passed = (datetime.now() - dt).days
        if km >= KM_LIMIT or (days_passed is not None and days_passed >= DAYS_LIMIT):
            alerts.append({
                "coach_id": r["coach_id"],
                "type": r["type"],
                "last_maintenance": last,
                "km_run": km,
                "days_passed": days_passed
            })
    return alerts

# ---------------- Session state defaults ----------------
if "system_user" not in st.session_state:
    st.session_state["system_user"] = None
if "engineer" not in st.session_state:
    st.session_state["engineer"] = None
if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"

# initialize DB/tables
init_db()

# ---------------- UI Helpers ----------------
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
    if not os.path.exists(db_path):
        return None
    with open(db_path, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    return f'<a href="data:application/octet-stream;base64,{b64}" download="{os.path.basename(db_path)}">Download DB file</a>'

# ---------------- Pages ----------------

def page_top_bar():
    st.title("INDIAN RAILWAYS — Maintenance System (Web)")
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

# ---------------- DASHBOARD ----------------
def page_dashboard():
    page_top_bar()
    st.subheader("Dashboard")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) as c FROM coaches"); coaches_count = cur.fetchone()["c"]
        cur.execute("SELECT count(*) as c FROM trains"); trains_count = cur.fetchone()["c"]
        cur.execute("SELECT count(*) as c FROM engineers"); eng_count = cur.fetchone()["c"]
        cur.execute("SELECT count(*) as c FROM maintenance_records"); mr_count = cur.fetchone()["c"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Coaches", coaches_count)
    c2.metric("Trains", trains_count)
    c3.metric("Engineers", eng_count)
    c4.metric("Maintenance Logs", mr_count)

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

# ---------------- Coaches Management ----------------
def page_coaches():
    page_top_bar()
    st.subheader("Coaches Management")

    # Load coaches
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT coach_id, type, last_maintenance, km_run, status
            FROM coaches
            ORDER BY coach_id
        """)
        rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows, columns=["coach_id", "type", "last_maintenance", "km_run", "status"])
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
        status = st.selectbox("Status", ["Active", "Inactive", "Removed"])
        if st.form_submit_button("Save Coach"):
            if not coach_id.strip():
                show_error("Coach ID is required.")
            else:
                try:
                    kmv = int(km_run) if km_run else 0
                except:
                    show_error("KM Run must be an integer.")
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
                    except sqlite3.IntegrityError:
                        show_error("Coach ID already exists.")

    # Edit/Delete Coach
    st.markdown("### Edit / Delete Coach")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT coach_id FROM coaches ORDER BY coach_id")
        options = [r["coach_id"] for r in cur.fetchall()]

    sel = st.selectbox("Select coach to edit/delete", [""] + options)
    if sel:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM coaches WHERE coach_id=?", (sel,))
            r = cur.fetchone()

        if r:
            with st.form("edit_coach"):
                c1, c2 = st.columns(2)
                type_new = c1.text_input("Type", value=r["type"] or "")
                km_new = c2.text_input("KM Run", value=str(r["km_run"] or 0))
                last_new = st.text_input("Last maintenance (dd-mm-YYYY)", value=r["last_maintenance"] or "")
                status_new = st.selectbox(
                    "Status",
                    ["Active", "Inactive", "Removed"],
                    index=0 if (r["status"] or "Active") == "Active"
                    else (1 if (r["status"] or "") == "Inactive" else 2)
                )
                if st.form_submit_button("Save changes"):
                    try:
                        kmv = int(km_new) if km_new else 0
                    except:
                        show_error("KM Run must be integer.")
                    else:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute("""
                                UPDATE coaches
                                SET type=?, last_maintenance=?, km_run=?, status=?
                                WHERE coach_id=?
                            """, (type_new.strip() or None, last_new.strip() or None, kmv, status_new, sel))
                            conn.commit()
                        show_success("Coach updated.")
                        st.stop()
                if st.button("Delete coach (permanent)"):
                    confirm = st.checkbox("Confirm permanent delete of coach and related data")
                    if confirm:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM coaches WHERE coach_id=?", (sel,))
                            cur.execute("DELETE FROM train_coaches WHERE coach_id=?", (sel,))
                            cur.execute("DELETE FROM maintenance_records WHERE coach_id=?", (sel,))
                            conn.commit()
                        show_success("Coach and related data deleted.")
                        st.stop()

# ---------------- Trains Management ----------------
def page_trains():
    page_top_bar()
    st.subheader("Trains Management")

    # Load trains
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM trains ORDER BY train_no")
        rows = cur.fetchall()
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["train_no", "train_name", "source", "destination"])
    if not df.empty:
        st.dataframe(df, width="stretch")
        st.markdown(dataframe_download_link(df, "trains.csv"), unsafe_allow_html=True)
    else:
        st.info("No trains registered. Add a train below.")

    # Add Train
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
                try:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT INTO trains (train_no, train_name, source, destination) VALUES (?, ?, ?, ?)",
                                    (train_no.strip(), train_name.strip(), source.strip(), dest.strip()))
                        conn.commit()
                    show_success("Train added.")
                    st.stop()
                except sqlite3.IntegrityError:
                    show_error("Train No already exists.")

    # Edit/Delete Train
    st.markdown("### Edit / Delete Train")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT train_no FROM trains ORDER BY train_no")
        options = [r["train_no"] for r in cur.fetchall()]
    sel = st.selectbox("Select train", [""] + options)
    if sel:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM trains WHERE train_no=?", (sel,))
            r = cur.fetchone()
        if r:
            with st.form("edit_train"):
                train_name_new = st.text_input("Train Name", value=r["train_name"] or "")
                source_new = st.text_input("Source", value=r["source"] or "")
                dest_new = st.text_input("Destination", value=r["destination"] or "")
                if st.form_submit_button("Save changes"):
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("UPDATE trains SET train_name=?, source=?, destination=? WHERE train_no=?",
                                    (train_name_new.strip(), source_new.strip(), dest_new.strip(), sel))
                        conn.commit()
                    show_success("Train updated.")
                    st.stop()
                if st.button("Delete train (remove assignments)"):
                    confirm = st.checkbox("Confirm delete train and its assignments")
                    if confirm:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute("DELETE FROM trains WHERE train_no=?", (sel,))
                            cur.execute("DELETE FROM train_coaches WHERE train_no=?", (sel,))
                            conn.commit()
                        show_success("Train and assignments removed.")
                        st.stop()


# ---------------- Assign Coaches to Trains ----------------
def page_assign():
    page_top_bar()
    st.subheader("Assign Coaches to Trains")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
        trains = cur.fetchall()
        cur.execute("SELECT coach_id FROM coaches WHERE status!='Removed' ORDER BY coach_id")
        coaches = cur.fetchall()

    train_options = [f"{r['train_no']} — {r['train_name']}" for r in trains]
    coach_options = [r['coach_id'] for r in coaches]

    c1, c2 = st.columns(2)
    train_sel = c1.selectbox("Select Train", [""] + train_options)
    coach_sel = c2.selectbox("Select Coach", [""] + coach_options)

    if st.button("Assign"):
        if not train_sel:
            show_error("Select a train.")
        elif not coach_sel:
            show_error("Select a coach.")
        else:
            train_no = train_sel.split(" — ")[0].strip()
            coach_id = coach_sel.strip()
            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("INSERT INTO train_coaches (train_no, coach_id) VALUES (?, ?)", (train_no, coach_id))
                    conn.commit()
                show_success(f"Assigned {coach_id} to {train_no}.")
                st.stop()
            except sqlite3.IntegrityError:
                show_error("This coach is already assigned to this train.")

    st.markdown("---")
    st.subheader("Assigned Coaches (by train)")
    with get_conn() as conn:
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


# ---------------- View / Remove Train Coaches ----------------
def page_train_coaches():
    page_top_bar()
    st.subheader("View / Remove Train Coaches")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
        trains = cur.fetchall()
    train_options = [f"{r['train_no']} — {r['train_name']}" for r in trains]
    sel = st.selectbox("Select train", [""] + train_options)

    if sel:
        train_no = sel.split(" — ")[0].strip()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT coach_id FROM train_coaches WHERE train_no=? ORDER BY coach_id", (train_no,))
            rows = cur.fetchall()
        coaches = [r["coach_id"] for r in rows]

        if not coaches:
            st.info("No coaches assigned to this train.")
        else:
            choices = st.multiselect("Assigned coaches (select to remove)", coaches)
            if st.button("Remove selected"):
                if not choices:
                    show_error("Select at least one coach")
                else:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        for c in choices:
                            cur.execute("DELETE FROM train_coaches WHERE train_no=? AND coach_id=?", (train_no, c))
                        conn.commit()
                    show_success("Selected coaches removed.")
                    st.stop()


# ---------------- Record Maintenance ----------------
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
        cur = conn.cursor()
        cur.execute("SELECT coach_id FROM coaches ORDER BY coach_id")
        coaches = [r["coach_id"] for r in cur.fetchall()]
        cur.execute("SELECT train_no, train_name FROM trains ORDER BY train_no")
        trains = [f"{r['train_no']} — {r['train_name']}" for r in cur.fetchall()]

    c1, c2 = st.columns(2)
    coach_sel = c1.selectbox("Select Coach", [""] + coaches)
    train_sel = c2.selectbox("Train (optional)", [""] + trains)
    mtype = st.text_input("Maintenance Type / Task")
    notes = st.text_area("Notes (optional)", height=120)
    date_val = st.text_input("Date (dd-mm-YYYY)", value=datetime.now().strftime("%d-%m-%Y"))

    if st.button("Save Maintenance"):
        if not coach_sel:
            show_error("Select a coach.")
        else:
            train_no = train_sel.split(" — ")[0].strip() if train_sel else None
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO maintenance_records
                    (coach_id, train_no, date, maintenance_type, engineer, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (coach_sel, train_no, date_val.strip(), mtype.strip(), st.session_state["engineer"], notes.strip() or None))
                cur.execute("UPDATE coaches SET last_maintenance=? WHERE coach_id=?", (date_val.strip(), coach_sel))
                conn.commit()
            show_success("Maintenance recorded.")
            st.stop()


# ---------------- Maintenance History ----------------
def page_history():
    page_top_bar()
    st.subheader("Maintenance History")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, train_no, coach_id, maintenance_type, engineer, notes
            FROM maintenance_records
            ORDER BY date DESC
        """)
        rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows, columns=["date", "train_no", "coach_id", "maintenance_type", "engineer", "notes"])
    else:
        df = pd.DataFrame(columns=["date", "train_no", "coach_id", "maintenance_type", "engineer", "notes"])

    # Format date safely
    if not df.empty and "date" in df.columns:
        df["date"] = df["date"].apply(format_date_for_display)

    # Filters
    c1, c2, c3 = st.columns(3)
    train_filter = c1.selectbox("Filter by Train", ["All"] + sorted(df["train_no"].dropna().astype(str).unique()))
    coach_filter = c2.selectbox("Filter by Coach", ["All"] + sorted(df["coach_id"].dropna().astype(str).unique()))
    eng_filter = c3.selectbox("Filter by Engineer", ["All"] + sorted(df["engineer"].dropna().astype(str).unique()))

    df_filt = df.copy()
    if train_filter != "All":
        df_filt = df_filt[df_filt["train_no"].astype(str) == train_filter]
    if coach_filter != "All":
        df_filt = df_filt[df_filt["coach_id"].astype(str) == coach_filter]
    if eng_filter != "All":
        df_filt = df_filt[df_filt["engineer"].astype(str) == eng_filter]

    st.dataframe(df_filt, width="stretch")
    st.markdown(dataframe_download_link(df_filt, "maintenance_history.csv"), unsafe_allow_html=True)

# ---------------- System Login / Logout ----------------
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
                    cur = conn.cursor()
                    cur.execute("SELECT password_hash FROM system_users WHERE username=?", (username.strip(),))
                    r = cur.fetchone()
                if r and hash_password(password) == r["password_hash"]:
                    st.session_state["system_user"] = username.strip()
                    show_success("SYSTEM login successful.")
                    st.stop()
                else:
                    show_error("Invalid username or password.")


def page_system_logout():
    page_top_bar()
    st.subheader("System Logout")
    if st.session_state.get("system_user") or st.session_state.get("engineer"):
        st.write(f"SYSTEM: {st.session_state.get('system_user')}, Engineer: {st.session_state.get('engineer')}")
        if st.button("Logout both"):
            st.session_state["system_user"] = None
            st.session_state["engineer"] = None
            show_success("Logged out.")
            st.stop()
    else:
        st.info("No user logged in.")


# ---------------- Engineer Login / Management ----------------
def page_engineer_login():
    page_top_bar()
    st.subheader("Engineer Login / Management")

    if st.session_state.get("engineer"):
        st.success(f"Engineer logged in: {st.session_state['engineer']}")
        if st.button("Logout Engineer"):
            st.session_state["engineer"] = None
            st.stop()
        return

    with st.form("engineer_login"):
        username = st.text_input("Engineer Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            if not username.strip() or not password:
                show_error("Username and password required.")
            else:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT password_hash FROM engineers WHERE username=?", (username.strip(),))
                    r = cur.fetchone()
                if not r:
                    show_error("Engineer not found.")
                elif hash_password(password) == r["password_hash"]:
                    st.session_state["engineer"] = username.strip()
                    show_success("Engineer logged in.")
                    st.stop()
                else:
                    show_error("Incorrect password.")


# ---------------- Add / List Engineers ----------------
def page_add_engineer():
    page_top_bar()
    st.subheader("Add Engineer (SYSTEM only)")

    if not st.session_state.get("system_user"):
        st.info("SYSTEM login is required to add engineers.")
        if st.button("Go to System Login"):
            st.session_state["page"] = "System Login"
            st.stop()
        return

    with st.form("add_engineer", clear_on_submit=True):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Save Engineer"):
            if not username.strip() or not password:
                show_error("Username and password required.")
            else:
                hashed = hash_password(password)
                try:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT INTO engineers (username, password_hash) VALUES (?, ?)", (username.strip(), hashed))
                        conn.commit()
                    show_success("Engineer added.")
                    st.stop()
                except sqlite3.IntegrityError:
                    show_error("Engineer username already exists.")


def page_engineer_list():
    page_top_bar()
    st.subheader("Engineers")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT username FROM engineers ORDER BY username")
        rows = cur.fetchall()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch")
    else:
        st.info("No engineers yet. Add via 'Add Engineer' page.")


# ---------------- Page router & Sidebar ----------------
PAGES = {
    "Dashboard": page_dashboard,
    "Coaches": page_coaches,
    "Trains": page_trains,
    "Assign": page_assign,
    "Train Coaches": page_train_coaches,
    "Record Maintenance": page_record_maintenance,
    "History": page_history,
    "Engineer Login": page_engineer_login,
    "Add Engineer": page_add_engineer,
    "Engineers": page_engineer_list,
    "System Login": page_system_login,
    "System Logout": page_system_logout
}

# Sidebar & layout
st.set_page_config(page_title="Railway Maintenance System", layout="wide")
with st.sidebar:
    st.markdown("## Menu")
    page = st.radio("Go to", list(PAGES.keys()), index=list(PAGES.keys()).index(st.session_state.get("page", "Dashboard")))
    st.markdown("---")
    st.write(f"Logged in (SYSTEM): {st.session_state.get('system_user')}\nEngineer: {st.session_state.get('engineer')}")
    st.markdown("---")
    st.caption("Advanced mode: status cards, data export. Use DB download to backup.")

# Set current page
st.session_state["page"] = page
func = PAGES.get(page)
if func:
    func()
else:
    st.write("Page not found.")

