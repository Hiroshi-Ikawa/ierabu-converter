#!/bin/bash
# いえらぶ変換ツール 起動スクリプト
# ダブルクリックで起動できます

cd "$(dirname "$0")"

# 依存ライブラリが未インストールの場合はインストール
pip3 install -q -r requirements.txt

# IPアドレス取得
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "localhost")

echo ""
echo "======================================"
echo "  いえらぶ変換ツール 起動中..."
echo "======================================"
echo ""
echo "  アクセスURL: http://${IP}:8080"
echo ""
echo "  このURLを他のメンバーに共有してください。"
echo "  終了するにはこのウィンドウを閉じてください。"
echo "======================================"
echo ""

# ブラウザで自動的に開く
sleep 1 && open "http://localhost:8080" &

python3 app.py
