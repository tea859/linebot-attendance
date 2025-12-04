import os
import requests
import google.generativeai as genai
import base64 # ★追加
from datetime import datetime, timedelta, time, date
from sqlalchemy import text
from threading import Thread # ★追加: これがないとメール送信でエラーになります
from linebot import LineBotApi
from linebot.models import (
    TextSendMessage, FlexSendMessage, BubbleContainer, BoxComponent, 
    TextComponent, SeparatorComponent, ButtonComponent, URIAction
)
from .extensions import db, mail
from .models import 学生, 授業, 時間割, 授業計画, 出席記録, 在室履歴, TimeTable, ReportRecord, 日別時間割, LineUser, User

# ★追加: 画像保存先フォルダの設定
UPLOAD_FOLDER = 'uploaded_images'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# 定数や変数の定義
YOBI_MAP = {'月': 1, '火': 2, '水': 3, '木': 4, '金': 5, '土': 6, '日': 0}
YOBI_MAP_REVERSE = {v: k for k, v in YOBI_MAP.items()}
TEMP_EXIT_STATUS = "一時退出中"
sensor_data = [] 
auth_commands = {}

# Geminiの設定
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')
else:
    gemini_model = None

print("----- AVAILABLE MODELS START -----")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"Model Name: {m.name}")
except Exception as e:
    print(f"Error listing models: {e}")
print("----- AVAILABLE MODELS END -----")

admin_user_db = {
    "1": User("1", "admin", os.environ.get('ADMIN_PASSWORD'))
}

# app/services.py に追加（または置き換え）
import json

def parse_message_with_ai(text):
    """
    自由記述のメッセージを解析し、構造化データとして返す
    戻り値: {
        "is_report": True/False,  # 届出かどうか
        "report_type": "遅刻" or "欠席" or None,
        "category": "交通機関" etc,
        "reason_summary": "電車遅延" etc,
        "reply_text": "AIからの返信メッセージ"
    }
    """
    if not gemini_model:
        return None

    prompt = f"""
    あなたは学校の勤怠管理システムのAIです。
    学生から送られてきたメッセージを解析し、JSON形式で結果を返してください。

    【ルール】
    1. メッセージが「遅刻」や「欠席」に関する報告であれば、`is_report`をtrueにしてください。
       - 「遅れます」「休みます」「行けません」「寝坊した」などは報告です。
       - 「こんにちは」「ありがとう」「時間割教えて」などは報告ではありません（false）。
    2. 報告の場合、`report_type`は"遅刻"または"欠席"のどちらかに分類してください。
    3. `category`は [体調不良, 交通機関, 寝坊, 就活, その他] から選んでください。
    4. `reason_summary`は理由を5文字以内で要約してください。
    5. `reply_text`には、学生への労いや了解の返信メッセージ（20文字以内・敬語）を作成してください。

    【入力メッセージ】
    {text}

    【出力フォーマット(JSONのみ)】
    {{
        "is_report": boolean,
        "report_type": "遅刻" or "欠席" or null,
        "category": "文字列",
        "reason_summary": "文字列",
        "reply_text": "文字列"
    }}
    """

    try:
        response = gemini_model.generate_content(prompt)
        cleaned_text = response.text.strip()
        # JSONの前後に ```json ... ``` がつく場合があるので除去
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text.split("\n", 1)[1]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text.rsplit("\n", 1)[0]
        
        return json.loads(cleaned_text)
    except Exception as e:
        print(f"Gemini Parse Error: {e}")
        return None
def save_image(base64_data, student_id):
    try:
        # データURLスキームを取り除く
        if "base64," in base64_data:
            header, encoded = base64_data.split(",", 1)
        else:
            encoded = base64_data
            
        encoded = encoded.strip()
        data = base64.b64decode(encoded)
        
        # ファイル名: YYYYMMDD_HHMMSS_学生ID.jpg
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{student_id}.jpg"
        filepath = os.path.join(UPLOAD_FOLDER, filename) # ★ここでUPLOAD_FOLDERを使います
        
        with open(filepath, "wb") as f:
            f.write(data)
        return filename
    except Exception as e:
        print(f"画像保存エラー: {e}")
        return None

def send_gas_background(url, payload):
    """裏側（バックグラウンド）でGASに送信する関数"""
    try:
        requests.post(url, json=payload)
        print(f"✅ [Background] メール送信リクエスト完了: {payload.get('to')}")
    except Exception as e:
        print(f"❌ [Background] 送信エラー: {e}")

#AI応答
def ask_ai_about_schedule(user_question, student_name):
    if not gemini_model:
        return "⚠️ AI機能の準備ができていません（APIキー設定待ち）"

    today = datetime.now().date()
    one_week_later = today + timedelta(days=7)
    
    sql = text("""
        SELECT 
            P."日付", 
            P."授業曜日", 
            P."備考" as 日の備考,
            T."時限",
            S."授業科目名",
            S."担当教員",
            T."備考" as 授業備考
        FROM "授業計画" P
        LEFT JOIN "時間割" T ON CAST(P."期" AS VARCHAR) = T."学期" AND 
             (CASE P."授業曜日" 
                 WHEN 1 THEN '月' WHEN 2 THEN '火' WHEN 3 THEN '水' 
                 WHEN 4 THEN '木' WHEN 5 THEN '金' END) = T."曜日"
        LEFT JOIN "授業" S ON T."授業ID" = S."授業ID"
        WHERE TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD') BETWEEN :start AND :end
        ORDER BY P."日付", T."時限"
    """)
    
    try:
        rows = db.session.execute(sql, {"start": today, "end": one_week_later}).fetchall()
    except Exception as e:
        print(f"DB Error: {e}")
        return f"データ取得エラーが発生しました: {e}"

    schedule_text = ""
    current_date = ""
    
    if not rows:
        schedule_text = "（期間内の授業データはありません）"
    
    for row in rows:
        date_str = row[0]
        if current_date != date_str:
            schedule_text += f"\n■ {date_str} の予定:\n"
            current_date = date_str
            if row[2]: schedule_text += f"  (特記事項: {row[2]})\n"
        
        if row[3]: 
            subject = row[4] or "空き/不明"
            teacher = f"({row[5]})" if row[5] else ""
            memo = f"※{row[6]}" if row[6] else ""
            schedule_text += f"  - {row[3]}限: {subject} {teacher} {memo}\n"

    prompt = f"""
    あなたは学校の親切な「授業コンシェルジュ」です。
    学生（{student_name}さん）からの質問に、以下の「週間スケジュール」をもとにして答えてください。
    
    【ルール】
    - スケジュールに載っていないことは「情報がありません」と正直に答えること。
    - 学生に親しみやすく、かつ丁寧な敬語で話しかけること。
    - 必要に応じて絵文字を使って。
    - 今日の日付は {today} です。
    
    【週間スケジュール情報】
    {schedule_text}
    
    【学生の質問】
    {user_question}
    """

    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"AI Error: {e}")
        return "申し訳ありません。AIの応答に失敗しました。"

def analyze_student_habits(student_id):
    if not gemini_model:
        return "⚠️ AI機能が有効になっていません。"

    student = 学生.query.get(student_id)
    if not student:
        return "学生データが見つかりません。"

    one_month_ago = datetime.now() - timedelta(days=30)
    
    records = db.session.query(出席記録, 授業.授業科目名)\
        .join(授業, 出席記録.授業ID == 授業.授業ID)\
        .filter(出席記録.学生ID == student_id, 出席記録.出席時刻 >= one_month_ago)\
        .order_by(出席記録.出席時刻).all()

    if not records:
        return "直近の出席データがないため、分析できませんでした。"

    history_text = ""
    late_count = 0
    
    for r, subject_name in records:
        date_str = r.出席時刻.strftime("%m/%d(%a)")
        time_str = r.出席時刻.strftime("%H:%M")
        history_text += f"- {date_str} {time_str}: {subject_name} ({r.状態})\n"
        if r.状態 == "遅刻":
            late_count += 1

    prompt = f"""
    あなたは学校の親切な先生（AIアドバイザー）です。
    学生（{student.学生名}さん）の直近30日間の出席記録を分析して、優しくアドバイスしてください。

    【分析のポイント】
    1. 遅刻が多い曜日や時間帯の傾向はあるか？（なければ「順調です」と褒める）
    2. 特定の授業で欠席や遅刻が続いていないか？
    3. 全体的にどのような生活リズムに見えるか推測する。
    4. 最後はポジティブな励ましの言葉で締めくくる。
    5. 150文字程度で簡潔にまとめる。

    【出席データ】
    {history_text}
    """

    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"AI Analyze Error: {e}")
        return "申し訳ありません。AIの分析に失敗しました。"

def check_and_send_alert(student_id, subject_id):
    print(f"🔍 [DEBUG] アラート判定開始: 学生ID={student_id}, 授業ID={subject_id}")

    try:
        student = 学生.query.get(student_id)
        subject = 授業.query.get(subject_id)
        
        if not student or not subject:
            return

        current_kiki = get_current_kiki()
        kiki_int = int(current_kiki)
        
        sql_days = text('SELECT "曜日", COUNT("時限") FROM "時間割" WHERE "授業ID"=:sid AND "学期"=:kiki GROUP BY "曜日"')
        schedule_data = db.session.execute(sql_days, {"sid": subject_id, "kiki": current_kiki}).fetchall()
        
        total_so_far = 0
        for day_name, count in schedule_data:
            day_code = YOBI_MAP.get(day_name)
            if day_code is not None:
                sql_plan = text('SELECT COUNT(*) FROM "授業計画" WHERE "期"=:kiki AND "授業曜日"=:code AND TO_DATE(REPLACE("日付", \'/\', \'-\'), \'YYYY-MM-DD\') <= CURRENT_DATE')
                days_count = db.session.execute(sql_plan, {"kiki": kiki_int, "code": day_code}).scalar()
                total_so_far += (days_count * count)

        if total_so_far == 0: return

        sql_attend = text('SELECT COUNT(*) FROM "出席記録" WHERE "学生ID"=:sid AND "授業ID"=:subid AND "状態" IN (\'出席\', \'遅刻\', \'公欠\')')
        attended_count = db.session.execute(sql_attend, {"sid": student_id, "subid": subject_id}).scalar()

        rate = round((attended_count / total_so_far) * 100, 1)

        if rate < 80:
            print(f"[DEBUG] 出席率 {rate}% (80%未満) なので通知を送ります")
            
            msg_subject = f"【出席率注意】{student.学生名}さん - {subject.授業科目名}"
            msg_body = (
                f"出席管理システムからの自動通知\n"
                f"--------------------------------\n"
                f"学生: {student.学生名}\n"
                f"授業: {subject.授業科目名}\n"
                f"出席率: {rate}% ({attended_count}/{total_so_far})\n"
                f"--------------------------------"
            )
            
            recipients = [os.environ.get('MAIL_USERNAME')]
            if student.parent_email:
                recipients.append(student.parent_email)
            
            gas_url = os.environ.get('GAS_API_URL')
            gas_token = os.environ.get('GAS_AUTH_TOKEN')
            
            if gas_url and gas_token:
                payload = {
                    "to": ",".join(recipients),
                    "subject": msg_subject,
                    "body": msg_body,
                    "auth_token": gas_token
                }
                
                # ★修正: ここでThreadを使うので、インポートが必要です
                thread = Thread(target=send_gas_background, args=(gas_url, payload))
                thread.start()
                
                print("✅ [INFO] メール送信処理をバックグラウンドで開始しました")
            else:
                print("⚠️ [ERROR] GAS_API_URL または GAS_AUTH_TOKEN が設定されていません")

    except Exception as e:
        print(f" [ERROR] アラート処理エラー: {e}")

def get_current_kiki():
    now = datetime.now()
    today_str = f"{now.year}/{now.month}/{now.day}"
    result = 授業計画.query.filter_by(日付=today_str).first()
    return str(result.期) if result else "1"

def get_schedule_for_line(target_date):
    date_str_db = target_date.strftime("%Y/%m/%d")
    date_str_disp = target_date.strftime("%m/%d")
    
    plan_row = 授業計画.query.get(date_str_db)
    
    python_weekday = target_date.weekday()
    yobi_str = YOBI_MAP_REVERSE.get((python_weekday + 1) % 7)

    if plan_row:
        kiki = str(plan_row.期)
        master_yobi_code = plan_row.授業曜日 
        master_yobi_str = YOBI_MAP_REVERSE.get(master_yobi_code)
    else:
        kiki = get_current_kiki()
        master_yobi_str = yobi_str
        
    if not master_yobi_str or master_yobi_str == '日' or (plan_row and plan_row.授業曜日 == 0):
        return f"📅 {date_str_disp} ({yobi_str}):\nお休み（休校日）です💤"

    master_rows = db.session.query(
        時間割, 授業.授業科目名, 授業.担当教員
    ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
     .filter(時間割.学期 == kiki, 時間割.曜日 == master_yobi_str)\
     .all()

    final_schedule = {}
    for row in master_rows:
        timetable, subj_name, teacher = row
        name = subj_name if subj_name else "授業なし"
        display = timetable.備考 if timetable.時限 == 5 and timetable.備考 else name
        
        final_schedule[timetable.時限] = {
            "name": display,
            "teacher": teacher,
            "is_exception": False
        }

    exceptions = db.session.query(
        日別時間割, 授業.授業科目名, 授業.担当教員
    ).outerjoin(授業, 日別時間割.授業ID == 授業.授業ID)\
     .filter(日別時間割.日付 == date_str_db)\
     .all()
     
    for row in exceptions:
        exc, subj_name, teacher = row
        name = subj_name if subj_name else "授業なし"
        display = exc.備考 if (exc.時限 == 5 and exc.備考) else name
        if not exc.授業ID and not exc.備考:
            display = "【休講/空き】"
            name = "空欄"
        
        final_schedule[exc.時限] = {
            "name": display,
            "teacher": teacher,
            "is_exception": True
        }

    body_contents = []
    
    body_contents.append(TextComponent(
        text=f"📅 {date_str_disp} ({yobi_str})",
        weight="bold", size="xl", color="#333333"
    ))
    body_contents.append(TextComponent(
        text=f"第{kiki}期 の時間割",
        size="xs", color="#aaaaaa", margin="sm"
    ))
    
    if plan_row and plan_row.備考:
        body_contents.append(TextComponent(
            text=f"※ {plan_row.備考}",
            size="sm", color="#ff5555", margin="md", wrap=True
        ))
        
    body_contents.append(SeparatorComponent(margin="lg"))

    has_class = False
    for period in range(1, 6):
        slot = final_schedule.get(period)
        
        if slot:
            has_class = True
            time_row = TimeTable.query.get(period)
            time_str = f"{time_row.開始時刻.strftime('%H:%M')}-" if time_row else ""
            
            name_color = "#d97706" if slot["is_exception"] else "#333333"
            bg_color = "#fffbeb" if slot["is_exception"] else "#ffffff"

            period_contents = [
                BoxComponent(
                    layout="horizontal",
                    contents=[
                        TextComponent(
                            text=f"{period}限",
                            weight="bold", color="#1E90FF", size="sm", flex=1
                        ),
                        TextComponent(
                            text=f"{time_str}",
                            size="xs", color="#aaaaaa", flex=0, align="end"
                        )
                    ]
                ),
                TextComponent(
                    text=slot["name"],
                    weight="bold", size="md", color=name_color, wrap=True, margin="sm"
                )
            ]
            
            if slot["teacher"]:
                period_contents.append(TextComponent(
                    text=f"👨‍🏫 {slot['teacher']}",
                    size="xs", color="#666666", margin="xs"
                ))
            
            if slot["is_exception"]:
                 period_contents.append(TextComponent(
                    text="※変更あり",
                    size="xxs", color="#d97706", margin="xs", weight="bold"
                ))

            period_box = BoxComponent(
                layout="vertical",
                margin="md",
                paddingAll="md",
                backgroundColor=bg_color,
                cornerRadius="md",
                contents=period_contents
            )
            body_contents.append(period_box)
    
    if not has_class:
        body_contents.append(TextComponent(
            text="予定されている授業はありません",
            margin="lg", color="#999999", align="center"
        ))

    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=body_contents
        )
    )
    return bubble
    
def get_attendance_summary_for_line(line_user_id):
    student_id = get_student_id_from_line_user(line_user_id)
    if student_id is None:
        return "⚠️ あなたの学生IDが登録されていません。\n「登録:学生ID」の形式で一度登録してください。"
    
    student = 学生.query.get(student_id)
    selected_kiki = get_current_kiki()
    kiki_int = int(selected_kiki)

    sql_enrolled = text("""
        SELECT DISTINCT S."授業科目名", S."授業ID"
        FROM "時間割" T
        JOIN "授業" S ON T."授業ID" = S."授業ID"
        WHERE T."学期" = :kiki AND T."授業ID" != 0 
        ORDER BY S."授業科目名"
    """)
    enrolled_subjects = db.session.execute(sql_enrolled, {"kiki": selected_kiki}).fetchall()

    if not enrolled_subjects:
        return f"📅 第{selected_kiki}期: \n履修中の授業データが見つかりませんでした。"

    report_data = []
    
    for subject_name, subject_id in enrolled_subjects:
        sql_schedule = text('SELECT T."曜日", COUNT(T."時限") FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."曜日"')
        schedule_data = db.session.execute(sql_schedule, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
        
        total_classes_so_far = 0
        for day_of_week, periods_per_day in schedule_data:
            day_code = YOBI_MAP.get(day_of_week)
            if day_code is not None:
                sql_days_so_far = text("""
                    SELECT COUNT("日付") FROM "授業計画" 
                    WHERE "期" = :kiki AND "授業曜日" = :code 
                    AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                """)
                total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": kiki_int, "code": day_code}).scalar()
                total_classes_so_far += total_days_so_far * periods_per_day
        
        sql_records = text("""
            SELECT R."状態", COUNT(R."状態")
            FROM "出席記録" R
            JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
            WHERE R."学生ID" = :sid 
              AND P."期" = :kiki_int
              AND R."授業ID" = :subject_id
            GROUP BY R."状態"
        """)
        records_count = dict(db.session.execute(sql_records, {
            "sid": student_id, 
            "kiki_int": kiki_int,
            "subject_id": subject_id
        }).fetchall())

        attendance_count = records_count.get('出席', 0)
        tardy_count = records_count.get('遅刻', 0)
        absent_count_db = records_count.get('欠席', 0)

        attendance_rate = 0.0
        if total_classes_so_far > 0:
            attendance_rate = round((attendance_count / total_classes_so_far) * 100, 1)
        
        total_recorded = attendance_count + tardy_count + absent_count_db
        unrecorded_count = total_classes_so_far - total_recorded
        if unrecorded_count < 0: unrecorded_count = 0
        
        total_absent = absent_count_db + unrecorded_count

        report_data.append({
            "subject_name": subject_name,
            "rate": attendance_rate,
            "total_so_far": total_classes_so_far,
            "attendance": attendance_count,
            "tardy": tardy_count,
            "absent": total_absent
        })

    body_contents = []
    
    body_contents.append(TextComponent(
        text=f"{student.学生名} さん",
        weight="bold", size="lg", margin="md"
    ))
    body_contents.append(TextComponent(
        text=f"第{selected_kiki}期 出席サマリー (授業ごと)",
        size="sm", color="#666666", margin="sm", wrap=True
    ))

    for item in report_data:
        body_contents.append(SeparatorComponent(margin="lg"))
        
        subject_box = BoxComponent(
            layout="vertical",
            margin="lg",
            spacing="sm",
            contents=[
                TextComponent(
                    text=f"■ {item['subject_name']}",
                    weight="bold",
                    size="md",
                    wrap=True
                ),
                TextComponent(
                    text=f"{item['rate']}%",
                    weight="bold",
                    size="lg",
                    color="#1E90FF",
                    margin="sm"
                ),
                TextComponent(
                    text=f"出席 {item['attendance']} / 総計 {item['total_so_far']}コマ",
                    size="sm",
                    color="#666666",
                    wrap=True
                ),
                TextComponent(
                    text=f"(遅刻 {item['tardy']}, 欠席 {item['absent']})",
                    size="sm",
                    color="#AAAAAA",
                    wrap=True,
                    margin="sm"
                )
            ]
        )
        body_contents.append(subject_box)

    # 開発中はローカル、本番は環境変数から取得
    BASE_URL = os.environ.get('BASE_URL', 'http://127.0.0.1:5000') 
    # Flaskのurl_forを使いたいところですが、context外で呼ぶとエラーになるのでハードコードか、
    # request context内で呼ぶ必要があります。ここではシンプルに文字列結合で
    portal_url = f"{BASE_URL}/student_login"

    footer_box = BoxComponent(
        layout="vertical",
        spacing="sm",
        contents=[
            SeparatorComponent(),
            ButtonComponent(
                style="link",
                height="sm",
                action=URIAction(label="Webポータルで詳細を見る", uri=portal_url)
            )
        ]
    )
    
    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=body_contents
        ),
        footer=footer_box
    )
    
    return bubble

def process_exit_record(line_user_id):
    student_id = get_student_id_from_line_user(line_user_id) 
    if student_id is None:
        return "⚠️ あなたの学生IDが登録されていません。\n「登録:学生ID」の形式で一度登録してください。"

    existing_session = 在室履歴.query.filter_by(学生ID=student_id, 退室時刻=None).first()
    
    if existing_session:
        existing_session.退室時刻 = datetime.now()
        db.session.commit()
        student = 学生.query.get(student_id) 
        return f"🚪 {student.学生名}さんの最終退室時刻を記録しました。またのご利用をお待ちしております！"
    else:
        return "⚠️ 現在、入室記録が見つかりませんでした。"

def process_temporary_exit(line_user_id):
    student_id = get_student_id_from_line_user(line_user_id) 
    if student_id is None:
        return "⚠️ 学生IDが登録されていません。「登録:学生ID」で登録してください。"

    existing_session = 在室履歴.query.filter_by(学生ID=student_id, 退室時刻=None).first()
    
    if existing_session and existing_session.備考 == TEMP_EXIT_STATUS:
        return "⚠️ すでに一時退出中です。戻られたら「戻りました」をタップしてください。"
    
    if existing_session:
        existing_session.備考 = TEMP_EXIT_STATUS
        db.session.commit()
        return "🚶 一時退出を記録しました。戻られましたら「戻りました」をタップしてください。"
    else:
        return "⚠️ 入室記録が見つかりません。カメラでの入室認証が必要です。"

def process_return_from_exit(line_user_id):
    student_id = get_student_id_from_line_user(line_user_id) 
    if student_id is None:
        return "⚠️ 学生IDが登録されていません。「登録:学生ID」で登録してください。"

    existing_session = 在室履歴.query.filter_by(
        学生ID=student_id, 退室時刻=None, 備考=TEMP_EXIT_STATUS
    ).first()
    
    if existing_session:
        existing_session.備考 = None
        db.session.commit()
        return "🎉 おかえりなさい！在室記録を再開します。"
    else:
        return "⚠️ 一時退出中の記録が見つかりません。"

def 判定(時限, 登録時刻):
    row = TimeTable.query.get(時限)
    if not row: return "未定義"
    
    開始 = datetime.combine(登録時刻.date(), row.開始時刻)
    経過 = (登録時刻 - 開始).total_seconds() / 60
    
    if 経過 <= 0: return "出席"
    elif 経過 <= 20: return "遅刻"
    else: return "欠席"

def get_student_id_from_line_user(line_user_id):
    mapping = LineUser.query.filter_by(line_user_id=line_user_id).first()
    return mapping.student_id if mapping else None
