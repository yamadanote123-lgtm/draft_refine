import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from llm_client import get_client
from section_manager import (
    append_history,
    build_all,
    find_section_by_name,
    get_article_dir,
    list_sections,
    load_state,
    print_section_list,
    read_section,
    save_section_iteration,
    save_state,
    split_markdown_to_sections,
    write_all,
    write_section,
)


def parse_section_args(raw_sections) -> list[str]:
    if not raw_sections:
        return []

    names: list[str] = []
    for raw in raw_sections:
        for part in raw.split(","):
            name = part.strip()
            if name:
                names.append(name)
    return names


def resolve_target_sections(article_dir: Path, sections: list[dict], raw_sections) -> list[dict]:
    requested = parse_section_args(raw_sections)
    if not requested:
        return []

    resolved: list[dict] = []
    seen = set()
    for name in requested:
        section = find_section_by_name(article_dir, name)
        if not section:
            print(f"ERROR: section not found: {name}")
            print("Available sections:")
            for s in sections:
                print(f"  {s['filename']}")
            sys.exit(1)
        if section["filename"] not in seen:
            resolved.append(section)
            seen.add(section["filename"])
    return resolved


def cmd_setup(args):
    source = Path(args.setup)
    if not source.exists():
        print(f"ERROR: source markdown not found: {source}")
        sys.exit(1)
    if source.is_dir():
        print(f"ERROR: expected a .md file, got a directory: {source}")
        print("Usage: python orchestrator.py --setup my_article.md")
        sys.exit(1)
    if source.suffix.lower() != ".md":
        print(f"ERROR: expected a .md file: {source}")
        sys.exit(1)
    if source.stat().st_size == 0:
        print(f"ERROR: source markdown is empty: {source}")
        sys.exit(1)

    article_dir = get_article_dir(source)
    article_dir.mkdir(parents=True, exist_ok=True)
    sections = split_markdown_to_sections(source, article_dir)
    print(f"Section split completed: {article_dir}")
    print(f"  {len(sections)} sections generated")
    print_section_list(article_dir)


def cmd_list(args):
    article_dir = Path(args.list)
    if not article_dir.exists():
        print(f"ERROR: article directory not found: {article_dir}")
        print("Run --setup first.")
        sys.exit(1)

    state = load_state(article_dir)
    print_section_list(article_dir)

    history = state.get("history", [])
    if history:
        print("\nIteration history:")
        for h in history:
            verdict_icon = "OK" if h.get("verdict") == "pass" else "NG"
            coherence_icon = "LINK" if h.get("coherence_applied") else "--"
            score = h.get("validation_score", "-")
            section = h.get("target_section", "unknown")
            summary = h.get("critique_summary", "")[:40]
            iter_num = h.get("iter", "?")
            print(f"  #{iter_num:02d} {verdict_icon} {coherence_icon} [{section}] score={score} | {summary}")


def cmd_refine(args):
    article_dir = Path(args.article)
    if not article_dir.exists():
        print(f"ERROR: article directory not found: {article_dir}")
        print("Run --setup first.")
        sys.exit(1)

    audio_dir = Path(__file__).parent / "audio"
    audio_dir.mkdir(exist_ok=True)

    client = get_client()
    from agents import coherence, critic, editor, validator

    state = load_state(article_dir)
    iter_num = state.get("iteration", 0) + 1

    sections = list_sections(article_dir)
    if not sections:
        print(f"ERROR: no section files found: {article_dir}")
        sys.exit(1)

    sections_content = {s["filename"]: read_section(s) for s in sections}

    requested_sections = resolve_target_sections(article_dir, sections, args.section)
    target_hint = ", ".join(s["filename"] for s in requested_sections) if requested_sections else None

    article_name = article_dir.name
    target_display = target_hint if target_hint else "auto"
    print("=" * 65)
    print("  note article refine")
    print(f"  article: {article_name} | iteration #{iter_num}")
    print(f"  target section: {target_display}")
    print("=" * 65)

    history = state.get("history", [])
    if history:
        print("\nIteration history:")
        for h in history:
            verdict_icon = "OK" if h.get("verdict") == "pass" else "NG"
            coherence_icon = "LINK" if h.get("coherence_applied") else "--"
            score = h.get("validation_score", "-")
            section = h.get("target_section", "unknown")
            summary = h.get("critique_summary", "")[:40]
            h_iter = h.get("iter", "?")
            print(f"  #{h_iter:02d} {verdict_icon} {coherence_icon} [{section}] score={score} | {summary}")
        print()

    print_section_list(article_dir)
    print()

    if args.text_feedback:
        feedback = args.text_feedback
        print(f"Text feedback: {feedback[:80]}...")
    else:
        if shutil.which("sox"):
            timestamp = datetime.now().strftime("%H%M%S")
            temp_dir = Path(tempfile.gettempdir())
            audio_path = str(temp_dir / f"feedback_{timestamp}.wav")
            print("Recording... press Enter to stop")
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
                print(f"ERROR: recording failed. sox error: {sox_err.strip()}")
                print("Falling back to text feedback.")
                feedback = input("Feedback > ").strip()
            else:
                print(f"Recording saved: {audio_path}")
                from transcribe import transcribe
                feedback = transcribe(audio_path)
        else:
            print("sox was not found. Falling back to text feedback.")
            feedback = input("Feedback > ").strip()

    if requested_sections:
        target_sections = requested_sections
    else:
        critique = critic.run(sections_content, feedback, target_hint, client)
        target_section_dict = None
        critic_target = critique.get("target_section")
        if critic_target:
            target_section_dict = find_section_by_name(article_dir, critic_target)

        if not target_section_dict:
            print("\nChoose target section:")
            for i, s in enumerate(sections):
                print(f"  [{i + 1}] {s['filename']}")
            choice = input("Number > ").strip()
            try:
                idx = int(choice) - 1
                target_section_dict = sections[idx]
            except (ValueError, IndexError):
                print("ERROR: invalid selection.")
                sys.exit(1)
        target_sections = [target_section_dict]

    updated_names: list[str] = []
    last_improved = ""
    coherence_applied = False

    for offset, target_section_dict in enumerate(target_sections):
        target_name = target_section_dict["filename"]
        target_content = sections_content[target_name]
        current_iter = iter_num + offset
        current_hint = target_name

        print(f"\nTarget section selected: {target_name}")
        critique = critic.run(sections_content, feedback, current_hint, client)

        improved = editor.run(target_name, target_content, sections_content, critique, client)

        validation_result = {"verdict": "pass", "score": 100, "coherence_risk": "low", "recommendation": ""}
        if not args.skip_validation:
            validation_result = validator.run(target_name, target_content, feedback, improved, critique, client)
        else:
            print("Validator skipped.")

        write_section(target_section_dict, improved)
        save_section_iteration(article_dir, target_section_dict, improved, current_iter)
        print(f"\nSaved: {target_name}")

        sections_content[target_name] = improved
        updated_names.append(target_name)
        last_improved = improved

        verdict = validation_result.get("verdict", "pass")
        score = validation_result.get("score", 100)
        critique_summary = critique.get("summary", "")

        append_history(
            state, current_iter, target_name, feedback,
            critique_summary, score, verdict, False
        )

    if args.skip_coherence:
        write_all(article_dir)
        print("Coherence skipped. all.md regenerated from section files.")
    else:
        last_target = updated_names[-1] if updated_names else ""
        all_text, _coherence_report = coherence.run(sections_content, last_target, client)
        all_path = article_dir / "all.md"
        all_path.write_text(all_text, encoding="utf-8")
        coherence_applied = True
        print("Updated: all.md")

    if updated_names:
        for entry in state["history"][-len(updated_names):]:
            entry["coherence_applied"] = coherence_applied

    save_state(article_dir, state)

    print("\n" + "=" * 65)
    print(f"  {len(updated_names)} section(s) updated")
    print("=" * 65)
    print(f"\nUpdated sections: {', '.join(updated_names)}\n")
    print(last_improved[:500] + ("..." if len(last_improved) > 500 else ""))
    print("\nNext commands:")
    print(f"  python orchestrator.py --article {article_dir}")
    print(f"  python orchestrator.py --list {article_dir}")


def cmd_coherence_only(args):
    article_dir = Path(args.article)
    if not article_dir.exists():
        print(f"ERROR: article directory not found: {article_dir}")
        sys.exit(1)

    client = get_client()
    from agents import coherence

    sections = list_sections(article_dir)
    sections_content = {s["filename"]: read_section(s) for s in sections}

    all_text, _report = coherence.run(sections_content, "", client)
    all_path = article_dir / "all.md"
    all_path.write_text(all_text, encoding="utf-8")
    print("Updated: all.md")


def main():
    parser = argparse.ArgumentParser(description="note article refine system")
    parser.add_argument("--setup", metavar="SOURCE_MD", help="split one markdown into section files")
    parser.add_argument("--list", metavar="ARTICLE_DIR", help="show section list and iteration history")
    parser.add_argument("--article", metavar="ARTICLE_DIR", help="run the refine loop")
    parser.add_argument("--section", metavar="NAME", action="append",
                        help="target section name; repeat or use comma-separated values")
    parser.add_argument("--text-feedback", metavar="TEXT", help="use text feedback instead of recording audio")
    parser.add_argument("--skip-validation", action="store_true", help="skip Validator")
    parser.add_argument("--skip-coherence", action="store_true", help="skip CoherenceAgent and only regenerate all.md")
    parser.add_argument("--coherence-only", action="store_true", help="run only CoherenceAgent and rewrite all.md")

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
