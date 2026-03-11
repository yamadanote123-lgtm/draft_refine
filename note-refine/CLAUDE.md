# CLAUDE.md - note-refine プロジェクト指示書

## プロジェクト概要

音声フィードバック（またはテキスト入力）を受け取り、4エージェントパイプラインでnote記事をセクション単位で反復リファインするシステムです。

**パイプライン:**
```
音声/テキストフィードバック
    → CriticAgent（問題構造化分析）
    → EditorAgent（セクション改善）
    → ValidatorAgent（改善検証）
    → CoherenceAgent（全体整合性調整）
    → all.md 出力
```

記事は `sec01_xxx.md`, `sec02_xxx.md` ... のセクションファイルに分割管理され、イテレーションごとにスナップショットが保存されます。

---

## ディレクトリ構成

```
note-refine/
├── CLAUDE.md               ← このファイル
├── orchestrator.py         ← メインエントリーポイント（CLI）
├── section_manager.py      ← セクションファイル管理ユーティリティ
├── transcribe.py           ← Whisper音声文字起こし
├── record.sh               ← Sox録音スクリプト
├── agents/
│   ├── __init__.py
│   ├── critic.py           ← CriticAgent
│   ├── editor.py           ← EditorAgent
│   ├── validator.py        ← ValidatorAgent
│   └── coherence.py        ← CoherenceAgent
├── drafts/
│   └── sample_article/     ← サンプル記事（sec01〜sec04）
├── audio/                  ← 録音ファイル置き場（自動作成）
└── README.md
```

### セクションファイルの命名規則
- `sec{NN}_{slug}.md` 形式（例: `sec01_intro.md`, `sec02_mistake.md`）
- `iterations/` サブディレクトリに各イテレーションのスナップショットを保存
- `_state.json` でイテレーション番号と履歴を管理
- `all.md` は全セクション結合ファイル

---

## 各エージェントの役割

### CriticAgent (`agents/critic.py`)
- 入力: 全セクション内容 + ユーザーフィードバック
- 処理: フィードバックを分析し「どのセクションの・何が問題か」を構造化抽出
- 出力: JSON（summary, target_section, issues[], preserve[], overall_direction）

### EditorAgent (`agents/editor.py`)
- 入力: 対象セクション + 全セクション（参照用） + Criticの分析結果
- 処理: Criticの指摘に基づいて対象セクションのみを改善
- 出力: 改善済みMarkdownテキスト

### ValidatorAgent (`agents/validator.py`)
- 入力: 元セクション + フィードバック + 改善後セクション + Criticの分析
- 処理: 改善がフィードバックを正しく反映しているか検証、スコアリング
- 出力: JSON（verdict, score, feedback_addressed[], coherence_risk）

### CoherenceAgent (`agents/coherence.py`)
- 入力: 全セクション内容（改善済み含む）
- 処理: セクション間のトーン・用語・流れ・重複・矛盾を分析・調整
- 出力: 整合性調整済み全文Markdown + 分析レポートJSON

---

## `python orchestrator.py` の使い方

### セットアップ（単一mdファイルをセクション分割）
```bash
python orchestrator.py --setup drafts/sample_article.md
```

### セクション一覧と履歴の表示
```bash
python orchestrator.py --list drafts/sample_article
```

### メインのリファインループ（音声録音 or テキスト入力）
```bash
# 音声フィードバックで実行
python orchestrator.py --article drafts/sample_article

# テキストフィードバックで実行
python orchestrator.py --article drafts/sample_article --text-feedback "導入部分がもっと共感を呼ぶように改善してほしい"

# 対象セクションを明示指定
python orchestrator.py --article drafts/sample_article --section sec02_mistake --text-feedback "落とし穴の説明をもっと具体的に"

# Validatorをスキップ（高速化）
python orchestrator.py --article drafts/sample_article --text-feedback "まとめを強化して" --skip-validation

# CoherenceAgentをスキップ（単純結合）
python orchestrator.py --article drafts/sample_article --text-feedback "..." --skip-coherence
```

### CoherenceAgentのみ実行
```bash
python orchestrator.py --article drafts/sample_article --coherence-only
```

---

## 必要環境変数

| 変数名 | 説明 |
|--------|------|
| `ANTHROPIC_API_KEY` | Anthropic APIキー（必須） |

```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

---

## 依存パッケージ

```bash
pip install anthropic          # Claude API クライアント（必須）
pip install openai-whisper     # 音声文字起こし（音声フィードバック使用時）
```

### 音声録音（オプション）
- `sox` コマンドラインツール（`brew install sox` / `apt install sox`）
- soxがない場合はテキスト入力にフォールバック

### Python バージョン
- Python 3.10以上（`str | Path` ユニオン型記法を使用）
