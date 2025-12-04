from app import create_app

print("🚀 アプリを作成中...") 
app = create_app()
print("✅ アプリ作成完了！起動します...") 

if __name__ == "__main__":
    try:
        # デバッグモードをTrueにして詳細を表示させる
        app.run(host="0.0.0.0", port=5000, debug=True)
    except Exception as e:
        print(f"❌ 起動エラー: {e}")