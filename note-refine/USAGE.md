# note-refine 使い方ガイド

## 概要

note記事を「フィードバック → AI改善 → 検証」のサイクルで磨いていくシステムです。
音声で話しかけるか、テキストで指示するだけで、4つのAIエージェントが連携して記事を改善します。

---

## 最初にやること（初回のみ）

### 1. APIキーを設定する

```bash
export ANTHROPIC_API_KEY="sk-ant-xxxxx"
```

Windowsの場合：
```
set ANTHROPIC_API_KEY=sk-ant-xxxxx
```

### 2. パッケージをインストールする

```bash
pip install anthropic
```

音声フィードバックを使う場合はさらに：
```bash
pip install openai-whisper
brew install sox      # macOS
# apt install sox     # Ubuntu/Debian
```

---

## 基本的な使い方

### パターンA：既存の記事をセットアップして使う

手元にある `.md` ファイルを分割してシステムに取り込みます。

```bash
# 記事ファイルをセクションに分割
python orchestrator.py --setup my_article.md
```

`## 見出し` でセクションが分割され、`my_article/` ディレクトリに保存されます。

```
my_article/
├── sec01_intro.md
├── sec02_xxx.md
├── sec03_xxx.md
└── all.md  ← 全セクション結合（自動生成）
```

### パターンB：サンプルで試す

リポジトリに付属のサンプル記事（恋愛・デート系テーマ）がそのまま使えます。

```bash
python orchestrator.py --list drafts/sample_article/
```

---

## メインの使い方：記事を改善する

### テキストでフィードバックを送る（一番かんたん）

```bash
python orchestrator.py --article drafts/sample_article/ \
  --text-feedback "導入部分をもっと共感しやすくしてほしい"
```

実行すると以下の順で処理が走ります：

```
1. CriticAgent   → フィードバックを分析して問題点を抽出
2. EditorAgent   → 対象セクションを改善
3. ValidatorAgent → 改善内容を検証・スコアリング
4. CoherenceAgent → 全セクションの整合性を調整して all.md を更新
```

### 音声でフィードバックを送る

`sox` がインストールされていれば、マイク録音 → 自動文字起こしができます。

```bash
python orchestrator.py --article drafts/sample_article/
```

起動後「Enterキーで停止」と表示されるので、しゃべってからEnterを押します。

### 対象セクションを自分で指定する

```bash
# セクション名の様々な指定方法（すべて同じセクションを指す）
--section sec02_mistake
--section sec02_mistake.md
--section mistake
--section sec02
```

例：
```bash
python orchestrator.py --article drafts/sample_article/ \
  --section sec03_technique \
  --text-feedback "具体的な事例を1つ追加してほしい"
```

指定しない場合は CriticAgent がフィードバックの内容からセクションを自動判断します。

---

## その他のコマンド

### 履歴を確認する

```bash
python orchestrator.py --list drafts/sample_article/
```

出力例：
```
  📂 セクション一覧:
    [01] sec01_intro.md            (342文字)
    [02] sec02_mistake.md          (412文字)
    [03] sec03_technique.md        (480文字)
    [04] sec04_conclusion.md       (356文字)
    📄 all.md  (全1590文字)

📊 イテレーション履歴:
  #01 ✅🔗 [sec01_intro.md] スコア: 82 | 導入の共感性が不足
  #02 ⚠️   [sec03_technique.md] スコア: 71 | 具体例の追加が必要
```

- `✅` = 改善がフィードバックを反映できた（pass）
- `⚠️` = 改善が不十分（needs_revision）
- `🔗` = CoherenceAgent による整合性調整が適用された

### 整合性だけを調整する

複数のセクションを個別に編集した後、全体の流れを整えたいときに使います。

```bash
python orchestrator.py --article drafts/sample_article/ --coherence-only
```

---

## オプション一覧

| オプション | 説明 |
|-----------|------|
| `--text-feedback "テキスト"` | 音声録音をスキップして直接フィードバックを渡す |
| `--section <名前>` | 対象セクションを明示（省略するとCriticAgentが自動判断） |
| `--skip-validation` | ValidatorAgent をスキップ（高速化したい場合） |
| `--skip-coherence` | CoherenceAgent をスキップして all.md を単純結合 |
| `--coherence-only` | CoherenceAgent だけを実行して all.md を更新 |

---

## ファイルの保存場所

記事ディレクトリの中に以下が自動で作られます：

| ファイル/フォルダ | 内容 |
|-----------------|------|
| `sec01_intro.md` など | 各セクションの現在の内容 |
| `all.md` | 全セクション結合の最終版 |
| `_state.json` | イテレーション番号と改善履歴 |
| `iterations/` | 各イテレーションのスナップショット |

過去の状態に戻したい場合は `iterations/` の中のファイルを参照してください。

---

## よくある使い方のパターン

### 記事全体を一通りリファインしたい

```bash
# セクションを順番に改善していく
python orchestrator.py --article my_article/ --section sec01 --text-feedback "..."
python orchestrator.py --article my_article/ --section sec02 --text-feedback "..."
python orchestrator.py --article my_article/ --section sec03 --text-feedback "..."

# 最後に全体の整合性を調整
python orchestrator.py --article my_article/ --coherence-only
```

### 素早くたくさん試したい（高速モード）

Validator と CoherenceAgent の両方をスキップします。

```bash
python orchestrator.py --article my_article/ \
  --text-feedback "..." \
  --skip-validation \
  --skip-coherence
```

### 仕上げに一度だけ整合性を整える

```bash
python orchestrator.py --article my_article/ --coherence-only
```

---

## トラブルシューティング

**`ANTHROPIC_API_KEY が設定されていません` と表示される**
→ `export ANTHROPIC_API_KEY="..."` を実行してから再度試してください。

**`セクションファイルが見つかりません` と表示される**
→ まず `--setup` でセットアップするか、`--list` でディレクトリ内を確認してください。

**音声文字起こしのモジュールがないと言われる**
→ `pip install openai-whisper` を実行してください。

**Whisper の精度を上げたい**
→ `transcribe.py` の `whisper.load_model("base")` を `"small"` または `"medium"` に変更すると精度が上がります（処理時間は長くなります）。
