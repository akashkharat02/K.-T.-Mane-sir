import os, sqlite3, uuid, json, datetime
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, jsonify, abort)
from werkzeug.security import generate_password_hash, check_password_hash
import ai

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "civic.db")
UPLOADS = os.path.join(BASE, "static", "uploads")
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

STATUSES = ["REPORTED", "VERIFIED", "ASSIGNED", "IN_PROGRESS", "RESOLVED", "CONFIRMED"]

# ---------- DB ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def init_db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT,
        role TEXT, department TEXT);
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY, code TEXT, category TEXT, description TEXT,
        lat REAL, lng REAL, photo TEXT, after_photo TEXT,
        ai_confidence REAL, severity INTEGER, priority TEXT, breakdown TEXT,
        department TEXT, status TEXT DEFAULT 'REPORTED',
        reporter_id INTEGER, worker_id INTEGER, created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS history(
        id INTEGER PRIMARY KEY, ticket_id INTEGER, status TEXT, note TEXT, at TEXT);
    """)
    if not c.execute("SELECT 1 FROM users").fetchone():
        users = [("admin", "admin123", "admin", None), ("citizen", "citizen123", "citizen", None)]
        for i, d in enumerate(ai.DEPARTMENTS, 1):
            users.append((f"worker{i}", "worker123", "worker", d))
        for u, p, r, d in users:
            c.execute("INSERT INTO users(username,password,role,department) VALUES(?,?,?,?)",
                      (u, generate_password_hash(p), r, d))
    c.commit(); c.close()

def now(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

def log(ticket_id, status, note=""):
    db().execute("INSERT INTO history(ticket_id,status,note,at) VALUES(?,?,?,?)",
                 (ticket_id, status, note, now()))
    db().execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, now(), ticket_id))

def save_photo(f):
    if not f or not f.filename: return None
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"): return None
    name = uuid.uuid4().hex + ext
    f.save(os.path.join(UPLOADS, name))
    return name

# ---------- auth ----------
def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            if "uid" not in session: return redirect(url_for("login"))
            if roles and session["role"] not in roles: abort(403)
            return fn(*a, **k)
        return w
    return deco

@app.context_processor
def inject():
    return dict(session=session, STATUSES=STATUSES, CATS=ai.CATEGORIES)

@app.route("/")
def home():
    if "uid" not in session: return redirect(url_for("login"))
    return redirect({"citizen": "/my", "admin": "/admin", "worker": "/worker"}[session["role"]])

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE username=?", (request.form["username"],)).fetchone()
        if u and check_password_hash(u["password"], request.form["password"]):
            session.update(uid=u["id"], role=u["role"], name=u["username"], dept=u["department"])
            return redirect(url_for("home"))
        flash("Invalid username or password")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            db().execute("INSERT INTO users(username,password,role) VALUES(?,?,'citizen')",
                         (request.form["username"], generate_password_hash(request.form["password"])))
            db().commit()
            flash("Account created. Please log in.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already taken")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

# ---------- citizen ----------
@app.route("/api/classify", methods=["POST"])
@login_required("citizen")
def api_classify():
    name = save_photo(request.files.get("photo"))
    if not name: return jsonify(error="bad file"), 400
    key, conf = ai.classify_image(os.path.join(UPLOADS, name))
    return jsonify(category=key, confidence=round(conf * 100, 1), photo=name)

@app.route("/report", methods=["GET", "POST"])
@login_required("citizen")
def report():
    if request.method == "POST":
        f = request.form
        photo = f.get("photo_name") or save_photo(request.files.get("photo"))
        cat = f["category"]
        if cat not in ai.CATEGORIES or not photo or not f.get("lat") or not f.get("lng"):
            flash("Photo, category and location are required"); return redirect(url_for("report"))
        lat, lng = float(f["lat"]), float(f["lng"])
        dupes = sum(1 for t in db().execute(
            "SELECT lat,lng FROM tickets WHERE category=? AND status NOT IN ('RESOLVED','CONFIRMED')", (cat,))
            if ai.haversine_m(lat, lng, t["lat"], t["lng"]) < 75)
        score, prio, parts = ai.severity(cat, f.get("size", "medium"), bool(f.get("near")),
                                         bool(f.get("main_road")), dupes)
        cur = db().execute("""INSERT INTO tickets(category,description,lat,lng,photo,ai_confidence,severity,
            priority,breakdown,department,reporter_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cat, f.get("description", ""), lat, lng, photo, float(f.get("confidence") or 0),
             score, prio, json.dumps(parts), ai.CATEGORIES[cat]["dept"], session["uid"], now(), now()))
        tid = cur.lastrowid
        db().execute("UPDATE tickets SET code=? WHERE id=?", (f"CIV-{datetime.date.today().year}-{tid:05d}", tid))
        log(tid, "REPORTED", "Complaint submitted")
        db().commit()
        return redirect(url_for("ticket", tid=tid))
    return render_template("report.html")

@app.route("/my")
@login_required("citizen")
def my():
    rows = db().execute("SELECT * FROM tickets WHERE reporter_id=? ORDER BY id DESC", (session["uid"],)).fetchall()
    return render_template("my.html", tickets=rows)

@app.route("/ticket/<int:tid>")
@login_required()
def ticket(tid):
    t = db().execute("SELECT t.*, w.username AS worker FROM tickets t LEFT JOIN users w ON w.id=t.worker_id WHERE t.id=?", (tid,)).fetchone()
    if not t: abort(404)
    if session["role"] == "citizen" and t["reporter_id"] != session["uid"]: abort(403)
    hist = db().execute("SELECT * FROM history WHERE ticket_id=? ORDER BY id", (tid,)).fetchall()
    return render_template("ticket.html", t=t, hist=hist, parts=json.loads(t["breakdown"]))

@app.route("/ticket/<int:tid>/feedback", methods=["POST"])
@login_required("citizen")
def feedback(tid):
    t = db().execute("SELECT * FROM tickets WHERE id=? AND reporter_id=?", (tid, session["uid"])).fetchone()
    if not t or t["status"] != "RESOLVED": abort(400)
    if request.form["answer"] == "yes":
        log(tid, "CONFIRMED", "Citizen confirmed the fix")
    else:
        log(tid, "ASSIGNED", "Citizen rejected the fix - reopened")
    db().commit()
    return redirect(url_for("ticket", tid=tid))

# ---------- admin ----------
@app.route("/admin")
@login_required("admin")
def admin():
    q = "SELECT * FROM tickets WHERE 1=1"; args = []
    for k in ("status", "priority", "department"):
        if request.args.get(k):
            q += f" AND {k}=?"; args.append(request.args[k])
    q += " ORDER BY CASE status WHEN 'CONFIRMED' THEN 1 WHEN 'RESOLVED' THEN 1 ELSE 0 END, severity DESC"
    tickets = db().execute(q, args).fetchall()
    workers = db().execute("SELECT id,username,department FROM users WHERE role='worker'").fetchall()
    return render_template("admin.html", tickets=tickets, workers=workers, depts=ai.DEPARTMENTS)

@app.route("/admin/verify/<int:tid>", methods=["POST"])
@login_required("admin")
def verify(tid):
    log(tid, "VERIFIED", "Verified by admin"); db().commit()
    return redirect(request.referrer or url_for("admin"))

@app.route("/admin/assign/<int:tid>", methods=["POST"])
@login_required("admin")
def assign(tid):
    wid = int(request.form["worker_id"])
    db().execute("UPDATE tickets SET worker_id=? WHERE id=?", (wid, tid))
    log(tid, "ASSIGNED", "Assigned to field worker"); db().commit()
    return redirect(request.referrer or url_for("admin"))

@app.route("/api/stats")
@login_required("admin")
def stats():
    d = db()
    def grp(col): return {r[0]: r[1] for r in d.execute(f"SELECT {col}, COUNT(*) FROM tickets GROUP BY {col}")}
    pts = [dict(id=r["id"], code=r["code"], lat=r["lat"], lng=r["lng"], priority=r["priority"],
                cat=ai.CATEGORIES[r["category"]]["label"], status=r["status"])
           for r in d.execute("SELECT * FROM tickets")]
    pend = {r[0]: r[1] for r in d.execute(
        "SELECT department, COUNT(*) FROM tickets WHERE status NOT IN ('RESOLVED','CONFIRMED') GROUP BY department")}
    return jsonify(status=grp("status"), priority=grp("priority"), category=grp("category"),
                   pending_by_dept=pend, points=pts)

# ---------- worker ----------
@app.route("/worker")
@login_required("worker")
def worker():
    rows = db().execute("""SELECT * FROM tickets WHERE worker_id=? ORDER BY
        CASE status WHEN 'RESOLVED' THEN 1 WHEN 'CONFIRMED' THEN 1 ELSE 0 END, severity DESC""",
        (session["uid"],)).fetchall()
    return render_template("worker.html", tickets=rows)

@app.route("/worker/start/<int:tid>", methods=["POST"])
@login_required("worker")
def start(tid):
    log(tid, "IN_PROGRESS", "Work started"); db().commit()
    return redirect(url_for("worker"))

@app.route("/worker/resolve/<int:tid>", methods=["POST"])
@login_required("worker")
def resolve(tid):
    name = save_photo(request.files.get("after_photo"))
    if not name:
        flash("An after-repair photo is required"); return redirect(url_for("worker"))
    db().execute("UPDATE tickets SET after_photo=? WHERE id=? AND worker_id=?", (name, tid, session["uid"]))
    log(tid, "RESOLVED", "Repair done - after photo uploaded"); db().commit()
    return redirect(url_for("worker"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
