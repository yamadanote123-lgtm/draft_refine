# note-refine

音声フィードバック → 4エージェントパイプラインでnote記事をセクション単位でリファインするシステムです。

## エージェント構成図

```
音声/テキストフィードバック
         │
         ▼
  ┌─────────────┐
  │ CriticAgent │  フィードバックを構造化分析
  │             │  → 対象セクション特定
  │             │  → 問題点を severity 付きで抽出
  └──────┬──────┘
         │ critique (JSON)
         ▼
  ┌─────────────┐
  │ EditorAgent │  対象セクションのみ改善
  │             │  → preserve リストを守る
  │             │  → high severity から優先対処
  └──────┬──────┘
         │ improved (Markdown)
         ▼
  ┌───────────────┐
  │ValidatorAgent │  改善結果を検証・スコアリング
  │               │  → verdict: pass / needs_revision
  │               │  → coherence_risk 評価
  └───────┬───────┘
          │ validation_result (JSON)
          ▼
  ┌────────────────┐
  │CoherenceAgent  │  全セクション整合性を調整
  │                │  → トーン・用語・流れ・重複・矛盾
  │                │  → 最小限の変更で統一
  └───────┬────────┘
          │ all.md (整合性調整済み全文)
          ▼
     💾 ファイル保存
```

## セットアップ

### 必要環境

- Python 3.10 以上
- Anthropic API キー

### インストール

```bash
pip install anthropic

# 音声フィードバックを使う場合
pip install openai-whisper

# 録音に sox を使う場合（macOS）
brew install sox
```

### 環境変数

```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

## ワークフロー

### 1. 記事のセットアップ（初回のみ）

単一の Markdown ファイルをセクション分割します。

```bash
# 既存の記事ファイルを分割
python orchestrator.py --setup my_article.md

# → my_article/ ディレクトリが作成され sec01_xxx.md ... が生成される
```

または、`drafts/sample_article/` のように最初からセクション分割済みのディレクトリを使うこともできます。

### 2. セクション一覧確認

```bash
python orchestrator.py --list drafts/sample_article
```

出力例：
```
  📂 セクション一覧:
    [01] sec01_intro.md            (342文字)
    [02] sec02_mistake.md          (412文字)
    [03] sec03_technique.md        (480文字)
    [04] sec04_conclusion.md       (356文字)
    📄 all.md  (全1590文字)
```

### 3. リファインループ

```bash
# 音声フィードバックで実行（sox + Whisper が必要）
python orchestrator.py --article drafts/sample_article

# テキストフィードバックで実行
python orchestrator.py --article drafts/sample_article \
  --text-feedback "導入部分がもっと共感を呼ぶように改善してほしい"

# 対象セクションを明示
python orchestrator.py --article drafts/sample_article \
  --section sec02_mistake \
  --text-feedback "落とし穴の説明をもっと具体的な事例を使って"

# 高速モード（Validator スキップ）
python orchestrator.py --article drafts/sample_article \
  --text-feedback "まとめを強化して" \
  --skip-validation

# CoherenceAgent スキップ（単純結合）
python orchestrator.py --article drafts/sample_article \
  --text-feedback "..." \
  --skip-coherence
```

### 4. CoherenceAgent のみ実行

記事全体の整合性を単体で調整したい場合：

```bash
python orchestrator.py --article drafts/sample_article --coherence-only
```

## ディレクトリ構成

```
note-refine/
├── CLAUDE.md               # Claude Code 向け指示書
├── orchestrator.py         # メインエントリーポイント
├── section_manager.py      # セクションファイル管理
├── transcribe.py           # Whisper 音声文字起こし
├── record.sh               # Sox 録音スクリプト
├── agents/
│   ├── __init__.py
│   ├── critic.py           # CriticAgent
│   ├── editor.py           # EditorAgent
│   ├── validator.py        # ValidatorAgent
│   └── coherence.py        # CoherenceAgent
├── drafts/
│   └── sample_article/     # サンプル記事
│       ├── sec01_intro.md
│       ├── sec02_mistake.md
│       ├── sec03_technique.md
│       ├── sec04_conclusion.md
│       ├── all.md          # 全セクション結合（自動生成）
│       ├── _state.json     # イテレーション状態（自動生成）
│       └── iterations/     # スナップショット（自動生成）
├── audio/                  # 録音ファイル置き場
└── README.md
```

## セクション命名規則

- `sec{NN}_{slug}.md` 形式
- `NN` は 2 桁のゼロパディング番号
- `slug` は日本語・英数字・アンダースコアのみ（最大 30 文字）

`--section` オプションでは以下の形式をすべて受け付けます：
- `sec02_mistake` （フルスラグ）
- `sec02_mistake.md` （ファイル名）
- `mistake` （スラグのみ）
- `sec02` （番号のみ）

## 状態管理

各記事ディレクトリの `_state.json` にイテレーション番号と履歴が保存されます。

```json
{
  "iteration": 3,
  "history": [
    {
      "iter": 1,
      "timestamp": "2026-03-11T10:00:00",
      "target_section": "sec01_intro.md",
      "feedback_snippet": "導入部分をもっと共感を呼ぶように...",
      "critique_summary": "導入の共感性が不足",
      "validation_score": 82,
      "verdict": "pass",
      "coherence_applied": true
    }
  ]
}
```
