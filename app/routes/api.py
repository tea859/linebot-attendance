from flask import Blueprint, request, jsonify, current_app # ★current_appを追加
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models import 学生, 授業, 時間割, 授業計画, 出席記録, 在室履歴, TimeTable, ReportRecord, 日別時間割, 教室
from ..services import save_image, check_and_send_alert, 判定, get_current_kiki, YOBI_MAP, YOBI_MAP_REVERSE, sensor_data, auth_commands

api_bp = Blueprint('api', __name__)

# --- ポータル顔認証 ---
@api_bp.route("/api/portal_face_auth", methods=["POST"])
@login_required
def api_portal_face_auth():
    """ポータルからの顔認証出席登録"""
    try:
        data = request.get_json()
        image_data = data.get("image")
        
        if not image_data:
            return jsonify({"status": "error", "message": "画像データがありません"}), 400

        student = current_user
        student_id = student.学生ID
        
        # 画像を保存
        saved_filename = save_image(image_data, student_id)
        print(f"📸 [Web認証] {student.学生名} (ID:{student_id}) の画像を保存: {saved_filename}")

        # 授業判定
        now = datetime.now()
        target_period = None
        
        all_periods = TimeTable.query.all()
        for p in all_periods:
            p_start = datetime.combine(now.date(), p.開始時刻)
            p_end = datetime.combine(now.date(), p.終了時刻)
            if (p_start - timedelta(minutes=20)) <= now <= (p_end + timedelta(minutes=20)):
                target_period = p.時限
                break
        
        if not target_period:
            return jsonify({"status": "error", "message": "現在は授業時間外です"}), 200

        # 重複チェック用変数
        today_yobi_str = YOBI_MAP_REVERSE.get((now.weekday() + 1) % 7)
        kiki = get_current_kiki()
        class_row = 時間割.query.filter_by(学期=kiki, 曜日=today_yobi_str, 時限=target_period).first()
        subject_id = class_row.授業ID if class_row else 0
        
        if subject_id == 0:
             return jsonify({"status": "error", "message": "この時間は授業がありません"}), 200

        existing = 出席記録.query.filter_by(学生ID=student_id, 授業ID=subject_id, 出席日付=now.date(), 時限=target_period).first()

        if not existing:
            new_attendance = 出席記録(
                学生ID=student_id,
                授業ID=subject_id,
                出席時刻=now,
                状態="出席",
                時限=target_period
            )
            db.session.add(new_attendance)
            db.session.commit()
            return jsonify({"status": "success", "message": f"{target_period}限 ({class_row.授業.授業科目名}) の出席を受け付けました！"})
        else:
            return jsonify({"status": "info", "message": "既に出席済みです"})

    except Exception as e:
        print(f"Portal Auth Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- 日別スケジュール取得 ---
@api_bp.route("/api/get_daily_schedule", methods=["GET"])
@login_required
def api_get_daily_schedule():
    """(API) 指定日のスケジュールデータ（マスター＋例外）を取得してJSONで返す"""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({"status": "error", "message": "日付が必要です"}), 400

    try:
        base_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"status": "error", "message": "日付形式エラー"}), 400

    date_db_str = base_date.strftime('%Y/%m/%d')
    lesson_plan = 授業計画.query.filter_by(日付=date_db_str).first()
    
    kiki = str(lesson_plan.期) if lesson_plan else None
    master_yobi_num = lesson_plan.授業曜日 if lesson_plan else 0
    master_yobi = YOBI_MAP_REVERSE.get(master_yobi_num)

    master_schedule = []
    if master_yobi and kiki:
        master_rows = db.session.query(
            時間割.時限, 授業.授業科目名, 授業.担当教員, 教室.教室名, 時間割.授業ID, 時間割.備考
        ).outerjoin(授業, 時間割.授業ID == 授業.授業ID)\
         .outerjoin(教室, 授業.教室ID == 教室.教室ID)\
         .filter(時間割.学期 == kiki, 時間割.曜日 == master_yobi)\
         .all()
        
        for row in master_rows:
            master_schedule.append({
                'period': row.時限,
                'name': (row.備考 if row.時限 == 5 and row.備考 else (row.授業科目名 or "授業なし")),
                'teacher': row.担当教員 or '教員不明',
                'room': row.教室名 or '教室不明',
                'subject_id': row.授業ID,
                'remark': row.備考
            })

    exceptions_rows = db.session.query(
        日別時間割.ID, 日別時間割.時限, 授業.授業科目名, 授業.担当教員, 教室.教室名, 
        日別時間割.授業ID, 日別時間割.備考, 日別時間割.教室ID
    ).outerjoin(授業, 日別時間割.授業ID == 授業.授業ID)\
     .outerjoin(教室, 日別時間割.教室ID == 教室.教室ID)\
     .filter(日別時間割.日付 == date_db_str)\
     .all()

    exceptions_map = {}
    for row in exceptions_rows:
        exceptions_map[row.時限] = {
            'period': row.時限,
            'name': (row.備考 if row.時限 == 5 and row.備考 else (row.授業科目名 or "授業なし")),
            'teacher': row.担当教員 or '教員不明',
            'room': row.教室名 or '教室不明',
            'subject_id': row.授業ID,
            'room_id': row.教室ID,
            'remark': row.備考,
            'daily_id': row.ID,
            'is_exception': True
        }

    final_schedule = {}
    for p in range(1, 6):
        slot = {
            'period': p, 'name': '空欄', 'teacher': '-', 'room': '-', 
            'subject_id': 0, 'room_id': 0, 'remark': '', 
            'daily_id': None, 'is_exception': False
        }
        m_slot = next((m for m in master_schedule if m['period'] == p), None)
        if m_slot:
            slot.update(m_slot)
            if p == 5 and slot['name'] in ['空欄', '授業なし']: slot['name'] = '休憩/空欄'
        
        if p in exceptions_map:
            slot.update(exceptions_map[p])
        
        if not slot['subject_id'] and not slot['remark'] and p != 5:
             slot['name'] = '空欄'

        final_schedule[p] = slot

    subjects = [{'id': s.授業ID, 'name': s.授業科目名, 'default_room_id': s.教室ID if s.教室ID else 0} for s in 授業.query.all()]
    rooms = [{'id': r.教室ID, 'name': r.教室名} for r in 教室.query.all()]

    return jsonify({
        "status": "success",
        "date_str": date_str,
        "date_jpy": base_date.strftime('%Y年%m月%d日'),
        "schedule": final_schedule,
        "subjects": subjects,
        "rooms": rooms
    })

# --- スケジュール更新API ---
@api_bp.route('/api/schedule_update', methods=['POST'])
def api_schedule_update():
    token = request.form.get('token')
    # ここで SCHEDULE_API_TOKEN が必要なら services.py 等からimportするか、os.environから取得
    import os
    if token != os.environ.get('SCHEDULE_API_TOKEN'):
        return jsonify({'error': 'Unauthorized: Invalid API token'}), 401

    try:
        kiki = request.form.get('kiki')
        day = request.form.get('day')
        period = request.form.get('period')
        subject_id = request.form.get('subject_id')
        remark = request.form.get('remark')
        
        if not all([kiki, day, period, subject_id]):
            return jsonify({'success': False, 'error': 'Missing data'}), 400

        try:
            period_int = int(period)
            subject_id_int = int(subject_id)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid integer'}), 400

        existing_schedule = 時間割.query.filter_by(学期=str(kiki), 曜日=str(day), 時限=str(period)).first()
        kiki_int = int(kiki)

        if period_int == 5:
            if existing_schedule:
                existing_schedule.科目ID = None
                existing_schedule.備考 = remark
                db.session.commit()
            elif remark:
                new_schedule = 時間割(学期=kiki_int, 曜日=day, 時限=period_int, 授業ID=None, 備考=remark)
                db.session.add(new_schedule)
                db.session.commit()
        else:
            if subject_id_int == 0:
                if existing_schedule:
                    db.session.delete(existing_schedule)
                    db.session.commit()
            else:
                if existing_schedule:
                    existing_schedule.科目ID = subject_id_int
                    existing_schedule.備考 = None
                    db.session.commit()
                else:
                    new_schedule = 時間割(学期=kiki_int, 曜日=day, 時限=period_int, 授業ID=subject_id_int, 備考=None)
                    db.session.add(new_schedule)
                    db.session.commit()

        return jsonify({'success': True, 'message': 'Updated'}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Schedule update failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# --- 顔認証出席 ---
@api_bp.route("/api/register_attendance", methods=["POST"]) 
def api_register_attendance():
    try:
        data = request.get_json()
        if not data or "student_id" not in data:
            return jsonify({"error": "学生IDが必要です。"}), 400
            
        student_id = data.get("student_id")
        now = datetime.now()
        
        today_str = f"{now.year}/{now.month}/{now.day}"
        plan_row = 授業計画.query.get(today_str)
        if not plan_row: return jsonify({"error": "授業計画外です"}), 200 
        
        kiki, yobi_code = plan_row.期, plan_row.授業曜日
        period_row = TimeTable.query.filter(TimeTable.開始時刻 <= now.time(), TimeTable.終了時刻 >= now.time()).first()
        
        if not period_row: return jsonify({"error": "時間外です"}), 200
            
        current_period = period_row.時限
        yobi_str = YOBI_MAP_REVERSE.get(yobi_code)
        
        class_row = 時間割.query.filter_by(学期=str(kiki), 曜日=yobi_str, 時限=current_period).first()
        
        if not class_row or class_row.授業ID == 0:
            return jsonify({"error": "授業がありません"}), 200
            
        class_id = class_row.授業ID
        status = 判定(current_period, now)
        
        try:
            new_attendance = 出席記録(
                学生ID=student_id,
                授業ID=class_id,
                出席時刻=now,
                状態=status,
                時限=current_period
            )
            db.session.add(new_attendance)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        
        # 在室履歴
        room = 授業.query.get(class_id)
        room_id = room.教室ID if room and room.教室ID is not None else 999 
        existing_session = 在室履歴.query.filter_by(学生ID=student_id, 退室時刻=None).first()
        if not existing_session:
            new_session = 在室履歴(学生ID=student_id, 教室ID=room_id, 入室時刻=now, 退室時刻=None)
            db.session.add(new_session)
            db.session.commit()
            
        check_and_send_alert(student_id, class_id)
        return jsonify({"success": True, "message": f"{status}で記録しました"}), 201

    except Exception as e:
        db.session.rollback()
        print(f"API Error: {e}")
        return jsonify({"error": str(e)}), 500

# --- ステータス更新 ---
@api_bp.route("/api/update_status", methods=["POST"])
@login_required
def api_update_status():
    data = request.get_json()
    record_rowid = data.get("record_id")
    new_status = data.get("new_status")

    if not record_rowid or not new_status:
        return jsonify({"error": "不足データあり"}), 400

    try:
        record_to_update = 出席記録.query.get(record_rowid)
        if record_to_update:
            record_to_update.状態 = new_status
            db.session.commit()
            return jsonify({"success": True, "message": "更新しました"}), 200
        else:
             return jsonify({"error": "記録が見つかりません"}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# --- アラート数 ---
@api_bp.route('/api/alerts_count')
@login_required
def api_alerts_count():
    try:
        count = db.session.query(ReportRecord.record_id).filter(ReportRecord.is_resolved == False).count()
        return jsonify({'count': count})
    except Exception as e:
        # ★修正: app.logger -> current_app.logger
        current_app.logger.error(f"アラート件数のカウントに失敗: {e}")
        return jsonify({'count': 0, 'error': str(e)}), 500

# --- 在室状況 ---
@api_bp.route("/api/status")
def api_status():
    # 自動出席チェック等は省略せず必要なら services.py に切り出すのが理想ですが、
    # ここでは既存ロジックをそのまま維持します。
    active_sessions_data = db.session.query(
        在室履歴.学生ID, 教室.教室名, 在室履歴.入室時刻, 在室履歴.備考 
    ).outerjoin(教室, 在室履歴.教室ID == 教室.教室ID).filter(在室履歴.退室時刻 == None).all()
    
    active_student_ids = {s[0] for s in active_sessions_data}

    # 自動出席ロジック (簡易版)
    if active_student_ids:
        try:
            now = datetime.now()
            today_str = f"{now.year}/{now.month}/{now.day}"
            plan_row = 授業計画.query.get(today_str)
            if plan_row:
                kiki, yobi_code = plan_row.期, plan_row.授業曜日
                period_row = TimeTable.query.filter(TimeTable.開始時刻 <= now.time(), TimeTable.終了時刻 >= now.time()).first()
                if period_row:
                    current_period = period_row.時限
                    yobi_str = YOBI_MAP_REVERSE.get(yobi_code)
                    class_row = 時間割.query.filter_by(学期=str(kiki), 曜日=yobi_str, 時限=current_period).first()
                    if class_row and class_row.授業ID != 0:
                        class_id = class_row.授業ID
                        today_date = now.date()
                        existing_records = db.session.query(出席記録.学生ID).filter(
                            出席記録.学生ID.in_(active_student_ids), 
                            出席記録.授業ID == class_id,
                            出席記録.時限 == current_period,
                            出席記録.出席日付 == today_date
                        ).all()
                        recorded_student_ids = {r[0] for r in existing_records}
                        students_to_mark = active_student_ids - recorded_student_ids
                        
                        new_records = []
                        for student_id in students_to_mark:
                            status = 判定(current_period, now)
                            new_records.append(出席記録(学生ID=student_id, 授業ID=class_id, 出席時刻=now, 状態=status, 時限=current_period))
                        
                        if new_records:
                            db.session.add_all(new_records)
                            db.session.commit()
                            for record in new_records:
                                check_and_send_alert(record.学生ID, record.授業ID)
        except Exception as e:
            db.session.rollback()
            print(f"Auto-attend Error: {e}")

    # 一覧作成
    all_students = 学生.query.order_by(学生.学生ID).all()
    active_sessions = db.session.query(在室履歴.学生ID, 教室.教室名, 在室履歴.入室時刻, 在室履歴.備考).outerjoin(教室, 在室履歴.教室ID == 教室.教室ID).filter(在室履歴.退室時刻 == None).all()

    now = datetime.now()
    active_map = {}
    for sid, room_name, 入室時刻, 備考 in active_sessions:
        try:
            滞在秒 = int((now - 入室時刻).total_seconds())
            hh = 滞在秒 // 3600
            mm = (滞在秒 % 3600) // 60
            ss = 滞在秒 % 60
            duration = f"{hh:02}:{mm:02}:{ss:02}"
            status = "一時退出中" if 備考 == "一時退出中" else "在室"
            active_map[sid] = {"status": status, "room": room_name or '教室不明', "entry": 入室時刻.strftime("%Y-%m-%d %H:%M:%S"), "duration": duration}
        except:
             active_map[sid] = {"status": "Error", "room": "?", "entry": "", "duration": ""}

    result = []
    for s in all_students:
        if s.学生ID in active_map:
            d = active_map[s.学生ID]
            result.append({"name": s.学生名, "status": d["status"], "room": d["room"], "entry": d["entry"], "duration": d["duration"]})
        else:
            result.append({"name": s.学生名, "status": "退出", "room": "", "entry": "", "duration": ""})

    return jsonify({"students": result})

# --- センサー受信 ---
@api_bp.route("/api/sensor", methods=["POST"])
def receive_sensor():
    data = request.get_json()
    if data:
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "temperature": data.get("temperature"),
            "humidity": data.get("humidity"),
            "door": data.get("door", "不明"),
            "key": data.get("key", "不明"),
            "light": data.get("light", "不明")
        }
        sensor_data.append(entry)
        if len(sensor_data) > 100: sensor_data.pop(0)
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Invalid data"}), 400

@api_bp.route("/api/sensor_status")
def api_sensor_status():
    if sensor_data:
        return jsonify(sensor_data[-1])
    else:
        return jsonify({})

# --- 日別スケジュール編集API ---
@api_bp.route("/api/edit_daily_schedule", methods=["POST"])
@login_required
def api_edit_daily_schedule():
    try:
        data = request.get_json()
        date_str = data.get('date')
        period = data.get('period')
        action = data.get('action')
        daily_id = data.get('daily_id')
        subject_id = data.get('subject_id') 
        room_id = data.get('room_id') 
        remark = data.get('remark')
        
        if not date_str or not period:
            return jsonify({"status": "error", "message": "必須項目不足"}), 400

        try:
            date_db = datetime.strptime(date_str, '%Y-%m-%d').strftime('%Y/%m/%d')
            period = int(period)
        except ValueError:
            return jsonify({"status": "error", "message": "形式エラー"}), 400

        if action == 'add' or action == 'update':
            if period == 5:
                subject_id = None 
                room_id = None
            elif period != 5 and (not subject_id or not room_id):
                return jsonify({"status": "error", "message": "授業と教室は必須"}), 400

            new_subject_id = int(subject_id) if subject_id else None
            new_room_id = int(room_id) if room_id else 0
            new_remark = remark if remark else None
            
            if new_subject_id and new_room_id == 0:
                subject_obj = 授業.query.get(new_subject_id)
                if subject_obj and subject_obj.教室ID: new_room_id = subject_obj.教室ID
            
            daily_exception = 日別時間割.query.filter_by(日付=date_db, 時限=period).first()
            if daily_exception:
                daily_exception.授業ID = new_subject_id
                daily_exception.教室ID = new_room_id
                daily_exception.備考 = new_remark
                db.session.commit()
            else:
                new_exception = 日別時間割(日付=date_db, 時限=period, 授業ID=new_subject_id, 教室ID=new_room_id, 備考=new_remark)
                db.session.add(new_exception)
                db.session.commit()
            message = "更新しました"
        
        elif action == 'delete':
            if not daily_id: return jsonify({"status": "error", "message": "ID不足"}), 400
            daily_exception = 日別時間割.query.filter_by(ID=daily_id, 日付=date_db, 時限=period).first()
            if daily_exception:
                db.session.delete(daily_exception)
                db.session.commit()
                message = "削除しました"
            else:
                return jsonify({"status": "error", "message": "対象が見つかりません"}), 404
        else:
            return jsonify({"status": "error", "message": "無効な操作"}), 400

        return jsonify({"status": "success", "message": message, "date": date_str})

    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

# --- リモート認証用 ---
@api_bp.route("/api/trigger_remote_auth", methods=["POST"])
@login_required
def api_trigger_remote_auth():
    student_id = current_user.学生ID
    auth_commands[str(student_id)] = "START"
    return jsonify({"status": "success"})

@api_bp.route("/api/poll_command", methods=["GET"])
def api_poll_command():
    student_id = request.args.get("student_id")
    command = auth_commands.pop(str(student_id), None)
    return jsonify({"command": command})

@api_bp.route("/api/report_remote_result", methods=["POST"])
def api_report_remote_result():
    data = request.get_json()
    student_id = data.get("student_id")
    result = data.get("result")
    
    if result == "SUCCESS":
        now = datetime.now()
        target_period = None
        all_periods = TimeTable.query.all()
        for p in all_periods:
            p_start = datetime.combine(now.date(), p.開始時刻)
            p_end = datetime.combine(now.date(), p.終了時刻)
            if (p_start - timedelta(minutes=20)) <= now <= (p_end + timedelta(minutes=20)):
                target_period = p.時限
                break
        
        if target_period:
            today_yobi_str = YOBI_MAP_REVERSE.get((now.weekday() + 1) % 7)
            kiki = get_current_kiki()
            class_row = 時間割.query.filter_by(学期=kiki, 曜日=today_yobi_str, 時限=target_period).first()
            if class_row:
                subject_id = class_row.授業ID
                status = 判定(target_period, now)
                existing = 出席記録.query.filter_by(学生ID=student_id, 授業ID=subject_id, 出席日付=now.date(), 時限=target_period).first()
                if not existing:
                    new_attendance = 出席記録(学生ID=student_id, 授業ID=subject_id, 出席時刻=now, 状態=status, 時限=target_period)
                    db.session.add(new_attendance)
                    db.session.commit()
                    check_and_send_alert(student_id, subject_id)
                else:
                    existing.出席時刻 = now
                    db.session.commit()

        auth_commands[f"RESULT_{student_id}"] = "SUCCESS"
        return jsonify({"status": "received"})
    return jsonify({"status": "ignored"})

@api_bp.route("/api/check_remote_result", methods=["GET"])
@login_required
def api_check_remote_result():
    student_id = current_user.学生ID
    result = auth_commands.pop(f"RESULT_{student_id}", None)
    if result == "SUCCESS":
        return jsonify({"status": "success"})
    else:
        return jsonify({"status": "waiting"})

@api_bp.route('/api/upload_image', methods=['POST'])
def upload_image():
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        image_data = data.get('image')
        if not student_id or not image_data:
            return jsonify({"error": "データ不足"}), 400
        filename = save_image(image_data, student_id)
        if filename:
            return jsonify({"status": "success", "filename": filename}), 200
        else:
            return jsonify({"status": "error", "message": "保存失敗"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500