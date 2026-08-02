from flask import Flask, request, jsonify, send_file, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import yt_dlp
import os
import uuid
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production-please")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///viddrop.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

db = SQLAlchemy(app)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

jobs = {}

# ── Models ────────────────────────────────────────────────────────────────────

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DownloadHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(500), nullable=False)
    url = db.Column(db.String(1000), nullable=False)
    quality = db.Column(db.String(20), nullable=False)
    filename = db.Column(db.String(500), nullable=False)
    downloaded_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# ── Helpers ───────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Login required"}), 401
        return f(*args, **kwargs)
    return decorated

def cleanup_file(filepath, delay=600):
    def _delete():
        time.sleep(delay)
        if os.path.exists(filepath):
            os.remove(filepath)
    threading.Thread(target=_delete, daemon=True).start()

FORMAT_MAP = {
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]",
    "audio": "bestaudio[ext=m4a]/bestaudio",
}

def do_download(job_id, url, quality):
    jobs[job_id].update({"status": "downloading", "speed": "", "eta": "", "percent": 0})
    ext = "m4a" if quality == "audio" else "mp4"
    output_path = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")
    cookies_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

    def progress_hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            percent = int(downloaded / total * 100) if total else 0
            speed_raw = d.get("speed", 0) or 0
            speed = f"{speed_raw/1024/1024:.1f} MB/s" if speed_raw > 0 else ""
            eta = d.get("eta", "")
            eta_str = f"{int(eta)}s" if eta else ""
            jobs[job_id].update({"percent": percent, "speed": speed, "eta": eta_str})

    # Find ffmpeg location
    ffmpeg_locations = [
        r"C:\ffmpeg-8.1.1-essentials_build\bin",
        r"C:\ffmpeg\bin",
        "/usr/bin",
        "/usr/local/bin",
        "/nix/store",
    ]
    ffmpeg_loc = None
    for loc in ffmpeg_locations:
        if os.path.exists(loc):
            ffmpeg_loc = loc
            break
    
    # Also check PATH
    import shutil
    if not ffmpeg_loc and shutil.which("ffmpeg"):
        ffmpeg_loc = os.path.dirname(shutil.which("ffmpeg"))

    ydl_opts = {
        "format": FORMAT_MAP.get(quality, FORMAT_MAP["best"]),
        "outtmpl": output_path,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": ext,
        "progress_hooks": [progress_hook],
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "cookiefile": cookies_file if os.path.exists(cookies_file) else None,
        "ffmpeg_location": ffmpeg_loc,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")
            final_path = None
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(job_id):
                    final_path = os.path.join(DOWNLOAD_DIR, f)
                    ext = f.rsplit(".", 1)[-1]
                    break
            if not final_path:
                raise Exception("Downloaded file not found")
            jobs[job_id].update({
                "status": "done", "percent": 100,
                "filepath": final_path,
                "filename": f"{title}.{ext}",
                "title": title, "speed": "", "eta": ""
            })
            cleanup_file(final_path)

            # Save to history
            user_id = jobs[job_id].get("user_id")
            if user_id:
                with app.app_context():
                    history = DownloadHistory(
                        user_id=user_id,
                        title=title,
                        url=url,
                        quality=quality,
                        filename=f"{title}.{ext}"
                    )
                    db.session.add(history)
                    db.session.commit()

    except Exception as e:
        jobs[job_id].update({"status": "error", "message": str(e)})

# ── Auth Routes ───────────────────────────────────────────────────────────────

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    if not username or not email or not password:
        return jsonify({"error": "All fields are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Username already taken"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 400
    user = User(username=username, email=email, password=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    session["user_id"] = user.id
    session["username"] = user.username
    return jsonify({"message": "Account created!", "username": user.username, "is_admin": user.id == 1})

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    identifier = data.get("identifier", "").strip()
    password = data.get("password", "")
    remember = data.get("remember", False)
    user = User.query.filter(
        (User.username == identifier) | (User.email == identifier)
    ).first()
    if not user or not check_password_hash(user.password, password):
        return jsonify({"error": "Invalid username or password"}), 401
    session.permanent = remember
    session["user_id"] = user.id
    session["username"] = user.username
    return jsonify({"message": "Welcome back!", "username": user.username, "is_admin": user.id == 1})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})

@app.route("/api/me")
def me():
    if "user_id" in session:
        return jsonify({"logged_in": True, "username": session["username"], "is_admin": session["user_id"] == 1})
    return jsonify({"logged_in": False})

# ── Download Routes ───────────────────────────────────────────────────────────

@app.route("/api/download", methods=["POST"])
@login_required
def start_download():
    data = request.get_json()
    urls = data.get("urls", [])
    quality = data.get("quality", "best")
    if not urls:
        return jsonify({"error": "No URLs provided"}), 400
    job_ids = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        job_id = str(uuid.uuid4())
        jobs[job_id] = {"status": "queued", "url": url, "percent": 0, "speed": "", "eta": "", "user_id": session.get("user_id"), "quality": quality}
        t = threading.Thread(target=do_download, args=(job_id, url, quality), daemon=True)
        t.start()
        job_ids.append(job_id)
    return jsonify({"job_ids": job_ids})

@app.route("/api/status/<job_id>")
@login_required
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "error", "message": "Job not found"}), 404
    return jsonify(job)

@app.route("/api/file/<job_id>")
@login_required
def serve_file(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return "File not ready", 404
    filepath = job["filepath"]
    filename = job["filename"]
    if not os.path.exists(filepath):
        return "File expired", 410
    return send_file(filepath, as_attachment=True, download_name=filename)

# ── History Routes ────────────────────────────────────────────────────────────

@app.route("/api/history")
@login_required
def get_history():
    user_id = session["user_id"]
    records = DownloadHistory.query.filter_by(user_id=user_id).order_by(DownloadHistory.downloaded_at.desc()).limit(50).all()
    return jsonify([{
        "id": r.id,
        "title": r.title,
        "url": r.url,
        "quality": r.quality,
        "filename": r.filename,
        "downloaded_at": r.downloaded_at.strftime("%b %d, %Y %I:%M %p")
    } for r in records])

@app.route("/api/history/<int:record_id>", methods=["DELETE"])
@login_required
def delete_history(record_id):
    record = DownloadHistory.query.filter_by(id=record_id, user_id=session["user_id"]).first()
    if not record:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(record)
    db.session.commit()
    return jsonify({"message": "Deleted"})

# ── Admin Routes ──────────────────────────────────────────────────────────────

@app.route("/api/admin/update-cookies", methods=["POST"])
@login_required
def update_cookies():
    if session["user_id"] != 1:
        return jsonify({"error": "Admin only"}), 403
    if "cookies" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["cookies"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    file.save(cookies_path)
    return jsonify({"message": "Cookies updated! YouTube downloads should work now."})

# ── Serve Frontend ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    with open("templates/index.html", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
