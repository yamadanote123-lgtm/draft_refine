import json
import anthropic


def run(
    section_name: str,
    original: str,
    feedback: str,
    improved: str,
    critique: dict,
    client: anthropic.Anthropic
) -> dict:
    print(f"✅ [ValidatorAgent] {section_name} の改善結果を検証中...")

    system_prompt = """あなたはnote記事の改善結果を検証するValidatorエージェントです。
改善後セクションがフィードバックを正しく反映しているか検証してください。

出力は必ずJSONのみ（前後の説明文・コードブロック記法なし）。
以下のJSONスキーマで出力してください:

{
  "verdict": "pass | needs_revision",
  "score": 0から100の整数,
  "feedback_addressed": [
    {"issue_id": "issue_1", "status": "resolved | partial | unresolved", "comment": ""}
  ],
  "new_issues": ["新たに生じた問題点（あれば）"],
  "quality_check": {
    "structure": "良い | 普通 | 要改善",
    "readability": "良い | 普通 | 要改善",
    "tone": "良い | 普通 | 要改善",
    "value": "良い | 普通 | 要改善"
  },
  "coherence_risk": "low | medium | high",
  "recommendation": "次のアクションへのアドバイス"
}

coherence_riskは「この編集が他セクションとの整合性を崩すリスク」を示します。
"""

    user_prompt = f"""以下の改善結果を検証してください。

## セクション名: {section_name}

## 元のセクション内容
{original}

## ユーザーフィードバック
{feedback}

## 改善後セクション内容
{improved}

## CriticAgentの分析
{json.dumps(critique, ensure_ascii=False, indent=2)}"""

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

    verdict = result.get("verdict", "needs_revision")
    score = result.get("score", 0)
    coherence_risk = result.get("coherence_risk", "low")
    recommendation = result.get("recommendation", "")

    verdict_icon = "✅" if verdict == "pass" else "⚠️"
    risk_icons = {"low": "🟢", "medium": "🟡", "high": "🔴"}
    risk_icon = risk_icons.get(coherence_risk, "🟢")

    print(f"  {verdict_icon} 判定: {verdict} | スコア: {score}")
    print(f"  {risk_icon} coherence_risk: {coherence_risk}")
    print(f"  💡 {recommendation}")

    return result
