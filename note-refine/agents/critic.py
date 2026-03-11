import json
import anthropic


def run(
    sections_content: dict[str, str],
    feedback: str,
    target_hint: str | None,
    client: anthropic.Anthropic
) -> dict:
    print("🔍 [CriticAgent] フィードバックを分析中...")

    sections_text = "\n\n".join(
        f"=== {name} ===\n{content}" for name, content in sections_content.items()
    )

    hint_text = f"\nCLI指定の対象セクション: {target_hint}" if target_hint else ""

    system_prompt = """あなたはnote記事の改善を支援するCriticエージェントです。
ユーザーの音声フィードバックを分析し、「どのセクションの・何が問題か」を構造化抽出してください。

出力は必ずJSONのみ（前後の説明文・コードブロック記法なし）。
以下のJSONスキーマで出力してください:

{
  "summary": "フィードバックの一言要約",
  "target_section": "対象セクションのファイル名 or null",
  "target_section_confidence": "explicit | inferred | unclear",
  "issues": [
    {
      "id": "issue_1",
      "section": "対象セクションのファイル名",
      "category": "構成 | 文体 | 内容 | 表現 | その他",
      "severity": "high | medium | low",
      "location": "該当箇所の引用テキスト or null",
      "problem": "何が問題か",
      "user_intent": "ユーザーが本当に求めていること",
      "suggestion": "具体的な改善方向性"
    }
  ],
  "preserve": ["変えてはいけない要素"],
  "overall_direction": "全体的な改善方向性",
  "affects_other_sections": ["波及影響の可能性があるセクションのファイル名"]
}

セクション特定ルール：
- ユーザーが「導入」「最初の部分」などと言ったら対応ファイルを推定
- 複数セクションへの指摘はissuesを複数発行して各sectionを明記
- 不明ならtarget_section: null, target_section_confidence: "unclear"
"""

    user_prompt = f"""以下のnote記事セクションへのフィードバックを分析してください。

{sections_text}

{hint_text}

ユーザーフィードバック:
{feedback}"""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw)

    # ログ出力
    confidence = result.get("target_section_confidence", "unclear")
    target = result.get("target_section", "未確定")
    icons = {"explicit": "🎯", "inferred": "🔮", "unclear": "❓"}
    icon = icons.get(confidence, "❓")
    print(f"  {icon} 対象セクション: {target} (確信度: {confidence})")

    for issue in result.get("issues", []):
        sev = issue.get("severity", "low")
        sev_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        sev_icon = sev_icons.get(sev, "🟢")
        print(f"  {sev_icon} [{issue.get('category', '')}] {issue.get('problem', '')}")

    affects = result.get("affects_other_sections", [])
    if affects:
        print(f"  ⚡ 波及影響の可能性: {', '.join(affects)}")

    return result
