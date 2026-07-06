import streamlit as st
import pandas as pd
import re
from datetime import date, datetime, timedelta
import pytz
from supabase import create_client
from postgrest.exceptions import APIError
from streamlit_autorefresh import st_autorefresh

MMT = pytz.timezone("Asia/Rangoon")

def now_mmt() -> datetime:
    return datetime.now(MMT)

def fmt_mmt(dt_str: str) -> str:
    if not dt_str:
        return "—"
    try:
        if "T" in str(dt_str) or " " in str(dt_str):
            s = str(dt_str).replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
            except ValueError:
                dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
                dt = pytz.utc.localize(dt)
            if dt.tzinfo is None:
                dt = pytz.utc.localize(dt)
            dt_mmt = dt.astimezone(MMT)
            return dt_mmt.strftime("%Y-%m-%d %H:%M (MMT)")
        else:
            return str(dt_str)
    except Exception:
        return str(dt_str)

# ══════════════════════════════════════════════════════════
# 1. APP CONFIG
# ══════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Work Manager",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "user" in st.session_state:
    st_autorefresh(interval=300_000, key="auto_refresh")

DEFAULT_ADMIN_EMAIL    = "admin@gmail.com"
DEFAULT_ADMIN_PASSWORD = "Admin@1234"

ADMIN_EMAIL    = st.secrets.get("ADMIN_EMAIL",    DEFAULT_ADMIN_EMAIL)
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)

missing_secrets = [k for k in ("SUPABASE_URL", "SUPABASE_KEY") if k not in st.secrets]
if missing_secrets:
    st.error(f"Secrets မတွေ့ပါ: {missing_secrets}")
    st.stop()

try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
except Exception as exc:
    st.error(f"Supabase ချိတ်ဆက်မှု မအောင်မြင်ပါ: {exc}")
    st.stop()

supabase_admin = None
_admin_init_error = None
if "SUPABASE_SERVICE_KEY" in st.secrets:
    try:
        supabase_admin = create_client(
            st.secrets["SUPABASE_URL"],
            st.secrets["SUPABASE_SERVICE_KEY"]
        )
    except Exception as _e:
        _admin_init_error = str(_e)
        supabase_admin = None
else:
    _admin_init_error = "SUPABASE_SERVICE_KEY not found in secrets"

# ══════════════════════════════════════════════════════════
# 2. HELPERS
# ══════════════════════════════════════════════════════════

def get_registered_users(branch: str | None = None) -> list[str]:
    """Get users; if branch given, filter by that branch only."""
    if supabase_admin is None:
        return []
    try:
        res = supabase_admin.auth.admin.list_users()
        all_emails = [
            u.email.strip().lower() for u in res
            if u.email and u.email.strip().lower() != ADMIN_EMAIL.strip().lower()
        ]
        branch_admin_emails = get_all_branch_admin_emails()
        all_emails = [e for e in all_emails if e not in branch_admin_emails]

        if branch:
            try:
                q = supabase_admin.table("user_profiles").select("email,branch").eq("branch", branch).eq("is_approved", True).execute()
                branch_emails = {r["email"].strip().lower() for r in (q.data or [])}
                return sorted([e for e in all_emails if e in branch_emails])
            except Exception:
                return sorted(all_emails)
        return sorted(all_emails)
    except Exception:
        return []

def get_all_branch_admin_emails() -> set:
    """Return set of branch admin emails (excluding super admin)."""
    try:
        _db = supabase_admin if supabase_admin else supabase
        res = _db.table("admin_profiles").select("email").execute()
        return {r["email"].strip().lower() for r in (res.data or [])}
    except Exception:
        return set()

def get_branch_admin_profile(email: str) -> dict | None:
    """Return branch admin profile dict or None."""
    try:
        _db = supabase_admin if supabase_admin else supabase
        res = _db.table("admin_profiles").select("*").eq("email", email.strip().lower()).maybe_single().execute()
        return res.data
    except Exception:
        return None

def get_all_branches() -> list[str]:
    """Get all branch names from admin_profiles."""
    try:
        _db = supabase_admin if supabase_admin else supabase
        res = _db.table("admin_profiles").select("branch").execute()
        branches = sorted(list({r["branch"] for r in (res.data or []) if r.get("branch")}))
        return branches
    except Exception:
        return []

def is_valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()))

def try_register(email: str, password: str) -> dict:
    try:
        res = supabase.auth.sign_up({"email": email, "password": password})
        if res.user and res.user.id:
            try:
                _db = supabase_admin if supabase_admin else supabase
                _db.table("user_profiles").upsert({
                    "id": res.user.id,
                    "email": email.strip().lower(),
                    "is_approved": False,
                }).execute()
            except Exception:
                pass
            return {"ok": True}
        return {"ok": False, "msg": "Response မမှန်ကန်ပါ"}
    except Exception as exc:
        msg = str(exc).lower()
        if any(k in msg for k in ("already registered", "already exists", "user already", "email already")):
            return {"ok": False, "duplicate": True}
        if any(k in msg for k in ("rate limit", "over_email_send_rate_limit", "too many requests", "429")):
            return {"ok": False, "rate_limit": True}
        return {"ok": False, "msg": str(exc)}

def safe_get_tasks(filter_email: str | None = None, branch: str | None = None) -> pd.DataFrame:
    try:
        _db = supabase_admin if supabase_admin else supabase
        q = _db.table("tasks").select("*")
        if filter_email:
            q = q.eq("assigned_to_email", filter_email.strip().lower())
        if branch:
            q = q.eq("branch", branch)
        res = q.order("created_at", desc=True).execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame()
    except APIError as exc:
        st.error(f"Database Error: {exc.message}")
        return pd.DataFrame()
    except Exception as exc:
        st.error(f"Error: {exc}")
        return pd.DataFrame()

def safe_get_logs(task_id: int) -> pd.DataFrame:
    try:
        _db = supabase_admin if supabase_admin else supabase
        res = _db.table("task_logs").select("*").eq("task_id", task_id).order("log_date", desc=True).execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def safe_get_all_logs(branch: str | None = None) -> pd.DataFrame:
    try:
        _db = supabase_admin if supabase_admin else supabase
        if branch:
            tasks_df = safe_get_tasks(branch=branch)
            if tasks_df.empty:
                return pd.DataFrame()
            task_ids = tasks_df["id"].tolist()
            res = _db.table("task_logs").select("*").in_("task_id", task_ids).order("log_date", desc=True).execute()
        else:
            res = _db.table("task_logs").select("*").order("log_date", desc=True).execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def status_badge(status: str) -> str:
    icons = {"Pending": "🟡", "In Progress": "🔵", "Completed": "🟢", "Cancelled": "🔴"}
    return f"{icons.get(status, '⚪')} {status}"

def send_notification(to_email: str, noti_type: str, message: str):
    try:
        _db = supabase_admin if supabase_admin else supabase
        _db.table("user_notifications").insert({
            "to_email": to_email.strip().lower(),
            "type": noti_type,
            "message": message,
            "is_read": False,
        }).execute()
    except Exception:
        pass

def get_unread_notifications(email: str) -> list[dict]:
    try:
        _db = supabase_admin if supabase_admin else supabase
        res = _db.table("user_notifications").select("*").eq("to_email", email.strip().lower()).eq("is_read", False).order("created_at", desc=False).execute()
        return res.data or []
    except Exception:
        return []

def mark_notifications_read(email: str):
    try:
        _db = supabase_admin if supabase_admin else supabase
        _db.table("user_notifications").update({"is_read": True}).eq("to_email", email.strip().lower()).eq("is_read", False).execute()
    except Exception:
        pass

def do_logout():
    if st.session_state.get("user", {}).get("role") in ("user", "branch_admin"):
        try:
            supabase.auth.sign_out()
        except Exception:
            pass
    st.session_state.clear()
    st.rerun()

# ══════════════════════════════════════════════════════════
# 3. AUTH UI
# ══════════════════════════════════════════════════════════
if "user" not in st.session_state:
    st.title("🚀 Work Manager")
    st.caption("Task စီမံခန့်ခွဲမှု စနစ်")

    login_tab, signup_tab = st.tabs(["🔑 Log In", "📝 Account ဖွင့်ရန်"])

    with login_tab:
        st.markdown("#### ဝင်ရောက်ရန်")

        with st.form("login_form", clear_on_submit=False):
            email    = st.text_input("Email လိပ်စာ")
            password = st.text_input("Password", type="password")
            login_btn = st.form_submit_button("🔑 Log In", use_container_width=True)

        if login_btn:
            email = email.strip().lower()

            if not email or not password:
                st.error("Email နှင့် Password နှစ်ခုလုံး ဖြည့်ပါ။")

            elif not is_valid_email(email):
                st.error("Email format မမှန်ကန်ပါ။ (ဥပမာ: you@example.com)")

            elif email == ADMIN_EMAIL.strip().lower() and password == ADMIN_PASSWORD:
                st.session_state.user = {"email": ADMIN_EMAIL, "role": "super_admin"}
                st.rerun()

            else:
                try:
                    res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                    if res.user:
                        branch_profile = get_branch_admin_profile(email)
                        if branch_profile:
                            st.session_state.user = {
                                "email": email,
                                "role": "branch_admin",
                                "branch": branch_profile.get("branch", ""),
                                "branch_name": branch_profile.get("branch_name", branch_profile.get("branch", "")),
                                "access_token": res.session.access_token,
                            }
                            st.rerun()
                        else:
                            _db = supabase_admin if supabase_admin else supabase
                            profile = (
                                _db.table("user_profiles")
                                .select("is_approved")
                                .eq("id", res.user.id)
                                .maybe_single()
                                .execute()
                            )
                            if profile.data is None or not profile.data.get("is_approved"):
                                try:
                                    supabase.auth.sign_out()
                                except Exception:
                                    pass
                                st.warning(
                                    "⏳ **Admin အတည်ပြုချက် မရသေးပါ။**\n\n"
                                    "Account ကို Admin မှ စစ်ဆေးပြီးမှသာ Log In ဝင်နိုင်သည်။ "
                                    "ခဏ စောင့်ပါ။"
                                )
                            else:
                                st.session_state.user = {
                                    "email": res.user.email,
                                    "role":  "user",
                                    "access_token": res.session.access_token,
                                }
                                st.rerun()
                    else:
                        st.error("Login မအောင်မြင်ပါ။ Email / Password ပြန်စစ်ပါ။")
                except Exception as e:
                    st.error(f"Login မအောင်မြင်ပါ။ ({e})")

    with signup_tab:
        st.markdown("#### ဝန်ထမ်းသစ် Account ဖွင့်ရန်")
        st.info(
            "**မှတ်သားရန်:** Account ဖွင့်ပြီးနောက် Admin မှ Approve လုပ်မှသာ Log In ဝင်နိုင်သည်။\n\n"
            "Email တစ်ခုဖြင့် Account တစ်ခုသာ ဖွင့်နိုင်သည်။"
        )
        with st.form("register_form", clear_on_submit=True):
            new_email  = st.text_input("Email လိပ်စာ *")
            new_pw     = st.text_input("Password (အနည်းဆုံး 8 လုံး) *", type="password")
            confirm_pw = st.text_input("Password ထပ်ထည့်ပါ *", type="password")
            reg_btn    = st.form_submit_button("Account လျှောက်ထားမည်", use_container_width=True)

        if reg_btn:
            new_email = new_email.strip().lower()
            if not new_email or not new_pw or not confirm_pw:
                st.error("အကွက်အားလုံး ဖြည့်ပါ။")
            elif not is_valid_email(new_email):
                st.error("Email format မမှန်ကန်ပါ။ (ဥပမာ: you@example.com)")
            elif len(new_pw) < 8:
                st.error("Password အနည်းဆုံး 8 လုံး ရှိရမည်။")
            elif new_pw != confirm_pw:
                st.error("Password နှစ်ခု မတူညီပါ။ ပြန်စစ်ပါ။")
            elif new_email == ADMIN_EMAIL.strip().lower():
                st.error("ဤ email လိပ်စာ ခွင့်မပြုပါ။")
            else:
                with st.spinner("Account ဖန်တီးနေသည်..."):
                    result = try_register(new_email, new_pw)
                if result["ok"]:
                    st.success(
                        "**Account လျှောက်ထားမှု ပြီးပါပြီ!** 🎉\n\n"
                        f"`{new_email}` — Admin မှ စစ်ဆေး Approve လုပ်ပြီးမှသာ Log In ဝင်နိုင်သည်။ "
                        "ခဏ ထားပြီး ထပ်ကြိုးစားပါ။"
                    )
                elif result.get("duplicate"):
                    st.error("**ဤ Email ဖြင့် Account ရှိပြီးဖြစ်သည်။**\n\nLog In tab တွင် ဝင်ရောက်ပါ။")
                elif result.get("rate_limit"):
                    st.warning("**မိနစ်အနည်းငယ် စောင့်ပြီး ထပ်ကြိုးစားပါ။**")
                else:
                    st.error(f"Account ဖွင့်မရပါ။ နောက်မှ ထပ်ကြိုးစားပါ။ ({result.get('msg','')})")

    st.stop()

# ══════════════════════════════════════════════════════════
# 4. SIDEBAR
# ══════════════════════════════════════════════════════════
current_user = st.session_state.user
is_super_admin  = current_user["role"] == "super_admin"
is_branch_admin = current_user["role"] == "branch_admin"
is_admin        = is_super_admin or is_branch_admin

with st.sidebar:
    st.title("📱 Control Panel")
    st.markdown("**Login ဝင်သူ:**")
    st.code(current_user["email"])
    if is_super_admin:
        st.markdown("**Role:** `👑 SUPER ADMIN`")
    elif is_branch_admin:
        st.markdown("**Role:** `🏢 BRANCH ADMIN`")
        st.markdown(f"**Branch:** `{current_user.get('branch_name', current_user.get('branch', ''))}`")
    else:
        st.markdown("**Role:** `👤 USER`")
    st.divider()
    _now = now_mmt().strftime("%H:%M:%S")
    st.caption(f"🔄 နောက်ဆုံး refresh: `{_now}`")
    st.caption("⏱️ ၅ မိနစ်တစ်ကြိမ် auto refresh")
    if st.button("🔃 ယခုပဲ Refresh", use_container_width=True):
        st.rerun()
    st.divider()
    if st.button("🚪 Log Out", use_container_width=True):
        do_logout()

# ══════════════════════════════════════════════════════════
# 4B. NOTIFICATIONS (User only)
# ══════════════════════════════════════════════════════════
if not is_admin:
    try:
        _today = date.today()
        _notif_res = (
            (supabase_admin if supabase_admin else supabase).table("tasks")
            .select("id, title, due_date, status")
            .eq("assigned_to_email", current_user["email"].strip().lower())
            .not_.in_("status", ["Completed", "Cancelled"])
            .execute()
        )
        if _notif_res.data:
            for _t in _notif_res.data:
                if _t.get("due_date"):
                    _due = date.fromisoformat(str(_t["due_date"]))
                    _days_left = (_due - _today).days
                    if _days_left == 1:
                        st.warning(f"⏰ **သတိပေးချက်:** «{_t['title']}» task သည် **မနက်ဖြန် ({_due})** ပြီးဆုံးရမည်!")
                    elif _days_left == 0:
                        st.error(f"🚨 **ယနေ့ပဲ!** «{_t['title']}» task သည် **ယနေ့ ({_due})** ပြီးဆုံးရမည်!")
    except Exception:
        pass

    _notifs = get_unread_notifications(current_user["email"])
    if _notifs:
        for _n in _notifs:
            _ntype = _n.get("type", "")
            _msg   = _n.get("message", "")
            if _ntype == "approved":
                st.success(f"🎉 **သတိပေးချက်:** {_msg}")
            elif _ntype == "enabled":
                st.success(f"✅ **သတိပေးချက်:** {_msg}")
            elif _ntype in ("rejected", "disabled", "deleted"):
                st.error(f"⛔ **သတိပေးချက်:** {_msg}")
            else:
                st.info(f"ℹ️ **သတိပေးချက်:** {_msg}")
        mark_notifications_read(current_user["email"])

# ══════════════════════════════════════════════════════════
# 5A-i. SUPER ADMIN DASHBOARD
# ══════════════════════════════════════════════════════════
if is_super_admin:
    st.title("👑 Super Admin Control Center")
    st.caption("ရုံးအခွဲများ၊ Branch Admin များနှင့် ဝန်ထမ်းအားလုံးကို စီမံနိုင်သည်")

    tab_tasks, tab_analytics, tab_users, tab_branch_admins = st.tabs([
        "📋 Task စီမံခန့်ခွဲမှု",
        "📊 Analytics Dashboard",
        "👥 User စီမံခန့်ခွဲမှု",
        "🏢 Branch Admin စီမံခန့်ခွဲမှု",
    ])

    # ── TAB 1: TASKS ─────────────────────────────────────
    with tab_tasks:
        with st.expander("➕ ဝန်ထမ်း / Branch Admin ကို Task ချပေးရန်", expanded=False):
            _reg_users = get_registered_users()
            # ════════════════════════════════════════════════
            # Branch Admin emails ကိုပါ ထည့်ပေး —
            # Super Admin သည် Branch Admin များကိုလည်း Task ချနိုင်ရန်
            # get_registered_users() မပြောင်းဘဲ ဒီနေရာမှာသာ merge လုပ်သည်
            # ════════════════════════════════════════════════
            _ba_emails      = sorted(get_all_branch_admin_emails())
            _all_assignable = sorted(set(_reg_users) | set(_ba_emails))

            if not _all_assignable:
                if supabase_admin is None:
                    st.error(f"⚠️ Admin client error: `{_admin_init_error}`")
                else:
                    st.warning("⚠️ Registered users မရှိသေးပါ။ Email ကို ကိုယ်တိုင် ရိုက်ထည့်ပါ။")

            with st.form("assign_form", clear_on_submit=True):
                col1, col2 = st.columns(2)
                t_title = col1.text_input("Task ခေါင်းစဉ် *")

                if _all_assignable:
                    # Branch Admin ဆိုရင် 🏢 label ထည့်ပြသည် — email raw value မပြောင်း
                    _display_opts = ["— ရွေးချယ်ပါ —"] + [
                        f"🏢 {e} (Branch Admin)" if e in _ba_emails else e
                        for e in _all_assignable
                    ]
                    _raw_opts = ["— ရွေးချယ်ပါ —"] + _all_assignable
                    _sel_idx  = col2.selectbox(
                        "ဝန်ထမ်း / Branch Admin ရွေးချယ်ပါ *",
                        options=range(len(_display_opts)),
                        format_func=lambda i: _display_opts[i],
                    )
                    t_user = _raw_opts[_sel_idx]
                else:
                    t_user = col2.text_input("ဝန်ထမ်း / Branch Admin Email *")

                t_desc   = st.text_area("အသေးစိတ် ဖော်ပြချက်", height=80)
                col3, col4, col5, col6 = st.columns(4)
                t_prio   = col3.selectbox("အရေးပါမှု", ["Low", "Medium", "High"])
                t_due    = col4.date_input("ပြီးဆုံးရမည့် ရက်", value=None)
                t_stat   = col5.selectbox("ကနဦး Status", ["Pending", "In Progress"])
                _branches = get_all_branches()
                t_branch = col6.selectbox("ရုံးအခွဲ", ["— ရွေးချယ်ပါ —"] + _branches) if _branches else col6.text_input("ရုံးအခွဲ")
                send_btn = st.form_submit_button("📤 Task ချပေးမည်", use_container_width=True)

            if send_btn:
                t_user = (t_user or "").strip().lower()
                t_branch_val = t_branch if t_branch != "— ရွေးချယ်ပါ —" else None
                if not t_title.strip():
                    st.error("Task ခေါင်းစဉ် ထည့်ပါ။")
                elif not t_user or t_user == "— ရွေးချယ်ပါ —":
                    st.error("ဝန်ထမ်း သို့မဟုတ် Branch Admin ရွေးချယ်ပါ။")
                elif not is_valid_email(t_user):
                    st.error("ဝန်ထမ်း email မမှန်မမှန် စစ်ပါ။")
                elif t_user == ADMIN_EMAIL.strip().lower():
                    st.error("Admin account သို့ Task ချ၍ မရပါ။")
                else:
                    payload = {
                        "title":             t_title.strip(),
                        "description":       t_desc.strip() or None,
                        "assigned_to_email": t_user,
                        "created_by_email":  ADMIN_EMAIL,
                        "priority":          t_prio,
                        "status":            t_stat,
                        "due_date":          str(t_due) if t_due else None,
                        "branch":            t_branch_val,
                    }
                    try:
                        _db = supabase_admin if supabase_admin else supabase
                        _db.table("tasks").insert(payload).execute()
                        _label = "Branch Admin" if t_user in _ba_emails else "ဝန်ထမ်း"
                        st.success(f"✅ **{_label} ({t_user})** သို့ Task ချပေးပြီးပါပြီ!")
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Task ဖန်တီးမရပါ: {exc.message}")

        # ════════════════════════════════════════════════════
        # NEW: Super Admin ကိုယ်တိုင် ကိုယ်ပိုင် Task ထည့်ရန်
        # ════════════════════════════════════════════════════
        with st.expander("➕ ကိုယ်ပိုင် Task ထည့်ရန် (Super Admin)", expanded=False):
            with st.form("sa_own_task_form", clear_on_submit=True):
                sa_own_title = st.text_input("Task ခေါင်းစဉ် *", key="sa_own_title")
                sa_own_desc  = st.text_area("အသေးစိတ် ဖော်ပြချက်", height=70, key="sa_own_desc")
                sa_col_a, sa_col_b = st.columns(2)
                sa_own_prio = sa_col_a.selectbox("အရေးပါမှု", ["Low", "Medium", "High"], key="sa_own_prio")
                sa_own_due  = sa_col_b.date_input("ပြီးဆုံးရမည့် ရက်", value=None, key="sa_own_due")
                sa_own_btn  = st.form_submit_button("Task ထည့်မည်", use_container_width=True)

            if sa_own_btn:
                if not sa_own_title.strip():
                    st.error("Task ခေါင်းစဉ် ထည့်ပါ။")
                else:
                    try:
                        _db = supabase_admin if supabase_admin else supabase
                        _db.table("tasks").insert({
                            "title":             sa_own_title.strip(),
                            "description":       sa_own_desc.strip() or None,
                            "assigned_to_email": ADMIN_EMAIL.strip().lower(),
                            "created_by_email":  ADMIN_EMAIL,
                            "priority":          sa_own_prio,
                            "status":            "Pending",
                            "due_date":          str(sa_own_due) if sa_own_due else None,
                        }).execute()
                        st.success("✅ ကိုယ်ပိုင် Task ထည့်ပြီးပါပြီ!")
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Task ထည့်မရပါ: {exc.message}")

        st.divider()
        st.subheader("📊 Team Progress Overview (အားလုံး)")

        _all_branches = ["အားလုံး"] + get_all_branches()
        _sel_branch = st.selectbox("🏢 ရုံးအခွဲ စစ်ထုတ်မည်", _all_branches, key="sa_branch_filter")
        df = safe_get_tasks(branch=None if _sel_branch == "အားလုံး" else _sel_branch)

        if df.empty:
            st.info("Database တွင် Task များ မရှိသေးပါ။")
        else:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("စုစုပေါင်း Task",  len(df))
            m2.metric("🟡 Pending",        len(df[df.status == "Pending"]))
            m3.metric("🔵 In Progress",    len(df[df.status == "In Progress"]))
            m4.metric("🟢 Completed",      len(df[df.status == "Completed"]))

            st.divider()
            filter_email = st.text_input("🔍 ဝန်ထမ်း Email ဖြင့် ရှာဖွေမည် (ဘာမှ မရိုက်ရင် အားလုံးပြသည်)")
            view_df = (
                df[df["assigned_to_email"].str.contains(filter_email.strip(), case=False, na=False)]
                if filter_email.strip() else df
            )
            display_cols = [c for c in ["id", "title", "description", "assigned_to_email", "branch", "priority", "status", "due_date", "created_at"] if c in view_df.columns]
            st.dataframe(view_df[display_cols], use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("Task တစ်ခုချင်စီ စီမံခန့်ခွဲမည်")

            for _, row in view_df.iterrows():
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
                    c1.markdown(f"**{row['title']}**")
                    _branch_label = f"🏢 {row.get('branch','')}" if row.get('branch') else ""
                    c1.caption(f"👤 {row['assigned_to_email']} {_branch_label} | 🎯 {row.get('priority','N/A')} | 📅 {row.get('due_date','—')}")
                    if row.get("description"):
                        c1.caption(f"📝 {row['description']}")
                    c2.markdown(status_badge(row["status"]))

                    status_options = ["Pending", "In Progress", "Completed", "Cancelled"]
                    cur_idx = status_options.index(row["status"]) if row["status"] in status_options else 0
                    new_status = c3.selectbox("Status", status_options, index=cur_idx, key=f"sa_sel_{row['id']}", label_visibility="collapsed")
                    if c3.button("💾", key=f"sa_upd_{row['id']}", help="Status ပြောင်းမည်"):
                        try:
                            (supabase_admin if supabase_admin else supabase).table("tasks").update({"status": new_status}).eq("id", row["id"]).execute()
                            st.rerun()
                        except APIError as exc:
                            st.error(f"Update မရပါ: {exc.message}")

                    if c4.button("🗑️", key=f"sa_del_{row['id']}", help="Task ဖျက်မည်"):
                        try:
                            (supabase_admin if supabase_admin else supabase).table("tasks").delete().eq("id", row["id"]).execute()
                            st.rerun()
                        except APIError as exc:
                            st.error(f"ဖျက်မရပါ: {exc.message}")

                    logs_df = safe_get_logs(row["id"])
                    if not logs_df.empty:
                        with st.expander(f"📒 Daily Logs ({len(logs_df)} ခု)", expanded=False):
                            for _, lg in logs_df.iterrows():
                                _log_time = lg.get("log_time", "")
                                _time_str = f" {_log_time} (MMT)" if _log_time else ""
                                st.markdown(f"**{lg['log_date']}{_time_str}** — {lg['user_email']}\n\n> {lg['comment']}")
                                if lg.get("attachment_url"):
                                    st.markdown(f"📎 **ဖိုင်:** [ဒေါင်းလုတ် / ကြည့်ရှုရန်]({lg['attachment_url']})", unsafe_allow_html=False)
                                st.divider()
                    else:
                        st.caption("📒 Daily Log မရှိသေးပါ")

    # ── TAB 2: ANALYTICS ─────────────────────────────────
    with tab_analytics:
        st.subheader("📊 Analytics Dashboard")
        st.caption("ရုံးအားလုံး၏ performance နှင့် task progress ကို ကြည့်ရှုနိုင်သည်")

        _ana_branches = ["အားလုံး"] + get_all_branches()
        _ana_branch = st.selectbox("🏢 ရုံးအခွဲ စစ်ထုတ်မည်", _ana_branches, key="sa_ana_branch")
        df_all = safe_get_tasks(branch=None if _ana_branch == "အားလုံး" else _ana_branch)

        if df_all.empty:
            st.info("📭 Task Data မရှိသေးပါ — Tasks ထည့်ပြီးမှ Analytics ကြည့်နိုင်သည်")
        else:
            _today = date.today()

            def is_overdue(row) -> bool:
                if not row.get("due_date") or row.get("status") in ("Completed", "Cancelled"):
                    return False
                try:
                    return date.fromisoformat(str(row["due_date"])) < _today
                except Exception:
                    return False

            total     = len(df_all)
            completed = int((df_all["status"] == "Completed").sum())
            in_prog   = int((df_all["status"] == "In Progress").sum())
            pending   = int((df_all["status"] == "Pending").sum())
            cancelled = int((df_all["status"] == "Cancelled").sum())
            overdue   = int(df_all.apply(is_overdue, axis=1).sum())
            rate      = round(completed / total * 100, 1) if total > 0 else 0.0

            mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
            mc1.metric("📋 Tasks",       total)
            mc2.metric("🟢 Completed",   completed, f"{rate}%")
            mc3.metric("🔵 In Progress", in_prog)
            mc4.metric("🟡 Pending",     pending)
            mc5.metric("🔴 Cancelled",   cancelled)
            mc6.metric("⚠️ Overdue",     overdue)

            st.divider()
            ch1, ch2 = st.columns(2)
            with ch1:
                st.markdown("#### Task Status ခွဲခြမ်း")
                s_df = df_all["status"].value_counts().reset_index()
                s_df.columns = ["Status", "Count"]
                _so = ["Pending", "In Progress", "Completed", "Cancelled"]
                s_df["Status"] = pd.Categorical(s_df["Status"], categories=_so, ordered=True)
                st.bar_chart(s_df.sort_values("Status").set_index("Status"), color="#1D9E75", use_container_width=True)
            with ch2:
                st.markdown("#### Priority အလိုက် Tasks")
                if "priority" in df_all.columns:
                    p_df = df_all["priority"].value_counts().reset_index()
                    p_df.columns = ["Priority", "Count"]
                    _po = ["High", "Medium", "Low"]
                    p_df["Priority"] = pd.Categorical(p_df["Priority"], categories=_po, ordered=True)
                    st.bar_chart(p_df.sort_values("Priority").set_index("Priority"), color="#378ADD", use_container_width=True)

            if _ana_branch == "အားလုံး" and "branch" in df_all.columns:
                st.divider()
                st.markdown("#### 🏢 ရုံးအခွဲ အလိုက် Comparison")
                b_stats = df_all.groupby("branch").agg(
                    Total=("id", "count"),
                    Completed=("status", lambda x: int((x == "Completed").sum())),
                ).reset_index()
                b_stats["Total"]     = b_stats["Total"].astype(int)
                b_stats["Completed"] = b_stats["Completed"].astype(int)
                b_stats["Rate (%)"]  = (b_stats["Completed"] / b_stats["Total"] * 100).round(1)
                b_stats.columns = ["Branch", "Total", "Completed ✅", "Rate (%)"]
                st.dataframe(b_stats.sort_values("Rate (%)", ascending=False), use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("#### 👥 ဝန်ထမ်းတစ်ဦးစီ Performance")
            if "assigned_to_email" in df_all.columns:
                u_stats = df_all.groupby("assigned_to_email").agg(
                    Total=("id", "count"),
                    Completed=("status", lambda x: int((x == "Completed").sum())),
                    In_Progress=("status", lambda x: int((x == "In Progress").sum())),
                    Pending=("status", lambda x: int((x == "Pending").sum())),
                    Cancelled=("status", lambda x: int((x == "Cancelled").sum())),
                ).reset_index()
                for _c in ["Total", "Completed", "In_Progress", "Pending", "Cancelled"]:
                    u_stats[_c] = u_stats[_c].astype(int)
                u_stats["Rate (%)"] = (u_stats["Completed"] / u_stats["Total"] * 100).round(1)
                overdue_per = df_all[df_all.apply(is_overdue, axis=1)].groupby("assigned_to_email").size().reset_index(name="Overdue")
                u_stats = u_stats.merge(overdue_per, on="assigned_to_email", how="left")
                u_stats["Overdue"] = u_stats["Overdue"].fillna(0).astype(int)
                u_stats = u_stats.sort_values("Rate (%)", ascending=False)
                u_stats.columns = ["Email", "Total", "Completed ✅", "In Progress 🔵", "Pending 🟡", "Cancelled 🔴", "Rate (%)", "Overdue ⚠️"]
                st.dataframe(u_stats, use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("#### 🔥 Priority × Status Matrix")
            if "priority" in df_all.columns and "status" in df_all.columns:
                st.dataframe(pd.crosstab(df_all["priority"], df_all["status"]), use_container_width=True)

            st.divider()
            st.markdown("#### 📅 Tasks ဖန်တီးမှု Trend (နောက်ဆုံး ၃၀ ရက်)")
            if "created_at" in df_all.columns:
                try:
                    df_tr = df_all.copy()
                    df_tr["created_date"] = pd.to_datetime(df_tr["created_at"], utc=True).dt.tz_convert("Asia/Rangoon").dt.date
                    df_tr = df_tr[df_tr["created_date"] >= _today - timedelta(days=30)]
                    if not df_tr.empty:
                        st.line_chart(df_tr.groupby("created_date").size().reset_index(name="Tasks ဖန်တီးမှု").set_index("created_date"), use_container_width=True)
                    else:
                        st.info("နောက်ဆုံး ၃၀ ရက်အတွင်း Tasks မဖန်တီးရသေးပါ")
                except Exception:
                    st.info("Trend data မရနိုင်ပါ")

            st.divider()
            st.markdown("#### 📒 Daily Log Activity (နောက်ဆုံး ၁၄ ရက်)")
            logs_all = safe_get_all_logs(branch=None if _ana_branch == "အားလုံး" else _ana_branch)
            if not logs_all.empty and "log_date" in logs_all.columns:
                try:
                    logs_all["log_date"] = pd.to_datetime(logs_all["log_date"]).dt.date
                    logs_r = logs_all[logs_all["log_date"] >= _today - timedelta(days=14)]
                    if not logs_r.empty:
                        st.bar_chart(logs_r.groupby("log_date").size().reset_index(name="Logs တင်မှု").set_index("log_date"), color="#7F77DD", use_container_width=True)
                        top_log = logs_r.groupby("user_email").size().reset_index(name="Log Count").sort_values("Log Count", ascending=False).head(10).rename(columns={"user_email": "Email"})
                        st.dataframe(top_log, use_container_width=True, hide_index=True)
                    else:
                        st.info("နောက်ဆုံး ၁၄ ရက်အတွင်း Logs မရှိပါ")
                except Exception:
                    pass
            else:
                st.info("Daily Logs မရှိသေးပါ")

            st.divider()
            st.markdown("#### ⏰ Due Date နီးနေသော Tasks (နောက် ၇ ရက်)")
            if "due_date" in df_all.columns:
                def _upcoming(row) -> bool:
                    if not row.get("due_date") or row.get("status") in ("Completed", "Cancelled"):
                        return False
                    try:
                        return 0 <= (date.fromisoformat(str(row["due_date"])) - _today).days <= 7
                    except Exception:
                        return False
                upcoming = df_all[df_all.apply(_upcoming, axis=1)].copy()
                if not upcoming.empty:
                    upcoming = upcoming.sort_values("due_date")
                if upcoming.empty:
                    st.success("✅ ၇ ရက်အတွင်း Due Date ရှိသော Tasks မရှိပါ")
                else:
                    for _, row in upcoming.iterrows():
                        _due  = date.fromisoformat(str(row["due_date"]))
                        _days = (_due - _today).days
                        _icon = "🔴" if _days == 0 else ("🟠" if _days <= 2 else "🟡")
                        _txt  = "ယနေ့ပဲ!" if _days == 0 else f"{_days} ရက် ကျန်"
                        with st.container(border=True):
                            st.markdown(f"{_icon} **{row['title']}**")
                            _b = f" | 🏢 {row.get('branch','')}" if row.get('branch') else ""
                            st.caption(f"👤 {row['assigned_to_email']}{_b} | 📅 {_due} ({_txt}) | 🎯 {row.get('priority','N/A')}")

    # ── TAB 3: USER MANAGEMENT ───────────────────────────
    with tab_users:
        st.subheader("👥 Registered Users စီမံခန့်ခွဲမှု")

        if supabase_admin is None:
            st.error(f"⚠️ Admin client error: `{_admin_init_error}`")
        else:
            st.markdown("### ⏳ Approval စောင့်ဆိုင်းနေသော Users")
            try:
                _pending_res = supabase_admin.table("user_profiles").select("*").eq("is_approved", False).order("created_at", desc=False).execute()
                _pending = _pending_res.data or []
            except Exception as e:
                st.error(f"Pending users ဆွဲမရပါ: {e}")
                _pending = []

            if not _pending:
                st.success("✅ Approval စောင့်နေသော Users မရှိပါ။")
            else:
                st.warning(f"**{len(_pending)} ဦး** Approval စောင့်နေသည်။")
                _all_branches_list = get_all_branches()
                for _p in _pending:
                    with st.container(border=True):
                        pc1, pc2, pc3, pc4 = st.columns([4, 2, 1, 1])
                        pc1.markdown(f"**{_p['email']}**")
                        pc1.caption(f"📅 Registered: {str(_p.get('created_at', ''))[:10]}")
                        _sel_br = pc2.selectbox(
                            "ရုံးအခွဲ သတ်မှတ်",
                            ["— ရွေးချယ်ပါ —"] + _all_branches_list,
                            key=f"branch_sel_{_p['id']}"
                        )
                        if pc3.button("✅ Approve", key=f"approve_{_p['id']}"):
                            try:
                                _update = {
                                    "is_approved": True,
                                    "approved_by": ADMIN_EMAIL,
                                    "approved_at": date.today().isoformat(),
                                }
                                if _sel_br != "— ရွေးချယ်ပါ —":
                                    _update["branch"] = _sel_br
                                supabase_admin.table("user_profiles").update(_update).eq("id", _p["id"]).execute()
                                send_notification(_p["email"], "approved", "သင့် Account ကို Admin မှ Approve လုပ်ပြီးပါပြီ။ အကောင်းဆုံး ကြိုဆိုပါသည်! 🎉")
                                st.success(f"✅ **{_p['email']}** ကို Approve လုပ်ပြီးပါပြီ!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Approve မရပါ: {e}")
                        if pc4.button("❌ Reject", key=f"reject_{_p['id']}"):
                            try:
                                send_notification(_p["email"], "rejected", "သင့် Account လျှောက်ထားမှုကို Admin မှ ငြင်းဆိုပါသည်။ အသေးစိတ်အတွက် Admin ထံ ဆက်သွယ်ပါ။")
                                supabase_admin.auth.admin.delete_user(_p["id"])
                                st.success(f"❌ **{_p['email']}** ကို ပယ်ဖျက်ပြီးပါပြီ!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Reject မရပါ: {e}")

            st.divider()
            st.markdown("### ✅ Approved Users စီမံခန့်ခွဲမှု")

            try:
                _all_users = supabase_admin.auth.admin.list_users()
                _branch_admin_emails = get_all_branch_admin_emails()
                _users = [
                    u for u in _all_users
                    if u.email
                    and u.email.strip().lower() != ADMIN_EMAIL.strip().lower()
                    and u.email.strip().lower() not in _branch_admin_emails
                ]
            except Exception as e:
                st.error(f"Users ဆွဲမရပါ: {e}")
                _users = []

            try:
                _approved_ids_res = supabase_admin.table("user_profiles").select("id,branch").eq("is_approved", True).execute()
                _approved_map = {r["id"]: r.get("branch", "") for r in (_approved_ids_res.data or [])}
                _approved_ids = set(_approved_map.keys())
            except Exception:
                _approved_ids = set()
                _approved_map = {}

            _approved_users = [u for u in _users if str(u.id) in _approved_ids]

            _ub_list = ["အားလုံး"] + get_all_branches()
            _ub_sel = st.selectbox("🏢 ရုံးအခွဲ စစ်ထုတ်မည်", _ub_list, key="sa_user_branch")
            if _ub_sel != "အားလုံး":
                _approved_users = [u for u in _approved_users if _approved_map.get(str(u.id), "") == _ub_sel]

            if not _approved_users:
                st.info("Approved users မရှိသေးပါ။")
            else:
                _total  = len(_approved_users)
                _active = sum(1 for u in _approved_users if not getattr(u, "banned_until", None))
                _banned = _total - _active
                um1, um2, um3 = st.columns(3)
                um1.metric("👥 Approved Users", _total)
                um2.metric("🟢 Active",         _active)
                um3.metric("🔴 Disabled",       _banned)
                st.divider()

                for _u in _approved_users:
                    _email     = _u.email
                    _uid       = _u.id
                    _created   = str(_u.created_at)[:10] if _u.created_at else "—"
                    _is_banned = bool(getattr(_u, "banned_until", None))
                    _confirmed = bool(_u.email_confirmed_at)
                    _u_branch  = _approved_map.get(str(_uid), "—")

                    with st.container(border=True):
                        uc1, uc2, uc3, uc4, uc5, uc6 = st.columns([3, 1, 1, 1, 1, 1])
                        uc1.markdown(f"**{_email}**")
                        uc1.caption(f"📅 {_created} | 🏢 {_u_branch} | {'✅ Confirmed' if _confirmed else '⏳ Unconfirmed'}")
                        uc2.markdown("🔴 Disabled" if _is_banned else "🟢 Active")

                        _all_b_opts = get_all_branches()
                        _cur_b_idx = _all_b_opts.index(_u_branch) if _u_branch in _all_b_opts else 0
                        _new_branch = uc3.selectbox("Branch", _all_b_opts, index=_cur_b_idx, key=f"chbr_{_uid}", label_visibility="collapsed") if _all_b_opts else None
                        if _new_branch and uc3.button("🏢", key=f"savebr_{_uid}", help="Branch ပြောင်းမည်"):
                            try:
                                supabase_admin.table("user_profiles").update({"branch": _new_branch}).eq("id", str(_uid)).execute()
                                # User ရဲ့ tasks table ထဲက branch ကိုပါ အသစ်ပြောင်းလိုက်တဲ့ Branch အတိုင်း
                                # တပြိုင်နက် update လုပ်ပေးမယ် — အဟောင်း Branch မှာ ကျန်မနေအောင်
                                try:
                                    supabase_admin.table("tasks").update({"branch": _new_branch}).eq("assigned_to_email", _email.strip().lower()).execute()
                                except Exception as _te:
                                    st.warning(f"⚠️ User ရဲ့ Task များကို Branch ပြောင်းမရပါ (Profile ကတော့ ပြောင်းပြီးပါပြီ): {_te}")
                                st.success(f"Branch ပြောင်းပြီးပါပြီ! (Task များပါ လိုက်ပြောင်းပေးပါပြီ)")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Branch ပြောင်းမရပါ: {e}")

                        if _is_banned:
                            if uc4.button("🟢 Enable", key=f"enable_{_uid}"):
                                try:
                                    supabase_admin.auth.admin.update_user_by_id(_uid, {"ban_duration": "none"})
                                    send_notification(_email, "enabled", "သင့် Account ကို Admin မှ ပြန်လည် Enable လုပ်ပြီးပါပြီ။ ✅")
                                    st.success(f"{_email} ကို Enable လုပ်ပြီးပါပြီ!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Enable မရပါ: {e}")
                        else:
                            if uc4.button("🔴 Disable", key=f"disable_{_uid}"):
                                try:
                                    supabase_admin.auth.admin.update_user_by_id(_uid, {"ban_duration": "876600h"})
                                    send_notification(_email, "disabled", "သင့် Account ကို Admin မှ ယာယီ ပိတ်ထားသည်။ 🔴")
                                    st.success(f"{_email} ကို Disable လုပ်ပြီးပါပြီ!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Disable မရပါ: {e}")

                        if uc5.button("🔑", key=f"reset_{_uid}", help="Password Reset ပို့မည်"):
                            try:
                                supabase.auth.reset_password_email(_email)
                                st.success(f"**{_email}** သို့ Password reset link ပို့ပြီးပါပြီ!")
                            except Exception as e:
                                st.error(f"Reset မရပါ: {e}")

                        if uc6.button("🗑️", key=f"del_user_{_uid}", help="User ဖျက်မည်"):
                            if st.session_state.get(f"confirm_del_{_uid}"):
                                try:
                                    send_notification(_email, "deleted", "သင့် Account ကို Admin မှ ဖျက်သိမ်းပြီးပါပြီ။")
                                    supabase_admin.auth.admin.delete_user(_uid)
                                    st.success(f"{_email} ကို ဖျက်ပြီးပါပြီ!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"ဖျက်မရပါ: {e}")
                            else:
                                st.session_state[f"confirm_del_{_uid}"] = True
                                st.warning(f"⚠️ **{_email}** ကို သေချာ ဖျက်မည်လား? 🗑️ ကို ထပ်နှိပ်ပါ။")

    # ── TAB 4: BRANCH ADMIN MANAGEMENT ──────────────────
    with tab_branch_admins:
        st.subheader("🏢 Branch Admin စီမံခန့်ခွဲမှု")
        st.caption("ရုံးအခွဲ Admin များ ဖန်တီး၊ ဖျက်၊ တည်းဖြတ်နိုင်သည်")

        if supabase_admin is None:
            st.error(f"⚠️ Admin client error: `{_admin_init_error}`")
        else:
            with st.expander("➕ Branch Admin အသစ် ဖန်တီးရန်", expanded=False):
                with st.form("create_branch_admin_form", clear_on_submit=True):
                    ba_col1, ba_col2 = st.columns(2)
                    ba_email    = ba_col1.text_input("Email လိပ်စာ *")
                    ba_pw       = ba_col2.text_input("Password (အနည်းဆုံး 8 လုံး) *", type="password")
                    ba_col3, ba_col4 = st.columns(2)
                    ba_branch   = ba_col3.text_input("Branch Code (ဥပမာ: HQ, BRN1) *")
                    ba_bname    = ba_col4.text_input("Branch ပြည့်စုံသောနာမည် (ဥပမာ:  Branch 1)")
                    ba_btn      = st.form_submit_button("🏢 Branch Admin ဖန်တီးမည်", use_container_width=True)

                if ba_btn:
                    ba_email  = ba_email.strip().lower()
                    ba_branch = ba_branch.strip().upper()
                    if not ba_email or not ba_pw or not ba_branch:
                        st.error("Email, Password နှင့် Branch Code ထည့်ပါ။")
                    elif not is_valid_email(ba_email):
                        st.error("Email format မမှန်မမှန် စစ်ပါ။")
                    elif len(ba_pw) < 8:
                        st.error("Password အနည်းဆုံး 8 လုံး ရှိရမည်။")
                    elif ba_email == ADMIN_EMAIL.strip().lower():
                        st.error("Super Admin email သုံး၍ မရပါ။")
                    elif supabase_admin is None:
                        st.error(f"⚠️ Admin client error: `{_admin_init_error}`")
                    else:
                        with st.spinner("Branch Admin ဖန်တီးနေသည်..."):
                            try:
                                _existing = supabase_admin.auth.admin.list_users()
                                _exist_user = next(
                                    (u for u in _existing if u.email and u.email.strip().lower() == ba_email),
                                    None
                                )

                                if _exist_user:
                                    _uid2 = _exist_user.id
                                else:
                                    _new_user = supabase_admin.auth.admin.create_user({
                                        "email": ba_email,
                                        "password": ba_pw,
                                        "email_confirm": True,
                                    })
                                    _uid2 = _new_user.user.id

                                supabase_admin.table("user_profiles").upsert({
                                    "id": str(_uid2),
                                    "email": ba_email,
                                    "is_approved": True,
                                    "branch": ba_branch,
                                    "approved_by": ADMIN_EMAIL,
                                    "approved_at": date.today().isoformat(),
                                }).execute()

                                supabase_admin.table("admin_profiles").upsert({
                                    "email": ba_email,
                                    "branch": ba_branch,
                                    "branch_name": ba_bname.strip() or ba_branch,
                                    "created_by": ADMIN_EMAIL,
                                }).execute()

                                st.success(f"✅ **{ba_email}** ကို Branch Admin ({ba_branch}) အဖြစ် ဖန်တီးပြီးပါပြီ!")
                                st.rerun()
                            except Exception as e:
                                err_msg = str(e)
                                if "already been registered" in err_msg or "already exists" in err_msg.lower():
                                    st.error(f"**{ba_email}** သည် ရှိပြီးသော Account ဖြစ်သည်။ Email အသစ်သုံးပါ။")
                                else:
                                    st.error(f"Branch Admin ဖန်တီးမရပါ: {e}")

            st.divider()
            st.markdown("### 🏢 လက်ရှိ Branch Admins")

            try:
                _ba_res = supabase_admin.table("admin_profiles").select("*").order("created_at", desc=False).execute()
                _ba_list = _ba_res.data or []
            except Exception as e:
                st.error(f"Branch Admin list ဆွဲမရပါ: {e}")
                _ba_list = []

            if not _ba_list:
                st.info("Branch Admin မရှိသေးပါ — အပေါ်မှ ဖန်တီးပါ။")
            else:
                for _ba in _ba_list:
                    with st.container(border=True):
                        bac1, bac2, bac3 = st.columns([5, 1, 1])
                        bac1.markdown(f"**{_ba['email']}**")
                        bac1.caption(f"🏢 Branch: `{_ba.get('branch','')}` — {_ba.get('branch_name','')} | Created: {str(_ba.get('created_at',''))[:10]}")

                        if bac2.button("🔑", key=f"ba_reset_{_ba['email']}", help="Password Reset ပို့မည်"):
                            try:
                                supabase.auth.reset_password_email(_ba["email"])
                                st.success(f"Password reset link ပို့ပြီးပါပြီ!")
                            except Exception as e:
                                st.error(f"Reset မရပါ: {e}")

                        if bac3.button("🗑️", key=f"ba_del_{_ba['email']}", help="Branch Admin ဖျက်မည်"):
                            if st.session_state.get(f"ba_confirm_{_ba['email']}"):
                                try:
                                    supabase_admin.table("admin_profiles").delete().eq("email", _ba["email"]).execute()
                                    _ba_users = supabase_admin.auth.admin.list_users()
                                    _ba_uid = next((u.id for u in _ba_users if u.email and u.email.strip().lower() == _ba["email"]), None)
                                    if _ba_uid:
                                        supabase_admin.auth.admin.delete_user(_ba_uid)
                                    st.success(f"Branch Admin **{_ba['email']}** ကို ဖျက်ပြီးပါပြီ!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"ဖျက်မရပါ: {e}")
                            else:
                                st.session_state[f"ba_confirm_{_ba['email']}"] = True
                                st.warning(f"⚠️ **{_ba['email']}** ကို ဖျက်မည်လား? 🗑️ ကို ထပ်နှိပ်ပါ။")

# ══════════════════════════════════════════════════════════
# 5A-ii. BRANCH ADMIN DASHBOARD
# ══════════════════════════════════════════════════════════
elif is_branch_admin:
    my_branch      = current_user.get("branch", "")
    my_branch_name = current_user.get("branch_name", my_branch)

    st.title(f"🏢 Branch Admin — {my_branch_name}")
    st.caption(f"ရုံးအခွဲ **{my_branch_name}** ၏ ဝန်ထမ်းများနှင့် Tasks သာ စီမံနိုင်သည်")

    tab_tasks, tab_analytics, tab_users = st.tabs([
        "📋 Task စီမံခန့်ခွဲမှု",
        "📊 Analytics Dashboard",
        "👥 User စီမံခန့်ခွဲမှု",
    ])

    with tab_tasks:
        with st.expander("➕ ဝန်ထမ်းကို Task ချပေးရန်", expanded=False):
            _reg_users = get_registered_users(branch=my_branch)
            if not _reg_users:
                if supabase_admin is None:
                    st.error(f"⚠️ Admin client error: `{_admin_init_error}`")
                else:
                    st.warning("⚠️ ဤ branch တွင် registered users မရှိပါ။ Email ကို ကိုယ်တိုင် ရိုက်ထည့်ပါ။")

            with st.form("ba_assign_form", clear_on_submit=True):
                col1, col2 = st.columns(2)
                t_title = col1.text_input("Task ခေါင်းစဉ် *")
                if _reg_users:
                    t_user = col2.selectbox("ဝန်ထမ်း ရွေးချယ်ပါ *", options=["— ရွေးချယ်ပါ —"] + _reg_users)
                else:
                    t_user = col2.text_input("ဝန်ထမ်း Email *")
                t_desc   = st.text_area("အသေးစိတ် ဖော်ပြချက်", height=80)
                col3, col4, col5 = st.columns(3)
                t_prio   = col3.selectbox("အရေးပါမှု", ["Low", "Medium", "High"])
                t_due    = col4.date_input("ပြီးဆုံးရမည့် ရက်", value=None)
                t_stat   = col5.selectbox("ကနဦး Status", ["Pending", "In Progress"])
                send_btn = st.form_submit_button("📤 Task ချပေးမည်", use_container_width=True)

            if send_btn:
                t_user = (t_user or "").strip().lower()
                if not t_title.strip():
                    st.error("Task ခေါင်းစဉ် ထည့်ပါ။")
                elif not t_user or t_user == "— ရွေးချယ်ပါ —":
                    st.error("ဝန်ထမ်း ရွေးချယ်ပါ သို့မဟုတ် Email ထည့်ပါ။")
                elif not is_valid_email(t_user):
                    st.error("ဝန်ထမ်း email မမှန်မမှန် စစ်ပါ။")
                elif t_user == ADMIN_EMAIL.strip().lower():
                    st.error("Admin account သို့ Task ချ၍ မရပါ။")
                else:
                    payload = {
                        "title":             t_title.strip(),
                        "description":       t_desc.strip() or None,
                        "assigned_to_email": t_user,
                        "created_by_email":  current_user["email"],
                        "priority":          t_prio,
                        "status":            t_stat,
                        "due_date":          str(t_due) if t_due else None,
                        "branch":            my_branch,
                    }
                    try:
                        _db = supabase_admin if supabase_admin else supabase
                        _db.table("tasks").insert(payload).execute()
                        st.success(f"✅ **{t_user}** သို့ Task ချပေးပြီးပါပြီ!")
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Task ဖန်တီးမရပါ: {exc.message}")

        # ════════════════════════════════════════════════════
        # NEW: Branch Admin ကိုယ်တိုင် ကိုယ်ပိုင် Task ထည့်ရန်
        # ════════════════════════════════════════════════════
        with st.expander("➕ ကိုယ်ပိုင် Task ထည့်ရန် (Branch Admin)", expanded=False):
            with st.form("ba_own_task_form", clear_on_submit=True):
                ba_own_title = st.text_input("Task ခေါင်းစဉ် *", key="ba_own_title")
                ba_own_desc  = st.text_area("အသေးစိတ် ဖော်ပြချက်", height=70, key="ba_own_desc")
                ba_col_a, ba_col_b = st.columns(2)
                ba_own_prio = ba_col_a.selectbox("အရေးပါမှု", ["Low", "Medium", "High"], key="ba_own_prio")
                ba_own_due  = ba_col_b.date_input("ပြီးဆုံးရမည့် ရက်", value=None, key="ba_own_due")
                ba_own_btn  = st.form_submit_button("Task ထည့်မည်", use_container_width=True)

            if ba_own_btn:
                if not ba_own_title.strip():
                    st.error("Task ခေါင်းစဉ် ထည့်ပါ။")
                else:
                    try:
                        _db = supabase_admin if supabase_admin else supabase
                        _db.table("tasks").insert({
                            "title":             ba_own_title.strip(),
                            "description":       ba_own_desc.strip() or None,
                            "assigned_to_email": current_user["email"].strip().lower(),
                            "created_by_email":  current_user["email"],
                            "priority":          ba_own_prio,
                            "status":            "Pending",
                            "due_date":          str(ba_own_due) if ba_own_due else None,
                            "branch":            my_branch,
                        }).execute()
                        st.success("✅ ကိုယ်ပိုင် Task ထည့်ပြီးပါပြီ!")
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Task ထည့်မရပါ: {exc.message}")

        # ════════════════════════════════════════════════════
        # NEW: Super Admin မှ ဤ Branch Admin ကို တိုက်ရိုက်ချပေးထားသော
        # Task များအတွက် Daily Log ရေးရန် (User Dashboard ပုံစံအတိုင်း)
        # ════════════════════════════════════════════════════
        st.divider()
        st.subheader("📒 Super Admin မှ ကျွန်ုပ်ကို ချပေးသော Task များ — Daily Log")

        my_admin_tasks = safe_get_tasks(filter_email=current_user["email"])
        if not my_admin_tasks.empty and "created_by_email" in my_admin_tasks.columns:
            my_admin_tasks = my_admin_tasks[
                my_admin_tasks["created_by_email"].str.strip().str.lower() == ADMIN_EMAIL.strip().lower()
            ]

        if my_admin_tasks.empty:
            st.info("Super Admin မှ ချပေးသော Task မရှိသေးပါ။")
        else:
            ba_status_filter = st.selectbox(
                "စစ်ထုတ်မည်",
                ["အားလုံး", "Pending", "In Progress", "Completed", "Cancelled"],
                key="ba_recv_status_filter",
            )
            ba_filtered = (
                my_admin_tasks if ba_status_filter == "အားလုံး"
                else my_admin_tasks[my_admin_tasks.status == ba_status_filter]
            )

            for _, row in ba_filtered.iterrows():
                with st.container(border=True):
                    rc1, rc2 = st.columns([5, 1])
                    rc1.markdown(f"**{row['title']}**")
                    r_meta = [f"မှ: {row['created_by_email']}", f"Priority: {row.get('priority','N/A')}"]
                    if row.get("due_date"):
                        r_due     = date.fromisoformat(str(row["due_date"]))
                        r_overdue = r_due < date.today() and row["status"] not in ("Completed", "Cancelled")
                        r_meta.append(f"{'⚠️ နောက်ကျ' if r_overdue else '📅'} ရက်: {r_due}")
                    rc1.caption(" | ".join(r_meta))
                    if row.get("description"):
                        rc1.caption(f"📝 {row['description']}")
                    rc2.markdown(status_badge(row["status"]))

                    # Super Admin ချပေးသော Task ဖြစ်၍ Branch Admin တွင် Status ပြင်ခွင့် /
                    # ဖျက်ခွင့် မရှိပါ — Daily Log ဖြည့်ခွင့်သာ ရှိသည်
                    # (Branch အောက်ရှိ User Task များကိုသာ အပြည့်အဝ Control ပိုင်ခွင့်ရှိသည်)

                    ba_logs_df = safe_get_logs(row["id"])
                    with st.expander(f"📒 Daily Log ရေးမည် / ကြည့်မည် ({len(ba_logs_df)} ခု)", expanded=False):
                        with st.form(f"ba_recv_log_form_{row['id']}", clear_on_submit=True):
                            blc1, blc2 = st.columns([1, 3])
                            ba_log_date = blc1.date_input("ရက်စွဲ", value=date.today(), key=f"ba_recv_ld_{row['id']}")
                            blc1.caption(f"🕐 {now_mmt().strftime('%H:%M')} (MMT)")
                            ba_log_comment = blc2.text_area("ယနေ့ ဘာလုပ်ခဲ့သည်?", height=70, key=f"ba_recv_lc_{row['id']}")
                            ba_log_file = st.file_uploader(
                                "📎 ဖိုင် တင်ရန် (ချိတ်ဆက်မှု ဖိုင်) — ချန်လှပ်ထားလည်း ရသည်",
                                type=["pdf", "png", "jpg", "jpeg", "docx", "xlsx", "txt", "zip"],
                                key=f"ba_recv_lf_{row['id']}",
                            )
                            ba_log_btn = st.form_submit_button("📝 Log ထည့်မည်", use_container_width=True)

                        if ba_log_btn:
                            if not ba_log_comment.strip():
                                st.error("Comment ရေးပါ။")
                            else:
                                try:
                                    _db = supabase_admin if supabase_admin else supabase
                                    ba_attachment_url = None
                                    if ba_log_file is not None:
                                        try:
                                            import uuid as _uuid
                                            _ext       = ba_log_file.name.rsplit(".", 1)[-1]
                                            _safe_name = f"{row['id']}/{ba_log_date}_{_uuid.uuid4().hex[:8]}.{_ext}"
                                            _db.storage.from_("task-logs").upload(
                                                _safe_name, ba_log_file.read(),
                                                {"content-type": ba_log_file.type or "application/octet-stream"}
                                            )
                                            ba_attachment_url = _db.storage.from_("task-logs").get_public_url(_safe_name)
                                        except Exception as _fe:
                                            st.warning(f"⚠️ ဖိုင် တင်မရပါ (Log သာ သိမ်းမည်): {_fe}")

                                    _db.table("task_logs").insert({
                                        "task_id":        row["id"],
                                        "user_email":     current_user["email"].strip().lower(),
                                        "log_date":       str(ba_log_date),
                                        "log_time":       now_mmt().strftime("%H:%M:%S"),
                                        "comment":        ba_log_comment.strip(),
                                        "attachment_url": ba_attachment_url,
                                    }).execute()

                                    if ba_logs_df.empty and row["status"] == "Pending":
                                        _db.table("tasks").update({"status": "In Progress"}).eq("id", row["id"]).execute()
                                        st.info("🔵 Task status သည် **In Progress** သို့ အလိုအလျောက် ပြောင်းသွားပြီ!")

                                    st.success("Log ထည့်ပြီးပါပြီ!" + (" 📎 ဖိုင်လည်း တင်ပြီးပါပြီ!" if ba_attachment_url else ""))
                                    st.rerun()
                                except APIError as exc:
                                    st.error(f"Log ထည့်မရပါ: {exc.message}")

                        if ba_logs_df.empty:
                            st.info("Log မရှိသေးပါ — ပထမဆုံး log ရေးပါ။")
                        else:
                            st.markdown("---")
                            st.markdown("**မှတ်တမ်းများ:**")
                            for _, lg in ba_logs_df.iterrows():
                                _log_time = lg.get("log_time", "")
                                _time_str = f" {_log_time} (MMT)" if _log_time else ""
                                st.markdown(f"📅 **{lg['log_date']}{_time_str}**")
                                st.markdown(f"> {lg['comment']}")
                                if lg.get("attachment_url"):
                                    st.markdown(f"📎 **ဖိုင်:** [ဒေါင်းလုတ် / ကြည့်ရှုရန်]({lg['attachment_url']})", unsafe_allow_html=False)
                                st.divider()

        st.divider()
        st.subheader(f"📊 {my_branch_name} Team Progress")
        df = safe_get_tasks(branch=my_branch)

        if df.empty:
            st.info("Database တွင် Task များ မရှိသေးပါ။")
        else:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("စုစုပေါင်း Task",  len(df))
            m2.metric("🟡 Pending",        len(df[df.status == "Pending"]))
            m3.metric("🔵 In Progress",    len(df[df.status == "In Progress"]))
            m4.metric("🟢 Completed",      len(df[df.status == "Completed"]))

            st.divider()
            filter_email = st.text_input("🔍 ဝန်ထမ်း Email ဖြင့် ရှာဖွေမည်")
            view_df = (
                df[df["assigned_to_email"].str.contains(filter_email.strip(), case=False, na=False)]
                if filter_email.strip() else df
            )
            display_cols = [c for c in ["id", "title", "description", "assigned_to_email", "priority", "status", "due_date", "created_at"] if c in view_df.columns]
            st.dataframe(view_df[display_cols], use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("Task တစ်ခုချင်စီ စီမံခန့်ခွဲမည်")

            for _, row in view_df.iterrows():
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
                    c1.markdown(f"**{row['title']}**")
                    c1.caption(f"👤 {row['assigned_to_email']} | 🎯 {row.get('priority','N/A')} | 📅 {row.get('due_date','—')}")
                    if row.get("description"):
                        c1.caption(f"📝 {row['description']}")
                    c2.markdown(status_badge(row["status"]))

                    status_options = ["Pending", "In Progress", "Completed", "Cancelled"]
                    cur_idx = status_options.index(row["status"]) if row["status"] in status_options else 0
                    new_status = c3.selectbox("Status", status_options, index=cur_idx, key=f"ba_sel_{row['id']}", label_visibility="collapsed")
                    if c3.button("💾", key=f"ba_upd_{row['id']}", help="Status ပြောင်းမည်"):
                        try:
                            (supabase_admin if supabase_admin else supabase).table("tasks").update({"status": new_status}).eq("id", row["id"]).execute()
                            st.rerun()
                        except APIError as exc:
                            st.error(f"Update မရပါ: {exc.message}")

                    if c4.button("🗑️", key=f"ba_del_{row['id']}", help="Task ဖျက်မည်"):
                        try:
                            (supabase_admin if supabase_admin else supabase).table("tasks").delete().eq("id", row["id"]).execute()
                            st.rerun()
                        except APIError as exc:
                            st.error(f"ဖျက်မရပါ: {exc.message}")

                    logs_df = safe_get_logs(row["id"])
                    if not logs_df.empty:
                        with st.expander(f"📒 Daily Logs ({len(logs_df)} ခု)", expanded=False):
                            for _, lg in logs_df.iterrows():
                                _log_time = lg.get("log_time", "")
                                _time_str = f" {_log_time} (MMT)" if _log_time else ""
                                st.markdown(f"**{lg['log_date']}{_time_str}** — {lg['user_email']}\n\n> {lg['comment']}")
                                if lg.get("attachment_url"):
                                    st.markdown(f"📎 **ဖိုင်:** [ဒေါင်းလုတ် / ကြည့်ရှုရန်]({lg['attachment_url']})", unsafe_allow_html=False)
                                st.divider()
                    else:
                        st.caption("📒 Daily Log မရှိသေးပါ")

    with tab_analytics:
        st.subheader(f"📊 {my_branch_name} Analytics")
        df_all = safe_get_tasks(branch=my_branch)

        if df_all.empty:
            st.info("📭 Task Data မရှိသေးပါ")
        else:
            _today = date.today()

            def is_overdue(row) -> bool:
                if not row.get("due_date") or row.get("status") in ("Completed", "Cancelled"):
                    return False
                try:
                    return date.fromisoformat(str(row["due_date"])) < _today
                except Exception:
                    return False

            total     = len(df_all)
            completed = int((df_all["status"] == "Completed").sum())
            in_prog   = int((df_all["status"] == "In Progress").sum())
            pending   = int((df_all["status"] == "Pending").sum())
            cancelled = int((df_all["status"] == "Cancelled").sum())
            overdue   = int(df_all.apply(is_overdue, axis=1).sum())
            rate      = round(completed / total * 100, 1) if total > 0 else 0.0

            mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
            mc1.metric("📋 Tasks",       total)
            mc2.metric("🟢 Completed",   completed, f"{rate}%")
            mc3.metric("🔵 In Progress", in_prog)
            mc4.metric("🟡 Pending",     pending)
            mc5.metric("🔴 Cancelled",   cancelled)
            mc6.metric("⚠️ Overdue",     overdue)

            st.divider()
            ch1, ch2 = st.columns(2)
            with ch1:
                st.markdown("#### Task Status ခွဲခြမ်း")
                s_df = df_all["status"].value_counts().reset_index()
                s_df.columns = ["Status", "Count"]
                _so = ["Pending", "In Progress", "Completed", "Cancelled"]
                s_df["Status"] = pd.Categorical(s_df["Status"], categories=_so, ordered=True)
                st.bar_chart(s_df.sort_values("Status").set_index("Status"), color="#1D9E75", use_container_width=True)
            with ch2:
                st.markdown("#### Priority အလိုက် Tasks")
                if "priority" in df_all.columns:
                    p_df = df_all["priority"].value_counts().reset_index()
                    p_df.columns = ["Priority", "Count"]
                    _po = ["High", "Medium", "Low"]
                    p_df["Priority"] = pd.Categorical(p_df["Priority"], categories=_po, ordered=True)
                    st.bar_chart(p_df.sort_values("Priority").set_index("Priority"), color="#378ADD", use_container_width=True)

            st.divider()
            st.markdown("#### 👥 ဝန်ထမ်းတစ်ဦးစီ Performance")
            if "assigned_to_email" in df_all.columns:
                u_stats = df_all.groupby("assigned_to_email").agg(
                    Total=("id", "count"),
                    Completed=("status", lambda x: int((x == "Completed").sum())),
                    In_Progress=("status", lambda x: int((x == "In Progress").sum())),
                    Pending=("status", lambda x: int((x == "Pending").sum())),
                    Cancelled=("status", lambda x: int((x == "Cancelled").sum())),
                ).reset_index()
                for _c in ["Total", "Completed", "In_Progress", "Pending", "Cancelled"]:
                    u_stats[_c] = u_stats[_c].astype(int)
                u_stats["Rate (%)"] = (u_stats["Completed"] / u_stats["Total"] * 100).round(1)
                overdue_per = df_all[df_all.apply(is_overdue, axis=1)].groupby("assigned_to_email").size().reset_index(name="Overdue")
                u_stats = u_stats.merge(overdue_per, on="assigned_to_email", how="left")
                u_stats["Overdue"] = u_stats["Overdue"].fillna(0).astype(int)
                u_stats = u_stats.sort_values("Rate (%)", ascending=False)
                u_stats.columns = ["Email", "Total", "Completed ✅", "In Progress 🔵", "Pending 🟡", "Cancelled 🔴", "Rate (%)", "Overdue ⚠️"]
                st.dataframe(u_stats, use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("#### 📅 Tasks ဖန်တီးမှု Trend (နောက်ဆုံး ၃၀ ရက်)")
            if "created_at" in df_all.columns:
                try:
                    df_tr = df_all.copy()
                    df_tr["created_date"] = pd.to_datetime(df_tr["created_at"], utc=True).dt.tz_convert("Asia/Rangoon").dt.date
                    df_tr = df_tr[df_tr["created_date"] >= _today - timedelta(days=30)]
                    if not df_tr.empty:
                        st.line_chart(df_tr.groupby("created_date").size().reset_index(name="Tasks ဖန်တီးမှု").set_index("created_date"), use_container_width=True)
                    else:
                        st.info("နောက်ဆုံး ၃၀ ရက်အတွင်း Tasks မဖန်တီးရသေးပါ")
                except Exception:
                    st.info("Trend data မရနိုင်ပါ")

            st.divider()
            st.markdown("#### ⏰ Due Date နီးနေသော Tasks (နောက် ၇ ရက်)")
            if "due_date" in df_all.columns:
                def _upcoming(row) -> bool:
                    if not row.get("due_date") or row.get("status") in ("Completed", "Cancelled"):
                        return False
                    try:
                        return 0 <= (date.fromisoformat(str(row["due_date"])) - _today).days <= 7
                    except Exception:
                        return False
                upcoming = df_all[df_all.apply(_upcoming, axis=1)].copy()
                if not upcoming.empty:
                    upcoming = upcoming.sort_values("due_date")
                if upcoming.empty:
                    st.success("✅ ၇ ရက်အတွင်း Due Date ရှိသော Tasks မရှိပါ")
                else:
                    for _, row in upcoming.iterrows():
                        _due  = date.fromisoformat(str(row["due_date"]))
                        _days = (_due - _today).days
                        _icon = "🔴" if _days == 0 else ("🟠" if _days <= 2 else "🟡")
                        _txt  = "ယနေ့ပဲ!" if _days == 0 else f"{_days} ရက် ကျန်"
                        with st.container(border=True):
                            st.markdown(f"{_icon} **{row['title']}**")
                            st.caption(f"👤 {row['assigned_to_email']} | 📅 {_due} ({_txt}) | 🎯 {row.get('priority','N/A')}")

    with tab_users:
        st.subheader(f"👥 {my_branch_name} — User စီမံခန့်ခွဲမှု")

        if supabase_admin is None:
            st.error(f"⚠️ Admin client error: `{_admin_init_error}`")
        else:
            st.markdown("### ⏳ Approval စောင့်ဆိုင်းနေသော Users")
            try:
                _pending_res = supabase_admin.table("user_profiles").select("*").eq("is_approved", False).execute()
                _all_pending = _pending_res.data or []
                _pending = [p for p in _all_pending if not p.get("branch") or p.get("branch") == my_branch]
            except Exception as e:
                st.error(f"Pending users ဆွဲမရပါ: {e}")
                _pending = []

            if not _pending:
                st.success("✅ Approval စောင့်နေသော Users မရှိပါ။")
            else:
                st.warning(f"**{len(_pending)} ဦး** Approval စောင့်နေသည်။")
                for _p in _pending:
                    with st.container(border=True):
                        pc1, pc2, pc3 = st.columns([5, 1, 1])
                        pc1.markdown(f"**{_p['email']}**")
                        pc1.caption(f"📅 Registered: {str(_p.get('created_at', ''))[:10]}")
                        if pc2.button("✅ Approve", key=f"ba_approve_{_p['id']}"):
                            try:
                                supabase_admin.table("user_profiles").update({
                                    "is_approved": True,
                                    "branch": my_branch,
                                    "approved_by": current_user["email"],
                                    "approved_at": date.today().isoformat(),
                                }).eq("id", _p["id"]).execute()
                                send_notification(_p["email"], "approved", f"သင့် Account ကို {my_branch_name} Admin မှ Approve လုပ်ပြီးပါပြီ။ 🎉")
                                st.success(f"✅ **{_p['email']}** ကို Approve လုပ်ပြီးပါပြီ!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Approve မရပါ: {e}")
                        if pc3.button("❌ Reject", key=f"ba_reject_{_p['id']}"):
                            try:
                                send_notification(_p["email"], "rejected", "သင့် Account လျှောက်ထားမှုကို Admin မှ ငြင်းဆိုပါသည်။")
                                supabase_admin.auth.admin.delete_user(_p["id"])
                                st.success(f"❌ **{_p['email']}** ကို ပယ်ဖျက်ပြီးပါပြီ!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Reject မရပါ: {e}")

            st.divider()
            st.markdown(f"### ✅ {my_branch_name} Approved Users")

            try:
                _all_users = supabase_admin.auth.admin.list_users()
                _branch_admin_emails = get_all_branch_admin_emails()
                _users = [
                    u for u in _all_users
                    if u.email
                    and u.email.strip().lower() != ADMIN_EMAIL.strip().lower()
                    and u.email.strip().lower() not in _branch_admin_emails
                ]
            except Exception as e:
                st.error(f"Users ဆွဲမရပါ: {e}")
                _users = []

            try:
                _approved_ids_res = supabase_admin.table("user_profiles").select("id,branch").eq("is_approved", True).eq("branch", my_branch).execute()
                _approved_ids = {r["id"] for r in (_approved_ids_res.data or [])}
            except Exception:
                _approved_ids = set()

            _approved_users = [u for u in _users if str(u.id) in _approved_ids]

            if not _approved_users:
                st.info("ဤ branch တွင် Approved users မရှိသေးပါ။")
            else:
                _total  = len(_approved_users)
                _active = sum(1 for u in _approved_users if not getattr(u, "banned_until", None))
                _banned = _total - _active
                um1, um2, um3 = st.columns(3)
                um1.metric("👥 Approved Users", _total)
                um2.metric("🟢 Active",         _active)
                um3.metric("🔴 Disabled",       _banned)
                st.divider()

                for _u in _approved_users:
                    _email     = _u.email
                    _uid       = _u.id
                    _created   = str(_u.created_at)[:10] if _u.created_at else "—"
                    _is_banned = bool(getattr(_u, "banned_until", None))
                    _confirmed = bool(_u.email_confirmed_at)

                    with st.container(border=True):
                        uc1, uc2, uc3, uc4, uc5 = st.columns([4, 1, 1, 1, 1])
                        uc1.markdown(f"**{_email}**")
                        uc1.caption(f"📅 {_created} | {'✅ Confirmed' if _confirmed else '⏳ Unconfirmed'}")
                        uc2.markdown("🔴 Disabled" if _is_banned else "🟢 Active")

                        if _is_banned:
                            if uc3.button("🟢 Enable", key=f"ba_enable_{_uid}"):
                                try:
                                    supabase_admin.auth.admin.update_user_by_id(_uid, {"ban_duration": "none"})
                                    send_notification(_email, "enabled", "သင့် Account ကို Admin မှ ပြန်လည် Enable လုပ်ပြီးပါပြီ။ ✅")
                                    st.success(f"{_email} ကို Enable လုပ်ပြီးပါပြီ!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Enable မရပါ: {e}")
                        else:
                            if uc3.button("🔴 Disable", key=f"ba_disable_{_uid}"):
                                try:
                                    supabase_admin.auth.admin.update_user_by_id(_uid, {"ban_duration": "876600h"})
                                    send_notification(_email, "disabled", "သင့် Account ကို Admin မှ ယာယီ ပိတ်ထားသည်။ 🔴")
                                    st.success(f"{_email} ကို Disable လုပ်ပြီးပါပြီ!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Disable မရပါ: {e}")

                        if uc4.button("🔑", key=f"ba_reset_{_uid}", help="Password Reset ပို့မည်"):
                            try:
                                supabase.auth.reset_password_email(_email)
                                st.success(f"**{_email}** သို့ Password reset link ပို့ပြီးပါပြီ!")
                            except Exception as e:
                                st.error(f"Reset မရပါ: {e}")

                        if uc5.button("🗑️", key=f"ba_del_user_{_uid}", help="User ဖျက်မည်"):
                            if st.session_state.get(f"ba_confirm_del_{_uid}"):
                                try:
                                    send_notification(_email, "deleted", "သင့် Account ကို Admin မှ ဖျက်သိမ်းပြီးပါပြီ။")
                                    supabase_admin.auth.admin.delete_user(_uid)
                                    st.success(f"{_email} ကို ဖျက်ပြီးပါပြီ!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"ဖျက်မရပါ: {e}")
                            else:
                                st.session_state[f"ba_confirm_del_{_uid}"] = True
                                st.warning(f"⚠️ **{_email}** ကို ဖျက်မည်လား? 🗑️ ကို ထပ်နှိပ်ပါ။")

# ══════════════════════════════════════════════════════════
# 5B. USER DASHBOARD
# ══════════════════════════════════════════════════════════
else:
    st.title("📝 ကျွန်ုပ်၏ Task Dashboard")
    my_email = current_user["email"].strip().lower()

    with st.expander("📋 Task လုပ်ဆောင်နည်း အဆင့်ဆင့်", expanded=False):
        st.markdown("""
**အဆင့် ၁ — Task ရယူပါ / ဖန်တီးပါ** 🆕
- Admin မှ ချပေးသော Task (သို့) ကိုယ်တိုင် **"➕ ကိုယ်ပိုင် Task ထည့်ရန်"** ကို နှိပ်၍ ဖန်တီးပါ

**အဆင့် ၂ — Task စတင်ပါ** 🔵
- Task ကို တွေ့ရှိပြီး **"စတင်"** ကို ရွေးချယ်ပါ — Status 🟡 Pending မှ 🔵 In Progress သို့ ပြောင်းမည်

**အဆင့် ၃ — Task ပြီးစီးကြောင်း မှတ်တမ်းတင်ပါ** ✅
- **"ပြီးပြီ ✅"** ကို ရွေးချယ်ပါ — Status 🟢 Completed သို့ ပြောင်းမည်
        """)
        st.info("💡 In Progress ဖြစ်နေသော Task များကို ဦးစားပေး ဆောင်ရွက်ပါ။")

    with st.expander("➕ ကိုယ်ပိုင် Task ထည့်ရန်", expanded=False):
        with st.form("user_task_form", clear_on_submit=True):
            ut_title = st.text_input("Task ခေါင်းစဉ် *")
            ut_desc  = st.text_area("အသေးစိတ် ဖော်ပြချက်", height=70)
            col_a, col_b = st.columns(2)
            ut_prio  = col_a.selectbox("အရေးပါမှု", ["Low", "Medium", "High"])
            ut_due   = col_b.date_input("ပြီးဆုံးရမည့် ရက်", value=None)
            add_btn  = st.form_submit_button("Task ထည့်မည်", use_container_width=True)

        if add_btn:
            if not ut_title.strip():
                st.error("Task ခေါင်းစဉ် ထည့်ပါ။")
            else:
                try:
                    _db = supabase_admin if supabase_admin else supabase
                    _db.table("tasks").insert({
                        "title":             ut_title.strip(),
                        "description":       ut_desc.strip() or None,
                        "assigned_to_email": my_email,
                        "created_by_email":  my_email,
                        "priority":          ut_prio,
                        "status":            "Pending",
                        "due_date":          str(ut_due) if ut_due else None,
                    }).execute()
                    st.rerun()
                except APIError as exc:
                    st.error(f"Task ထည့်မရပါ: {exc.message}")

    st.divider()
    my_tasks = safe_get_tasks(filter_email=my_email)

    if my_tasks.empty:
        st.info("ယခု Task မရှိပါ — အားလပ်နေသည်!")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("စုစုပေါင်း",  len(my_tasks))
        m2.metric("🟡 Pending",  len(my_tasks[my_tasks.status == "Pending"]))
        m3.metric("🟢 ပြီးပါပြီ", len(my_tasks[my_tasks.status == "Completed"]))

        st.subheader("ကျွန်ုပ်၏ Tasks")
        status_filter = st.selectbox("စစ်ထုတ်မည်", ["အားလုံး", "Pending", "In Progress", "Completed", "Cancelled"])
        filtered = my_tasks if status_filter == "အားလုံး" else my_tasks[my_tasks.status == status_filter]

        for _, row in filtered.iterrows():
            with st.container(border=True):
                c1, c2, c3 = st.columns([5, 1, 1])
                c1.markdown(f"**{row['title']}**")
                meta = [f"မှ: {row['created_by_email']}", f"Priority: {row.get('priority','N/A')}"]
                if row.get("due_date"):
                    due     = date.fromisoformat(str(row["due_date"]))
                    overdue = due < date.today() and row["status"] not in ("Completed", "Cancelled")
                    meta.append(f"{'⚠️ နောက်ကျ' if overdue else '📅'} ရက်: {due}")
                c1.caption(" | ".join(meta))
                if row.get("description"):
                    c1.caption(f"📝 {row['description']}")
                c2.markdown(status_badge(row["status"]))

                if row["status"] not in ("Completed", "Cancelled"):
                    action = c3.selectbox(
                        "လုပ်မည်",
                        ["—", "စတင်", "ပြီးပြီ ✅", "ပယ်ဖျက် ❌"],
                        key=f"user_act_{row['id']}",
                        label_visibility="collapsed",
                    )
                    if action != "—":
                        action_map = {"စတင်": "In Progress", "ပြီးပြီ ✅": "Completed", "ပယ်ဖျက် ❌": "Cancelled"}
                        try:
                            (supabase_admin if supabase_admin else supabase).table("tasks").update({"status": action_map[action]}).eq("id", row["id"]).eq("assigned_to_email", my_email).execute()
                            st.rerun()
                        except APIError as exc:
                            st.error(f"Update မရပါ: {exc.message}")

                if row.get("created_by_email") == my_email and row["status"] != "Completed":
                    if c3.button("🗑️", key=f"user_del_{row['id']}", help="Task ဖျက်မည်"):
                        try:
                            (supabase_admin if supabase_admin else supabase).table("tasks").delete().eq("id", row["id"]).eq("created_by_email", my_email).execute()
                            st.rerun()
                        except APIError as exc:
                            st.error(f"ဖျက်မရပါ: {exc.message}")

                logs_df = safe_get_logs(row["id"])
                with st.expander(f"📒 Daily Log ရေးမည် / ကြည့်မည် ({len(logs_df)} ခု)", expanded=False):
                    with st.form(f"log_form_{row['id']}", clear_on_submit=True):
                        lc1, lc2 = st.columns([1, 3])
                        log_date    = lc1.date_input("ရက်စွဲ", value=date.today(), key=f"ld_{row['id']}")
                        lc1.caption(f"🕐 {now_mmt().strftime('%H:%M')} (MMT)")
                        log_comment = lc2.text_area("ယနေ့ ဘာလုပ်ခဲ့သည်?", height=70, key=f"lc_{row['id']}")
                        log_file    = st.file_uploader(
                            "📎 ဖိုင် တင်ရန် (ချိတ်ဆက်မှု ဖိုင်) — ချန်လှပ်ထားလည်း ရသည်",
                            type=["pdf", "png", "jpg", "jpeg", "docx", "xlsx", "txt", "zip"],
                            key=f"lf_{row['id']}",
                        )
                        log_btn = st.form_submit_button("📝 Log ထည့်မည်", use_container_width=True)

                    if log_btn:
                        if not log_comment.strip():
                            st.error("Comment ရေးပါ။")
                        else:
                            try:
                                _db = supabase_admin if supabase_admin else supabase
                                attachment_url = None
                                if log_file is not None:
                                    try:
                                        import uuid as _uuid
                                        _ext       = log_file.name.rsplit(".", 1)[-1]
                                        _safe_name = f"{row['id']}/{log_date}_{_uuid.uuid4().hex[:8]}.{_ext}"
                                        _db.storage.from_("task-logs").upload(_safe_name, log_file.read(), {"content-type": log_file.type or "application/octet-stream"})
                                        attachment_url = _db.storage.from_("task-logs").get_public_url(_safe_name)
                                    except Exception as _fe:
                                        st.warning(f"⚠️ ဖိုင် တင်မရပါ (Log သာ သိမ်းမည်): {_fe}")

                                _db.table("task_logs").insert({
                                    "task_id":        row["id"],
                                    "user_email":     my_email,
                                    "log_date":       str(log_date),
                                    "log_time":       now_mmt().strftime("%H:%M:%S"),
                                    "comment":        log_comment.strip(),
                                    "attachment_url": attachment_url,
                                }).execute()

                                if logs_df.empty and row["status"] == "Pending":
                                    _db.table("tasks").update({"status": "In Progress"}).eq("id", row["id"]).execute()
                                    st.info("🔵 Task status သည် **In Progress** သို့ အလိုအလျောက် ပြောင်းသွားပြီ!")

                                st.success("Log ထည့်ပြီးပါပြီ!" + (" 📎 ဖိုင်လည်း တင်ပြီးပါပြီ!" if attachment_url else ""))
                                st.rerun()
                            except APIError as exc:
                                st.error(f"Log ထည့်မရပါ: {exc.message}")

                    if logs_df.empty:
                        st.info("Log မရှိသေးပါ — ပထမဆုံး log ရေးပါ။")
                    else:
                        st.markdown("---")
                        st.markdown("**မှတ်တမ်းများ:**")
                        for _, lg in logs_df.iterrows():
                            _log_time = lg.get("log_time", "")
                            _time_str = f" {_log_time} (MMT)" if _log_time else ""
                            st.markdown(f"📅 **{lg['log_date']}{_time_str}**")
                            st.markdown(f"> {lg['comment']}")
                            if lg.get("attachment_url"):
                                st.markdown(f"📎 **ဖိုင်:** [ဒေါင်းလုတ် / ကြည့်ရှုရန်]({lg['attachment_url']})", unsafe_allow_html=False)
                            st.divider()
