from flask import Blueprint, render_template, request, redirect, url_for, flash, get_flashed_messages, make_response
from flask_login import login_required, current_user, login_user, logout_user
from sqlalchemy import text, func
from datetime import datetime, date, timedelta, time
import calendar
import csv
import io
from collections import OrderedDict
from urllib.parse import quote
from ..extensions import db
from ..models import 学生, 授業, 時間割, 授業計画, 出席記録, 在室履歴, TimeTable, ReportRecord, 日別時間割, 教室
from ..services import get_current_kiki, YOBI_MAP, YOBI_MAP_REVERSE, sensor_data, admin_user_db # 必要なものをインポート

# Blueprint作成
web_bp = Blueprint('web', __name__)

# ★ここが重要: @app.route ではなく @web_bp.route に書き換えて貼り付け！

@web_bp.route("/login", methods=["GET", "POST"])
def login():
    """ログインページと認証処理"""
    if current_user.is_authenticated:
        return redirect(url_for('web.index'))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        user_to_login = None
        for user_id, user_obj in admin_user_db.items():
            if user_obj.username == username:
                user_to_login = user_obj
                break
        
        if user_to_login and user_to_login.password == password:
            login_user(user_to_login) # ログイン状態をセッションに保存
            flash("✅ ログインしました。", "success")
            return redirect(url_for('web.index'))
        else:
            flash("❌ ユーザー名またはパスワードが間違っています。", "error")
            return redirect(url_for('web.login'))
    
    # GETリクエストの場合はログインページを表示
    return render_template("login.html")

# ----------------------------------------------------------------------
# 7.APIルート
# ----------------------------------------------------------------------

@web_bp.route("/logout")
@login_required  # ログインしている人だけがログアウトできる
def logout():
    """ログアウト処理"""
    logout_user() # セッションからユーザー情報を削除
    flash("✅ ログアウトしました。", "info")
    return redirect(url_for('web.index'))

# --- 7. メインページ (ダッシュボード) ---
@web_bp.route("/")
@login_required
def index():
    if current_user.get_id().startswith('student-'):
        flash("管理者権限がありません。", "error")
        return redirect(url_for('web.my_portal'))
    students = 学生.query.order_by(学生.学生ID).all()
    
    message = None
    category = None
    messages = get_flashed_messages(with_categories=True)
    if messages:
        category, message = messages[0]

    unresolved_alerts_count = 0
    if current_user.is_authenticated: # ログインしているユーザーのみ件数を取得
        try:
            unresolved_alerts_count = db.session.query(ReportRecord.record_id).filter(ReportRecord.is_resolved == False).count()
        except Exception as e:
            print(f"アラート件数のカウントに失敗: {e}")
            # エラーが発生してもページは表示させる
            unresolved_alerts_count = 0        
    links = [
        {"url": "/attendance?kiki=1", "name": "出席登録 / 全体記録", "badge": 0},
        {"url": "/schedule", "name": "時間割表示", "badge": 0},
        
        # ▼▼▼ URLを /edit_daily_schedule に変更 ▼▼▼
        {"url": "/edit_daily_schedule", "name": "時間割変更 (日別)", "badge": 0},
        
        {"url": "/manage_students", "name": "学生管理", "badge": 0},
        {"url": "/manage_subjects", "name": "授業科目管理", "badge": 0},
        {"url": "/alerts", "name": "連絡・掲示板", "badge": unresolved_alerts_count}, 
    ]
    
    return render_template("index.html", 
                           links=links, 
                           students=[(s.学生ID, s.学生名) for s in students], # テンプレートが (id, name) を期待
                           message=message,
                           category=category,
                           unresolved_alerts_count=unresolved_alerts_count)

# --- 9. 管理ページ (SQLAlchemy版) ---

@web_bp.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    """(手動登録) 出席登録フォームと全体記録 (ORM版)"""
    selected_kiki = request.args.get("kiki", get_current_kiki())

    if request.method == "POST":
        try:
            学生ID = int(request.form.get("student_id"))
            授業ID = int(request.form.get("class_id"))
            時限 = int(request.form.get("period"))
            登録時刻 = datetime.now()
            状態 = 判定(時限, 登録時刻)

            try:
                new_attendance = 出席記録(
                    学生ID=学生ID,
                    授業ID=授業ID,
                    出席時刻=登録時刻,
                    状態=状態,
                    時限=時限
                )
                check_and_send_alert(学生ID, 授業ID)
                db.session.add(new_attendance)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                print(f"INFO (Manual): 出席記録は登録済み (UniqueConstraint)。在室履歴をチェックします。")

            # (在室履歴ロジック - 統一版)
            room = 授業.query.get(授業ID)
            教室ID = room.教室ID if room and room.教室ID is not None else 999 

            existing_session = 在室履歴.query.filter_by(学生ID=学生ID, 退室時刻=None).first()
            if not existing_session:
                new_session = 在室履歴(学生ID=学生ID, 教室ID=教室ID, 入室時刻=登録時刻, 退室時刻=None)
                db.session.add(new_session)
                db.session.commit()
        
        except Exception as e:
            db.session.rollback()
            print(f"FATAL ERROR (Manual Attendance): {e}")
        
        return redirect(f"/attendance?kiki={selected_kiki}")

    # --- GETリクエスト時の表示データ取得 ---
    students = 学生.query.order_by(学生.学生ID).all()
    
    # 学期で絞り込んだ授業リスト
    classes = db.session.query(授業.授業ID, 授業.授業科目名)\
                .join(時間割, 授業.授業ID == 時間割.授業ID)\
                .filter(時間割.学期 == selected_kiki, 授業.授業ID != 0)\
                .distinct().order_by(授業.授業ID).all()
    
    # 学期で絞り込んだ出席記録 (生のSQL + text() を使用)
    sql_query = text("""
        SELECT ST."学生名", R."授業ID", R."出席時刻", R."状態", R."時限"
        FROM "出席記録" R
        JOIN "学生" ST ON R."学生ID" = ST."学生ID"
        WHERE R."授業ID" IN (
            SELECT DISTINCT T."授業ID" FROM "時間割" T WHERE T."学期" = :kiki
        )
        ORDER BY R."出席時刻" DESC
    """)
    attendance_records = db.session.execute(sql_query, {"kiki": selected_kiki}).fetchall()

    latest_sensor = sensor_data[-1] if sensor_data else None

    return render_template("attendance_combined.html",
                           students=[(s.学生ID, s.学生名) for s in students],
                           classes=classes, 
                           attendance=attendance_records, 
                           sensor=latest_sensor,
                           selected_kiki=selected_kiki, 
                           kikis=["1", "2", "3", "4"])

# app.py

@web_bp.route("/schedule")
@login_required
def schedule():
    """(閲覧) 週間リアルタイム時間割表示 (修正版)"""
    
    # 1. 表示する週の基準日を決定
    date_str = request.args.get('date')
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            target_date = datetime.now().date()
    else:
        target_date = datetime.now().date()

    # 2. その週の月曜日〜金曜日を計算
    monday = target_date - timedelta(days=target_date.weekday())
    week_dates = [monday + timedelta(days=i) for i in range(5)] # 月〜金
    
    # ナビゲーション用
    prev_week = (monday - timedelta(days=7)).strftime('%Y-%m-%d')
    next_week = (monday + timedelta(days=7)).strftime('%Y-%m-%d')

    # 3. グリッドの初期化
    順序 = ["月", "火", "水", "木", "金"]
    時限一覧 = list(range(1, 6))
    schedule_grid = OrderedDict()

    for j in 時限一覧:
        schedule_grid[j] = OrderedDict()
        for idx, yobi in enumerate(順序):
            this_date = week_dates[idx]
            schedule_grid[j][yobi] = {
                "subject": "空欄", "teacher": "", "room": "", 
                "display_text": "", # 初期値は空文字に
                "is_empty": True,
                "is_exception": False,
                "date": this_date,
                "date_str": this_date.strftime('%Y/%m/%d'),
                "date_query": this_date.strftime('%Y-%m-%d'),
                "status_label": ""
            }

    # 4. データを埋める
    for col_idx, date_obj in enumerate(week_dates):
        date_db_str = date_obj.strftime('%Y/%m/%d')
        physical_yobi_str = 順序[col_idx] 

        # A. 授業計画を取得
        plan = 授業計画.query.get(date_db_str)
        
        # ▼▼▼ 修正ポイント: 計画がない、または休日設定の場合はスキップ（空欄のまま） ▼▼▼
        if not plan or plan.授業曜日 == 0:
            # 休日等の表示用テキストを入れるならここで
            if plan and plan.備考:
                 # 備考があれば全コマに表示してもいいが、うるさいのでスキップ
                 pass
            continue
        # ▲▲▲ 修正ここまで ▲▲▲

        # 計画がある場合のみ以下を実行
        target_kiki = str(plan.期)
        target_yobi_code = plan.授業曜日
        target_yobi_str = YOBI_MAP_REVERSE.get(target_yobi_code)
        
        status_label = ""
        if physical_yobi_str != target_yobi_str:
            status_label = f"※{target_yobi_str}曜授業"
        elif plan.備考:
            status_label = f"※{plan.備考}"

        # B. マスター時間割を取得
        master_rows = db.session.query(
            時間割, 授業.授業科目名, 授業.担当教員, 教室.教室名
        ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
         .outerjoin(教室, 授業.教室ID == 教室.教室ID)\
         .filter(時間割.学期 == target_kiki, 時間割.曜日 == target_yobi_str)\
         .all()

        for row in master_rows:
            timetable, subj_name, teacher, room_name = row
            if timetable.時限 in 時限一覧:
                cell = schedule_grid[timetable.時限][physical_yobi_str]
                
                name = subj_name if subj_name else "授業なし"
                display = timetable.備考 if timetable.時限 == 5 and timetable.備考 else name
                
                cell.update({
                    "subject": name,
                    "teacher": teacher if teacher else "",
                    "room": room_name if room_name else "",
                    "display_text": display,
                    "is_empty": (not timetable.授業ID and not timetable.備考),
                    "status_label": status_label
                })

        # C. 日別例外を上書き
        exceptions = db.session.query(
            日別時間割, 授業.授業科目名, 授業.担当教員, 教室.教室名
        ).outerjoin(授業, 日別時間割.授業ID == 授業.授業ID)\
         .outerjoin(教室, 日別時間割.教室ID == 教室.教室ID)\
         .filter(日別時間割.日付 == date_db_str)\
         .all()

        for row in exceptions:
            exc, subj_name, teacher, room_name = row
            if exc.時限 in 時限一覧:
                cell = schedule_grid[exc.時限][physical_yobi_str]
                
                name = subj_name if subj_name else (exc.備考 if exc.備考 else "空欄")
                display = exc.備考 if exc.時限 == 5 and exc.備考 else name
                if not exc.授業ID and not exc.備考:
                     display = "休憩/空欄"
                     name = "空欄"

                cell.update({
                    "subject": name,
                    "teacher": teacher if teacher else "",
                    "room": room_name if room_name else "",
                    "display_text": display,
                    "is_empty": (not exc.授業ID and not exc.備考),
                    "is_exception": True
                })

    return render_template("schedule.html", 
                           schedule_grid=schedule_grid, 
                           曜日順=順序, 
                           時限一覧=時限一覧,
                           # selected_kiki は表示上の目安として、月曜日の計画があればそれを使う
                           selected_kiki=str(授業計画.query.get(week_dates[0].strftime('%Y/%m/%d')).期) if 授業計画.query.get(week_dates[0].strftime('%Y/%m/%d')) else "-", 
                           now_date_str=datetime.now().strftime('%Y-%m-%d'),
                           prev_week=prev_week,
                           next_week=next_week,
                           week_dates=week_dates,
                           display_date=monday
                           )
    
@web_bp.route("/edit_schedule", methods=["GET", "POST"])
@login_required
def edit_schedule():
    """(管理) 時間割編集ページ (ORM版)"""
    selected_kiki = request.args.get("kiki", get_current_kiki())
    success_message = None
    error_message = None

    if request.method == "POST":
        try:
            ターゲット学期 = request.form["kiki"]
            ターゲット曜日 = request.form["day"]
            ターゲット時限 = int(request.form["period"])
            新授業ID = request.form.get("new_subject_id")
            備考テキスト = request.form.get("remark_text")

            is_delete_action = (ターゲット時限 < 5 and 新授業ID == "0")

            existing_slot = 時間割.query.filter_by(
                学期=ターゲット学期, 曜日=ターゲット曜日, 時限=ターゲット時限
            ).first()

            if is_delete_action:
                if existing_slot:
                    db.session.delete(existing_slot)
                    db.session.commit()
                    success_message = f"✅ 第{ターゲット学期}期 {ターゲット曜日}{ターゲット時限}限 の時間割を削除しました。"
                else:
                    error_message = f"エラー: 第{ターゲット学期}期 {ターゲット曜日}{ターゲット時限}限 は元々空欄です。"
            
            else:
                final_remark = 備考テキスト if 備考テキスト and 備考テキスト.strip() else None
                
                if existing_slot:
                    if ターゲット時限 < 5:
                        existing_slot.授業ID = 新授業ID
                        existing_slot.備考 = None
                    else: # ターゲット時限 == 5
                        existing_slot.授業ID = 0
                        existing_slot.備考 = final_remark
                    success_message = f"✅ 時間割 (ID:{existing_slot.時間割ID}) が更新されました。"
                
                else: # 新規挿入
                    new_slot = 時間割(
                        学期=ターゲット学期, 曜日=ターゲット曜日, 時限=ターゲット時限,
                        授業ID = 新授業ID if ターゲット時限 < 5 else 0,
                        備考 = final_remark if ターゲット時限 == 5 else None
                    )
                    db.session.add(new_slot)
                    success_message = f"✅ 新しい時間割が登録されました。"
                
                db.session.commit()
                selected_kiki = ターゲット学期 
                
        except Exception as e:
            db.session.rollback()
            error_message = f"更新エラー: {e}"

    # --- GET/POST後の表示データ取得 ---
    subjects_query = 授業.query.order_by(授業.授業ID).all()
    subjects = [(s.授業ID, s.授業科目名) for s in subjects_query]
    subjects.insert(0, (0, "--- 授業を削除/空欄にする ---"))
    
    schedules_rows = db.session.query(
        時間割.時間割ID, 授業.授業科目名, 時間割.曜日, 時間割.時限, 
        時間割.授業ID, 時間割.学期, 時間割.備考
    ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
     .filter(時間割.学期 == selected_kiki)\
     .order_by(時間割.時限, 時間割.曜日).all()
    
    順序 = ["月", "火", "水", "木", "金"]
    時限一覧 = list(range(1, 6))
    schedule_grid = OrderedDict()
    
    for j in 時限一覧:
        schedule_grid[j] = {y: {"時間割ID": None, "授業科目名": "休憩/空欄", "授業ID": 0, "備考": None, "is_empty": True} for y in 順序}
        
    for row in schedules_rows:
        時間割ID, 授業科目名, 曜日, 時限, 授業ID, 学期, 備考 = row
        if 時限 in 時限一覧 and 曜日 in 順序:
            display_name = 備考 if 時限 == 5 else (授業科目名 if 授業科目名 else "休憩/空欄")
            
            schedule_grid[時限][曜日] = {
                "時間割ID": 時間割ID, "授業科目名": display_name,
                "授業ID": 授業ID if 授業ID else 0, "備考": 備考,
                "is_empty": (not 授業ID and not 備考)
            }
    
    return render_template("edit_schedule.html", 
                           schedule_grid=schedule_grid, subjects=subjects, 曜日順=順序,
                           時限一覧=時限一覧, selected_kiki=selected_kiki,
                           kikis=["1", "2", "3", "4"], error=error_message, success=success_message)

@web_bp.route("/restore_schedule", methods=["POST"])
@login_required
def restore_schedule():
    """(管理) 時間割をデフォルトに復元 (安全対策版)"""
    try:
        with db.engine.connect() as conn:
            # 1. まずバックアップがあるか確認
            count = conn.execute(text("SELECT COUNT(*) FROM \"時間割_デフォルト\"")).scalar()
            
            if count == 0:
                flash("❌ 復元用のバックアップデータがありません。処理を中止しました。（現在のデータは守られました）", "error")
                return redirect(url_for('web.edit_schedule')) # または index

            # 2. バックアップがある場合のみ実行
            conn.execute(text("DELETE FROM \"時間割\""))
            conn.execute(text("INSERT INTO \"時間割\" (\"学期\", \"曜日\", \"時限\", \"授業ID\", \"備考\") SELECT \"学期\", \"曜日\", \"時限\", \"授業ID\", \"備考\" FROM \"時間割_デフォルト\""))
            conn.commit()
            
        flash("✅ 時間割がデフォルトの状態に復元されました。", "success")
    except Exception as e:
        flash(f"❌ 時間割の復元中にエラーが発生しました: {e}", "error")
        
    return redirect(url_for('web.index'))

@web_bp.route("/manage_students", methods=["GET", "POST"])
@login_required
def manage_students():
    """(管理) 学生の追加・削除 (ORM版)"""
    message = None
    category = None

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                student_id = request.form.get("student_id")
                student_name = request.form.get("student_name")
                if student_id and student_name:
                    new_student = 学生(学生ID=student_id, 学生名=student_name)
                    db.session.add(new_student)
                    db.session.commit()
                    message = f"✅ 学生 '{student_name}' (ID: {student_id}) を追加しました。"
                    category = "success"
                else:
                    message = "❌ 学生IDと学生名の両方が必要です。"
                    category = "error"
            
            elif action == "delete":
                student_id = request.form.get("student_id")
                if student_id:
                    student_to_delete = 学生.query.get(student_id)
                    if student_to_delete:
                        # 関連する記録(出席記録, 在室履歴)は cascade="all, delete-orphan" により自動で削除される
                        db.session.delete(student_to_delete)
                        db.session.commit()
                        message = f"✅ 学生ID {student_id} と関連する出席記録をすべて削除しました。"
                        category = "success"
        except IntegrityError:
            db.session.rollback()
            message = f"❌ エラー: 学生ID {student_id} は既に使用されています。"
            category = "error"
        except Exception as e:
            db.session.rollback()
            message = f"❌ データベースエラー: {e}"
            category = "error"

    students = 学生.query.order_by(学生.学生ID).all()
    
    return render_template("manage_students.html", 
                           students=[(s.学生ID, s.学生名) for s in students], 
                           message=message, 
                           category=category)

@web_bp.route("/manage_subjects", methods=["GET", "POST"])
@login_required
def manage_subjects():
    """(管理) 授業科目の追加・削除 (ORM版)"""
    message = None
    category = None

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                subject_id = request.form.get("subject_id")
                subject_name = request.form.get("subject_name")
                teacher_name = request.form.get("teacher_name")
                room_id = request.form.get("room_id")
                room_id = room_id if room_id else None 

                if subject_id and subject_name:
                    new_subject = 授業(授業ID=subject_id, 授業科目名=subject_name, 担当教員=teacher_name, 教室ID=room_id)
                    db.session.add(new_subject)
                    db.session.commit()
                    message = f"✅ 授業 '{subject_name}' (ID: {subject_id}) を追加しました。"
                    category = "success"
                else:
                    message = "❌ 授業IDと授業科目名の両方が必要です。"
                    category = "error"
            
            elif action == "delete":
                subject_id = request.form.get("subject_id")
                if subject_id:
                    subject_to_delete = 授業.query.get(subject_id)
                    if subject_to_delete:
                        # 関連する記録(時間割, 出席記録)は cascade="all, delete-orphan" により自動で削除される
                        db.session.delete(subject_to_delete)
                        db.session.commit()
                        message = f"✅ 授業ID {subject_id} と関連する時間割・出席記録をすべて削除しました。"
                        category = "success"
        except IntegrityError:
            db.session.rollback()
            message = f"❌ エラー: 授業ID {subject_id} は既に使用されています。"
            category = "error"
        except Exception as e:
            db.session.rollback()
            message = f"❌ データベースエラー: {e}"
            category = "error"

    # 教室名もJOINして取得
    subjects = db.session.query(授業.授業ID, 授業.授業科目名, 授業.担当教員, 教室.教室名, 授業.教室ID)\
               .outerjoin(教室, 授業.教室ID == 教室.教室ID)\
               .order_by(授業.授業ID).all()
    
    rooms = 教室.query.order_by(教室.教室ID).all()
    
    return render_template("manage_subjects.html", 
                           subjects=subjects, 
                           rooms=[(r.教室ID, r.教室名) for r in rooms],
                           message=message, 
                           category=category)

# --- 10. レポート (SQLAlchemy版, text() ハイブリッド) ---

@web_bp.route("/my_attendance", methods=["GET"])
@login_required
def my_attendance():
    """(レポート) 個人別出席サマリー (text()版)"""
    student_id = request.args.get("student_id")
    selected_kiki = request.args.get("kiki", get_current_kiki())
    
    if not student_id or not student_id.isdigit():
        flash("学生IDが指定されていません。", "error")
        return redirect(url_for('web.index'))
    student_id = int(student_id)
    
    student_info = 学生.query.get(student_id)
    student_name = student_info.学生名 if student_info else "不明な学生"

    # 1. 履修科目を時間割から取得
    sql_enrolled = text("""
        SELECT DISTINCT S."授業科目名", S."授業ID"
        FROM "時間割" T
        JOIN "授業" S ON T."授業ID" = S."授業ID"
        WHERE T."学期" = :kiki AND T."授業ID" != 0 
        ORDER BY S."授業科目名"
    """)
    enrolled_subjects = db.session.execute(sql_enrolled, {"kiki": selected_kiki}).fetchall()

    grouped_attendance = OrderedDict()
    
    # 2. 総授業回数を計算
    for subject_name, subject_id in enrolled_subjects:
        if subject_name not in grouped_attendance:
            grouped_attendance[subject_name] = {
                "id": subject_id, "attendance_count": 0, "tardy_count": 0,
                "absent_count": 0, "total_classes_planned": 0, "total_classes_so_far": 0, "attendance_rate": 0.0
            }
        
        sql_schedule = text('SELECT T."曜日", COUNT(T."時限") FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."曜日"')
        schedule_data = db.session.execute(sql_schedule, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
        
        total_classes_planned = 0
        total_classes_so_far = 0
        
        for day_of_week, periods_per_day in schedule_data:
            day_code = YOBI_MAP.get(day_of_week)
            total_days_planned = 0
            total_days_so_far = 0
            if day_code is not None:
                sql_days_planned = text('SELECT COUNT(*) FROM "授業計画" WHERE "期" = :kiki AND "授業曜日" = :code')
                total_days_planned = db.session.execute(sql_days_planned, {"kiki": selected_kiki, "code": day_code}).scalar()
                
                sql_days_so_far = text("""
                    SELECT COUNT("日付") FROM "授業計画" 
                    WHERE "期" = :kiki AND "授業曜日" = :code 
                    AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                """)

                total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": selected_kiki, "code": day_code}).scalar()

            total_classes_planned += total_days_planned * periods_per_day
            total_classes_so_far += total_days_so_far * periods_per_day
        
        grouped_attendance[subject_name]["total_classes_planned"] = total_classes_planned
        grouped_attendance[subject_name]["total_classes_so_far"] = total_classes_so_far

    # 3. 出席記録を集計
    sql_records = text("""
        SELECT R."状態", S."授業科目名"
        FROM "出席記録" R
        JOIN "授業" S ON R."授業ID" = S."授業ID"
        WHERE R."学生ID" = :sid AND R."授業ID" IN (
            SELECT DISTINCT T."授業ID" FROM "時間割" T WHERE T."学期" = :kiki
        ) 
    """)
    records = db.session.execute(sql_records, {"sid": student_id, "kiki": selected_kiki}).fetchall()

    for status, subject_name in records:
        if subject_name in grouped_attendance:
            if status == "出席": grouped_attendance[subject_name]["attendance_count"] += 1
            elif status == "遅刻": grouped_attendance[subject_name]["tardy_count"] += 1
            elif status == "欠席": grouped_attendance[subject_name]["absent_count"] += 1 # ⬅️ これは DB上の欠席

    # 4. 出席率を計算
    report_data_summary = []
    for subject, data in grouped_attendance.items():
        total_classes_so_far = data["total_classes_so_far"] 
        if total_classes_so_far > 0:
            data["attendance_rate"] = round((data["attendance_count"] / total_classes_so_far) * 100, 1)
        else:
            data["attendance_rate"] = 0.0

        # 4b. 「未記録」を計算して「欠席」に合算
        total_recorded = data["attendance_count"] + data["tardy_count"] + data["absent_count"]
        unrecorded_count = total_classes_so_far - total_recorded
        if unrecorded_count < 0: unrecorded_count = 0
        
        total_absent = data["absent_count"] + unrecorded_count # ⬅️ 合算

        row = {
            "subject": subject,
            "attendance_rate": data["attendance_rate"],
            "total_classes": data["total_classes_planned"], 
            "total_classes_so_far": data["total_classes_so_far"], 
            "attendance_count": data["attendance_count"],
            "tardy_count": data["tardy_count"],
            "absent_count": total_absent, # ⬅️ 合算した値を渡す
        }
        report_data_summary.append(row)
        
    return render_template("my_attendance.html", 
                           student_id=student_id, student_name=student_name,
                           report_data=report_data_summary, selected_kiki=selected_kiki, 
                           kikis=["1", "2", "3", "4"])

@web_bp.route("/my_attendance_detail", methods=["GET"])
@login_required 
def my_attendance_detail():
    """(レポート) 個人別出席詳細 (未記録も「欠席」として表示)"""
    student_id = request.args.get("student_id")
    selected_kiki = request.args.get("kiki", "1")
    subject_name_filter = request.args.get("subject")

    if not student_id or not student_id.isdigit():
        return redirect("/my_attendance")
    student_id = int(student_id)
    
    student_info = 学生.query.get(student_id)
    student_name = student_info.学生名 if student_info else "不明な学生"
    
    if not subject_name_filter:
        flash("詳細を表示する授業が指定されていません。", "error")
        return redirect(url_for('web.my_attendance', student_id=student_id, kiki=selected_kiki))

    kiki_int = int(selected_kiki)

    # 1. 授業名から授業IDを取得
    subject_obj = 授業.query.filter_by(授業科目名=subject_name_filter).first()
    if not subject_obj:
        flash(f"授業「{subject_name_filter}」が見つかりません。", "error")
        return redirect(url_for('web.my_attendance', student_id=student_id, kiki=selected_kiki))
    subject_id = subject_obj.授業ID

    # 2. (リスト1) DBに「実在する」出席記録を取得 (「時限」も取得)
    sql_records = text("""
        SELECT R."ID", R."出席時刻", R."状態", R."出席日付", R."時限"
        FROM "出席記録" R
        JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
        WHERE R."学生ID" = :sid 
          AND R."授業ID" = :subject_id
          AND P."期" = :kiki_int
        ORDER BY R."出席日付", R."時限"
    """)
    actual_records_raw = db.session.execute(sql_records, {
        "sid": student_id, 
        "subject_id": subject_id,
        "kiki_int": kiki_int
    }).fetchall()
    
    # (記録済みの「(日付, 時限)」のタプルをSet型に保存)
    actual_tuples = {(record.出席日付.date(), record.時限) for record in actual_records_raw}

    # 3. (リスト2) この授業があった「昨日までの全日程・全時限」を取得
    
    # 3a. この授業の「曜日」と「時限」のペアを取得 (例: [('月', 1), ('月', 2), ('水', 1)])
    sql_schedule_slots = text("""
        SELECT T."曜日", T."時限" 
        FROM "時間割" T 
        WHERE T."授業ID" = :sid AND T."学期" = :kiki AND T."授業ID" != 0
    """)
    schedule_slots = db.session.execute(sql_schedule_slots, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
    # (検索しやすいように 曜日 -> [時限リスト] の辞書に変換)
    schedule_map = {}
    for yobi, jigen in schedule_slots:
        if yobi not in schedule_map:
            schedule_map[yobi] = []
        schedule_map[yobi].append(jigen)

    # 3b. 授業計画から「昨日まで」の「日付」と「曜日コード」を取得
    planned_days_raw = []
    if schedule_map: # (スケジュールがある場合のみ)
        sql_planned_dates = text("""
            SELECT "日付", "授業曜日" 
            FROM "授業計画" 
            WHERE "期" = :kiki_int 
              AND "授業曜日" IN :day_codes
              AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
        """)
        day_codes = [YOBI_MAP.get(name) for name in schedule_map.keys()] #
        planned_days_raw = db.session.execute(sql_planned_dates, {
            "kiki_int": kiki_int,
            "day_codes": tuple(day_codes)
        }).fetchall()

    # 4. (合体処理) 2つのリストを合体させる
    merged_report_list = []
    
    # 4a. まず「実在する記録」を全部入れる
    for record in actual_records_raw:
        merged_report_list.append({
            "record_id": record.ID,
            "timestamp": record.出席時刻, # datetimeオブジェクト
            "status": record.状態,
            "jigen": record.時限, # ⬅️ 時限を追加
            "is_phantom": False # 「幽霊」ではない (実在する)
        })
        
    # 4b. 次に「授業があった日」をチェック
    for date_row in planned_days_raw:
        try:
            planned_date = datetime.strptime(date_row[0], '%Y/%m/%d').date()
            yobi_code = date_row.授業曜日
            yobi_name = YOBI_MAP_REVERSE.get(yobi_code) #
        except (ValueError, TypeError):
            continue # 日付形式エラーや曜日不正はスキップ
            
        # (その曜日に予定されていた時限リストを取得)
        periods_for_this_day = schedule_map.get(yobi_name)
        if not periods_for_this_day:
            continue
            
        # (その日の時限を1つずつチェック)
        for jigen in periods_for_this_day:
            current_tuple = (planned_date, jigen)
            
            # (もし「授業があった(日, 時限)」が「記録済みリスト」になかったら)
            if current_tuple not in actual_tuples:
                # ＝ これが「未記録＝欠席」だ！
                
                # (ダミーの「×」データを作る)
                fake_timestamp = datetime.combine(planned_date, time(8, 50))
                
                merged_report_list.append({
                    "record_id": None,
                    "timestamp": fake_timestamp, 
                    "status": "欠席",
                    "jigen": jigen, # ⬅️ 時限を追加
                    "is_phantom": True
                })
            
    # 4c. 最終リストを日付順・時限順に並び替え
    merged_report_list.sort(key=lambda x: (x["timestamp"], x["jigen"]))

    # 5. データをHTMLで使いやすいように「仕分け」する
    report_data_detail = []
    status_map = {"出席": "○", "遅刻": "△", "欠席": "×"}
    
    if merged_report_list:
        row = {"subject": subject_name_filter}
        max_recorded_count = len(merged_report_list)
        
        for i in range(max_recorded_count):
            count_str = str(i + 1) 
            item = merged_report_list[i]
            
            formatted_date = item["timestamp"].strftime('%m/%d') 
            status_symbol = status_map.get(item["status"], item["status"])
            
            row[f"count_{count_str}_id"] = item["record_id"] if not item["is_phantom"] else f"phantom-{i}"
            row[f"count_{count_str}_status"] = status_symbol
            row[f"count_{count_str}_display"] = f"{status_symbol} ({formatted_date})"
            row[f"count_{count_str}_original_status"] = item["status"]
            row[f"count_{count_str}_is_phantom"] = item["is_phantom"]
            row[f"count_{count_str}_jigen"] = item["jigen"] # ⬅️ 時限をHTMLへ
        
        report_data_detail.append(row)
    else:
        max_recorded_count = 0 
        report_data_detail.append({"subject": subject_name_filter})

    # 6. HTMLテンプレートにデータを渡す
    return render_template("my_attendance_detail.html", 
                           student_id=student_id, student_name=student_name,
                           report_data=report_data_detail, max_count=max_recorded_count,
                           selected_kiki=selected_kiki, kikis=["1", "2", "3", "4"],
                           subject_filter=subject_name_filter,
                           is_portal_view=False 
                           )

@web_bp.route("/report_summary", methods=["GET"])
@login_required
def report_summary():
    """(レポート) 授業別出席管理表 (text()版)"""
    
    sql_subjects = text("""
        SELECT DISTINCT S."授業ID", S."授業科目名", T."学期" 
        FROM "授業" S 
        JOIN "時間割" T ON S."授業ID" = T."授業ID" 
        WHERE S."授業ID" != 0 
        ORDER BY T."学期", S."授業ID"
    """)
    all_subjects = db.session.execute(sql_subjects).fetchall()
    
    report_data = None
    selected_subject_key = request.args.get("subject_key") 
    selected_subject_id = None
    selected_kiki = None
    current_kiki_str = get_current_kiki()

    if selected_subject_key:
        try:
            parts = selected_subject_key.split('-')
            if len(parts) == 2:
                selected_kiki = parts[0]
                selected_subject_id = int(parts[1])
            else:
                raise ValueError("無効なsubject_keyです")

            sql_schedule = text('SELECT T."曜日", T."学期", COUNT(T."時限") AS periods_per_day FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."学期", T."曜日"')
            schedule_entries = db.session.execute(sql_schedule, {"sid": selected_subject_id, "kiki": selected_kiki}).fetchall()
            
            if schedule_entries:
                total_classes_planned = 0
                total_classes_so_far = 0 

                for target_yobi_str, target_kiki_str, periods_per_day in schedule_entries:
                    target_yobi_code = YOBI_MAP.get(target_yobi_str)
                    target_kiki_num = int(selected_kiki) 

                    if target_yobi_code is not None:
                        sql_days_planned = text('SELECT COUNT("日付") FROM "授業計画" WHERE "期" = :kiki AND "授業曜日" = :code')
                        total_days_planned = db.session.execute(sql_days_planned, {"kiki": target_kiki_num, "code": target_yobi_code}).scalar()
                        total_classes_planned += total_days_planned * periods_per_day
                        
                        sql_days_so_far = text("""
                            SELECT COUNT("日付") FROM "授業計画" 
                            WHERE "期" = :kiki AND "授業曜日" = :code 
                            AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                        """)
                        total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": target_kiki_num, "code": target_yobi_code}).scalar()
                        total_classes_so_far += total_days_so_far * periods_per_day
                
                max_count = total_classes_planned 

                students = 学生.query.order_by(学生.学生ID).all()
                
                summary = []
                for student in students:
                    sql_counts = text("""
                        SELECT R."状態", COUNT(R."状態")
                        FROM "出席記録" R
                        JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
                        WHERE R."授業ID" = :sid 
                          AND R."学生ID" = :stid 
                          AND P."期" = :kiki_int
                        GROUP BY R."状態"
                    """)
                    
                    # :kiki (文字列) ではなく :kiki_int (整数) を渡すように変更
                    counts = dict(db.session.execute(sql_counts, {
                        "sid": selected_subject_id, 
                        "stid": student.学生ID, 
                        "kiki_int": int(selected_kiki) 
                    }).fetchall())
                    
                    attended_count = counts.get('出席', 0)
                    tardy_count = counts.get('遅刻', 0)
                    absent_count_db = counts.get('欠席', 0) # 1. DBからの欠席
                    
                    attendance_rate = 0.0
                    if total_classes_so_far > 0:
                        attendance_rate = round((attended_count / total_classes_so_far) * 100, 1)
                    
                    total_recorded_so_far = attended_count + tardy_count + absent_count_db
                    unrecorded_count = total_classes_so_far - total_recorded_so_far
                    if unrecorded_count < 0: unrecorded_count = 0
                        
                    total_absent = absent_count_db + unrecorded_count
                    
                    summary.append({
                        'id': student.学生ID, 'name': student.学生名,
                        'max_count': max_count, 'attendance_rate': attendance_rate,
                        'total_classes_so_far': total_classes_so_far,
                        'counts': {
                            '出席': attended_count,
                            '遅刻': tardy_count,
                            '欠席': total_absent,   # ⬅️ ✔️ カンマをここに追加
                            'その他': 0
                        }
                    })
                report_data = summary
            
        except ValueError:
             selected_subject_key = None
            
    return render_template("report_summary.html", 
                           all_subjects=all_subjects,
                           report_data=report_data,
                           selected_subject_key=selected_subject_key,
                           current_kiki_str=current_kiki_str) 

@web_bp.route("/export/report_summary")
@login_required
def export_report_summary():
    """(エクスポート) 授業別レポートをCSVでダウンロード (text()版)"""
    selected_subject_key = request.args.get("subject_key")
    if not selected_subject_key:
        return redirect(url_for('web.report_summary'))

    try:
        parts = selected_subject_key.split('-')
        if len(parts) == 2:
            selected_kiki = parts[0]
            selected_subject_id = int(parts[1])
        else:
            raise ValueError("無効なsubject_keyです")

        subject_info = 授業.query.get(selected_subject_id)
        subject_name = subject_info.授業科目名 if subject_info else "UnknownSubject"
        
        # (report_summary と同じロジック)
        sql_schedule = text('SELECT T."曜日", T."学期", COUNT(T."時限") AS periods_per_day FROM "時間割" T WHERE T."授業ID" = :sid AND T."学期" = :kiki GROUP BY T."学期", T."曜日"')
        schedule_entries = db.session.execute(sql_schedule, {"sid": selected_subject_id, "kiki": selected_kiki}).fetchall()
        
        total_classes_planned = 0
        total_classes_so_far = 0
        
        if schedule_entries:
            for target_yobi_str, target_kiki_str, periods_per_day in schedule_entries:
                target_yobi_code = YOBI_MAP.get(target_yobi_str)
                target_kiki_num = int(selected_kiki)

                if target_yobi_code is not None:
                    sql_days_planned = text('SELECT COUNT("日付") FROM "授業計画" WHERE "期" = :kiki AND "授業曜日" = :code')
                    total_days_planned = db.session.execute(sql_days_planned, {"kiki": target_kiki_num, "code": target_yobi_code}).scalar()
                    total_classes_planned += total_days_planned * periods_per_day
                    
                    sql_days_so_far = text("""
                        SELECT COUNT("日付") FROM "授業計画" 
                        WHERE "期" = :kiki AND "授業曜日" = :code 
                        AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
                    """)
                    total_days_so_far = db.session.execute(sql_days_so_far, {"kiki": target_kiki_num, "code": target_yobi_code}).scalar()
                    total_classes_so_far += total_days_so_far * periods_per_day

        students = 学生.query.order_by(学生.学生ID).all()
        
        report_data = [] 
        for student in students:
            sql_counts = text("""
                SELECT R."状態", COUNT(R."状態")
                FROM "出席記録" R
                JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
                WHERE R."授業ID" = :sid 
                  AND R."学生ID" = :stid 
                  AND P."期" = :kiki_int
                GROUP BY R."状態"
            """)

            # :kiki (文字列) ではなく :kiki_int (整数) を渡すように変更
            counts = dict(db.session.execute(sql_counts, {
                "sid": selected_subject_id, 
                "stid": student.学生ID, 
                "kiki_int": int(selected_kiki)
            }).fetchall())
            
            attended_count = counts.get('出席', 0)
            tardy_count = counts.get('遅刻', 0)
            absent_count = counts.get('欠席', 0)
            
            attendance_rate = 0.0
            if total_classes_so_far > 0:
                attendance_rate = round((attended_count / total_classes_so_far) * 100, 1)

            total_recorded_so_far = attended_count + tardy_count + absent_count
            unrecorded_count = total_classes_so_far - total_recorded_so_far
            if unrecorded_count < 0: unrecorded_count = 0
            
            report_data.append({
                'id': student.学生ID, 'name': student.学生名,
                'max_count': total_classes_planned, 'total_classes_so_far': total_classes_so_far, 
                'attendance_rate': attendance_rate, 'attended_count': attended_count,
                'tardy_count': tardy_count, 'absent_count': absent_count,
                'unrecorded_count': unrecorded_count
            })

        si = io.StringIO()
        si.write('\ufeff') 
        headers = ['学生ID', '学生名', '出席率 (%)', '総授業回数(予定)', '今日までの授業回数', '出席', '遅刻', '欠席', '未記録(今日まで)']
        writer = csv.writer(si)
        writer.writerow(headers) 

        for row in report_data:
            writer.writerow([
                f"=\"{row['id']}\"", 
                row['name'], row['attendance_rate'],
                row['max_count'], row['total_classes_so_far'], 
                row['attended_count'], row['tardy_count'],
                row['absent_count'], row['unrecorded_count']
            ])

        output = make_response(si.getvalue())
        today = datetime.now().strftime('%Y%m%d')
        filename_str = f"{today}_{selected_kiki}期_{subject_name}_出席レポート.csv"
        filename_encoded = quote(filename_str) 

        output.headers["Content-Disposition"] = (
            f"attachment; "
            f"filename=\"report.csv\"; "
            f"filename*=UTF-8''{filename_encoded}"
        )
        output.headers["Content-type"] = "text/csv; charset=utf-8"
        return output

    except Exception as e:
        db.session.rollback()
        print(f"Error exporting CSV: {e}")
        return redirect(url_for('web.report_summary'))

@web_bp.route("/send_schedule_email")
@login_required
def send_schedule_email_route():
    """(メール送信) GAS経由のテストメール"""
    try:
        payload = {
            "to": os.environ.get('MAIL_USERNAME'), # 自分宛て
            "subject": "時間割情報のお知らせ（GASテスト）",
            "body": "これはGAS経由のテストメールです。\nRenderから送信されています。",
            "auth_token": os.environ.get('GAS_AUTH_TOKEN', 'YOUR_SECRET_GAS_TOKEN')
        }
        gas_url = os.environ.get('GAS_API_URL')
        
        if gas_url:
            requests.post(gas_url, json=payload)
            flash("✅ GAS経由でテストメールを送信しました。", "success")
        else:
            flash("❌ GAS_API_URLが設定されていません。", "error")
            
    except Exception as e:
        flash(f"❌ 送信エラー: {e}", "error")
        
    return redirect(url_for('web.index'))

@web_bp.route("/alerts")
@login_required
def alerts():
    """(管理) 遅刻・欠席の連絡掲示板"""
    
    # 未解決の連絡を最新のものから取得
    unresolved_reports = db.session.query(ReportRecord, 学生.学生名) \
        .join(学生, ReportRecord.student_id == 学生.学生ID) \
        .filter(ReportRecord.is_resolved == False) \
        .order_by(ReportRecord.report_date.desc()) \
        .all()
        
    return render_template("alerts.html", reports=unresolved_reports)

@web_bp.route("/resolve_alert/<int:record_id>", methods=["POST"])
@login_required
def resolve_alert(record_id):
    """連絡を管理者確認済みにする"""
    report = ReportRecord.query.get(record_id)
    if report:
        report.is_resolved = True
        db.session.commit()
        flash("✅ 連絡を確認済みにしました。", "success")
    return redirect(url_for('web.alerts'))

# --- 11. LINE Bot Webhook (SQLAlchemy版) ---

@web_bp.route("/save_as_default", methods=["POST"])
@login_required
def save_as_default():
    """(管理) 現在の時間割をデフォルト（バックアップ）として保存"""
    try:
        with db.engine.connect() as conn:
            # 既存のバックアップを空にする
            conn.execute(text("DELETE FROM \"時間割_デフォルト\""))
            # 現在のデータをバックアップにコピー
            conn.execute(text("INSERT INTO \"時間割_デフォルト\" (\"学期\", \"曜日\", \"時限\", \"授業ID\", \"備考\") SELECT \"学期\", \"曜日\", \"時限\", \"授業ID\", \"備考\" FROM \"時間割\""))
            conn.commit()
        flash("✅ 現在の時間割設定を『復元ポイント』として保存しました。", "success")
    except Exception as e:
        flash(f"❌ 保存中にエラーが発生しました: {e}", "error")
        
    return redirect(url_for('web.edit_schedule'))

# ----------------------------------------------------------------------
# 11. 学生専用ポータル (Student Portal)
# ----------------------------------------------------------------------
@web_bp.route("/student_register", methods=["GET", "POST"])
def student_register():
    """学生専用の初回パスワード設定ページ"""
    if current_user.is_authenticated:
        return redirect(url_for('web.my_portal')) # ログイン済ならポータルへ

    if request.method == "POST":
        try:
            student_id = int(request.form.get("student_id"))
            password = request.form.get("password")
            password_confirm = request.form.get("password_confirm")

            if not student_id or not password or not password_confirm:
                flash("❌ すべての項目を入力してください。", "error")
                return redirect(url_for('web.student_register'))
            
            if password != password_confirm:
                flash("❌ パスワードが一致しません。", "error")
                return redirect(url_for('web.student_register'))

            student = 学生.query.get(student_id)
            
            if not student:
                flash("❌ その学生IDは存在しません。管理者に確認してください。", "error")
                return redirect(url_for('web.student_register'))
            
            # 🚨 ここが重要: すでにパスワードが設定済みかチェック
            if student.password_hash is not None:
                flash("⚠️ この学生IDは既にパスワード設定済みです。ログイン画面からログインしてください。", "warning")
                return redirect(url_for('web.student_login'))
            
            # パスワードをハッシュ化して設定
            student.set_password(password)
            db.session.commit()
            
            flash("✅ パスワードを設定しました！ ログインしてください。", "success")
            return redirect(url_for('web.student_login'))

        except ValueError:
            flash("学生IDは数字で入力してください。", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"登録エラーが発生しました: {e}", "error")
            
    return render_template("student_register.html")

@web_bp.route("/student_login", methods=["GET", "POST"])
def student_login():
    """学生専用のログインページ"""
    if current_user.is_authenticated:
        # すでにログイン済みの場合
        if current_user.get_id().startswith('student-'):
            return redirect(url_for('web.my_portal'))
        else:
            return redirect(url_for('web.index')) # 管理者は管理画面へ

    if request.method == "POST":
        try:
            student_id = int(request.form.get("student_id"))
            password = request.form.get("password")
            
            student = 学生.query.get(student_id)
            
            # データベースにパスワードが設定されているか、ハッシュで一致するかを確認
            if student and student.check_password(password):
                login_user(student) # ⬅️ 学生としてログイン
                return redirect(url_for('web.my_portal'))
            else:
                flash("学生IDまたはパスワードが間違っています。", "error")
                
        except ValueError:
            flash("学生IDは数字で入力してください。", "error")
        except Exception as e:
            flash(f"ログインエラーが発生しました: {e}", "error")
            
    return render_template("student_login.html")


# app.py の my_portal 関数をこれに置き換えてください

@web_bp.route("/my_portal")
@login_required 
def my_portal():
    """学生専用ポータル (リアルタイム週次時間割対応版)"""
    
    if not current_user.get_id().startswith('student-'):
        flash("管理者はこのページにアクセスできません。", "error")
        return redirect(url_for('web.index'))
    
    student_id = current_user.学生ID
    student_name = current_user.学生名
    
    # --- 1. 日付情報の準備 (今週の計算) ---
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday()) # 今週の月曜
    week_dates = [monday + timedelta(days=i) for i in range(5)] # 月〜金
    week_dates_str = [d.strftime('%Y/%m/%d') for d in week_dates] # DB検索用
    
    # --- 2. 学期の取得 ---
    # 月曜日の授業計画を見て期を決定 (なければ現在日付から)
    kiki_param = request.args.get('kiki')
    
    if kiki_param:
        selected_kiki = kiki_param
    else:
        # 指定がない場合、今週の月曜日の授業計画から判定
        plan_row = 授業計画.query.get(week_dates_str[0])
        selected_kiki = str(plan_row.期) if plan_row else get_current_kiki()
        
    kiki_int = int(selected_kiki)

    # --- 3. 出席サマリーデータの作成 (既存ロジック維持) ---
    sql_enrolled = text("""
        SELECT DISTINCT S."授業科目名", S."授業ID"
        FROM "時間割" T
        JOIN "授業" S ON T."授業ID" = S."授業ID"
        WHERE T."学期" = :kiki AND T."授業ID" != 0 
        ORDER BY S."授業科目名"
    """) 
    enrolled_subjects = db.session.execute(sql_enrolled, {"kiki": selected_kiki}).fetchall()

    report_data = [] 
    if enrolled_subjects:
        for subject_name, subject_id in enrolled_subjects:
            # (集計ロジックは長いので省略しませんが、元のコードと同じ内容です)
            # ... コマ数計算 ...
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
            
            # ... 出席数集計 ...
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
                "sid": student_id, "kiki_int": kiki_int, "subject_id": subject_id
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
                "subject": subject_name,
                "attendance_rate": attendance_rate,
                "total_classes_so_far": total_classes_so_far, 
                "attendance_count": attendance_count,
                "tardy_count": tardy_count,
                "absent_count": total_absent
            })
            
    # --- 4. ヒートマップ (既存ロジック) ---
    try:
        one_year_ago = datetime.now() - timedelta(days=365)
        heatmap_query = db.session.query(
            func.date(出席記録.出席時刻), func.count(出席記録.ID)
        ).filter(
            出席記録.学生ID == student_id,
            出席記録.状態.in_(['出席', '遅刻']),
            出席記録.出席時刻 >= one_year_ago
        ).group_by(func.date(出席記録.出席時刻)).all()
        heatmap_data = {str(d): c for d, c in heatmap_query}
    except:
        heatmap_data = {}
    
    # --- 5. 【修正】リアルタイム週間時間割データの構築 ---
    順序 = ["月", "火", "水", "木", "金"]
    時限一覧 = list(range(1, 6))
    schedule_grid = OrderedDict()

    # まず空枠作成 (日付情報付き)
    for j in 時限一覧:
        schedule_grid[j] = {}
        for idx, yobi in enumerate(順序):
            schedule_grid[j][yobi] = {
                "display_text": "休憩/空欄", "teacher": "", "room": "", 
                "is_empty": True, "is_exception": False,
                "date_str": week_dates[idx].strftime('%Y-%m-%d') # 今日の判定用
            }

    # マスター時間割で埋める
    master_rows = db.session.query(
        時間割, 授業.授業科目名, 授業.担当教員, 教室.教室名
    ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
     .outerjoin(教室, 授業.教室ID == 教室.教室ID)\
     .filter(時間割.学期 == selected_kiki).all()

    for row in master_rows:
        timetable, subj_name, teacher, room_name = row
        if timetable.時限 in 時限一覧 and timetable.曜日 in 順序:
            cell = schedule_grid[timetable.時限][timetable.曜日]
            name = subj_name if subj_name else "授業なし"
            display = timetable.備考 if timetable.時限 == 5 and timetable.備考 else name
            
            cell.update({
                "display_text": display,
                "teacher": teacher if teacher else "",
                "room": room_name if room_name else "",
                "is_empty": (not timetable.授業ID and not timetable.備考)
            })

    # 日別例外で上書き
    exceptions = db.session.query(
        日別時間割, 授業.授業科目名, 授業.担当教員, 教室.教室名
    ).outerjoin(授業, 日別時間割.授業ID == 授業.授業ID)\
     .outerjoin(教室, 日別時間割.教室ID == 教室.教室ID)\
     .filter(日別時間割.日付.in_(week_dates_str)).all()

    for row in exceptions:
        exc, subj_name, teacher, room_name = row
        try:
            exc_date = datetime.strptime(exc.日付, '%Y/%m/%d').date()
            yobi_idx = exc_date.weekday()
            if 0 <= yobi_idx <= 4:
                yobi_str = 順序[yobi_idx]
                cell = schedule_grid[exc.時限][yobi_str]
                
                name = subj_name if subj_name else (exc.備考 if exc.備考 else "空欄")
                display = exc.備考 if exc.時限 == 5 and exc.備考 else name
                if not exc.授業ID and not exc.備考:
                     display = "休憩/空欄"
                
                cell.update({
                    "display_text": display,
                    "teacher": teacher if teacher else "",
                    "room": room_name if room_name else "",
                    "is_empty": (not exc.授業ID and not exc.備考),
                    "is_exception": True
                })
        except:
            pass

    return render_template("my_portal.html", 
                           student_name=student_name,
                           report_data=report_data, 
                           selected_kiki=selected_kiki,
                           schedule_grid=schedule_grid,
                           曜日順=順序,
                           時限一覧=時限一覧,
                           kikis=["1", "2", "3", "4"], # 学期リストを追加
                           heatmap_data=heatmap_data,
                           # ▼ 週表示用の変数を追加
                           today_yobi=YOBI_MAP_REVERSE.get(today.weekday() + 1),
                           week_dates=week_dates,
                           now_date_str=today.strftime('%Y-%m-%d')
                           )

@web_bp.route("/update_parent_email", methods=["POST"])
@login_required
def update_parent_email():
    """学生が自分の保護者メアドを更新する処理"""
    
    # 学生以外は弾く
    if not current_user.get_id().startswith('student-'):
        return redirect(url_for('web.index'))
    
    parent_email = request.form.get("parent_email")
    
    # データベースを更新
    try:
        # current_user はログイン中の学生データそのもの
        student = 学生.query.get(current_user.学生ID)
        student.parent_email = parent_email
        db.session.commit()
        flash("✅ 保護者のメールアドレスを保存しました。", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ 保存に失敗しました: {e}", "error")
        
    return redirect(url_for('web.my_portal'))

@web_bp.route("/my_portal_detail")
@login_required #
def my_portal_detail():
    """ (新機能) 学生専用ポータル - 出席詳細 (未記録も「欠席」として表示) """

    if not current_user.get_id().startswith('student-'):
        flash("管理者はこのページにアクセスできません。", "error")
        return redirect(url_for('web.index'))
    
    # --- ▼▼▼ 修正点1: IDを自分自身に固定 ▼▼▼ ---
    student_id = current_user.学生ID
    student_name = current_user.学生名
    
    selected_kiki = request.args.get("kiki", "1")
    subject_name_filter = request.args.get("subject")

    if not subject_name_filter:
        flash("詳細を表示する授業が指定されていません。", "error")
        return redirect(url_for('web.my_portal'))

    kiki_int = int(selected_kiki)

    # --- ▼▼▼ (ここから /my_attendance_detail と全く同じロジック) ▼▼▼ ---
    
    # 1. 授業名から授業IDを取得
    subject_obj = 授業.query.filter_by(授業科目名=subject_name_filter).first()
    if not subject_obj:
        flash(f"授業「{subject_name_filter}」が見つかりません。", "error")
        return redirect(url_for('web.my_portal'))
    subject_id = subject_obj.授業ID

    # 2. (リスト1) DBに「実在する」出席記録を取得 (「時限」も取得)
    sql_records = text("""
        SELECT R."ID", R."出席時刻", R."状態", R."出席日付", R."時限"
        FROM "出席記録" R
        JOIN "授業計画" P ON R."出席日付" = TO_DATE(REPLACE(P."日付", '/', '-'), 'YYYY-MM-DD')
        WHERE R."学生ID" = :sid 
          AND R."授業ID" = :subject_id
          AND P."期" = :kiki_int
        ORDER BY R."出席日付", R."時限"
    """)
    actual_records_raw = db.session.execute(sql_records, {
        "sid": student_id, # ⬅️ (自分のIDが使われる)
        "subject_id": subject_id,
        "kiki_int": kiki_int
    }).fetchall()
    
    actual_tuples = {(record.出席日付.date(), record.時限) for record in actual_records_raw}

    # 3. (リスト2) この授業があった「昨日までの全日程・全時限」を取得
    sql_schedule_slots = text("""
        SELECT T."曜日", T."時限" 
        FROM "時間割" T 
        WHERE T."授業ID" = :sid AND T."学期" = :kiki AND T."授業ID" != 0
    """)
    schedule_slots = db.session.execute(sql_schedule_slots, {"sid": subject_id, "kiki": selected_kiki}).fetchall()
    schedule_map = {}
    for yobi, jigen in schedule_slots:
        if yobi not in schedule_map:
            schedule_map[yobi] = []
        schedule_map[yobi].append(jigen)

    planned_days_raw = []
    if schedule_map:
        sql_planned_dates = text("""
            SELECT "日付", "授業曜日" 
            FROM "授業計画" 
            WHERE "期" = :kiki_int 
              AND "授業曜日" IN :day_codes
              AND TO_DATE(REPLACE("日付", '/', '-'), 'YYYY-MM-DD') <= CURRENT_DATE
        """)
        day_codes = [YOBI_MAP.get(name) for name in schedule_map.keys()]
        planned_days_raw = db.session.execute(sql_planned_dates, {
            "kiki_int": kiki_int,
            "day_codes": tuple(day_codes)
        }).fetchall()

    # 4. (合体処理) 2つのリストを合体させる
    merged_report_list = []
    
    for record in actual_records_raw:
        merged_report_list.append({
            "record_id": record.ID, "timestamp": record.出席時刻,
            "status": record.状態, "jigen": record.時限, "is_phantom": False
        })
        
    for date_row in planned_days_raw:
        try:
            planned_date = datetime.strptime(date_row[0], '%Y/%m/%d').date()
            yobi_code = date_row.授業曜日
            yobi_name = YOBI_MAP_REVERSE.get(yobi_code)
        except (ValueError, TypeError):
            continue
            
        periods_for_this_day = schedule_map.get(yobi_name)
        if not periods_for_this_day:
            continue
            
        for jigen in periods_for_this_day:
            current_tuple = (planned_date, jigen)
            if current_tuple not in actual_tuples:
                fake_timestamp = datetime.combine(planned_date, time(8, 50))
                merged_report_list.append({
                    "record_id": None, "timestamp": fake_timestamp, 
                    "status": "欠席", "jigen": jigen, "is_phantom": True
                })
            
    merged_report_list.sort(key=lambda x: (x["timestamp"], x["jigen"]))

    # 5. データをHTMLで使いやすいように「仕分け」する
    report_data_detail = []
    status_map = {"出席": "○", "遅刻": "△", "欠席": "×"}
    
    if merged_report_list:
        row = {"subject": subject_name_filter}
        max_recorded_count = len(merged_report_list)
        
        for i in range(max_recorded_count):
            count_str = str(i + 1) 
            item = merged_report_list[i]
            
            formatted_date = item["timestamp"].strftime('%m/%d') 
            status_symbol = status_map.get(item["status"], item["status"])
            
            row[f"count_{count_str}_id"] = item["record_id"] if not item["is_phantom"] else f"phantom-{i}"
            row[f"count_{count_str}_status"] = status_symbol
            row[f"count_{count_str}_display"] = f"{status_symbol} ({formatted_date})"
            row[f"count_{count_str}_original_status"] = item["status"]
            row[f"count_{count_str}_is_phantom"] = item["is_phantom"]
            row[f"count_{count_str}_jigen"] = item["jigen"] # ⬅️ 時限をHTMLへ
        
        report_data_detail.append(row)
    else:
        max_recorded_count = 0 
        report_data_detail.append({"subject": subject_name_filter})

    # --- ▲▲▲ (ロジックはここまで同じ) ▲▲▲ ---

    # 6. HTMLテンプレートにデータを渡す
    return render_template("my_attendance_detail.html", 
                           student_id=student_id, 
                           student_name=student_name,
                           report_data=report_data_detail, 
                           max_count=max_recorded_count,
                           selected_kiki=selected_kiki, 
                           kikis=["1", "2", "3", "4"],
                           subject_filter=subject_name_filter,
                           # --- ▼▼▼ 修正点2: ポータルビューフラグをTrueに ▼▼▼ ---
                           is_portal_view=True 
                           )

# app/routes/web.py の schedule_monthly 関数をこれに置き換えてください

@web_bp.route("/schedule_monthly", methods=["GET"])
@login_required
def schedule_monthly():
    """月間予定表 (カレンダー表示)"""
    
    # 1. 取得する年月の決定
    now = datetime.now()
    try:
        year = int(request.args.get('year', now.year))
        month = int(request.args.get('month', now.month))
    except ValueError:
        year, month = now.year, now.month

    # 2. 前月・来月の計算
    today = date(now.year, now.month, now.day)
    try:
        current_date = date(year, month, 1)
    except ValueError:
        current_date = date(now.year, now.month, 1)
        year, month = now.year, now.month
    
    prev_month_date = (current_date.replace(day=1) - timedelta(days=1))
    next_month_date = (current_date.replace(day=28) + timedelta(days=4)).replace(day=1)
    
    prev_year, prev_month = prev_month_date.year, prev_month_date.month
    next_year, next_month = next_month_date.year, next_month_date.month
    
    # 3. カレンダーデータの生成
    cal = calendar.Calendar(firstweekday=calendar.MONDAY) # 月曜日始まり
    calendar_data = []
    
    # 授業計画とTimeTableデータを事前に取得
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    
    # 1ヶ月分の授業計画をまとめて取得
    planned_dates = db.session.query(授業計画).filter(
        func.date(func.replace(授業計画.日付, '/', '-')).between(first_day, last_day)
    ).all()
    plan_map = {datetime.strptime(p.日付, '%Y/%m/%d').date(): p for p in planned_dates}

    all_timetable = TimeTable.query.order_by(TimeTable.時限).all()
    
    for week in cal.monthdatescalendar(year, month):
        week_data = []
        for date_obj in week:
            day_info = None
            if date_obj.month == month:
                weekday_name = ['月', '火', '水', '木', '金', '土', '日'][date_obj.weekday()]
                plan = plan_map.get(date_obj)
                
                day_info = {
                    "day": date_obj.day,
                    "date_str": date_obj.strftime('%Y/%m/%d'),
                    # ▼▼▼ これが不足していました！ここを追加 ▼▼▼
                    "date_query": date_obj.strftime('%Y-%m-%d'), 
                    # ▲▲▲ 追加ここまで ▲▲▲
                    "weekday": weekday_name,
                    "is_holiday": (date_obj.weekday() >= 5) or (plan and plan.授業曜日 == 0),
                    "remark": "", 
                    "classes": {},
                    "kiki_display": None,
                }
                
                # --- 時間割取得ロジック ---
                classes_for_day = OrderedDict()
                date_str_key = date_obj.strftime('%Y/%m/%d')
                
                # 1. その日の日別例外(日別時間割)を取得 (優先)
                daily_exceptions = db.session.query(日別時間割, 授業)\
                    .outerjoin(授業, 日別時間割.授業ID == 授業.授業ID)\
                    .filter(日別時間割.日付 == date_str_key)\
                    .order_by(日別時間割.時限).all()

                for exception, subject_info in daily_exceptions:
                    name = subject_info.授業科目名 if subject_info else (exception.備考 if exception.備考 else "不明/空欄")
                    classes_for_day[exception.時限] = name

                # 2. 授業計画が存在し、日別例外で埋まっていない時限をマスター時間割で埋める
                if plan and plan.授業曜日 in YOBI_MAP_REVERSE and plan.授業曜日 != 0:
                    
                    day_info["kiki_display"] = str(plan.期) # 表示重複を防ぐため数字のみに

                    kiki_str = str(plan.期)
                    yobi_str = YOBI_MAP_REVERSE.get(plan.授業曜日)

                    master_slots = db.session.query(時間割, 授業)\
                        .outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
                        .filter(時間割.学期 == kiki_str, 時間割.曜日 == yobi_str)\
                        .order_by(時間割.時限).all()
                    
                    for slot, subject_info in master_slots:
                        if slot.時限 not in classes_for_day:
                            if slot.時限 == 5:
                                name = slot.備考 if slot.備考 else "休憩/空欄"
                            else:
                                name = subject_info.授業科目名 if subject_info else "不明/空欄"
                            classes_for_day[slot.時限] = name
                    
                    if plan.備考: day_info["remark"] = plan.備考
                
                # 3. 1〜5限の枠を埋める
                periods = {p.時限: '-' for p in all_timetable}
                periods.update(classes_for_day)
                day_info["classes"] = periods
                
            week_data.append(day_info)
        calendar_data.append(week_data)
        
    is_editable = not current_user.get_id().startswith('student-')
    return render_template("schedule_monthly.html",
                           year=year,
                           month=month,
                           today=today,
                           prev_year=prev_year,
                           prev_month=prev_month,
                           next_year=next_year,
                           next_month=next_month,
                           calendar_data=calendar_data,
                           is_editable=is_editable)

@web_bp.route("/edit_daily_schedule", methods=["GET"])
@login_required
def edit_daily_schedule():
    """日別時間割（例外）を編集するページ"""
    
    # 1. 基準日の決定（クエリパラメータから取得）
    date_str = request.args.get('date')
    if not date_str:
        # 日付が指定されなければ今日
        base_date = date.today()
        date_str = base_date.strftime('%Y-%m-%d')
    else:
        try:
            base_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            flash("無効な日付形式です。")
            return redirect(url_for('web.schedule_monthly')) # 戻り先を月間カレンダーに変更

    date_jpy = base_date.strftime('%Y年%m月%d日')
    
    # 2. その日の授業計画（学期と曜日）を取得
    lesson_plan = 授業計画.query.filter(
        func.date(func.replace(授業計画.日付, '/', '-')) == base_date
    ).first()

    kiki = lesson_plan.期 if lesson_plan else 'N/A' 
    master_yobi_num = lesson_plan.授業曜日 if lesson_plan else 0
    master_yobi = YOBI_MAP_REVERSE.get(master_yobi_num, 'N/A')
    
    # 3. マスター時間割の取得
    master_schedule = []
    if master_yobi != 'N/A' and kiki != 'N/A':
        master_schedule_rows = db.session.query(
            時間割.時限, 授業.授業科目名, 授業.担当教員, 教室.教室名, 時間割.授業ID, 時間割.備考
        ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
         .outerjoin(教室, 授業.教室ID == 教室.教室ID)\
         .filter(時間割.学期 == str(kiki), 時間割.曜日 == master_yobi)\
         .order_by(時間割.時限).all()
        
        for row in master_schedule_rows:
            時限, 授業科目名, 担当教員, 教室名, 授業ID, 備考 = row
            name = 授業科目名 if 授業科目名 else "授業なし"
            display_name = 備考 if 時限 == 5 and 備考 else name
            
            master_schedule.append({
                'period': 時限,
                'name': display_name,
                'teacher': 担当教員 if 担当教員 else '教員不明',
                'room': 教室名 if 教室名 else '教室不明',
                'subject_id': 授業ID,
                'remark': 備考,
                'is_master': True
            })

    # 4. 日別例外の取得
    # ★★★ ここが修正箇所です (日別時間割.ID に修正済み) ★★★
    daily_exceptions_rows = db.session.query(
        日別時間割.ID,          # ⬅️ ここを修正しました
        日別時間割.時限, 
        授業.授業科目名, 
        授業.担当教員, 
        教室.教室名, 
        日別時間割.授業ID, 
        日別時間割.備考, 
        日別時間割.教室ID       # ⬅️ 教室IDも追加しました
    ).outerjoin(授業, 日別時間割.授業ID == 授業.授業ID)\
     .outerjoin(教室, 日別時間割.教室ID == 教室.教室ID)\
     .filter(func.date(func.replace(日別時間割.日付, '/', '-')) == base_date)\
     .order_by(日別時間割.時限).all()

    exceptions_map = {}
    for row in daily_exceptions_rows:
        # ★★★ 変数受け取りも修正 ★★★
        日別ID, 時限, 授業科目名, 担当教員, 教室名, 授業ID, 備考, 教室ID_val = row
        
        name = 授業科目名 if 授業科目名 else "授業なし"
        display_name = 備考 if 時限 == 5 and 備考 else name

        exceptions_map[時限] = {
            'period': 時限,
            'name': display_name,
            'teacher': 担当教員 if 担当教員 else '教員不明',
            'room': 教室名 if 教室名 else '教室不明',
            'subject_id': 授業ID,
            'room_id': 教室ID_val, # 教室IDもHTMLへ渡す
            'remark': 備考,
            'is_exception': True,
            'daily_id': 日別ID  # HTML側で使っている変数名に合わせる
        }

    # 5. 最終的な表示データの構築
    period_nums = [p.時限 for p in TimeTable.query.order_by(TimeTable.時限).all()]
    final_schedule = {}

    for p_num in period_nums:
        slot = {'period': p_num, 'name': '空欄', 'teacher': '-', 'room': '-', 'remark': None, 'is_exception': False, 'subject_id': None, 'room_id': None, 'daily_id': None, 'master_id': None}
        
        master_slot = next((m for m in master_schedule if m['period'] == p_num), None)
        if master_slot:
            slot.update(master_slot)
            slot['master_id'] = f"{kiki}-{master_yobi}-{p_num}"
        
        if p_num in exceptions_map:
            slot.update(exceptions_map[p_num])
        
        if p_num == 5 and slot['name'] in ['空欄', '授業なし']:
             slot['name'] = '休憩/空欄'
             
        if not slot['subject_id'] and not slot['remark'] and p_num != 5:
            slot['name'] = '空欄'
            slot['teacher'] = '-'
            slot['room'] = '-'
        elif p_num == 5 and not slot['remark']:
            slot['name'] = '休憩/空欄'
            slot['teacher'] = '-'
            slot['room'] = '-'
            
        final_schedule[p_num] = slot
        
    # 6. 選択可能な授業・教室リスト
    subjects = 授業.query.all()
    subject_list = [{'id': s.授業ID, 'name': s.授業科目名} for s in subjects]
    
    rooms = 教室.query.all()
    room_list = [{'id': r.教室ID, 'name': r.教室名} for r in rooms]

    return render_template("edit_daily_schedule.html",
                           date_str=date_str,
                           date_jpy=date_jpy,
                           kiki=kiki,
                           master_yobi=master_yobi,
                           schedule=final_schedule,
                           period_nums=period_nums,
                           subject_list=subject_list,
                           room_list=room_list,
                           is_today=base_date == date.today())


@web_bp.route("/student_logout")
@login_required
def student_logout():
    """学生専用ログアウト"""
    logout_user()
    flash("ログアウトしました。", "success")
    return redirect(url_for('web.student_login'))

@web_bp.route("/reset_database_secret_command")
@login_required
def reset_database():
    """
    【注意】データベースを強制的に作り直す隠しコマンド
    アクセスすると全データが消えます。
    """
    # 管理者以外は実行できないようにガード（念のため）
    if current_user.get_id().startswith('student-'):
        return "学生はこの操作を実行できません。", 403

    try:
        # 1. 全テーブルを削除
        db.drop_all()
        
        # 2. 全テーブルを再作成（新しい構造で）
        db.create_all()
        
        return "✅ データベースをリセットしました。（新しいカラムが追加されました）"
    except Exception as e:
        return f"❌ エラーが発生しました: {e}"

# app/routes/web.py の seed_database 関数をこれに置き換えてください

# app/routes/web.py の seed_database 関数をこれに置き換えてください

@web_bp.route("/seed_database_secret_command")
@login_required
def seed_database():
    """
    【復旧用】リアルなデータを一括登録するコマンド
    ※日付フォーマット(YYYY/MM/DD)を自動補正します
    """
    from datetime import time, datetime
    
    try:
        # --- 1. 教室 (Rooms) ---
        if 教室.query.count() == 0:
            print("教室データを挿入中...")
            room_data = [
                (999, '教室不明'), (1205, 'A205'), (2102, 'B102/103'), (2201, 'B201'),
                (2202, 'B202'), (2204, 'B204'), (2205, 'B205'), (2301, 'B301'),
                (2302, 'B302'), (2303, 'B303'), (2304, 'B304'), (2305, 'B305'),
                (2306, 'B306(視聴覚室)'), (3101, 'C101(生産ロボット室)'), (3103, 'C103(開発課題実習室)'),
                (3201, 'C201'), (3202, 'C202(応用課程計測制御応用実習室)'), (3203, 'C203'), (3204, 'C204'),
                (3231, 'C231(資料室)'), (3301, 'C301(マルチメディア実習室)'), (3302, 'C302(システム開発実習室)'),
                (3303, 'C303(システム開発実習室Ⅱ)'), (3304, 'C304/305(応用課程生産管理ネットワーク応用実習室)'),
                (3306, 'C306(共通実習室)'), (4102, 'D102(回路基板加工室)'), (4201, 'D201(開発課題実習室)'),
                (4202, 'D202(電子情報技術科教官室)'), (4231, 'D231(準備室)'), (4301, 'D301'),
                (4302, 'D302(PC実習室)')
            ]
            db.session.add_all([教室(教室ID=rid, 教室名=rname) for rid, rname in room_data])
            db.session.commit()

        # --- 2. 授業科目 (Subjects) ---
        if 授業.query.count() == 0:
            print("授業データを挿入中...")
            subject_data = [
                (301, '工業技術英語', 'ワット', 2201), (302, '生産管理', '佐藤先生', None), (303, '品質管理', '田中先生', None),
                (304, '経営管理', None, None), (305, '創造的開発技法', None, None), (306, '工業法規', None, None),
                (307, '職業能力開発体系論', None, None), (308, '機械工学概論', '上野', 2301), (309, 'アナログ回路応用設計技術', '諏訪原', 3301),
                (310, 'ディジタル回路応用設計技術', '岡田', 3301), (311, '複合電子回路応用設計技術', '近藤', 3301), (312, 'ロボット工学', '杉原', 3101),
                (313, '通信プロトコル実装設計', '中山', 3301), (314, 'セキュアシステム設計', '寺内', 3301), (315, '組込システム設計', '下泉', 3302),
                (316, '安全衛生管理', None, None), (317, '機械工作・組立実習', '生産機械', 3302), (318, '実装設計製作実習', '近藤', 3302),
                (319, 'EMC応用実習', None, None), (320, '電子回路設計製作応用実習', '諏訪原', 3302), (321, '制御回路設計製作実習', '玉井', 3302),
                (322, 'センシングシステム構築実習', '玉井', 3302), (323, 'ロボット工学実習', '杉原', 3301), (324, '通信プロトコル実装実習', '中山', 3302),
                (325, 'セキュアシステム構築実習', '寺内', 3301), (326, '生産管理システム構築実習Ⅰ', '中山', 3301), (327, '生産管理システム構築実習Ⅱ', '中山', 3301),
                (328, '組込システム構築実習', '下泉', 3302), (329, '組込デバイス設計実習', '岡田', 3302), (330, '組込システム構築課題実習', None, None),
                (331, '電子通信機器設計制作課題実習', None, None), (332, 'ロボット機器制作課題実習(電子情報)', None, None), (333, 'ロボット機器運用課題実習(電子情報)', None, None),
                (334, '電子装置設計製作応用課題実習', None, None), (335, '組込システム応用課題実習', None, None), (336, '通信システム応用課題実習', None, None),
                (337, 'ロボットシステム応用課題実習', None, None), (380, '標準課題Ⅰ', None, 3301), (381, '標準課題Ⅱ', '全員', None),
                (390, '開発課題', None, None),
                (0, '--- 授業なし/空欄 ---', None, None)
            ]
            db.session.add_all([授業(授業ID=sid, 授業科目名=sname, 担当教員=teacher, 教室ID=room) for sid, sname, teacher, room in subject_data])
            db.session.commit()

        # --- 3. 学生 (Students) ---
        if 学生.query.count() == 0:
            print("学生データを挿入中...")
            student_data = [
                (222521301, '青井 渓一郎'), (222521302, '赤坂 龍成'), (222521303, '秋好 拓海'), (222521304, '伊川 翔'),
                (222521305, '岩切 亮太'), (222521306, '上田 和輝'), (222521307, '江本 龍之介'), (222521308, '大久保 碧瀧'),
                (222521309, '加來 涼雅'), (222521310, '梶原 悠平'), (222521311, '管野 友富紀'), (222521312, '髙口 翔真'),
                (222521313, '古城 静雅'), (222521314, '小柳 知也'), (222521315, '酒元 翼'), (222521316, '座光寺 孝彦'),
                (222521317, '佐野 勇太'), (222521318, '清水 健心'), (222521319, '新谷 雄飛'), (222521320, '関原 響樹'),
                (222521321, '髙橋 優人'), (222521322, '武富 義樹'), (222521323, '内藤 俊介'), (222521324, '野田 千尋'),
                (222521325, '野中 雄学'), (222521326, '東 奈月'), (222521327, '古田 雅也'), (222521328, '牧野 倭大'),
                (222521329, '松隈 駿介'), (222521330, '宮岡 嘉熙')
            ]
            students = []
            for sid, sname in student_data:
                s = 学生(学生ID=sid, 学生名=sname)
                s.set_password('password')
                students.append(s)
            db.session.add_all(students)
            db.session.commit()

        # --- 4. TimeTable ---
        if TimeTable.query.count() == 0:
            print("TimeTableデータを挿入中...")
            period_data = [
                (1, time(8, 50), time(10, 30), '1限目'),
                (2, time(10, 35), time(12, 15), '2限目'),
                (3, time(13, 0), time(14, 40), '3限目'),
                (4, time(14, 45), time(16, 25), '4限目'),
                (5, time(16, 40), time(18, 20), '5限目'),
            ]
            db.session.add_all([TimeTable(時限=p, 開始時刻=s, 終了時刻=e, 備考=r) for p, s, e, r in period_data])
            db.session.commit()

        # --- 5. 時間割 (Schedule) ---
        if 時間割.query.count() == 0:
            print("時間割データを挿入中...")
            schedule_data = [
                (1, '月', 1, 325, None), (1, '月', 2, 325, None), (1, '月', 4, 313, None), (1, '火', 1, 314, None),
                (1, '火', 2, 309, None), (1, '火', 3, 310, None), (1, '火', 4, 311, None), (1, '水', 1, 312, None),
                (1, '水', 2, 312, None), (1, '木', 1, 315, None), (1, '木', 2, 328, None), (1, '木', 3, 322, None),
                (1, '木', 4, 322, None), (1, '金', 1, 315, None), (1, '金', 2, 328, None), (1, '金', 3, 318, None),
                (1, '金', 4, 318, None), (2, '月', 1, 325, None), (2, '月', 2, 325, None), (2, '月', 3, 301, None),
                (2, '月', 4, 313, None), (2, '火', 1, 325, None), (2, '火', 2, 309, None), (2, '火', 3, 310, None),
                (2, '火', 4, 311, None), (2, '水', 1, 324, None), (2, '水', 2, 324, None), (2, '木', 1, 323, None),
                (2, '木', 2, 323, None), (2, '木', 3, 315, None), (2, '木', 4, 328, None), (2, '金', 1, 315, None),
                (2, '金', 2, 328, None), (2, '金', 3, 322, None), (2, '金', 4, 322, None), (3, '月', 1, 327, None),
                (3, '月', 2, 327, None), (3, '月', 3, 380, None), (3, '月', 4, 380, None), (3, '火', 1, 317, None),
                (3, '火', 2, 317, None), (3, '火', 3, 380, None), (3, '火', 4, 380, None), (3, '水', 1, 329, None),
                (3, '水', 2, 329, None), (3, '水', 3, 308, None), (3, '木', 1, 380, None), (3, '木', 2, 380, None),
                (3, '木', 3, 380, None), (3, '木', 4, 380, None), (3, '金', 1, 321, None), (3, '金', 2, 321, None),
                (3, '金', 3, 380, None), (3, '金', 4, 380, None), (4, '月', 1, 381, None), (4, '月', 2, 381, None),
                (4, '火', 1, 317, None), (4, '火', 2, 317, None), (4, '火', 3, 381, None), (4, '火', 4, 381, None),
                (4, '水', 1, 329, None), (4, '水', 2, 329, None), (4, '水', 3, 308, None), (4, '木', 1, 331, None),
                (4, '木', 2, 331, None), (4, '木', 3, 331, None), (4, '木', 4, 331, None), (4, '金', 1, 331, None),
                (4, '金', 2, 331, None), (1, '月', 5, 0, 'なし'), (1, '火', 5, 0, 'なし'), (1, '水', 5, 0, 'なし'),
                (1, '木', 5, 0, 'なし'), (1, '金', 5, 0, 'なし'), (1, '月', 3, 301, None), (3, '月', 5, 0, 'なし')
            ]
            db.session.add_all([時間割(学期=str(k), 曜日=y, 時限=j, 授業ID=s, 備考=b) for k, y, j, s, b in schedule_data])
            db.session.commit()

        # --- 6. 授業計画 (Calendar) ---
        if 授業計画.query.count() == 0:
            print("授業計画データを挿入中...")
            plan_data = [
                # --- 第1期 ---
                ('2025/4/8', 1, 2, None), ('2025/4/9', 1, 3, None), ('2025/4/10', 1, 4, None), ('2025/4/11', 1, 5, None),
                ('2025/4/14', 1, 1, None), ('2025/4/15', 1, 2, None), ('2025/4/16', 1, 3, None), ('2025/4/17', 1, 4, None), ('2025/4/18', 1, 5, None),
                ('2025/4/21', 1, 1, None), ('2025/4/22', 1, 2, None), ('2025/4/23', 1, 3, None), ('2025/4/24', 1, 4, None), ('2025/4/25', 1, 5, None),
                ('2025/4/28', 1, 1, None),
                ('2025/5/7', 1, 3, None), ('2025/5/8', 1, 4, None), ('2025/5/9', 1, 5, None),
                ('2025/5/12', 1, 1, None), ('2025/5/13', 1, 2, None), ('2025/5/15', 1, 4, None), ('2025/5/16', 1, 5, None),
                ('2025/5/19', 1, 1, None), ('2025/5/20', 1, 2, None), ('2025/5/21', 1, 3, None), ('2025/5/22', 1, 4, None), ('2025/5/23', 1, 5, None),
                ('2025/5/26', 1, 1, None), ('2025/5/27', 1, 2, None), ('2025/5/28', 1, 3, None), ('2025/5/29', 1, 4, None), ('2025/5/30', 1, 5, None),
                ('2025/6/2', 1, 1, None), ('2025/6/3', 1, 2, None), ('2025/6/4', 1, 3, None), ('2025/6/5', 1, 4, None), ('2025/6/6', 1, 5, None),
                ('2025/6/9', 1, 1, None), ('2025/6/10', 1, 2, None), ('2025/6/11', 1, 3, None), ('2025/6/12', 1, 4, None), ('2025/6/13', 1, 5, None),
                ('2025/6/16', 1, 1, None), ('2025/6/17', 1, 2, None), ('2025/6/18', 1, 3, None),
                # --- 第2期 ---
                ('2025/6/19', 2, 4, None), ('2025/6/20', 2, 5, None),
                ('2025/6/23', 2, 1, None), ('2025/6/24', 2, 2, None), ('2025/6/25', 2, 3, None), ('2025/6/26', 2, 4, None), ('2025/6/27', 2, 5, None),
                ('2025/6/30', 2, 1, None), ('2025/7/1', 2, 2, None), ('2025/7/2', 2, 3, None), ('2025/7/3', 2, 4, None), ('2025/7/4', 2, 5, None),
                ('2025/7/7', 2, 1, None), ('2025/7/8', 2, 2, None), ('2025/7/9', 2, 3, None), ('2025/7/10', 2, 4, None), ('2025/7/11', 2, 5, None),
                ('2025/7/14', 2, 1, None),
                # --- 第9期 (夏期集中) ---
                ('2025/7/15', 9, 0, '夏期集中実習'), ('2025/7/16', 9, 0, '夏期集中実習'), ('2025/7/17', 9, 0, '夏期集中実習'),
                ('2025/7/18', 9, 0, '夏期集中実習'), ('2025/7/21', 9, 0, '夏期集中実習'), ('2025/7/22', 9, 0, '夏期集中実習'),
                ('2025/7/23', 9, 0, '夏期集中実習'), ('2025/7/24', 9, 0, '夏期集中実習'), ('2025/7/25', 9, 0, '夏期集中実習'),
                # --- 第2期 (続き) ---
                ('2025/8/20', 2, 3, None), ('2025/8/21', 2, 4, None), ('2025/8/22', 2, 5, None), ('2025/8/23', 2, 2, None),
                ('2025/8/25', 2, 1, None), ('2025/8/26', 2, 2, None), ('2025/8/27', 2, 3, None), ('2025/8/28', 2, 4, None), ('2025/8/29', 2, 5, None),
                ('2025/9/1', 2, 1, None), ('2025/9/2', 2, 2, None), ('2025/9/3', 2, 3, None), ('2025/9/4', 2, 4, None), ('2025/9/5', 2, 5, None),
                ('2025/9/8', 2, 1, None), ('2025/9/9', 2, 2, None), ('2025/9/10', 2, 3, None), ('2025/9/11', 2, 4, None), ('2025/9/12', 2, 5, None),
                ('2025/9/16', 2, 2, None), ('2025/9/17', 2, 3, None), ('2025/9/18', 2, 1, None), ('2025/9/19', 2, 5, None),
                ('2025/9/22', 2, 1, None), ('2025/9/24', 2, 3, None), ('2025/9/25', 2, 4, None), ('2025/9/26', 2, 2, None),
                ('2025/9/29', 2, 0, '補講日等'),
                # --- 第10期 (秋期集中) ---
                ('2025/9/30', 10, 0, '秋期集中実習'), ('2025/10/1', 10, 0, '秋期集中実習'), ('2025/10/2', 10, 0, '秋期集中実習'),
                ('2025/10/3', 10, 0, '秋期集中実習'), ('2025/10/6', 10, 0, '秋期集中実習'), ('2025/10/7', 10, 0, '秋期集中実習'),
                ('2025/10/8', 10, 0, '秋期集中実習'), ('2025/10/9', 10, 0, '秋期集中実習'), ('2025/10/10', 10, 0, '秋期集中実習'),
                # --- 第3期 ---
                ('2025/10/14', 3, 2, None), ('2025/10/15', 3, 3, None), ('2025/10/16', 3, 4, None), ('2025/10/17', 3, 5, None),
                ('2025/10/20', 3, 1, None), ('2025/10/21', 3, 2, None), ('2025/10/22', 3, 3, None), ('2025/10/23', 3, 4, None), ('2025/10/24', 3, 5, None),
                ('2025/10/27', 3, 1, None), ('2025/10/28', 3, 2, None), ('2025/10/29', 3, 3, None), ('2025/10/30', 3, 4, None), ('2025/10/31', 3, 5, None),
                ('2025/11/4', 3, 2, None), ('2025/11/5', 3, 3, None), ('2025/11/6', 3, 1, None), ('2025/11/7', 3, 5, None),
                ('2025/11/10', 3, 1, None), ('2025/11/11', 3, 2, None), ('2025/11/12', 3, 3, None), ('2025/11/13', 3, 4, None), ('2025/11/14', 3, 5, None),
                ('2025/11/17', 3, 1, None), ('2025/11/18', 3, 2, None), ('2025/11/19', 3, 3, None), ('2025/11/20', 3, 4, None), ('2025/11/21', 3, 5, None),
                ('2025/11/25', 3, 1, None), ('2025/11/26', 3, 3, None), ('2025/11/27', 3, 4, None), ('2025/11/28', 3, 5, None),
                ('2025/12/1', 3, 1, None), ('2025/12/2', 3, 2, None), ('2025/12/3', 3, 3, None), ('2025/12/4', 3, 4, None),
                # 12/5 はデータがないのでスキップ（空欄のままでOK）
                ('2025/12/8', 3, 1, None), ('2025/12/9', 3, 2, None), ('2025/12/10', 3, 3, None), ('2025/12/11', 3, 4, None), ('2025/12/12', 3, 5, None),
                ('2025/12/15', 3, 1, None), ('2025/12/16', 3, 2, None), ('2025/12/18', 3, 4, None), ('2025/12/19', 3, 5, None),
                # --- 第4期 ---
                # ★修正: 12/17は「第4期」ではなく「第3期」が正しいようなので修正しておきます
                ('2025/12/17', 3, 3, None), 
                ('2025/12/22', 4, 1, None), ('2025/12/23', 4, 2, None), ('2025/12/24', 4, 3, None), ('2025/12/25', 4, 4, None), ('2025/12/26', 4, 5, None),
                ('2026/1/13', 4, 1, None), ('2026/1/14', 4, 3, None), ('2026/1/15', 4, 4, None), ('2026/1/16', 4, 5, None),
                ('2026/1/19', 4, 1, None), ('2026/1/20', 4, 2, None), ('2026/1/21', 4, 3, None), ('2026/1/22', 4, 4, None), ('2026/1/23', 4, 5, None),
                ('2026/1/26', 4, 1, None), ('2026/1/27', 4, 2, None), ('2026/1/28', 4, 3, None), ('2026/1/29', 4, 4, None), ('2026/1/30', 4, 5, None),
                ('2026/2/2', 4, 1, None), ('2026/2/3', 4, 2, None), ('2026/2/4', 4, 3, None), ('2026/2/6', 4, 5, None),
                ('2026/2/9', 4, 1, None), ('2026/2/10', 4, 2, None), ('2026/2/12', 4, 4, None), ('2026/2/13', 4, 5, None),
                ('2026/2/16', 4, 1, None), ('2026/2/17', 4, 2, None), ('2026/2/18', 4, 3, None), ('2026/2/19', 4, 4, None), ('2026/2/20', 4, 5, None),
                ('2026/2/21', 4, 4, '補講日等'),
                ('2026/2/24', 4, 2, None), ('2026/2/25', 4, 3, None), ('2026/2/26', 4, 4, None), ('2026/2/27', 4, 5, None),
                ('2026/3/2', 4, 1, None), ('2026/3/3', 4, 2, None), ('2026/3/4', 4, 3, None), ('2026/3/5', 4, 4, None), ('2026/3/6', 4, 5, None),
                ('2026/3/9', 4, 1, None), ('2026/3/10', 4, 2, None), ('2026/3/11', 4, 0, '補講日等')
            ]
            
            # 【重要】ここで日付を正しい形式(YYYY/MM/DD)に変換してからDBに入れます
            # これがないと「12月1日」が検索できずにバグります
            initial_plan = []
            for d, k, y, r in plan_data:
                formatted_date = datetime.strptime(d, '%Y/%m/%d').strftime('%Y/%m/%d')
                initial_plan.append(授業計画(日付=formatted_date, 期=k, 授業曜日=y, 備考=r))
            
            db.session.add_all(initial_plan)
            db.session.commit()

        return "✅ 初期データを投入しました！（日付バグ修正・補完済み）"

    except Exception as e:
        db.session.rollback()
        return f"❌ エラー: {e}"

