import sys
import os
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from section_manager import (
    get_article_dir, list_sections, read_section, write_section,
    find_section_by_name, write_all, read_all, split_markdown_to_sections,
    save_section_iteration, load_state, save_state, append_history,
    print_section_list, build_all
)


def get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY 環境変数が設定されていません。")
        print("設定方法: export ANTHROPIC_API_KEY='your-api-key'")
        sys.exit(1)
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def cmd_setup(args):
    source = Path(args.setup)
    if not source.exists():
        print(f"❌ ファイルが見つかりません: {source}")
        sys.exit(1)
    if source.is_dir():
        print(f"❌ ディレクトリではなく .md ファイルを指定してください: {source}")
        print(f"例: python orchestrator.py --setup my_article.md")
        sys.exit(1)
    if source.suffix.lower() != ".md":
        print(f"❌ .md ファイルを指定してください: {source}")
        sys.exit(1)
    article_dir = get_article_dir(source)
    article_dir.mkdir(parents=True, exist_ok=True)
    sections = split_markdown_to_sections(source, article_dir)
    print(f"✅ セクション分割完了: {article_dir}")
    print(f"  {len(sections)} セクション生成")
    print_section_list(article_dir)


def cmd_list(args):
    article_dir = Path(args.list)
    if not article_dir.exists():
        print(f"❌ ディレクトリが見つかりません: {article_dir}")
        print("まず --setup でセットアップしてください。")
        sys.exit(1)

    state = load_state(article_dir)
    print_section_list(article_dir)

    history = state.get("history", [])
    if history:
        print("\n📊 イテレーション履歴:")
        for h in history:
            verdict_icon = "✅" if h.get("verdict") == "pass" else "⚠️ "
            coherence_icon = "🔗" if h.get("coherence_applied") else "  "
            score = h.get("validation_score", "-")
            section = h.get("target_section", "不明")
            summary = h.get("critique_summary", "")[:40]
            iter_num = h.get("iter", "?")
            print(f"  #{iter_num:02d} {verdict_icon}{coherence_icon} [{section}] スコア: {score} | {summary}")


def cmd_refine(args):
    article_dir = Path(args.article)
    if not article_dir.exists():
        print(f"❌ ディレクトリが見つかりません: {article_dir}")
        print("まず --setup でセットアップしてください。")
        sys.exit(1)

    # audio/ ディレクトリ自動作成
    audio_dir = Path(__file__).parent / "audio"
    audio_dir.mkdir(exist_ok=True)

    client = get_client()

    from agents import critic, editor, validator, coherence

    state = load_state(article_dir)
    iter_num = state.get("iteration", 0) + 1

    sections = list_sections(article_dir)
    if not sections:
        print(f"❌ セクションファイルが見つかりません: {article_dir}")
        sys.exit(1)

    # セクション内容を読み込む
    sections_content = {s["filename"]: read_section(s) for s in sections}

    # 対象セクションのヒント
    target_hint = None
    target_section_dict = None
    if args.section:
        target_section_dict = find_section_by_name(article_dir, args.section)
        if not target_section_dict:
            print(f"❌ セクションが見つかりません: {args.section}")
            print("利用可能なセクション:")
            for s in sections:
                print(f"  {s['filename']}")
            sys.exit(1)
        target_hint = target_section_dict["filename"]

    # ヘッダー表示
    article_name = article_dir.name
    target_display = target_hint if target_hint else "（CriticAgentが判断）"
    print("=" * 65)
    print(f"  📝 note記事リファインシステム")
    print(f"  記事: {article_name} | イテレーション #{iter_num}")
    print(f"  🎯 対象セクション: {target_display}")
    print("=" * 65)

    # 履歴表示
    history = state.get("history", [])
    if history:
        print("\n📊 イテレーション履歴:")
        for h in history:
            verdict_icon = "✅" if h.get("verdict") == "pass" else "⚠️ "
            coherence_icon = "🔗" if h.get("coherence_applied") else "  "
            score = h.get("validation_score", "-")
            section = h.get("target_section", "不明")
            summary = h.get("critique_summary", "")[:40]
            h_iter = h.get("iter", "?")
            print(f"  #{h_iter:02d} {verdict_icon}{coherence_icon} [{section}] スコア: {score} | {summary}")
        print()

    # セクション一覧表示
    print_section_list(article_dir)
    print()

    # フィードバック取得
    if args.text_feedback:
        feedback = args.text_feedback
        print(f"📝 テキストフィードバック: {feedback[:80]}...")
    else:
        # sox チェック
        import shutil
        if shutil.which("sox"):
            import tempfile
            import subprocess
            timestamp = __import__("datetime").datetime.now().strftime("%H%M%S")
            # ffmpegが日本語パスを読めないため、ASCIIのみのtempディレクトリに保存
            temp_dir = Path(tempfile.gettempdir())
            audio_path = str(temp_dir / f"feedback_{timestamp}.wav")
            print(f"🎙️  録音中... Enterキーで停止")
            proc = subprocess.Popen(
                ["sox", "-t", "waveaudio", "default", "-r", "16000", "-c", "1", audio_path],
                stderr=subprocess.PIPE
            )
            input()
            proc.terminate()
            sox_err = proc.stderr.read().decode(errors="replace")
            proc.wait()

            wav = Path(audio_path)
            if not wav.exists() or wav.stat().st_size == 0:
                print(f"❌ 録音失敗。sox エラー: {sox_err.strip()}")
                print("ℹ️  テキストでフィードバックを入力してください。")
                feedback = input("フィードバック > ").strip()
            else:
                print(f"✅ 録音完了: {audio_path}")
                from transcribe import transcribe
                feedback = transcribe(audio_path)
        else:
            print("ℹ️  Sox が見つかりません。テキストでフィードバックを入力してください。")
            feedback = input("フィードバック > ").strip()

    # CriticAgent
    critique = critic.run(sections_content, feedback, target_hint, client)

    # 対象セクション確定
    critic_target = critique.get("target_section")
    if critic_target:
        target_section_dict = find_section_by_name(article_dir, critic_target)

    if not target_section_dict:
        print("\n❓ 対象セクションを選択してください:")
        for i, s in enumerate(sections):
            print(f"  [{i+1}] {s['filename']}")
        choice = input("番号を入力 > ").strip()
        try:
            idx = int(choice) - 1
            target_section_dict = sections[idx]
        except (ValueError, IndexError):
            print("❌ 無効な選択です。")
            sys.exit(1)

    target_name = target_section_dict["filename"]
    target_content = sections_content[target_name]

    print(f"\n🎯 対象セクション確定: {target_name}")

    # EditorAgent
    improved = editor.run(target_name, target_content, sections_content, critique, client)

    # ValidatorAgent
    validation_result = {"verdict": "pass", "score": 100, "coherence_risk": "low", "recommendation": ""}
    if not args.skip_validation:
        validation_result = validator.run(target_name, target_content, feedback, improved, critique, client)
    else:
        print("⏭️  ValidatorAgent をスキップ")

    # セクション保存
    write_section(target_section_dict, improved)
    save_section_iteration(article_dir, target_section_dict, improved, iter_num)
    print(f"\n💾 {target_name} を保存しました。")

    # CoherenceAgent
    updated_sections_content = {**sections_content, target_name: improved}
    coherence_applied = False

    if args.skip_coherence:
        write_all(article_dir)
        print("⏭️  CoherenceAgent をスキップ（単純結合）")
    else:
        all_text, coherence_report = coherence.run(updated_sections_content, target_name, client)
        all_path = article_dir / "all.md"
        all_path.write_text(all_text, encoding="utf-8")
        coherence_applied = True
        print(f"💾 all.md を更新しました。")

    # 状態保存
    verdict = validation_result.get("verdict", "pass")
    score = validation_result.get("score", 100)
    critique_summary = critique.get("summary", "")

    append_history(
        state, iter_num, target_name, feedback,
        critique_summary, score, verdict, coherence_applied
    )
    save_state(article_dir, state)

    # 改善後表示
    print("\n" + "=" * 65)
    print(f"  ✅ イテレーション #{iter_num} 完了")
    print("=" * 65)
    print(f"\n改善後の {target_name}:\n")
    print(improved[:500] + ("..." if len(improved) > 500 else ""))
    print(f"\n次のアクション:")
    print(f"  python orchestrator.py --article {article_dir}  （次のイテレーション）")
    print(f"  python orchestrator.py --list {article_dir}     （履歴確認）")


def cmd_coherence_only(args):
    article_dir = Path(args.article)
    if not article_dir.exists():
        print(f"❌ ディレクトリが見つかりません: {article_dir}")
        sys.exit(1)

    client = get_client()
    from agents import coherence

    sections = list_sections(article_dir)
    sections_content = {s["filename"]: read_section(s) for s in sections}

    all_text, report = coherence.run(sections_content, "", client)
    all_path = article_dir / "all.md"
    all_path.write_text(all_text, encoding="utf-8")
    print(f"✅ all.md を更新しました。")


def main():
    parser = argparse.ArgumentParser(description="note記事リファインシステム")
    parser.add_argument("--setup", metavar="SOURCE_MD", help="単一mdをセクション分割してディレクトリを初期化")
    parser.add_argument("--list", metavar="ARTICLE_DIR", help="セクション一覧とイテレーション履歴を表示")
    parser.add_argument("--article", metavar="ARTICLE_DIR", help="メインのリファインループを実行")
    parser.add_argument("--section", metavar="NAME", help="対象セクション名")
    parser.add_argument("--text-feedback", metavar="TEXT", help="テキストでフィードバック（音声録音をスキップ）")
    parser.add_argument("--skip-validation", action="store_true", help="Validatorをスキップ")
    parser.add_argument("--skip-coherence", action="store_true", help="CoherenceAgentをスキップしてall.mdを単純結合")
    parser.add_argument("--coherence-only", action="store_true", help="CoherenceAgentのみ実行してall.mdを更新")

    args = parser.parse_args()

    if args.setup:
        cmd_setup(args)
    elif args.list:
        cmd_list(args)
    elif args.article:
        if args.coherence_only:
            cmd_coherence_only(args)
        else:
            cmd_refine(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
