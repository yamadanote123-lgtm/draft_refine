#!/bin/bash
# 使い方: ./record.sh [出力ファイル名]
# Ctrl+C または Enterキーで停止

OUTPUT=${1:-"audio/feedback_$(date +%H%M%S).wav"}

echo "🎙️  録音開始: $OUTPUT"
echo "Enterキーで停止..."

# バックグラウンドでsox録音（16000Hz, モノラル）
sox -d -r 16000 -c 1 "$OUTPUT" &
SOX_PID=$!

# Enterキー待機
read -r

# 録音停止
kill $SOX_PID 2>/dev/null
wait $SOX_PID 2>/dev/null

echo "✅ 録音完了: $OUTPUT"
