import json
import re
from datetime import datetime
from pathlib import Path


def get_article_dir(article_path: str | Path) -> Path:
    """If article_path is a file, return a sibling directory named after its stem."""
    p = Path(article_path)
    if p.is_dir():
        return p
    return p.parent / p.stem


def list_sections(article_dir: Path) -> list[dict]:
    """Return section metadata for files named secXX_slug.md."""
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
    Resolve section by exact filename, filename without extension, slug, or secXX.
    Falls back to partial match.
    """
    sections = list_sections(article_dir)

    for s in sections:
        if name in (s["filename"], s["filename"].replace(".md", ""), s["slug"]):
            return s

    m = re.match(r'^sec(\d{2})$', name)
    if m:
        idx = int(m.group(1))
        for s in sections:
            if s["index"] == idx:
                return s

    for s in sections:
        if name in s["filename"] or name in s["slug"]:
            return s
    return None


def build_all(article_dir: Path) -> str:
    """Concatenate all section files into all.md with section markers."""
    sections = list_sections(article_dir)
    parts = []
    for s in sections:
        parts.append(f"<!-- section: {s['filename']} -->")
        parts.append(read_section(s))
    return "\n\n".join(parts)


def write_all(article_dir: Path) -> Path:
    """Write the concatenated article to article_dir/all.md."""
    content = build_all(article_dir)
    all_path = article_dir / "all.md"
    all_path.write_text(content, encoding="utf-8")
    return all_path


def read_all(article_dir: Path) -> str:
    """Read all.md, generating it first if needed."""
    all_path = article_dir / "all.md"
    if not all_path.exists():
        write_all(article_dir)
    return all_path.read_text(encoding="utf-8")


def split_markdown_to_sections(source_md: Path, article_dir: Path) -> list[dict]:
    """
    Split one Markdown file into section files.

    Section boundaries are detected from:
    1. `* * *` followed by `title + -----`
    2. `### ...`
    3. thematic breaks like `---`, where the next non-empty line starts a section

    Any text before the first detected section becomes `intro`. This special case is
    handled independently so it does not block later section splitting.
    """
    article_dir.mkdir(parents=True, exist_ok=True)
    for existing in article_dir.glob("sec*.md"):
        existing.unlink()
    content = source_md.read_text(encoding="utf-8")
    lines = content.split("\n")

    def is_thematic_break(line: str) -> bool:
        return bool(re.match(r'^-{3,}$', line.strip()))

    def is_h3_heading(line: str) -> bool:
        return bool(re.match(r'^###(?:\s+|$)', line))

    def next_non_empty_line(start: int) -> int | None:
        for idx in range(start, len(lines)):
            if lines[idx].strip():
                return idx
        return None

    def title_from_index(start: int) -> str:
        idx = next_non_empty_line(start)
        if idx is None:
            return "section"

        line = lines[idx].strip()
        if is_h3_heading(line):
            return line.lstrip('#').strip() or "section"
        return line or "section"

    def has_setext_heading_at(index: int) -> bool:
        return (
            index + 1 < len(lines)
            and lines[index].strip() != ""
            and is_thematic_break(lines[index + 1])
        )

    breaks: list[dict] = []
    seen_starts: set[int] = set()
    i = 0
    while i < len(lines):
        line = lines[i]

        if has_setext_heading_at(i):
            title = lines[i].strip() or "section"
            if i not in seen_starts:
                breaks.append({"boundary": i, "start": i, "title": title})
                seen_starts.add(i)
            i += 2
            continue

        if is_h3_heading(line):
            title = line.lstrip('#').strip() or "section"
            if i not in seen_starts:
                breaks.append({"boundary": i, "start": i, "title": title})
                seen_starts.add(i)
            i += 1
            continue

        if is_thematic_break(line):
            start = next_non_empty_line(i + 1)
            if start is not None and start not in seen_starts:
                breaks.append({
                    "boundary": i,
                    "start": start,
                    "title": title_from_index(start),
                })
                seen_starts.add(start)
            i += 1
            continue

        if re.match(r'^\*\s*\*\s*\*\s*$', line.strip()):
            j = next_non_empty_line(i + 1)
            if j is not None and j + 1 < len(lines):
                title_candidate = lines[j].strip()
                underline_candidate = lines[j + 1].strip()
                if title_candidate and re.match(r'^-{2,}$', underline_candidate):
                    if j not in seen_starts:
                        breaks.append({"boundary": i, "start": j, "title": title_candidate})
                        seen_starts.add(j)
                    i += 1
                    continue

        i += 1

    breaks.sort(key=lambda x: x["boundary"])

    if not breaks:
        filename = "sec01_intro.md"
        (article_dir / filename).write_text(content.strip(), encoding="utf-8")
        write_all(article_dir)
        return list_sections(article_dir)

    sections_data = []

    pre = "\n".join(lines[:breaks[0]["boundary"]]).strip()
    if pre:
        sections_data.append(("intro", pre))

    for idx, br in enumerate(breaks):
        end = breaks[idx + 1]["boundary"] if idx + 1 < len(breaks) else len(lines)
        sec = "\n".join(lines[br["start"]:end]).strip()
        if sec:
            sections_data.append((_slugify(br["title"]), sec))

    for i, (slug, sec_content) in enumerate(sections_data, 1):
        filename = f"sec{i:02d}_{slug}.md"
        (article_dir / filename).write_text(sec_content, encoding="utf-8")

    write_all(article_dir)
    return list_sections(article_dir)


def _slugify(text: str) -> str:
    """
    Create a filename-safe slug while keeping Japanese characters.
    Falls back to "section" and limits length to 30 chars.
    """
    if not text:
        return "section"
    text = text.replace(" ", "_").replace("　", "_")
    result = re.sub(r'[^\w\u3000-\u9fff\u3040-\u30ff\uff00-\uffef]', '', text)
    result = result[:30]
    return result if result else "section"


def get_iterations_dir(article_dir: Path) -> Path:
    """Ensure article_dir/iterations exists and return it."""
    d = article_dir / "iterations"
    d.mkdir(exist_ok=True)
    return d


def save_section_iteration(article_dir: Path, section: dict, content: str, iter_num: int) -> Path:
    """Save a snapshot to iterations/{section}_iterNN.md."""
    idir = get_iterations_dir(article_dir)
    stem = Path(section["filename"]).stem
    path = idir / f"{stem}_iter{iter_num:02d}.md"
    path.write_text(content, encoding="utf-8")
    return path


def load_state(article_dir: Path) -> dict:
    """Load _state.json or return the default structure."""
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
    Append one iteration entry and update state["iteration"].
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
    Print section list and character counts.
    """
    sections = list_sections(article_dir)
    print("  Sections:")
    total = 0
    for s in sections:
        content = read_section(s)
        length = len(content)
        total += length
        print(f"    [{s['index']:02d}] {s['filename']:<30} ({length} chars)")
    all_path = article_dir / "all.md"
    if all_path.exists():
        all_len = len(all_path.read_text(encoding="utf-8"))
        print(f"    all.md ({all_len} chars)")
    else:
        print(f"    all.md (not generated, expected {total} chars)")
