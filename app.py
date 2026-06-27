import os
import time
import sqlite3
from datetime import datetime

import cv2
from flask import Flask, Response, jsonify, render_template



PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(PROJECT_ROOT, "static")
CAPTURES_DIR = os.path.join(STATIC_DIR, "captures")
DB_PATH = os.path.join(PROJECT_ROOT, "database.db")


def ensure_dirs() -> None:
    os.makedirs(CAPTURES_DIR, exist_ok=True)


def init_db() -> None:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS detections_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                faces INTEGER NOT NULL,
                detection TEXT NOT NULL,
                confidence REAL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS captured_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                filename TEXT NOT NULL,
                url TEXT NOT NULL,
                faces INTEGER,
                fps REAL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


app = Flask(__name__)

# Load Haarcascade
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# Capture session state (updated per-frame)
cap = cv2.VideoCapture(0)
start_time = time.time()
last_good_frame_ts = 0.0
last_frame_lock = None
last_frame_jpg: bytes | None = None
last_faces = 0
last_fps = 0.0
last_detection = "Stopped"

# Simple throttling for history inserts
HISTORY_INSERT_EVERY_N_FRAMES = 10

frame_counter = 0
last_detection_status_change_ts = 0.0


def _try_reopen_camera():
    global cap
    try:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        cap = cv2.VideoCapture(0)
        # Give it a moment
        time.sleep(0.1)
    except Exception:
        cap = cv2.VideoCapture(0)


def _camera_online() -> bool:
    if cap is None:
        return False
    # Best-effort: isOpened + last_good_frame_ts freshness
    if not cap.isOpened():
        return False
    if time.time() - last_good_frame_ts > 3.0:
        return False
    return True


def compute_fps(prev_t, current_t, fallback=0.0):
    dt = current_t - prev_t
    if dt <= 0:
        return fallback
    return 1.0 / dt


def format_camera_status() -> str:
    return "Online" if _camera_online() else "Offline"


def draw_detections(frame, gray):
    global last_faces
    faces = face_cascade.detectMultiScale(gray, 1.3, 6)
    last_faces = int(len(faces))

    for (x, y, w, h) in faces:
        color = (0, 255, 0)
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 3)
        cv2.putText(
            frame,
            "Face Detected",
            (x, y - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )

    return faces


def process_frame(frame):
    """Process a frame and update globals."""
    global last_frame_jpg, last_fps, last_detection, last_good_frame_ts
    global frame_counter, last_detection_status_change_ts

    # FPS measurement
    if not hasattr(process_frame, "_prev_t"):
        process_frame._prev_t = time.time()
    prev_t = process_frame._prev_t
    current_t = time.time()
    process_frame._prev_t = current_t
    last_fps = compute_fps(prev_t, current_t, fallback=last_fps)

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = draw_detections(frame, gray)

    detection = "Running"
    if len(faces) > 0:
        detection = "Face Detected"
    else:
        detection = "No Face"

    last_detection = detection

    ret, buffer = cv2.imencode(".jpg", frame)
    if ret:
        last_frame_jpg = buffer.tobytes()
        last_good_frame_ts = time.time()

    frame_counter += 1

    # Insert detection history occasionally
    if frame_counter % HISTORY_INSERT_EVERY_N_FRAMES == 0:
        try:
            conn = get_db_conn()
            try:
                conn.execute(
                    "INSERT INTO detections_history (timestamp, faces, detection, confidence) VALUES (?, ?, ?, ?)" ,
                    (now_iso(), int(len(faces)), detection, None),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            # Don’t crash the stream
            pass


def generate_frames():
    global last_detection

    while True:
        try:
            if cap is None or not cap.isOpened():
                _try_reopen_camera()

            success, frame = cap.read()
            if not success:
                last_detection = "Stopped"
                # If camera stopped, break so browser can reconnect on retry.
                time.sleep(0.2)
                continue

            process_frame(frame)

            # Yield last_frame_jpg (already encoded)
            if last_frame_jpg is None:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + last_frame_jpg + b"\r\n"
            )
        except GeneratorExit:
            break
        except Exception:
            # On unexpected errors, attempt reopen
            last_detection = "Stopped"
            _try_reopen_camera()
            time.sleep(0.3)


@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/video")
def video():
    # MJPEG stream
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/stats")
def stats():
    camera = format_camera_status()
    session_seconds = max(0.0, time.time() - start_time)

    # Resolution from last frame is not stored; provide best-effort via last_frame_jpg decode? (skip for perf)
    # If you want exact resolution, we can store it per frame later.
    resolution = "--"

    detection = last_detection
    faces = int(last_faces)
    fps = float(last_fps) if last_fps else 0.0

    # For offline, clamp fps/faces
    if camera != "Online":
        faces = 0
        fps = 0.0
        detection = "Stopped"

    return jsonify(
        {
            "faces": faces,
            "fps": fps,
            "camera": camera,
            "detection": detection,
            "session_seconds": int(session_seconds),
            "resolution": resolution,
        }
    )


@app.route("/capture", methods=["POST"])
def capture():
    global last_frame_jpg

    camera = format_camera_status()
    if camera != "Online" or last_frame_jpg is None:
        return jsonify({"success": False, "error": "Camera offline or no frame available."}), 503

    # Save JPG to static/captures
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}.jpg"
    filepath = os.path.join(CAPTURES_DIR, filename)
    try:
        with open(filepath, "wb") as f:
            f.write(last_frame_jpg)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    url = f"/static/captures/{filename}"

    # Persist capture metadata
    try:
        conn = get_db_conn()
        try:
            conn.execute(
                "INSERT INTO captured_images (timestamp, filename, url, faces, fps) VALUES (?, ?, ?, ?, ?)" ,
                (now_iso(), filename, url, int(last_faces), float(last_fps) if last_fps else None),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

    return jsonify({"success": True, "url": url, "filename": filename})


@app.route("/gallery")
def gallery():
    try:
        conn = get_db_conn()
        try:
            cur = conn.execute(
                "SELECT timestamp, filename, url FROM captured_images ORDER BY id DESC LIMIT 12"
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        images = [
            {
                "timestamp": r["timestamp"],
                "filename": r["filename"],
                "url": r["url"],
            }
            for r in rows
        ]
        return jsonify({"images": images})
    except Exception as e:
        return jsonify({"images": [], "error": str(e)}), 500


@app.route("/history")
def history():
    try:
        conn = get_db_conn()
        try:
            cur = conn.execute(
                "SELECT timestamp, faces, detection FROM detections_history ORDER BY id DESC LIMIT 30"
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        hist = [
            {
                "timestamp": r["timestamp"],
                "faces": int(r["faces"]),
                "detection": r["detection"],
            }
            for r in rows
        ]
        return jsonify({"history": hist})
    except Exception as e:
        return jsonify({"history": [], "error": str(e)}), 500


@app.route("/health")
def health():
    online = format_camera_status()
    return jsonify({"status": "OK", "camera": online})


if __name__ == "__main__":
    init_db()
    # Avoid debug reloader for camera reliability
    app.run(host="0.0.0.0", port=5000, debug=False)

