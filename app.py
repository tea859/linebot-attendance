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
    """(ダッシュボードAPI) リアルタイム在室状況を返す + 自動出席チェック"""
    
    # 1. まず「在室中」の学生リストを取得 (学生ID, 教室名, 入室時刻, 備考)
    # 退出時刻が NULL (None) のものだけを取得
    active_sessions_data = db.session.query(
        在室履歴.学生ID, 教室.教室名, 在室履歴.入室時刻, 在室履歴.備考 
    ).outerjoin(教室, 在室履歴.教室ID == 教室.教室ID)\
     .filter(在室履歴.退室時刻 == None).all()
    
    # (在室中のIDだけSet型にしておく)
    active_student_ids = {s[0] for s in active_sessions_data}

    # --- ▼▼▼ 自動出席チェック機能 ▼▼▼ ---
    # (在室中なのに出席登録がない場合、自動で出席にする便利機能)
    if active_student_ids: 
        try:
            now = datetime.now()
            today_str = f"{now.year}/{now.month}/{now.day}"
            
            # 今が授業中か確認
            plan_row = 授業計画.query.get(today_str)
            if plan_row:
                kiki, yobi_code = plan_row.期, plan_row.授業曜日
                period_row = TimeTable.query.filter(
                    TimeTable.開始時刻 <= now.time(),
                    TimeTable.終了時刻 >= now.time()
                ).first()
                
                if period_row:
                    current_period = period_row.時限
                    yobi_str = YOBI_MAP_REVERSE.get(yobi_code)
                    class_row = 時間割.query.filter_by(
                        学期=str(kiki), 曜日=yobi_str, 時限=current_period
                    ).first()
                    
                    if class_row and class_row.授業ID != 0:
                        class_id = class_row.授業ID
                        today_date = now.date()

                        # 既に記録済みの学生を除外
                        existing_records = db.session.query(出席記録.学生ID).filter(
                            出席記録.学生ID.in_(active_student_ids), 
                            出席記録.授業ID == class_id,
                            出席記録.時限 == current_period,
                            出席記録.出席日付 == today_date
                        ).all()
                        recorded_student_ids = {r[0] for r in existing_records}

                        # 未記録の学生を登録
                        students_to_mark = active_student_ids - recorded_student_ids
                        new_records = []
                        for student_id in students_to_mark:
                            status = 判定(current_period, now) 
                            new_records.append(出席記録(
                                学生ID=student_id, 
                                授業ID=class_id, 
                                出席時刻=now,
                                状態=status, 
                                時限=current_period
                            ))
                        
                        if new_records:
                            db.session.add_all(new_records)
                            db.session.commit()
                            # 必要ならアラートチェック
                            for record in new_records:
                                check_and_send_alert(record.学生ID, record.授業ID)

        except Exception as e:
            db.session.rollback()
            print(f"自動出席エラー: {e}")
    
    all_students = 学生.query.order_by(学生.学生ID).all()
    
    now = datetime.now()
    active_map = {}
    
    # 在室データを辞書に変換
    for sid, room_name, 入室時刻, 備考 in active_sessions_data:
        try:
            # 滞在時間の計算
            滞在秒 = int((now - 入室時刻).total_seconds())
            hh = 滞在秒 // 3600
            mm = (滞在秒 % 3600) // 60
            ss = 滞在秒 % 60
            duration = f"{hh:02}:{mm:02}:{ss:02}"
            
            # ステータス判定 (一時退出かどうか)
            status = "一時退出中" if 備考 == "一時退出中" else "在室"
    
            active_map[sid] = {
                "status": status,
                "room": room_name or '教室不明', 
                "entry": 入室時刻.strftime("%Y-%m-%d %H:%M:%S"), 
                "duration": duration
            }
        except Exception:
             active_map[sid] = {"status": "エラー", "room": "", "entry": "", "duration": ""}

    # 全学生のリストを作成 (出席していない人は「退出」)
    result = []
    for s in all_students:
        if s.学生ID in active_map:
            session_data = active_map[s.学生ID]
            result.append({
                "name": s.学生名, 
                "status": session_data["status"], 
                "room": session_data["room"], 
                "entry": session_data["entry"], 
                "duration": session_data["duration"]
            })
        else:
            result.append({
                "name": s.学生名, "status": "退出",
                "room": "", "entry": "", "duration": ""
            })

    return jsonify({"students": result})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)

