import json

from llm_client import DEFAULT_GEMINI_MODEL


def analyze(sections_content: dict[str, str], client) -> dict:
    print("🔗 [CoherenceAgent] 整合性を分析中...")

    sections_text = "\n\n".join(
        f"=== {name} ===\n{content}" for name, content in sections_content.items()
    )

    system_prompt = """あなたはnote記事の整合性を分析するCoherenceエージェントです。
全セクションを読んで整合性の問題を分析してください。

出力は必ずJSONのみ（前後の説明文・コードブロック記法なし）。
以下のJSONスキーマで出力してください:

{
  "coherence_score": 0から100の整数,
  "issues_found": [
    {
      "type": "tone | terminology | flow | duplication | contradiction | structure",
      "description": "問題の内容",
      "sections_involved": ["sec01_intro.md", "sec03_body.md"],
      "severity": "high | medium | low"
    }
  ],
  "terminology_map": {"元の表現": "統一後の表現"},
  "tone_assessment": "一貫している | やや不統一 | 要修正",
  "flow_assessment": "自然 | やや不自然 | 要修正",
  "summary": "一言サマリー"
}
"""

    message = client.messages.create(
        model=getattr(client, "model", DEFAULT_GEMINI_MODEL),
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": f"以下のnote記事セクションの整合性を分析してください:\n\n{sections_text}"}]
    )

    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw)

    score = result.get("coherence_score", 0)
    issues = result.get("issues_found", [])
    print(f"  スコア: {score} | 問題件数: {len(issues)}")

    for issue in issues:
        sev = issue.get("severity", "low")
        sev_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        sev_icon = sev_icons.get(sev, "🟢")
        print(f"  {sev_icon} [{issue.get('type', '')}] {issue.get('description', '')}")

    return result


def run(
    sections_content: dict[str, str],
    edited_section_name: str,
    client
) -> tuple[str, dict]:
    report = analyze(sections_content, client)

    score = report.get("coherence_score", 0)
    issues = report.get("issues_found", [])

    if score >= 90 and not issues:
        # そのまま結合
        combined = "\n\n".join(
            f"<!-- section: {name} -->\n\n{content}"
            for name, content in sections_content.items()
        )
        total = len(combined)
        print(f"🔗 [CoherenceAgent] 整合性良好（スコア{score}）。そのまま結合。 ({total}文字)")
        return combined, report

    print("🔗 [CoherenceAgent] all.md を整合性調整中...")

    sections_text = "\n\n".join(
        f"=== {name} ===\n{content}" for name, content in sections_content.items()
    )

    system_prompt = """あなたはnote記事全体の整合性を調整するCoherenceエージェントです。

調整対象: セクション間の流れ・用語・トーン・重複・矛盾
重要なルール:
- 各セクションの核となる主張・情報は変えない
- 必要最小限の変更に留める
- note記事のトーン（読者との親しみやすさ）を維持
- 出力: 調整済み全文Markdownのみ（説明文・セクションコメント不要）
"""

    user_prompt = f"""以下の整合性問題を修正して、全文を調整してください。

## 整合性分析レポート
{json.dumps(report, ensure_ascii=False, indent=2)}

## セクション内容
{sections_text}

調整済みの全文Markdownのみを出力してください。"""

    message = client.messages.create(
        model=getattr(client, "model", DEFAULT_GEMINI_MODEL),
        max_tokens=5000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    result_text = message.content[0].text.strip()
    total = len(result_text)
    print(f"  完了: {total}文字")

    return result_text, report
