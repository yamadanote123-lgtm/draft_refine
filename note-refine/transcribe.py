import sys


def transcribe(audio_path: str) -> str:
    """
    openai-whisper パッケージを使って日本語音声を文字起こし。
    whisperがimportできない場合はエラーメッセージとインストールコマンドを表示してsys.exit(1)。
    モデルは "base" を使用（コメントでsmall/medium/largeも可と記載）。
    language="ja"を指定。
    """
    try:
        import whisper
    except ImportError:
        print("❌ openai-whisper がインストールされていません。")
        print("インストールコマンド: pip install openai-whisper")
        sys.exit(1)

    print(f"🎙️  [Whisper] 文字起こし中: {audio_path}")

    # モデル選択: base (速い) / small / medium / large (より高精度)
    model = whisper.load_model("base")
    result = model.transcribe(audio_path, language="ja")

    text = result["text"]
    print(f"  プレビュー: {text[:100]}")

    return text
