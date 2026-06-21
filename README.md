# いえらぶクラウド 消し込みCSV変換ツール

複数の保証会社・引落データ（CSV / PDF）を、いえらぶクラウドのインポート形式に一括変換するWebアプリです。

## 起動方法

```bash
cd ierabu_converter
pip install -r requirements.txt
python app.py
# ブラウザで http://localhost:5000 を開く
```

## 使い方

1. ブラウザで `http://localhost:5000` を開く
2. 変換したい CSV / PDF ファイルをドロップゾーンにドラッグ＆ドロップ（複数同時可）
3. 「変換してCSVをダウンロード」ボタンをクリック
4. 結果を確認し、「CSVをダウンロード」ボタンで保存
5. ダウンロードした CSV をいえらぶクラウドにインポート

## 対応フォーマット

| フォーマット | ファイル名パターン | 種別 |
|---|---|---|
| FB引落データ（エポス・ジェイリース等） | `FB_YYYYMMDD_*.csv` | CSV |
| 日本セーフティー保証引落 | `いえらふ__*.csv` | CSV |
| 楽天保証収納 支店・営業所単位 | `YYYYMM_支店_*.csv` | CSV |
| 日本セーフティー送金明細 | `セーフティ*.pdf` | PDF |

## 出力フォーマット

```
勘定日,金額,振込依頼人コード,振込依頼人カナ
20260526,172750,,ﾔﾏﾀﾞ ﾕｳﾔ
20260527,246530,3803222,ｶﾗｻﾜ ｶﾅｺ
```

- 文字コード: UTF-8 BOM付き
- ファイル名: `YYYYMM_ierabu_import.csv`（実行月の年月）

## 新フォーマットの追加方法

`app.py` の `FORMAT_REGISTRY` リストに1エントリ追加し、対応する変換関数を実装するだけです。

```python
# FORMAT_REGISTRY に追加
{
    "id": "new_format",
    "name": "新しいフォーマット名",
    "match": lambda name: name.startswith("新フォーマット"),
    "type": "csv",
},

# 変換関数を追加
def convert_new_format(raw: bytes, filename: str) -> list[dict]:
    ...

# CONVERTERS に登録
CONVERTERS["new_format"] = convert_new_format
```
