"""Todo リスト（課題1）

やること（タイトル・内容・期日）を Google スプレッドシートに保存する Flask アプリ。
ページは「一覧」「登録」「編集」の3つ。
"""

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import gspread
from flask import Flask, abort, redirect, render_template, request, url_for

app = Flask(__name__)

# スプレッドシートのID（アドレスの /d/ と /edit の間の文字）
SHEET_ID = os.environ.get("SHEET_ID", "")

# 1行目の見出し。列の順番はこの通り
HEADER = ["ID", "タイトル", "内容", "期日", "登録日"]

_client = None

# 日本時間（Render のサーバーはイギリス時間で動くため）
JST = timezone(timedelta(hours=9))


def today_jst():
    """日本時間の今日の日付を「2026-11-16」の形で返す"""
    return datetime.now(JST).date().isoformat()


def get_sheet():
    """スプレッドシートの1枚目のシートを返す（見出しがなければ書く）"""
    global _client
    if _client is None:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        if creds_json:
            # Render では、鍵の中身を環境変数に入れておく
            _client = gspread.service_account_from_dict(json.loads(creds_json))
        else:
            # 自分のパソコンでは、同じフォルダーの credentials.json を使う
            _client = gspread.service_account(filename="credentials.json")
    ws = _client.open_by_key(SHEET_ID).sheet1
    if ws.row_values(1) != HEADER:
        ws.update(range_name="A1:E1", values=[HEADER], value_input_option="RAW")
    return ws


def load_todos(ws):
    """全部のやることを、期日の近い順に並べて返す"""
    rows = ws.get_all_values()[1:]  # 1行目（見出し）は飛ばす
    todos = []
    for r in rows:
        r = (r + [""] * len(HEADER))[: len(HEADER)]  # 足りない列は空にそろえる
        if not r[0]:
            continue
        todos.append(
            {"id": r[0], "title": r[1], "body": r[2], "due": r[3], "created": r[4]}
        )
    # 期日が空のものは最後に
    todos.sort(key=lambda t: (t["due"] == "", t["due"]))
    return todos


def find_row(ws, todo_id):
    """IDが入っている行の番号を返す（なければ None）"""
    ids = ws.col_values(1)
    if todo_id in ids[1:]:
        return ids.index(todo_id) + 1
    return None


def read_form():
    """入力画面の値を取り出して、まちがいがあればメッセージを返す"""
    todo = {
        "title": request.form.get("title", "").strip(),
        "body": request.form.get("body", "").strip(),
        "due": request.form.get("due", "").strip(),
    }
    errors = []
    if not todo["title"]:
        errors.append("タイトルを入れてください")
    if not todo["due"]:
        errors.append("期日を入れてください")
    else:
        try:
            date.fromisoformat(todo["due"])
        except ValueError:
            errors.append("期日の形がちがいます")
    return todo, errors


@app.route("/")
def index():
    """一覧のページ"""
    todos = load_todos(get_sheet())
    return render_template("index.html", todos=todos, today=today_jst())


@app.route("/new", methods=["GET", "POST"])
def new():
    """登録のページ"""
    if request.method == "POST":
        todo, errors = read_form()
        if errors:
            return render_template("form.html", todo=todo, errors=errors, mode="new")
        ws = get_sheet()
        ws.append_row(
            [uuid.uuid4().hex[:8], todo["title"], todo["body"], todo["due"],
             today_jst()],
            value_input_option="RAW",  # 日付を文字のまま保存する
        )
        return redirect(url_for("index"))
    return render_template("form.html", todo={}, errors=[], mode="new")


@app.route("/edit/<todo_id>", methods=["GET", "POST"])
def edit(todo_id):
    """編集のページ"""
    ws = get_sheet()
    row = find_row(ws, todo_id)
    if row is None:
        abort(404)
    if request.method == "POST":
        todo, errors = read_form()
        todo["id"] = todo_id
        if errors:
            return render_template("form.html", todo=todo, errors=errors, mode="edit")
        ws.update(
            range_name=f"B{row}:D{row}",
            values=[[todo["title"], todo["body"], todo["due"]]],
            value_input_option="RAW",
        )
        return redirect(url_for("index"))
    r = (ws.row_values(row) + [""] * len(HEADER))[: len(HEADER)]
    todo = {"id": r[0], "title": r[1], "body": r[2], "due": r[3]}
    return render_template("form.html", todo=todo, errors=[], mode="edit")


if __name__ == "__main__":
    # 自分のパソコンで試すとき： python app.py → http://127.0.0.1:5000
    app.run(debug=True)
