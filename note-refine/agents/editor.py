import anthropic


def run(
    target_section_name: str,
    target_content: str,
    all_sections_content: dict[str, str],
    critique: dict,
    client: anthropic.Anthropic
) -> str:
    print(f"✍️  [EditorAgent] {target_section_name} を改善中...")

    original_len = len(target_content)

    # 他セクションの先頭300文字
    other_sections = []
    for name, content in all_sections_content.items():
        if name != target_section_name:
            preview = content[:300]
            other_sections.append(f"=== {name} (参照用・編集不可) ===\n{preview}...")
    other_sections_text = "\n\n".join(other_sections)

    system_prompt = """あなたはnote記事の改善を担当するEditorエージェントです。

重要なルール:
- 対象セクションのみ編集する（他セクションは絶対に変えない）
- preserveリストの要素は変えない
- severity: high から優先的に対処する
- ユーザーのトーン・視点を維持する
- 改善済みのMarkdown本文のみ出力（説明文・コメント不要）
"""

    import json
    user_prompt = f"""以下のセクションをCriticAgentの分析結果に基づいて改善してください。

## 編集対象セクション: {target_section_name}
{target_content}

## 他セクション（参照用・編集しないこと）
{other_sections_text}

## CriticAgentの分析結果
{json.dumps(critique, ensure_ascii=False, indent=2)}

改善済みのセクション内容のみを出力してください。"""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=3000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    improved = message.content[0].text.strip()
    improved_len = len(improved)
    diff = improved_len - original_len
    sign = "+" if diff >= 0 else ""
    print(f"  完了: {original_len}文字 → {improved_len}文字 ({sign}{diff})")

    return improved
