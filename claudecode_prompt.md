2# note記事リファインシステム 実装プロンプト（Claude Code用）

以下の仕様に従って、note記事をセクション単位で音声フィードバックを使いながら
複数エージェントで反復改善するシステムを構築してください。

---

## ディレクトリ構成

プロジェクトルートに以下を作成してください：

```
note-refine/
├── CLAUDE.md
├── orchestrator.py
├── section_manager.py
├── transcribe.py
├── record.sh
├── agents/
│   ├── __init__.py
│   ├── critic.py
│   ├── editor.py
│   ├── validator.py
│   └── coherence.py
├── drafts/          # 空ディレクトリ（記事を置く場所）
├── audio/           # 空ディレクトリ（録音ファイル置き場）
└── README.md
```

---

## 各ファイルの実装仕様

### `CLAUDE.md`

Claude Codeがプロジェクトを理解するための指示書。以下の内容を記述：
- プロジェクト概要（音声フィードバック→4エージェントパイプラインでnote記事をセクション単位でリファイン）
- ディレクトリ構成の説明
- 各エージェントの役割
- `python orchestrator.py` の使い方一覧
- 必要環境変数（ANTHROPIC_API_KEY）と依存パッケージ

---

### `section_manager.py`

セクションファイルの管理ユーティリティ。以下の関数をすべて実装すること。

**命名規則**
- セクションファイルは `sec01_intro.md`, `sec02_body.md` のように `sec{nn}_{slug}.md` 形式
- 正規表現パターン: `^sec(\d{2})_(.+)\.md$`
- `all.md` = 全セクションを結合した完成ファイル（CoherenceAgentが生成）
- `_state.json` = イテレーション履歴

**実装する関数**

```python
def get_article_dir(article_path: str | Path) -> Path:
    """
    パスがディレクトリならそのまま、.mdファイルなら stem のディレクトリパスを返す
    """

def list_sections(article_dir: Path) -> list[dict]:
    """
    sec*.md を番号順に列挙。
    各要素: {"index": int, "slug": str, "path": Path, "filename": str}
    """

def read_section(section: dict) -> str: ...
def write_section(section: dict, content: str): ...

def find_section_by_name(article_dir: Path, name: str) -> dict | None:
    """
    "sec02_body", "sec02_body.md", "body", "sec02" のいずれでも該当セクションを返す。
    完全一致→部分一致の順で探す。
    """

def build_all(article_dir: Path) -> str:
    """
    全セクションを結合して all.md の内容文字列を生成。
    セクション間に <!-- section: {filename} --> のHTMLコメント区切りを挿入。
    """

def write_all(article_dir: Path) -> Path:
    """build_all() の結果を article_dir/all.md に書き込む"""

def read_all(article_dir: Path) -> str:
    """all.md を読む。なければ write_all() してから読む"""

def split_markdown_to_sections(source_md: Path, article_dir: Path) -> list[dict]:
    """
    単一の .md ファイルを H2見出し（##）単位でセクション分割して
    article_dir に sec01_xxx.md, sec02_xxx.md ... として書き出す。
    H1タイトル+H2前の本文は sec01_intro.md として扱う。
    見出しテキストは _slugify() でファイル名化する。
    分割後に write_all() も実行する。
    """

def _slugify(text: str) -> str:
    """
    日本語・英数字・アンダースコアを残し、スペースを_に変換。最大30文字。
    空文字の場合は "section" を返す。
    """

def get_iterations_dir(article_dir: Path) -> Path:
    """article_dir/iterations/ を作成して返す"""

def save_section_iteration(article_dir: Path, section: dict, content: str, iter_num: int) -> Path:
    """
    iterations/{filename_without_ext}_iter{nn}.md としてスナップショット保存
    """

def load_state(article_dir: Path) -> dict:
    """_state.json を読む。なければ {"iteration": 0, "history": []} を返す"""

def save_state(article_dir: Path, state: dict): ...

def append_history(state: dict, iter_num: int, target_section: str,
                   feedback: str, critique_summary: str,
                   validation_score: int, verdict: str, coherence_applied: bool):
    """
    state["history"] に以下のキーを持つエントリを追加：
    iter, timestamp(ISO形式), target_section, feedback_snippet(先頭80文字),
    critique_summary, validation_score, verdict, coherence_applied
    state["iteration"] も iter_num に更新する。
    """

def print_section_list(article_dir: Path):
    """
    セクション一覧を表示。例:
      📂 セクション一覧:
        [01] sec01_intro.md       (342文字)
        [02] sec02_body.md        (512文字)
        📄 all.md  (全854文字)
    """
```

---

### `agents/__init__.py`

空ファイルでよい（コメント1行のみ可）。

---

### `agents/critic.py`

**役割**: 音声フィードバックを分析し、「どのセクションの・何が問題か」を構造化抽出する。

**モデル**: `claude-opus-4-5`

**システムプロンプトに含めること**:
- 出力は必ずJSONのみ（前後の説明文・コードブロック記法なし）
- 以下のJSONスキーマを出力すること:

```json
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
```

- セクション特定ルール：
  - ユーザーが「導入」「最初の部分」などと言ったら対応ファイルを推定
  - 複数セクションへの指摘は issues を複数発行して各 section を明記
  - 不明なら `target_section: null`, `target_section_confidence: "unclear"`

**シグネチャ**:
```python
def run(
    sections_content: dict[str, str],  # {"sec01_intro.md": "内容...", ...}
    feedback: str,                      # 文字起こし済みフィードバック
    target_hint: str | None,           # CLIで明示指定されたファイル名（なければNone）
    client: anthropic.Anthropic
) -> dict:
```

**ログ出力**（printで）:
- `🔍 [CriticAgent] フィードバックを分析中...`
- 対象セクションと確信度（🎯=explicit, 🔮=inferred, ❓=unclear）
- 問題点ごとに severity アイコン（🔴high, 🟡medium, 🟢low）付きで表示
- 波及影響があれば `⚡ 波及影響の可能性: ...`

JSON抽出: ` ```json ... ``` ` で囲まれている場合はブロックを除去してからパースする。

---

### `agents/editor.py`

**役割**: CriticAgentの出力を受けて、対象セクションのみを書き換える。

**モデル**: `claude-opus-4-5`

**システムプロンプトに含めること**:
- 対象セクションのみ編集する（他セクションは絶対に変えない）
- preserve リストの要素は変えない
- severity: high から優先的に対処
- ユーザーのトーン・視点を維持する
- 改善済みのMarkdown本文のみ出力（説明文・コメント不要）

**ユーザープロンプトに含めること**:
- 編集対象セクション名と内容
- 他セクションの先頭300文字ずつ（流れ把握のための参照用、編集しない旨を明記）
- CriticAgentの分析結果（JSON文字列）

**シグネチャ**:
```python
def run(
    target_section_name: str,
    target_content: str,
    all_sections_content: dict[str, str],
    critique: dict,
    client: anthropic.Anthropic
) -> str:  # 改善済みセクション内容
```

**ログ出力**:
- `✍️  [EditorAgent] {section_name} を改善中...`
- 完了後: 元の文字数 → 改善後の文字数（差分をsign付きで）

---

### `agents/validator.py`

**役割**: 改善後セクションがフィードバックを正しく反映しているか検証する。

**モデル**: `claude-opus-4-5`

**システムプロンプトに含めること**:
- 出力は必ずJSONのみ
- 以下のJSONスキーマ:

```json
{
  "verdict": "pass | needs_revision",
  "score": 0-100,
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
```

`coherence_risk` は「この編集が他セクションとの整合性を崩すリスク」を示す。
これが medium または high のとき、orchestratorがCoherenceAgentを起動する。

**シグネチャ**:
```python
def run(
    section_name: str,
    original: str,
    feedback: str,
    improved: str,
    critique: dict,
    client: anthropic.Anthropic
) -> dict:
```

**ログ出力**:
- `✅ [ValidatorAgent] {section_name} の改善結果を検証中...`
- 判定（✅pass / ⚠️needs_revision）、スコア
- coherence_risk（🟢low / 🟡medium / 🔴high）
- recommendation

---

### `agents/coherence.py`

**役割**: 全セクション編集後に全体の一貫性を調整し、`all.md` の完成版全文を生成する。

**モデル**: `claude-opus-4-5`

**2つの関数を実装する**:

#### `analyze(sections_content, client) -> dict`

全セクションの整合性を分析してレポートを返す（本文は生成しない）。

システムプロンプト: 以下のJSONスキーマで出力:
```json
{
  "coherence_score": 0-100,
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
```

ログ: `🔗 [CoherenceAgent] 整合性を分析中...`、スコアと問題件数、各問題をseverityアイコン付きで表示。

#### `run(sections_content, edited_section_name, client) -> tuple[str, dict]`

整合性調整済みの全文と分析レポートのタプルを返す。

内部で `analyze()` を呼んだ後:
- `coherence_score >= 90` かつ `issues_found` が空なら全セクションをそのまま結合して返す
- それ以外は、問題点リストとセクション内容をプロンプトに渡して調整済み全文を生成する

全文生成のシステムプロンプトに含めること:
- 調整対象: セクション間の流れ・用語・トーン・重複・矛盾
- 各セクションの核となる主張・情報は変えない
- 必要最小限の変更に留める
- note記事のトーン（読者との親しみやすさ）を維持
- 出力: 調整済み全文Markdownのみ（説明文・セクションコメント不要）

ログ: `🔗 [CoherenceAgent] all.md を整合性調整中...`、完了メッセージと文字数。

**シグネチャ**:
```python
def analyze(sections_content: dict[str, str], client: anthropic.Anthropic) -> dict: ...

def run(
    sections_content: dict[str, str],
    edited_section_name: str,
    client: anthropic.Anthropic
) -> tuple[str, dict]:  # (整合性調整済み全文, 分析レポート)
```

---

### `transcribe.py`

Whisperを使った音声→テキスト変換モジュール。

**実装する関数**:
```python
def transcribe(audio_path: str) -> str:
    """
    openai-whisper パッケージを使って日本語音声を文字起こし。
    whisperがimportできない場合はエラーメッセージとインストールコマンドを表示してsys.exit(1)。
    モデルは "base" を使用（コメントで small/medium/large も可と記載）。
    language="ja" を指定。
    ログ: 🎙️  [Whisper] 文字起こし中: {audio_path}
    完了後: 先頭100文字をプレビュー表示
    """
```

---

### `record.sh`

Soxを使ったマイク録音スクリプト。

```bash
#!/bin/bash
# 使い方: ./record.sh [出力ファイル名]
# Ctrl+C または Enterキーで停止

OUTPUT=${1:-"audio/feedback_$(date +%H%M%S).wav"}

# バックグラウンドでsox録音（16000Hz, モノラル）
# Enterで停止
# 完了メッセージを表示
```

---

### `orchestrator.py`

メインオーケストレーター。以下のサブコマンドを `argparse` で実装する。

**CLIインターフェース**:

```
python orchestrator.py --setup <source.md>
    単一mdをセクション分割してディレクトリを初期化

python orchestrator.py --list <article_dir/>
    セクション一覧とイテレーション履歴を表示

python orchestrator.py --article <article_dir/> [オプション]
    メインのリファインループを実行

  オプション:
    --section <name>          対象セクション名（省略可）
    --text-feedback <text>    テキストでフィードバック（音声録音をスキップ）
    --skip-validation         Validatorをスキップ
    --skip-coherence          CoherenceAgentをスキップしてall.mdを単純結合
    --coherence-only          CoherenceAgentのみ実行してall.mdを更新
```

**`--section` の名前解決**:
`sec02_body`, `sec02_body.md`, `body`, `sec02` のいずれでも同じセクションを指せるように
`find_section_by_name()` を使う。

**メインリファインループ (`cmd_refine`) の処理順序**:

1. セクション一覧を読み込む
2. `--section` が指定されていれば解決、なければ後でCriticに自動判断させる
3. ヘッダーと履歴と現在のセクション一覧を表示
4. フィードバック取得:
   - `--text-feedback` があればそれを使う
   - Soxがある場合はマイク録音 → `transcribe()` で文字起こし
   - Soxがない場合はテキスト入力にフォールバック
5. **CriticAgent** を実行（全セクション内容 + フィードバック + ヒントを渡す）
6. 対象セクションを確定:
   - CriticAgentの `target_section` が非nullならそれを使う
   - null（unclear）ならCLIでユーザーに選択させる
7. **EditorAgent** を実行（対象セクションのみ）
8. **ValidatorAgent** を実行（`--skip-validation` でスキップ可）
9. 対象セクションファイルを上書き保存 + イテレーションスナップショット保存
10. **CoherenceAgent** を実行:
    - `--skip-coherence` なら `write_all()` で単純結合してスキップ
    - それ以外は常に実行（`coherence_risk` に関わらず）
    - 生成した全文を `all.md` に書き込む
11. `_state.json` にイテレーション情報を追記
12. 改善後セクション内容を表示、次のアクションを案内

**エラーハンドリング**:
- `ANTHROPIC_API_KEY` がない場合は明確なエラーメッセージを出して終了
- 指定セクションが見つからない場合は利用可能なセクション一覧を表示して終了
- `--article` 指定のディレクトリが存在しない場合は `--setup` を案内して終了

**ログ・表示**:
ヘッダー例:
```
=================================================================
  📝 note記事リファインシステム
  記事: my_article | イテレーション #3
  🎯 対象セクション: sec02_body.md
=================================================================
```

履歴表示例:
```
📊 イテレーション履歴:
  #01 ✅🔗 [sec01_intro.md] スコア: 82 | 導入部の共感度を上げる
  #02 ⚠️   [sec03_technique.md] スコア: 71 | 具体例の追加
```
（🔗はCoherenceAgent適用済みを示す）

---

### `README.md`

以下の内容を含めること:
- エージェント構成図（ASCIIアート）
- セットアップ手順（pip install, brew install sox, APIキー設定）
- 典型的なワークフロー（--setup → --list → --article のステップ）
- `--section` に渡せる名前のバリエーション例
- サンプル記事での動作確認コマンド

---

## 実装上の注意事項

1. **Python バージョン**: 3.10以上を前提とする（`str | Path` のユニオン型記法を使用）

2. **JSON抽出の堅牢化**: APIレスポンスが ` ```json ... ``` ` で囲まれていても正しくパースできるよう、すべてのエージェントで以下のパターンを実装する:
   ```python
   raw = message.content[0].text.strip()
   if "```" in raw:
       raw = raw.split("```")[1]
       if raw.startswith("json"):
           raw = raw[4:]
   result = json.loads(raw)
   ```

3. **モデル統一**: すべてのエージェントで `claude-opus-4-5` を使用する

4. **max_tokens**:
   - CriticAgent, ValidatorAgent, CoherenceAgent（analyze）: 2000
   - EditorAgent: 3000
   - CoherenceAgent（run / 全文生成）: 5000

5. **エンコーディング**: すべてのファイル読み書きで `encoding="utf-8"` を指定する

6. **`sys.path` の設定**: `orchestrator.py` 冒頭で以下を実行してエージェントをimportできるようにする:
   ```python
   sys.path.insert(0, str(Path(__file__).parent))
   ```

7. **ディレクトリ自動作成**: `audio/` ディレクトリは `orchestrator.py` の起動時に `mkdir(exist_ok=True)` で自動作成する。`iterations/` は `save_section_iteration()` 内で作成する。

8. **サンプル記事**: `drafts/sample_article/` に以下の4セクションファイルを作成する（日本語のnote記事のサンプル。恋愛・デート系のテーマで）:
   - `sec01_intro.md`
   - `sec02_mistake.md`
   - `sec03_technique.md`
   - `sec04_conclusion.md`

9. **動作確認コマンド**: 実装完了後、以下をREADMEに記載し、実際に実行して構文エラーがないことを確認する:
   ```bash
   python orchestrator.py --list drafts/sample_article/
   ```

---

## 実装完了の確認チェックリスト

実装が完了したら、以下を順番に確認してください：

- [ ] `python orchestrator.py --list drafts/sample_article/` が正常に動作する
- [ ] `python -c "from agents import critic, editor, validator, coherence; print('OK')"` が通る
- [ ] `python -c "from section_manager import list_sections, find_section_by_name; print('OK')"` が通る
- [ ] `python orchestrator.py --help` が正常に表示される
- [ ] `drafts/sample_article/` に4つのセクションファイルが存在する
- [ ] `CLAUDE.md` が存在する
