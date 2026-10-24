import time

import pandas as pd
import streamlit as st

import agent
import db

st.set_page_config(page_title="PMO Resource Agent", page_icon="📊", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
.block-container {max-width: 1200px; padding-top: 2rem;}
div[data-baseweb="tab-list"] {overflow-x: auto; flex-wrap: nowrap;}
.stMarkdown table {display: block; overflow-x: auto; max-width: 100%;}
div[data-testid="stChatMessage"] {border-radius: 14px;}
@media (max-width: 768px) {
  .block-container {padding: 3.5rem 0.75rem 6rem;}
  h1 {font-size: 1.5rem !important;}
  button[data-baseweb="tab"] {padding: 0.4rem 0.6rem; font-size: 0.85rem;}
}
</style>
""", unsafe_allow_html=True)

# ---- Sign in (local profile) ----
if "user" not in st.session_state:
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.title("📊 PMO Resource Agent")
        st.caption("Your data is stored locally in `data/pmo.db`.")
        known = [u["name"] for u in db.query("SELECT name FROM users ORDER BY name")]
        with st.form("login"):
            name = st.text_input("Your name", placeholder="e.g. Priya")
            pick = st.selectbox("...or continue as", [""] + known)
            if st.form_submit_button("Enter", type="primary", width="stretch"):
                chosen = (name or pick).strip()
                if chosen:
                    db.execute("INSERT OR IGNORE INTO users(name) VALUES(?)", (chosen,))
                    st.session_state.user = chosen
                    st.rerun()
                else:
                    st.error("Please enter a name.")
    st.stop()

user = st.session_state.user
st.session_state.setdefault("agent_state", {})
st.session_state.setdefault("suggestions", agent.DEFAULT_SUGGESTIONS)
st.session_state.setdefault("queued", None)

with st.sidebar:
    st.write(f"👤 **{user}**")
    if st.button("Sign out", width="stretch"):
        for k in ("user", "agent_state", "suggestions", "queued"):
            st.session_state.pop(k, None)
        st.rerun()

head_l, head_r = st.columns([4, 1], vertical_alignment="center")
head_l.title("📊 PMO Resource Management")
head_r.caption(f"👤 {user}")

tab_chat, tab_dash, tab_res, tab_proj, tab_alloc = st.tabs(
    ["💬 Agent", "📈 Dashboard", "👥 Resources", "📁 Projects", "🔗 Allocations"])


def queue(text):
    st.session_state.queued = text


def stream(text):
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.008)


with tab_chat:
    history = db.query("SELECT role,content FROM chat WHERE user=? ORDER BY id", (user,))
    if not history:
        history = [{"role": "assistant", "content": agent.HELP}]
    for h in history:
        with st.chat_message(h["role"]):
            st.markdown(h["content"])

    prompt = st.session_state.queued or st.chat_input("Ask the PMO agent... (try 'summary' or 'help')")
    st.session_state.queued = None
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                reply, suggestions = agent.respond(prompt, st.session_state.agent_state)
            st.write_stream(stream(reply))
        st.session_state.suggestions = suggestions or agent.DEFAULT_SUGGESTIONS
        db.execute("INSERT INTO chat(user,role,content) VALUES(?,?,?)", (user, "user", prompt))
        db.execute("INSERT INTO chat(user,role,content) VALUES(?,?,?)", (user, "assistant", reply))

    sugg = list(dict.fromkeys(st.session_state.suggestions))[:5]
    cols = st.columns(len(sugg))
    for i, (col, s) in enumerate(zip(cols, sugg)):
        col.button(s, key=f"sugg_{i}_{len(history)}_{bool(prompt)}", on_click=queue, args=(s,), width="stretch")
    if st.button("🧹 Clear chat", key="clear_chat"):
        db.execute("DELETE FROM chat WHERE user=?", (user,))
        st.session_state.agent_state = {}
        st.rerun()

with tab_dash:
    util = db.utilization()
    projects = db.query("SELECT * FROM projects")
    over = sum(1 for u in util if u["available"] < 0)
    total_cap = sum(u["capacity_hours"] for u in util)
    total_alloc = sum(u["allocated"] for u in util)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Resources", len(util))
    c2.metric("Projects", len(projects))
    c3.metric("Utilisation", f"{total_alloc / total_cap * 100:.0f}%" if total_cap else "—")
    c4.metric("Overallocated", over)
    for line in agent._alerts():
        st.warning(line)
    if util:
        df = pd.DataFrame(util)
        left, right = st.columns(2)
        with left:
            st.subheader("Capacity vs allocated")
            st.bar_chart(df.set_index("name")[["capacity_hours", "allocated"]])
        with right:
            st.subheader("Hours per project")
            per_proj = pd.DataFrame(db.allocation_rows())
            if not per_proj.empty:
                st.bar_chart(per_proj.groupby("project")["hours_per_week"].sum())
            else:
                st.info("No allocations yet.")
        st.dataframe(df.drop(columns="id"), hide_index=True, width="stretch")
    else:
        st.info("No resources yet. Load demo data, or add some in the Resources tab.")
        st.button("Load sample data", on_click=lambda: agent.sample_data("", {}))


def editor(tab, table, cols, key):
    with tab:
        df = pd.DataFrame(db.query(f"SELECT * FROM {table}"))
        if df.empty:
            df = pd.DataFrame(columns=["id"] + cols)
        edited = st.data_editor(df, num_rows="dynamic", hide_index=True, key=key,
                                disabled=["id"], width="stretch")
        if st.button("Save changes", key=key + "_save", type="primary"):
            keep = set()
            try:
                for _, row in edited.iterrows():
                    vals = [None if pd.isna(row[c]) else row[c] for c in cols]
                    if not vals[0]:
                        continue
                    if pd.isna(row["id"]):
                        keep.add(db.execute(
                            f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals))
                    else:
                        keep.add(int(row["id"]))
                        db.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in cols)} WHERE id=?",
                                   vals + [int(row["id"])])
                for old in df["id"].dropna():
                    if int(old) not in keep:
                        db.execute(f"DELETE FROM {table} WHERE id=?", (int(old),))
                st.toast("Saved ✅")
                st.rerun()
            except Exception as e:
                st.error(str(e))


editor(tab_res, "resources", ["name", "role", "skills", "capacity_hours", "cost_rate"], "res")
editor(tab_proj, "projects", ["name", "status", "start_date", "end_date", "budget"], "proj")

with tab_alloc:
    st.dataframe(pd.DataFrame(db.allocation_rows()), hide_index=True, width="stretch")
    res, proj = db.query("SELECT id,name FROM resources"), db.query("SELECT id,name FROM projects")
    if res and proj:
        with st.form("alloc"):
            a, b, c = st.columns(3)
            r = a.selectbox("Resource", res, format_func=lambda x: x["name"])
            p = b.selectbox("Project", proj, format_func=lambda x: x["name"])
            h = c.number_input("Hours per week", 1.0, 80.0, 10.0)
            if st.form_submit_button("Allocate", type="primary"):
                db.execute("INSERT INTO allocations(resource_id,project_id,hours_per_week) VALUES(?,?,?)",
                           (r["id"], p["id"], h))
                st.rerun()
    else:
        st.info("Add at least one resource and one project first.")
