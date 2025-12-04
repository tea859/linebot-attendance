import os
from flask import Blueprint, request, abort, current_app # ★current_appを追加
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, QuickReply, 
    QuickReplyButton, MessageAction, FlexSendMessage, BubbleContainer
)
from ..extensions import db
from ..models import 学生, LineUser, ReportRecord
from ..services import (
    get_schedule_for_line, get_attendance_summary_for_line, 
    process_temporary_exit, process_return_from_exit, process_exit_record,
    get_student_id_from_line_user, sensor_data, analyze_student_habits, ask_ai_about_schedule
)

line_bp = Blueprint('line', __name__)

# LINE設定
YOUR_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
YOUR_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')

line_bot_api = None
handler = None

if YOUR_CHANNEL_ACCESS_TOKEN and YOUR_CHANNEL_SECRET:
    line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(YOUR_CHANNEL_SECRET)

@line_bp.route("/callback", methods=['POST'])
def callback():
    """LINEからのWebhookを受け取る"""
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    # ★修正: app.logger ではなく current_app.logger を使う
    current_app.logger.info("Request body: " + body)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel secret.")
        abort(400)
    return 'OK'

# ハンドラー定義
if handler:
    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        """LINEのテキストメッセージを処理する"""
        received_text = event.message.text.strip()
        user_id = event.source.user_id
        reply_message = None

        print(f"📩 [LINE受信] User: {user_id}, Text: '{received_text}'")

        # ==========================================
        # 1. アカウント登録処理
        # ==========================================
        if received_text.startswith("登録"):
            try:
                parts = received_text.split(":")
                if len(parts) < 2: raise ValueError
                
                input_student_id = int(parts[1].strip())
                student = 学生.query.get(input_student_id)
                
                if not student:
                    reply_message = TextSendMessage(text=f"❌ 学生ID {input_student_id} はデータベースに存在しません。")
                else:
                    existing_mapping = LineUser.query.filter_by(line_user_id=user_id).first()
                    if existing_mapping:
                        existing_mapping.student_id = input_student_id
                        reply_message = TextSendMessage(text=f"✅ 登録情報を更新しました。\nID: {input_student_id} ({student.学生名})")
                    else:
                        new_mapping = LineUser(line_user_id=user_id, student_id=input_student_id)
                        db.session.add(new_mapping)
                        reply_message = TextSendMessage(text=f"🎉 登録完了！\nID: {input_student_id} ({student.学生名}) が紐づきました。")
                    db.session.commit()
            except:
                reply_message = TextSendMessage(text="❌ 入力形式が違います。\n「登録:学生ID」の形式で送信してください。\n例: 登録:222521301")

        # ==========================================
        # 2. リッチメニューからの親メニュー呼び出し
        # ==========================================

        # 【A. 時間割メニュー】
        elif received_text == "時間割メニュー":
            buttons = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="今日の時間割", text="今日の時間割")),
                QuickReplyButton(action=MessageAction(label="明日の時間割", text="明日の時間割")),
            ])
            reply_message = TextSendMessage(text="いつの時間割を表示しますか？", quick_reply=buttons)

        # 【B. 出席・連絡メニュー】
        elif received_text == "出席・連絡メニュー":
            buttons = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="出席サマリー", text="出席サマリー")),
                QuickReplyButton(action=MessageAction(label="遅刻連絡する", text="遅刻フォーム起動")),
                QuickReplyButton(action=MessageAction(label="欠席連絡する", text="欠席フォーム起動")),
            ])
            reply_message = TextSendMessage(text="機能を選択してください。", quick_reply=buttons)

        # 【C. 退出メニュー】
        elif received_text == "退出メニュー":
            buttons = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="一時退出", text="一時退出")),
                QuickReplyButton(action=MessageAction(label="戻りました", text="戻りました")),
                QuickReplyButton(action=MessageAction(label="最終退室", text="最終退室")),
            ])
            reply_message = TextSendMessage(text="退出操作を選んでください。", quick_reply=buttons)

        # ==========================================
        # 3. 入力補助（魔法のリンク）
        # ==========================================
        
        elif received_text == "遅刻フォーム起動":
            # ★BotのIDを確認してください (例: @742gxeis)
            bot_id = "@742gxeis" 
            url = f"https://line.me/R/oaMessage/{bot_id}/?遅刻連絡:"
            reply_message = TextSendMessage(text=f"▼ 以下のリンクをタップして理由を入力してください。\n{url}")

        elif received_text == "欠席フォーム起動":
            bot_id = "@742gxeis"
            url = f"https://line.me/R/oaMessage/{bot_id}/?欠席連絡:"
            reply_message = TextSendMessage(text=f"▼ 以下のリンクをタップして理由を入力してください。\n{url}")

        # ==========================================
        # 4. 各機能の実行
        # ==========================================

        # --- 時間割 ---
        elif received_text == "今日の時間割" or received_text == "明日の時間割":
            days_ahead = 0 if received_text == "今日の時間割" else 1
            target_date = datetime.now() + timedelta(days=days_ahead)
            
            # 土日チェック (5=土, 6=日)
            if target_date.weekday() >= 5:
                reply_message = TextSendMessage(text=f"📅 {target_date.strftime('%Y/%m/%d')} は休校日です。")
            else:
                data = get_schedule_for_line(target_date)
                if isinstance(data, BubbleContainer):
                    reply_message = FlexSendMessage(alt_text="時間割", contents=data)
                else:
                    reply_message = TextSendMessage(text=data)

        # --- 出席サマリー ---
        elif received_text == "出席サマリー":
            data = get_attendance_summary_for_line(user_id)
            if isinstance(data, BubbleContainer):
                reply_message = FlexSendMessage(alt_text="出席サマリー", contents=data)
            else:
                reply_message = TextSendMessage(text=data)

        # --- 退出管理 ---
        elif received_text == "一時退出":
            msg = process_temporary_exit(user_id)
            reply_message = TextSendMessage(text=msg)

        elif received_text == "戻りました":
            msg = process_return_from_exit(user_id)
            reply_message = TextSendMessage(text=msg)

        elif received_text == "最終退室":
            msg = process_exit_record(user_id)
            reply_message = TextSendMessage(text=msg)
            
        # --- センサー情報 ---
        elif received_text == "気温":
            if sensor_data:
                latest = sensor_data[-1]
                reply_message = TextSendMessage(text=f"現在の気温は {latest.get('temperature')}℃ です。")
            else:
                reply_message = TextSendMessage(text="センサーデータがまだありません。")

        # ==========================================
        # 5. 欠席・遅刻連絡の保存処理 (管理者メール通知付き)
        # ==========================================
        elif received_text.startswith("欠席連絡") or received_text.startswith("遅刻連絡:"):
            student_id = get_student_id_from_line_user(user_id)
            if student_id is None:
                reply_message = TextSendMessage(text="⚠️ 学生IDが登録されていません。\n「登録:学生ID」で紐付けてください。")
            else:
                report_type = "欠席" if received_text.startswith("欠席連絡") else "遅刻"
                try:
                    reason = received_text.split(":", 1)[1].strip()
                    if not reason: raise IndexError
                    
                    new_report = ReportRecord(
                        student_id=student_id,
                        report_type=report_type,
                        reason=reason,
                        report_date=datetime.now(),
                        is_resolved=False
                    )
                    db.session.add(new_report)
                    db.session.commit()
                    
                    # メール通知 (GAS経由)
                    try:
                        student = 学生.query.get(student_id)
                        admin_email = os.environ.get('MAIL_USERNAME')
                        if admin_email and os.environ.get('GAS_API_URL'):
                            payload = {
                                "to": admin_email,
                                "subject": f"【{report_type}連絡】{student.学生名}",
                                "body": f"学生: {student.学生名}\n理由: {reason}\n日時: {datetime.now()}",
                                "auth_token": os.environ.get('GAS_AUTH_TOKEN')
                            }
                            requests.post(os.environ.get('GAS_API_URL'), json=payload)
                    except Exception as e:
                        print(f"Email Error: {e}")

                    reply_message = TextSendMessage(
                        text=f"📢 {student.学生名}さん、{report_type}連絡を受け付けました。\n理由: {reason}\n管理者に通知されました。"
                    )
                except IndexError:
                    reply_message = TextSendMessage(text=f"❌ 理由が入力されていません。\n例: 「{report_type}連絡:風邪のため」")

        # ==========================================
        # 6. AIコンシェルジュ (分析・質問)
        # ==========================================
        elif "分析" in received_text or "アドバイス" in received_text or "傾向" in received_text:
            student_id = get_student_id_from_line_user(user_id)
            if not student_id:
                reply_message = TextSendMessage(text="⚠️ 学生IDが紐付いていません。「登録:ID」を行ってください。")
            else:
                # analyze_student_habits関数が存在することを確認してください
                if 'analyze_student_habits' in globals():
                    analysis_result = analyze_student_habits(student_id)
                    reply_message = TextSendMessage(text=f"🤖 {analysis_result}")
                else:
                    reply_message = TextSendMessage(text="⚠️ 分析機能の関数が見つかりません。")

        elif received_text.startswith("教えて") or received_text.startswith("AI"):
            student_id = get_student_id_from_line_user(user_id)
            student_name = "学生"
            if student_id:
                s = 学生.query.get(student_id)
                if s: student_name = s.学生名
                
            question = received_text.replace("教えて", "").replace("AI", "").strip()
            if not question:
                reply_message = TextSendMessage(text="❓ 何について知りたいですか？\n例：「教えて 明日の授業」")
            else:
                ai_answer = ask_ai_about_schedule(question, student_name)
                reply_message = TextSendMessage(text=ai_answer)

        # ==========================================
        # 7. 該当なしの場合（ヘルプを表示）
        # ==========================================
        else:
            # ここが重要！無視せずにメニューを出す
            print(f"⚠️ [未対応コマンド] '{received_text}'")
            reply_message = TextSendMessage(
                text="🤖 メニューを選択するか、以下のキーワードを送ってください。\n\n・時間割メニュー\n・出席・連絡メニュー\n・今日の時間割\n・分析\n・登録:学生ID"
            )

        # ==========================================
        # 8. 返信実行
        # ==========================================
        if reply_message:
            try:
                line_bot_api.reply_message(event.reply_token, reply_message)
                print("✅ 返信成功")
            except Exception as e:
                print(f"❌ 返信エラー: {e}")