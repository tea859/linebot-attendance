from flask import Flask, render_template, request, jsonify, redirect
import sqlite3
from datetime import datetime
from collections import OrderedDict
from dotenv import load_dotenv
import os

load_dotenv()

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

app = Flask(__name__)

def 判定(時限, 登録時刻):
    conn = sqlite3.connect("zaiseki.db")
    cur = conn.cursor()
    cur.execute("SELECT 開始時刻 FROM TimeTable WHERE 時限 = ?", (時限,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return "未定義"

    try:
        開始 = datetime.strptime(row[0], "%H:%M").replace(
            year=登録時刻.year, month=登録時刻.month, day=登録時刻.day
        )
    except ValueError:
        return "時刻エラー"

    経過 = (登録時刻 - 開始).total_seconds() / 60
    if 経過 <= 0:
        return "出席"
    elif 経過 <= 20:
        return "遅刻"
    else:
        return "欠席"


def update_status(student_id, status, timestamp):
    if status == "在室":
        active_map[student_id] = (room_id, timestamp, "00:00:00")  # 初期滞在時間
    else:
        if student_id in active_map:
            del active_map[student_id]

@app.route("/line_webhook", methods=["POST"])
def line_webhook():
    data = request.get_json()
    for event in data.get("events", []):
        if event["type"] == "message":
            reply_token = event["replyToken"]
            user_message = event["message"]["text"]

            # 応答内容を決定（例：センサー情報を返す）
            if "温度" in user_message or "湿度" in user_message:
                if sensor_data:
                    latest = sensor_data[-1]
                    reply_text = f"現在の教室温度：{latest['temperature']}℃ 湿度：{latest['humidity']}%"
                else:
                    reply_text = "センサー情報がまだありません"
            else:
                reply_text = f"受け取りました：{user_message}"

            send_line_reply(reply_token, reply_text)
    return "OK", 200

def send_line_reply(reply_token, text):
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload)

# センサーデータを一時保存（必要ならDBに変更可能）
sensor_data = []

@app.route("/api/sensor", methods=["POST"])
def receive_sensor():
    data = request.get_json()
    if data:
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "temperature": data.get("temperature"),
            "humidity": data.get("humidity")
        }
        sensor_data.append(entry)
        print("ESP32から受信:", entry)
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Invalid data"}), 400

@app.route("/api/sensor_status")
def api_sensor_status():
    if sensor_data:
        latest = sensor_data[-1]
        return jsonify({
            "timestamp": latest.get("timestamp"),
            "temperature": latest.get("temperature"),
            "humidity": latest.get("humidity")
        })
    else:
        return jsonify({})


@app.route("/attendance", methods=["GET", "POST"])
def attendance():
    conn = sqlite3.connect("zaiseki.db")
    cur = conn.cursor()

    if request.method == "POST":
        学生ID = int(request.form.get("student_id"))
        授業ID = int(request.form.get("class_id"))
        時限 = int(request.form.get("period"))
        登録時刻 = datetime.now()
        状態 = 判定(時限, 登録時刻)

        cur.execute("""
            INSERT INTO 出席記録 (学生ID, 授業ID, 出席時刻, 状態, 時限)
            VALUES (?, ?, ?, ?, ?)
        """, (学生ID, 授業ID, 登録時刻.strftime("%Y-%m-%d %H:%M:%S"), 状態, 時限))

        cur.execute("SELECT 教室ID FROM 授業 WHERE 授業ID = ?", (授業ID,))
        row = cur.fetchone()
        教室ID = row[0] if row and row[0] is not None else 授業ID

        if 状態 in ["出席", "遅刻"]:
            cur.execute("""
                SELECT 1 FROM 在室履歴
                WHERE 学生ID = ? AND 退室時刻 IS NULL
            """, (学生ID,))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO 在室履歴 (学生ID, 教室ID, 入室時刻, 退室時刻)
                    VALUES (?, ?, ?, NULL)
                """, (学生ID, 教室ID, 登録時刻.strftime("%Y-%m-%d %H:%M:%S")))

        conn.commit()

    # 表示用データ取得
    cur.execute("SELECT 学生ID, 学生名 FROM 学生")
    students = cur.fetchall()
    cur.execute("SELECT 授業ID, 授業科目名 FROM 授業")
    classes = cur.fetchall()
    cur.execute("""
        SELECT 学生.学生名, 授業ID, 出席時刻, 状態, 時限
        FROM 出席記録
        JOIN 学生 ON 出席記録.学生ID = 学生.学生ID
        ORDER BY 出席時刻 DESC
    """)
    attendance = cur.fetchall()
    conn.close()

    # 最新のセンサーデータを取得
    latest_sensor = sensor_data[-1] if sensor_data else None

    return render_template("attendance_combined.html",
                           students=students,
                           classes=classes,
                           attendance=attendance,
                           sensor=latest_sensor)

#時間割設定
@app.route("/add_schedule", methods=["GET", "POST"])
def add_schedule():
    conn = sqlite3.connect("zaiseki.db")
    cur = conn.cursor()

    if request.method == "POST":
        授業ID = int(request.form["class_id"])
        曜日 = request.form["day"]
        時限 = int(request.form["period"])

        # ✅ 重複チェックをここに追加！
        cur.execute("""
            SELECT 1 FROM 時間割 WHERE 曜日 = ? AND 時限 = ?
        """, (曜日, 時限))
        if cur.fetchone():
            conn.close()
            return "同じ曜日・時限にすでに授業が登録されています", 400

        # 登録処理
        cur.execute("""
            INSERT INTO 時間割 (授業ID, 曜日, 時限)
            VALUES (?, ?, ?)
        """, (授業ID, 曜日, 時限))
        conn.commit()
        conn.close()
        return redirect("/schedule")

    # GET処理（フォーム表示）
    cur.execute("SELECT 授業ID, 授業科目名 FROM 授業")
    classes = cur.fetchall()
    conn.close()
    return render_template("add_schedule.html", classes=classes)



@app.route("/edit_schedule", methods=["GET", "POST"])
def edit_schedule():
    conn = sqlite3.connect("zaiseki.db")
    cur = conn.cursor()

    if request.method == "POST":
        時間割ID = int(request.form["schedule_id"])
        新曜日 = request.form["day"]
        新時限 = int(request.form["period"])
        cur.execute("""
            UPDATE 時間割 SET 曜日 = ?, 時限 = ? WHERE 時間割ID = ?
        """, (新曜日, 新時限, 時間割ID))
        conn.commit()
        conn.close()
        return redirect("/schedule")

    cur.execute("""
        SELECT 時間割.時間割ID, 授業.授業科目名, 時間割.曜日, 時間割.時限
        FROM 時間割
        JOIN 授業 ON 時間割.授業ID = 授業.授業ID
        ORDER BY 時間割.曜日, 時間割.時限
    """)
    schedules = cur.fetchall()
    conn.close()
    return render_template("edit_schedule.html", schedules=schedules)



@app.route("/entry", methods=["POST"])
def entry_event():
    student_id = request.form["student_id"]
    now = datetime.now()

    # 直前の状態を確認
    last_status = get_last_status(student_id)

    if last_status == "退出":
        # 入室 → 在室に切り替え
        update_status(student_id, "在室", now)
    else:
        # 退出処理
        update_status(student_id, "退出", now)
    
    return "OK"

@app.route("/schedule")
def schedule():
    順序 = ["月", "火", "水", "木", "金"]
    conn = sqlite3.connect("zaiseki.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT 時間割.曜日, 時間割.時限, 授業.授業科目名, 授業.担当教員, 教室.教室名
        FROM 時間割
        JOIN 授業 ON 時間割.授業ID = 授業.授業ID
        LEFT JOIN 教室 ON 授業.教室ID = 教室.教室ID
        ORDER BY 時間割.曜日, 時間割.時限
    """)
    rows = cur.fetchall()
    conn.close()

    # ✅ 曜日ごとにグループ化
    grouped = {}
    for row in rows:
        曜日 = row[0]
        if 曜日 not in grouped:
            grouped[曜日] = []
        grouped[曜日].append(row[1:])  # 時限以降の情報だけ格納
    grouped_ordered = OrderedDict()
    for y in 順序:
        grouped_ordered[y] = grouped.get(y, [])  # データがなくても空リストで枠を作る
    # ✅ grouped をテンプレートに渡す
    return render_template("schedule.html", grouped=grouped_ordered)

@app.route("/api/status")
def api_status():
    conn = sqlite3.connect("zaiseki.db")
    cur = conn.cursor()

    cur.execute("SELECT 学生ID, 学生名 FROM 学生")
    all_students = cur.fetchall()

    cur.execute("""
        SELECT 学生ID, 教室.教室名, 入室時刻
        FROM 在室履歴
        JOIN 教室 ON 在室履歴.教室ID = 教室.教室ID
        WHERE 退室時刻 IS NULL
    """)
    active_rows = cur.fetchall()

    now = datetime.now()
    active_map = {}
    for sid, room_name, 入室時刻 in active_rows:
        入室 = datetime.strptime(入室時刻, "%Y-%m-%d %H:%M:%S")
        滞在秒 = int((now - 入室).total_seconds())
        hh = 滞在秒 // 3600
        mm = (滞在秒 % 3600) // 60
        ss = 滞在秒 % 60
        duration = f"{hh:02}:{mm:02}:{ss:02}"
        active_map[sid] = (room_name, 入室時刻, duration)

    result = []
    for sid, name in all_students:
        if sid in active_map:
            room, time, dur = active_map[sid]
            result.append({
                "name": name, "status": "在室",
                "room": room, "entry": time, "duration": dur
            })
        else:
            result.append({
                "name": name, "status": "退出",
                "room": "", "entry": "", "duration": ""
            })

    conn.close()
    return jsonify({"students": result})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
