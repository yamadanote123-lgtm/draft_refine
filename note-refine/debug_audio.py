"""
音声録音 → 文字起こしのデバッグスクリプト
各ステップを個別に確認します。
"""
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path


def check_sox():
    print("=" * 50)
    print("【STEP 1】sox の確認")
    path = shutil.which("sox")
    if path:
        print(f"  ✅ sox が見つかりました: {path}")
        result = subprocess.run(["sox", "--version"], capture_output=True, text=True)
        print(f"  バージョン: {result.stderr.strip() or result.stdout.strip()}")
        return True
    else:
        print("  ❌ sox が見つかりません")
        print("  → choco install sox  または  https://sourceforge.net/projects/sox/ からインストール")
        return False


def check_ffmpeg():
    print("\n" + "=" * 50)
    print("【STEP 2】ffmpeg の確認")
    path = shutil.which("ffmpeg")
    if path:
        print(f"  ✅ ffmpeg が見つかりました: {path}")
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        print(f"  バージョン: {result.stdout.splitlines()[0] if result.stdout else result.stderr.splitlines()[0]}")
        return True
    else:
        print("  ❌ ffmpeg が見つかりません")
        print("  → choco install ffmpeg  または  https://github.com/BtbN/FFmpeg-Builds/releases からインストール")
        return False


def check_whisper():
    print("\n" + "=" * 50)
    print("【STEP 3】whisper の確認")
    try:
        import whisper
        print(f"  ✅ openai-whisper がインストールされています")
        return True
    except ImportError:
        print("  ❌ openai-whisper が見つかりません")
        print("  → pip install openai-whisper")
        return False


def test_record():
    print("\n" + "=" * 50)
    print("【STEP 4-A】sox 利用可能なデバイス一覧")
    result = subprocess.run(
        ["sox", "-n", "--help"],
        capture_output=True, text=True, errors="replace"
    )
    print(f"  {result.stderr[:300] if result.stderr else '(出力なし)'}")

    print("\n【STEP 4-B】録音テスト（tempフォルダに保存）")
    temp_dir = Path(tempfile.gettempdir())
    audio_path = str(temp_dir / "debug_test.wav")
    print(f"  保存先: {audio_path}")
    print("  🎙️  録音中... Enterキーで停止してください（3秒程度話してください）")

    # stderrを取得してエラーを確認
    proc = subprocess.Popen(
        ["sox", "-t", "waveaudio", "default", "-r", "16000", "-c", "1", audio_path],
        stderr=subprocess.PIPE
    )
    input()
    proc.terminate()
    stderr_out = proc.stderr.read().decode(errors="replace")
    proc.wait()

    if stderr_out.strip():
        print(f"  sox stderr: {stderr_out.strip()}")

    wav = Path(audio_path)
    if wav.exists() and wav.stat().st_size > 0:
        print(f"  ✅ 録音成功 (waveaudio): {wav.stat().st_size} bytes")
        return audio_path

    # フォールバック: -d で試す
    print("  ⚠️  waveaudio 失敗。-d で再試行します...")
    print("  🎙️  録音中... Enterキーで停止してください")
    proc2 = subprocess.Popen(
        ["sox", "-d", "-r", "16000", "-c", "1", audio_path],
        stderr=subprocess.PIPE
    )
    input()
    proc2.terminate()
    stderr_out2 = proc2.stderr.read().decode(errors="replace")
    proc2.wait()

    if stderr_out2.strip():
        print(f"  sox stderr: {stderr_out2.strip()}")

    wav = Path(audio_path)
    if wav.exists() and wav.stat().st_size > 0:
        print(f"  ✅ 録音成功 (-d): {wav.stat().st_size} bytes")
        return audio_path
    else:
        print("  ❌ どちらの方法でも録音できませんでした")
        return None


def test_ffmpeg_read(audio_path: str):
    print("\n" + "=" * 50)
    print("【STEP 5】ffmpeg による wav 読み込みテスト")
    print(f"  ファイル: {audio_path}")
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-threads", "0", "-i", audio_path,
         "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", "16000", "-"],
        capture_output=True
    )
    if result.returncode == 0:
        print(f"  ✅ ffmpeg 読み込み成功 ({len(result.stdout)} bytes)")
        return True
    else:
        print(f"  ❌ ffmpeg 読み込み失敗 (exit code: {result.returncode})")
        print(f"  stderr: {result.stderr.decode(errors='replace')[-500:]}")
        return False


def test_transcribe(audio_path: str):
    print("\n" + "=" * 50)
    print("【STEP 6】Whisper 文字起こしテスト")
    import whisper
    print("  モデル読み込み中（初回はダウンロードあり）...")
    model = whisper.load_model("base")
    print("  文字起こし中...")
    result = model.transcribe(audio_path, language="ja")
    text = result["text"]
    print(f"  ✅ 文字起こし結果:")
    print(f"  「{text}」")
    return text


def main():
    print("🔧 音声デバッグスクリプト")
    print()

    ok_sox = check_sox()
    ok_ffmpeg = check_ffmpeg()
    ok_whisper = check_whisper()

    if not ok_sox:
        print("\n❌ sox がないため録音できません。先にインストールしてください。")
        sys.exit(1)

    if not ok_ffmpeg:
        print("\n❌ ffmpeg がないため文字起こしできません。先にインストールしてください。")
        sys.exit(1)

    if not ok_whisper:
        print("\n❌ whisper がないため文字起こしできません。先にインストールしてください。")
        sys.exit(1)

    # 録音テスト
    audio_path = test_record()
    if not audio_path:
        sys.exit(1)

    # ffmpeg読み込みテスト
    ok_read = test_ffmpeg_read(audio_path)
    if not ok_read:
        print("\n❌ ffmpegがwavを読めません。ffmpegの再インストールを試してください。")
        sys.exit(1)

    # 文字起こしテスト
    test_transcribe(audio_path)

    print("\n" + "=" * 50)
    print("✅ 全ステップ成功！orchestrator.py で音声入力が使えます。")


if __name__ == "__main__":
    main()
