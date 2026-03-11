import json
import re
from datetime import datetime
from pathlib import Path


def get_article_dir(article_path: str | Path) -> Path:
    """パスがディレクトリならそのまま、.mdファイルなら stem のディレクトリパスを返す"""
    p = Path(article_path)
    if p.is_dir():
        return p
    return p.parent / p.stem


def list_sections(article_dir: Path) -> list[dict]:
    """sec*.md を番号順に列挙。各要素: {"index": int, "slug": str, "path": Path, "filename": str}"""
    pattern = re.compile(r'^sec(\d{2})_(.+)\.md$')
    sections = []
    for f in article_dir.iterdir():
        m = pattern.match(f.name)
        if m:
            sections.append({
                "index": int(m.group(1)),
                "slug": m.group(2),
                "path": f,
                "filename": f.name
            })
    return sorted(sections, key=lambda x: x["index"])


def read_section(section: dict) -> str:
    return section["path"].read_text(encoding="utf-8")


def write_section(section: dict, content: str):
    section["path"].write_text(content, encoding="utf-8")


def find_section_by_name(article_dir: Path, name: str) -> dict | None:
    """
    "sec02_body", "sec02_body.md", "body", "sec02" のいずれでも該当セクションを返す。
    完全一致→部分一致の順で探す。
    """
    sections = list_sections(article_dir)
    # 完全一致
    for s in sections:
        if name in (s["filename"], s["filename"].replace(".md", ""), s["slug"]):
            return s
    # sec番号だけの場合
    m = re.match(r'^sec(\d{2})$', name)
    if m:
        idx = int(m.group(1))
        for s in sections:
            if s["index"] == idx:
                return s
    # 部分一致
    for s in sections:
        if name in s["filename"] or name in s["slug"]:
            return s
    return None


def build_all(article_dir: Path) -> str:
    """全セクションを結合してall.mdの内容文字列を生成。セクション間に<!-- section: {filename} -->のHTMLコメント区切りを挿入。"""
    sections = list_sections(article_dir)
    parts = []
    for s in sections:
        parts.append(f"<!-- section: {s['filename']} -->")
        parts.append(read_section(s))
    return "\n\n".join(parts)


def write_all(article_dir: Path) -> Path:
    """build_all()の結果をarticle_dir/all.mdに書き込む"""
    content = build_all(article_dir)
    all_path = article_dir / "all.md"
    all_path.write_text(content, encoding="utf-8")
    return all_path


def read_all(article_dir: Path) -> str:
    """all.mdを読む。なければwrite_all()してから読む"""
    all_path = article_dir / "all.md"
    if not all_path.exists():
        write_all(article_dir)
    return all_path.read_text(encoding="utf-8")


def split_markdown_to_sections(source_md: Path, article_dir: Path) -> list[dict]:
    """
    単一の.mdファイルを以下の2パターンでセクション分割する：
      1. * * * の後に「タイトル行 + ------- アンダーライン」が続くパターン
      2. ### 見出し行
    分割後に write_all() も実行する。
    """
    article_dir.mkdir(parents=True, exist_ok=True)
    content = source_md.read_text(encoding="utf-8")
    lines = content.split("\n")

    # セクション区切り点を探す: (行インデックス, タイトル文字列)
    breaks = []
    i = 0
    while i < len(lines):
        # パターン2: ### 見出し
        if re.match(r'^###\s+', lines[i]):
            title = lines[i].lstrip('#').strip()
            breaks.append((i, title))
            i += 1
            continue

        # パターン1: * * * の後にタイトル行 + ------- アンダーライン
        if re.match(r'^\*\s*\*\s*\*\s*$', lines[i].strip()):
            j = i + 1
            # 空行をスキップ
            while j < len(lines) and lines[j].strip() == '':
                j += 1
            # タイトル行 + アンダーライン（---）を確認
            if j < len(lines) and j + 1 < len(lines):
                title_candidate = lines[j].strip()
                underline_candidate = lines[j + 1].strip()
                if title_candidate and re.match(r'^-{2,}$', underline_candidate):
                    breaks.append((i, title_candidate))
                    i += 1
                    continue

        i += 1

    # 区切りが見つからなければ全体を1セクションとして保存
    if not breaks:
        filename = "sec01_intro.md"
        (article_dir / filename).write_text(content.strip(), encoding="utf-8")
        write_all(article_dir)
        return list_sections(article_dir)

    sections_data = []

    # 最初の区切り前のコンテンツ（イントロ）
    pre = "\n".join(lines[:breaks[0][0]]).strip()
    if pre:
        sections_data.append(("intro", pre))

    # 各セクション
    for idx, (bline, title) in enumerate(breaks):
        end = breaks[idx + 1][0] if idx + 1 < len(breaks) else len(lines)
        sec = "\n".join(lines[bline:end]).strip()
        sections_data.append((_slugify(title), sec))

    # ファイルに書き出す
    for i, (slug, sec_content) in enumerate(sections_data, 1):
        filename = f"sec{i:02d}_{slug}.md"
        (article_dir / filename).write_text(sec_content, encoding="utf-8")

    write_all(article_dir)
    return list_sections(article_dir)


def _slugify(text: str) -> str:
    """日本語・英数字・アンダースコアを残し、スペースを_に変換。最大30文字。空文字の場合は"section"を返す。"""
    if not text:
        return "section"
    text = text.replace(" ", "_").replace("　", "_")
    # Keep Japanese chars, alphanumeric, underscore
    result = re.sub(r'[^\w\u3000-\u9fff\u3040-\u30ff\uff00-\uffef]', '', text)
    result = result[:30]
    return result if result else "section"


def get_iterations_dir(article_dir: Path) -> Path:
    """article_dir/iterations/ を作成して返す"""
    d = article_dir / "iterations"
    d.mkdir(exist_ok=True)
    return d


def save_section_iteration(article_dir: Path, section: dict, content: str, iter_num: int) -> Path:
    """iterations/{filename_without_ext}_iter{nn}.md としてスナップショット保存"""
    idir = get_iterations_dir(article_dir)
    stem = Path(section["filename"]).stem
    path = idir / f"{stem}_iter{iter_num:02d}.md"
    path.write_text(content, encoding="utf-8")
    return path


def load_state(article_dir: Path) -> dict:
    """_state.json を読む。なければ {"iteration": 0, "history": []} を返す"""
    state_path = article_dir / "_state.json"
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"iteration": 0, "history": []}


def save_state(article_dir: Path, state: dict):
    state_path = article_dir / "_state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_history(state: dict, iter_num: int, target_section: str,
                   feedback: str, critique_summary: str,
                   validation_score: int, verdict: str, coherence_applied: bool):
    """
    state["history"] に以下のキーを持つエントリを追加：
    iter, timestamp(ISO形式), target_section, feedback_snippet(先頭80文字),
    critique_summary, validation_score, verdict, coherence_applied
    state["iteration"] も iter_num に更新する。
    """
    entry = {
        "iter": iter_num,
        "timestamp": datetime.now().isoformat(),
        "target_section": target_section,
        "feedback_snippet": feedback[:80],
        "critique_summary": critique_summary,
        "validation_score": validation_score,
        "verdict": verdict,
        "coherence_applied": coherence_applied
    }
    state["history"].append(entry)
    state["iteration"] = iter_num


def print_section_list(article_dir: Path):
    """
    セクション一覧を表示。例:
      📂 セクション一覧:
        [01] sec01_intro.md       (342文字)
        [02] sec02_body.md        (512文字)
        📄 all.md  (全854文字)
    """
    sections = list_sections(article_dir)
    print("  📂 セクション一覧:")
    total = 0
    for s in sections:
        content = read_section(s)
        length = len(content)
        total += length
        print(f"    [{s['index']:02d}] {s['filename']:<30} ({length}文字)")
    all_path = article_dir / "all.md"
    if all_path.exists():
        all_len = len(all_path.read_text(encoding="utf-8"))
        print(f"    📄 all.md  (全{all_len}文字)")
    else:
        print(f"    📄 all.md  (未生成、推定全{total}文字)")
