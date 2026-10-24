"""PMO agent: understands flexible natural language, remembers context, reacts proactively.

respond(msg, state) -> (reply_markdown, suggestions)
`state` is a dict kept per session (pending confirmations, last mentioned entities).
"""
import difflib
import re

import db

HELP = """I'm your PMO resource assistant. Talk to me naturally, for example:

- **Add:** `add resource Alice role Developer skills python, sql capacity 40 rate 80`
- **Add:** `add project Apollo budget 50000 from 2026-01-01 to 2026-06-30`
- **Allocate:** `assign Alice to Apollo 20 hours` · `put Bob on Apollo at 50%`
- **Remove:** `remove Alice from Apollo` · `delete project Apollo`
- **Ask:** `who is free?` · `who is overallocated?` · `who knows python for 15 hours?`
- **Reports:** `summary` · `utilization` · `cost of Apollo` · `rebalance`
- **Look up:** `show Alice` · `show Apollo` · `list resources` · `list projects`
- **Update:** `set Apollo status On Hold` · `set Alice capacity 32`
- **Demo:** `load sample data`"""

DEFAULT_SUGGESTIONS = ["Summary", "Who is free?", "Utilization", "Help"]


# ---------- helpers ----------
def table(rows, cols, labels=None):
    if not rows:
        return "_Nothing to show._"
    labels = labels or cols
    out = ["| " + " | ".join(labels) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join("" if r.get(c) is None else _fmt(r[c]) for c in cols) + " |")
    return "\n".join(out)


def _fmt(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


def _names(table_name):
    return [r["name"] for r in db.query(f"SELECT name FROM {table_name}")]


def resolve(kind, name):
    """Return (row, note). Tolerates typos and case differences."""
    name = name.strip().strip("'\"")
    row = db.find(kind, name)
    if row:
        return row, ""
    names = _names(kind)
    hit = difflib.get_close_matches(name.lower(), [n.lower() for n in names], n=1, cutoff=0.7)
    if not hit:
        sub = [n for n in names if name.lower() in n.lower() or n.lower() in name.lower()]
        hit = [sub[0].lower()] if len(sub) == 1 and name else []
    if hit:
        row = db.find(kind, next(n for n in names if n.lower() == hit[0]))
        return row, f"_(interpreting '{name}' as **{row['name']}**)_\n\n"
    label = "resource" if kind == "resources" else "project"
    known = ", ".join(names[:8]) or "none yet"
    return None, f"❌ I couldn't find a {label} called **{name}**. Known {label}s: {known}."


def _hours(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h\b|hrs?\b|hours?\b)", text, re.I)
    return float(m.group(1)) if m else None


def _kv(text, key, stop_keys):
    stops = "|".join(stop_keys)
    m = re.search(rf"\b{key}\s*[:=]?\s+(.+?)(?=\s+(?:{stops})\b|$)", text, re.I)
    return m.group(1).strip() if m else None


def _load(resource_id):
    r = next(u for u in db.utilization() if u["id"] == resource_id)
    return r["allocated"], r["capacity_hours"]


def _insights():
    util = db.utilization()
    over = [u for u in util if u["available"] < 0]
    bench = [u for u in util if u["capacity_hours"] and u["allocated"] / u["capacity_hours"] < 0.5]
    unstaffed = db.query("""SELECT p.name FROM projects p LEFT JOIN allocations a ON a.project_id=p.id
                            WHERE a.id IS NULL AND p.status='Active'""")
    return util, over, bench, unstaffed


def _alerts():
    _, over, bench, unstaffed = _insights()
    lines = []
    if over:
        lines.append("⚠️ Overallocated: " + ", ".join(f"{u['name']} ({u['allocated']:g}/{u['capacity_hours']:g}h)" for u in over))
    if unstaffed:
        lines.append("📌 Active projects with nobody assigned: " + ", ".join(p["name"] for p in unstaffed))
    if bench:
        lines.append("💤 Under 50% utilised: " + ", ".join(u["name"] for u in bench))
    return lines


# ---------- intent handlers ----------
def add_resource(t, state):
    m = re.match(r"(?:add|create|new|register|hire)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:resource|person|member|employee|engineer|developer)?\s*(.+)$", t, re.I)
    keys = ["role", "skills?", "capacity", "rate", "cost"]
    head = re.split(rf"\s+(?:{'|'.join(keys)})\b", m.group(1), maxsplit=1, flags=re.I)[0].strip()
    if not head:
        return "Who should I add? e.g. `add resource Alice role Developer skills python capacity 40`.", ["Help"]
    role = _kv(t, "role", keys) or ""
    skills = _kv(t, "skills?", keys) or ""
    cap = _kv(t, "capacity", keys)
    rate = _kv(t, "(?:rate|cost)", keys)
    try:
        cap = float(re.sub(r"[^\d.]", "", cap)) if cap else 40.0
        rate = float(re.sub(r"[^\d.]", "", rate)) if rate else 0.0
    except ValueError:
        return "❌ Capacity and rate must be numbers.", []
    if db.find("resources", head):
        return f"ℹ️ **{head}** already exists. Use `set {head} capacity 32` to update.", [f"Show {head}"]
    db.execute("INSERT INTO resources(name,role,skills,capacity_hours,cost_rate) VALUES(?,?,?,?,?)",
               (head, role, skills, cap, rate))
    state["last_resource"] = head
    msg = f"✅ Added **{head}**" + (f" ({role})" if role else "") + f" with {cap:g} h/week capacity."
    return msg, [f"Assign {head} to a project", f"Show {head}", "Who is free?"]


def add_project(t, state):
    m = re.match(r"(?:add|create|new|start)\s+(?:a\s+|an\s+)?(?:new\s+)?project\s+(.+)$", t, re.I)
    keys = ["budget", "from", "to", "until", "status"]
    head = re.split(rf"\s+(?:{'|'.join(keys)})\b", m.group(1), maxsplit=1, flags=re.I)[0].strip()
    if db.find("projects", head):
        return f"ℹ️ Project **{head}** already exists.", [f"Show {head}"]
    budget = _kv(t, "budget", keys)
    start = re.search(r"\bfrom\s+(\d{4}-\d{2}-\d{2})", t, re.I)
    end = re.search(r"\b(?:to|until)\s+(\d{4}-\d{2}-\d{2})", t, re.I)
    try:
        budget = float(re.sub(r"[^\d.]", "", budget)) if budget else 0.0
    except ValueError:
        return "❌ Budget must be a number.", []
    db.execute("INSERT INTO projects(name,budget,start_date,end_date) VALUES(?,?,?,?)",
               (head, budget, start.group(1) if start else None, end.group(1) if end else None))
    state["last_project"] = head
    return f"✅ Created project **{head}**" + (f" with budget {budget:,.0f}" if budget else "") + ".", \
        [f"Assign someone to {head}", "Who is free?", "Summary"]


def allocate(t, state):
    m = re.match(r"(?:allocate|assign|put|add|staff|book)\s+(.+?)\s+(?:to|on|onto|into|for)\s+(?:project\s+)?(.+)$", t, re.I)
    rname, rest = m.group(1), m.group(2)
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", rest)
    hrs = _hours(rest)
    pname = re.split(r"\s+(?:at|for|with|@)?\s*\d", rest, maxsplit=1)[0].strip()
    pname = re.sub(r"\s+(?:at|for|with|@)$", "", pname, flags=re.I)
    if rname.lower() in ("someone", "somebody", "anyone"):
        return who_available(t, state)
    if pname.lower() in ("a project", "project", "a new project", "something"):
        projects = _names("projects")[:4]
        if not projects:
            return "There are no projects yet. Try `add project Apollo`.", ["Load sample data"]
        return f"Which project should **{rname}** join?", [f"Assign {rname} to {p}" for p in projects]
    r, note1 = resolve("resources", rname.replace("him", state.get("last_resource", "")) if rname.lower() in ("him", "her", "them") else rname)
    if not r:
        return note1, ["List resources"]
    p, note2 = resolve("projects", pname)
    if not p:
        return note2, ["List projects"]
    if pct:
        hrs = r["capacity_hours"] * float(pct.group(1)) / 100
    if hrs is None:
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*$", rest)
        hrs = float(m2.group(1)) if m2 else None
    if hrs is None:
        state["pending"] = {"kind": "hours", "resource": r["name"], "project": p["name"]}
        return f"How many hours per week should **{r['name']}** work on **{p['name']}**? (reply with a number)", ["10", "20", "40", "Cancel"]
    return _do_allocate(r, p, hrs, state, note1 + note2)


def _do_allocate(r, p, hrs, state, note=""):
    used, cap = _load(r["id"])
    existing = db.query("SELECT id,hours_per_week FROM allocations WHERE resource_id=? AND project_id=?", (r["id"], p["id"]))
    if existing:
        db.execute("UPDATE allocations SET hours_per_week=? WHERE id=?", (hrs, existing[0]["id"]))
        used -= existing[0]["hours_per_week"]
        verb = "Updated"
    else:
        db.execute("INSERT INTO allocations(resource_id,project_id,hours_per_week) VALUES(?,?,?)", (r["id"], p["id"], hrs))
        verb = "Allocated"
    state["last_resource"], state["last_project"] = r["name"], p["name"]
    total = used + hrs
    msg = f"{note}✅ {verb} **{r['name']}** → **{p['name']}**: {hrs:g} h/week ({total:g}/{cap:g}h used)."
    if total > cap:
        msg += f"\n\n⚠️ **{r['name']}** is now over capacity by {total - cap:g}h. Say `rebalance` for ideas."
        return msg, ["Rebalance", f"Show {r['name']}", "Who is free?"]
    return msg, [f"Show {p['name']}", "Utilization", "Who is free?"]


def unallocate(t, state):
    m = re.match(r"(?:unallocate|unassign|remove|take|drop)\s+(.+?)\s+(?:from|off)\s+(?:project\s+)?(.+)$", t, re.I)
    r, n1 = resolve("resources", m.group(1))
    if not r:
        return n1, []
    p, n2 = resolve("projects", m.group(2))
    if not p:
        return n2, []
    db.execute("DELETE FROM allocations WHERE resource_id=? AND project_id=?", (r["id"], p["id"]))
    return f"{n1}{n2}✅ Removed **{r['name']}** from **{p['name']}**.", ["Who is free?", "Utilization"]


def delete(t, state):
    m = re.match(r"(?:delete|remove|drop)\s+(?:the\s+)?(resource|project|person)\s+(.+)$", t, re.I)
    kind = "projects" if m.group(1).lower() == "project" else "resources"
    row, note = resolve(kind, m.group(2))
    if not row:
        return note, []
    state["pending"] = {"kind": "delete", "table": kind, "name": row["name"]}
    return f"{note}🗑️ Delete **{row['name']}** and all its allocations? Reply **yes** to confirm or **no** to cancel.", ["Yes", "No"]


def set_attr(t, state):
    m = re.match(r"(?:set|change|update)\s+(?:project\s+|resource\s+)?(.+?)\s+(status|capacity|rate|role|skills?|budget)\s+(?:to\s+)?(.+)$", t, re.I)
    name, attr, val = m.group(1), m.group(2).lower(), m.group(3).strip()
    attr_map = {"status": ("projects", "status"), "budget": ("projects", "budget"),
                "capacity": ("resources", "capacity_hours"), "rate": ("resources", "cost_rate"),
                "role": ("resources", "role"), "skill": ("resources", "skills"), "skills": ("resources", "skills")}
    tbl, col = attr_map[attr]
    row, note = resolve(tbl, name)
    if not row:
        return note, []
    if col in ("capacity_hours", "cost_rate", "budget"):
        try:
            val = float(re.sub(r"[^\d.]", "", val))
        except ValueError:
            return "❌ That needs to be a number.", []
    db.execute(f"UPDATE {tbl} SET {col}=? WHERE id=?", (val, row["id"]))
    return f"{note}✅ Updated **{row['name']}**: {attr} → {val}.", [f"Show {row['name']}", "Summary"]


def who_available(t, state):
    util = db.utilization()
    need = _hours(t)
    rows = [u for u in util if u["available"] > 0 and (need is None or u["available"] >= need)]
    rows.sort(key=lambda u: -u["available"])
    if not rows:
        return "😕 Nobody has spare capacity" + (f" for {need:g}h." if need else "."), ["Rebalance", "Summary"]
    title = f"**{len(rows)} available**" + (f" with ≥{need:g}h free" if need else "")
    return title + "\n\n" + table(rows, ["name", "role", "capacity_hours", "allocated", "available"],
                                  ["Name", "Role", "Capacity", "Allocated", "Free"]), \
        [f"Assign {rows[0]['name']} to a project", "Utilization"]


def who_over(t, state):
    rows = [u for u in db.utilization() if u["available"] < 0]
    if not rows:
        return "✅ Nobody is overallocated. Nice and balanced!", ["Utilization", "Summary"]
    for r in rows:
        r["over"] = -r["available"]
    return "⚠️ **Overallocated resources**\n\n" + table(
        rows, ["name", "capacity_hours", "allocated", "over"], ["Name", "Capacity", "Allocated", "Over by"]), ["Rebalance"]


def utilization(t, state):
    rows = db.utilization()
    for u in rows:
        pct = u["allocated"] / u["capacity_hours"] * 100 if u["capacity_hours"] else 0
        icon = "🔴" if pct > 100 else "🟡" if pct >= 85 else "🟢" if pct >= 50 else "⚪"
        u["utilization"] = f"{icon} {pct:.0f}%"
    return "**Utilization**\n\n" + table(rows, ["name", "role", "capacity_hours", "allocated", "utilization"],
                                         ["Name", "Role", "Capacity", "Allocated", "Utilization"]), ["Who is free?", "Rebalance"]


def skill_search(t, state):
    resources = db.query("SELECT * FROM resources")
    vocab = {s.strip().lower() for r in resources for s in re.split(r"[,;/]", r["skills"] or "") if s.strip()}
    low = t.lower()
    wanted = [s for s in vocab if re.search(rf"(?<![\w+#]){re.escape(s)}(?![\w+#])", low)]
    if not wanted:
        known = ", ".join(sorted(vocab)) or "none recorded yet"
        return f"I couldn't spot a known skill in that. Skills on record: {known}.", ["List resources"]
    need = _hours(t)
    util = {u["id"]: u for u in db.utilization()}
    ranked = []
    for r in resources:
        mine = {s.strip().lower() for s in re.split(r"[,;/]", r["skills"] or "")}
        hit = [s for s in wanted if s in mine]
        if hit:
            free = util[r["id"]]["available"]
            ranked.append({"name": r["name"], "role": r["role"], "match": f"{len(hit)}/{len(wanted)}", "free": free,
                           "_s": (len(hit), free >= (need or 0), free)})
    ranked.sort(key=lambda x: x["_s"], reverse=True)
    if not ranked:
        return f"😕 Nobody has **{', '.join(wanted)}**.", ["List resources"]
    best = ranked[0]
    msg = f"**Candidates for {', '.join(wanted)}**" + (f" ({need:g}h/week)" if need else "") + "\n\n" + \
        table(ranked, ["name", "role", "match", "free"], ["Name", "Role", "Skill match", "Free hours"])
    msg += f"\n\n💡 Best fit: **{best['name']}** ({best['free']:g}h free)."
    return msg, [f"Assign {best['name']} to a project", "Who is free?"]


def summary(t, state):
    util, over, bench, unstaffed = _insights()
    projects = db.query("SELECT * FROM projects")
    total_cap = sum(u["capacity_hours"] for u in util)
    total_alloc = sum(u["allocated"] for u in util)
    weekly = db.query("""SELECT COALESCE(SUM(a.hours_per_week*r.cost_rate),0) AS c
                         FROM allocations a JOIN resources r ON r.id=a.resource_id""")[0]["c"]
    pct = total_alloc / total_cap * 100 if total_cap else 0
    msg = (f"### 📊 PMO snapshot\n- **Resources:** {len(util)} · **Projects:** {len(projects)}\n"
           f"- **Team utilisation:** {pct:.0f}% ({total_alloc:g}/{total_cap:g}h per week)\n"
           f"- **Weekly labour cost:** {weekly:,.0f}\n")
    alerts = _alerts()
    msg += "\n**Attention:**\n" + "\n".join(f"- {a}" for a in alerts) if alerts else "\n✅ No issues detected."
    sugg = ["Rebalance"] if over else []
    return msg, sugg + ["Who is free?", "Utilization"]


def cost(t, state):
    m = re.search(r"(?:cost|spend|burn|budget)\s+(?:of|for)?\s*(?:project\s+)?(.+)$", t, re.I)
    name = m.group(1).strip(" ?") if m else ""
    if not name or name.lower() in ("all", "everything", "projects"):
        rows = db.query("""SELECT p.name, p.budget, COALESCE(SUM(a.hours_per_week*r.cost_rate),0) AS weekly
                           FROM projects p LEFT JOIN allocations a ON a.project_id=p.id
                           LEFT JOIN resources r ON r.id=a.resource_id GROUP BY p.id""")
        return "**Weekly cost by project**\n\n" + table(rows, ["name", "weekly", "budget"], ["Project", "Weekly cost", "Budget"]), ["Summary"]
    p, note = resolve("projects", name)
    if not p:
        return note, []
    rows = db.query("""SELECT r.name, a.hours_per_week AS hours, r.cost_rate AS rate, a.hours_per_week*r.cost_rate AS cost
                       FROM allocations a JOIN resources r ON r.id=a.resource_id WHERE a.project_id=?""", (p["id"],))
    weekly = sum(r["cost"] for r in rows)
    msg = f"{note}**{p['name']}** costs **{weekly:,.0f}/week**"
    if p["budget"]:
        msg += f" → budget {p['budget']:,.0f} lasts ~{p['budget'] / weekly:.1f} weeks." if weekly else f" (budget {p['budget']:,.0f})."
    return msg + "\n\n" + table(rows, ["name", "hours", "rate", "cost"], ["Resource", "Hours/wk", "Rate", "Cost/wk"]), ["Summary"]


def rebalance(t, state):
    util = db.utilization()
    over = [u for u in util if u["available"] < 0]
    if not over:
        return "✅ Nobody is overallocated, so there's nothing to rebalance.", ["Summary"]
    free = sorted((u for u in util if u["available"] > 0), key=lambda u: -u["available"])
    lines = []
    for o in over:
        excess = -o["available"]
        allocs = db.query("""SELECT p.name AS project, a.hours_per_week AS h FROM allocations a
                             JOIN projects p ON p.id=a.project_id WHERE a.resource_id=? ORDER BY a.hours_per_week DESC""", (o["id"],))
        if not allocs:
            continue
        top = allocs[0]
        cand = next((f for f in free if f["available"] >= min(excess, top["h"])), None)
        if cand:
            lines.append(f"- Move {min(excess, top['h']):g}h of **{o['name']}**'s {top['project']} work to **{cand['name']}** ({cand['available']:g}h free).")
        else:
            lines.append(f"- **{o['name']}** is over by {excess:g}h and nobody has enough spare capacity. Consider reducing {top['project']} scope or hiring.")
    return "### ♻️ Rebalancing ideas\n" + "\n".join(lines), ["Who is free?", "Utilization"]


def show(t, state):
    m = re.match(r"(?:show|view|details?|about|tell me about|info(?: on)?|what(?:'s| is) (?:on|up with))\s+(?:me\s+)?(?:the\s+)?(?:resource\s+|project\s+)?(.+?)\??$", t, re.I)
    name = m.group(1).strip()
    if name.lower() in ("him", "her", "them", "it"):
        name = state.get("last_resource") or state.get("last_project") or name
    row, note = resolve("resources", name)
    if row:
        rows = [a for a in db.allocation_rows() if a["resource"] == row["name"]]
        u = next(x for x in db.utilization() if x["id"] == row["id"])
        state["last_resource"] = row["name"]
        head = (f"{note}### 👤 {row['name']}\n- **Role:** {row['role'] or '—'}\n- **Skills:** {row['skills'] or '—'}\n"
                f"- **Capacity:** {row['capacity_hours']:g}h · **Allocated:** {u['allocated']:g}h · **Free:** {u['available']:g}h\n"
                f"- **Rate:** {row['cost_rate']:g}/h\n\n")
        return head + table(rows, ["project", "hours_per_week"], ["Project", "Hours/wk"]), ["Assign " + row["name"] + " to a project", "Who is free?"]
    row, note = resolve("projects", name)
    if row:
        rows = [a for a in db.allocation_rows() if a["project"] == row["name"]]
        state["last_project"] = row["name"]
        head = (f"{note}### 📁 {row['name']}\n- **Status:** {row['status']}\n- **Dates:** {row['start_date'] or '—'} → {row['end_date'] or '—'}\n"
                f"- **Budget:** {row['budget']:,.0f} · **Team hours/wk:** {sum(r['hours_per_week'] for r in rows):g}\n\n")
        return head + table(rows, ["resource", "hours_per_week"], ["Resource", "Hours/wk"]), [f"Cost of {row['name']}", "Summary"]
    return f"❌ I couldn't find **{name}** as a resource or project.", ["List resources", "List projects"]


def list_resources(t, state):
    rows = db.query("SELECT name,role,skills,capacity_hours,cost_rate FROM resources ORDER BY name")
    return f"**{len(rows)} resources**\n\n" + table(rows, ["name", "role", "skills", "capacity_hours", "cost_rate"],
                                                      ["Name", "Role", "Skills", "Capacity", "Rate"]), ["Who is free?", "Utilization"]


def list_projects(t, state):
    rows = db.query("SELECT name,status,start_date,end_date,budget FROM projects ORDER BY name")
    return f"**{len(rows)} projects**\n\n" + table(rows, ["name", "status", "start_date", "end_date", "budget"],
                                                     ["Project", "Status", "Start", "End", "Budget"]), ["Summary", "Cost of all projects"]


def list_allocations(t, state):
    return "**Allocations**\n\n" + table(db.allocation_rows(), ["resource", "project", "hours_per_week"],
                                          ["Resource", "Project", "Hours/wk"]), ["Utilization"]


def sample_data(t, state):
    people = [("Alice", "Developer", "python, sql, streamlit", 40, 80), ("Bob", "Designer", "figma, ux", 40, 70),
              ("Carol", "Data Engineer", "python, spark, sql", 32, 90), ("Dave", "QA Engineer", "testing, selenium", 40, 60)]
    for n, r, s, c, rate in people:
        if not db.find("resources", n):
            db.execute("INSERT INTO resources(name,role,skills,capacity_hours,cost_rate) VALUES(?,?,?,?,?)", (n, r, s, c, rate))
    for n, b in [("Apollo", 50000), ("Zeus", 80000), ("Hermes", 20000)]:
        if not db.find("projects", n):
            db.execute("INSERT INTO projects(name,budget) VALUES(?,?)", (n, b))
    for rn, pn, h in [("Alice", "Apollo", 30), ("Alice", "Zeus", 15), ("Bob", "Apollo", 20), ("Carol", "Zeus", 16)]:
        r, p = db.find("resources", rn), db.find("projects", pn)
        if not db.query("SELECT 1 FROM allocations WHERE resource_id=? AND project_id=?", (r["id"], p["id"])):
            db.execute("INSERT INTO allocations(resource_id,project_id,hours_per_week) VALUES(?,?,?)", (r["id"], p["id"], h))
    return "✅ Sample data loaded (4 people, 3 projects). Notice Alice is overallocated. Try `summary`!", ["Summary", "Rebalance", "Who knows python?"]


# ---------- routing ----------
def _handle_pending(t, state):
    pend = state.get("pending")
    low = t.lower().strip(" .!")
    if low in ("no", "n", "cancel", "stop", "nevermind", "never mind"):
        state.pop("pending")
        return "👍 Cancelled.", DEFAULT_SUGGESTIONS
    if pend["kind"] == "delete" and low in ("yes", "y", "confirm", "ok", "sure", "do it"):
        state.pop("pending")
        db.execute(f"DELETE FROM {pend['table']} WHERE lower(name)=lower(?)", (pend["name"],))
        return f"🗑️ Deleted **{pend['name']}**.", ["Summary", "List resources"]
    if pend["kind"] == "hours":
        h = re.search(r"\d+(?:\.\d+)?", t)
        if h:
            state.pop("pending")
            r, p = db.find("resources", pend["resource"]), db.find("projects", pend["project"])
            return _do_allocate(r, p, float(h.group()), state)
    state.pop("pending")  # anything else abandons the question and is parsed normally
    return None


def respond(msg: str, state: dict | None = None):
    state = state if state is not None else {}
    t = msg.strip()
    low = t.lower().strip(" ?!.")
    if not t:
        return "Say something and I'll help. Try `help`.", DEFAULT_SUGGESTIONS
    try:
        if state.get("pending"):
            out = _handle_pending(t, state)
            if out:
                return out
        if re.fullmatch(r"(hi|hello|hey|yo|good (morning|afternoon|evening))( there)?", low):
            alerts = _alerts()
            extra = "\n\nHeads up:\n" + "\n".join(f"- {a}" for a in alerts) if alerts else ""
            return "👋 Hi! I can manage resources, projects and allocations for you." + extra, DEFAULT_SUGGESTIONS
        if re.fullmatch(r"(thanks|thank you|thx|great|cool|ok|okay|nice)( a lot)?", low):
            return "You're welcome! Anything else?", DEFAULT_SUGGESTIONS
        if low in ("help", "?", "commands", "what can you do"):
            return HELP, DEFAULT_SUGGESTIONS
        if re.search(r"sample|demo data", low) and re.search(r"load|add|seed|generate", low):
            return sample_data(t, state)

        rules = [
            (r"(?:add|create|new|register|hire)\s+(?:a\s+|an\s+)?(?:new\s+)?project\b", add_project),
            (r"(?:add|create|new|register|hire)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:resource|person|member|employee|engineer|developer)\b", add_resource),
            (r"(?:delete|remove|drop)\s+(?:the\s+)?(?:resource|project|person)\s+\S", delete),
            (r"(?:unallocate|unassign|remove|take|drop)\s+.+?\s+(?:from|off)\s+.+", unallocate),
            (r"(?:allocate|assign|put|staff|book|add)\s+.+?\s+(?:to|on|onto|into|for)\s+.+", allocate),
            (r"(?:set|change|update)\s+.+?\s+(?:status|capacity|rate|role|skills?|budget)\s+.+", set_attr),
        ]
        for pat, fn in rules:
            if re.match(pat, t, re.I):
                return fn(t, state)

        if re.search(r"over-?\s?allocat|overbooked|overloaded|over capacity", low):
            return who_over(t, state)
        if re.search(r"rebalanc|redistribut|fix (the )?overalloc", low):
            return rebalance(t, state)
        if re.search(r"\b(skill|skills|knows?|expert|experience)\b", low) or re.match(r"(suggest|recommend|who can)", low):
            return skill_search(t, state)
        if re.search(r"\b(free|available|availability|spare|capacity|bench|idle)\b", low) and re.search(r"\b(who|anyone|anybody|any|show|list|find)\b", low):
            return who_available(t, state)
        if re.search(r"utili[sz]ation|workload|how busy|load", low):
            return utilization(t, state)
        if re.search(r"\b(cost|spend|burn|budget)\b", low):
            return cost(t, state)
        if re.search(r"summary|overview|status report|dashboard|snapshot|how are we|report", low):
            return summary(t, state)
        if re.search(r"allocations?|who (is )?(working|assigned)", low):
            return list_allocations(t, state)
        if re.search(r"\b(list|show|all)\b.*\b(resources?|people|team|members)\b", low):
            return list_resources(t, state)
        if re.search(r"\b(list|show|all)\b.*\bprojects?\b", low):
            return list_projects(t, state)
        if re.match(r"(show|view|details?|about|tell me about|info|what(?:'s| is) )", low):
            return show(t, state)

        # last resort: a bare name of a known resource or project
        for kind in ("resources", "projects"):
            row, _ = resolve(kind, t)
            if row:
                return show(row["name"], state)
    except Exception as e:  # keep the chat alive on any parse/DB error
        return f"❌ Something went wrong: {e}", ["Help"]
    guess = difflib.get_close_matches(low, ["summary", "utilization", "help", "list resources", "list projects", "who is free"], n=1, cutoff=0.5)
    hint = f"Did you mean `{guess[0]}`? " if guess else ""
    return f"🤔 I didn't quite get that. {hint}Type `help` to see what I can do.", guess + DEFAULT_SUGGESTIONS
