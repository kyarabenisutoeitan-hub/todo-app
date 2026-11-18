"""Todo リスト（課題1）

やること（タイトル・内容・期日）を Google スプレッドシートに保存する Flask アプリ。
ページは「一覧」「登録」「編集」の3つ。
課題2で「完了と削除」を追加。
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
# 完了日：空なら未完了、日付が入っていれば完了（課題2で追加）
HEADER = ["ID", "タイトル", "内容", "期日", "登録日", "完了日"]

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
        # A1 から右へ見出しを書く（列が増えても、ここは直さなくてよい）
        ws.update(range_name="A1", values=[HEADER], value_input_option="RAW")
    return ws


def load_todos(ws):
    """全部のやることを返す（未完了は期日の近い順、完了は完了日の新しい順）"""
    rows = ws.get_all_values()[1:]  # 1行目（見出し）は飛ばす
    todos = []
    for r in rows:
        r = (r + [""] * len(HEADER))[: len(HEADER)]  # 足りない列は空にそろえる
        if not r[0]:
            continue
        todos.append(
            {"id": r[0], "title": r[1], "body": r[2], "due": r[3], "created": r[4],
             "done": r[5]}
        )
    # 期日が空のものは最後に
    todos.sort(key=lambda t: (t["due"] == "", t["due"]))
    # 完了したものは、最近完了したものが上に来るように並べ直す
    todos.sort(key=lambda t: t["done"], reverse=True)
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
    # 未完了と完了に分けて、画面では別々に出す
    open_todos = [t for t in todos if not t["done"]]
    done_todos = [t for t in todos if t["done"]]
    return render_template("index.html", todos=open_todos, done_todos=done_todos,
                           today=today_jst())


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


@app.route("/done/<todo_id>", methods=["POST"])
def toggle_done(todo_id):
    """完了ボタン：未完了なら今日の日付を入れて完了に、完了なら空にして戻す"""
    ws = get_sheet()
    row = find_row(ws, todo_id)
    if row is None:
        abort(404)
    current = ws.acell(f"F{row}").value or ""
    new_value = "" if current else today_jst()
    ws.update(range_name=f"F{row}", values=[[new_value]], value_input_option="RAW")
    return redirect(url_for("index"))


@app.route("/delete/<todo_id>", methods=["POST"])
def delete(todo_id):
    """削除ボタン：その行をシートから消す"""
    ws = get_sheet()
    row = find_row(ws, todo_id)
    if row is None:
        abort(404)
    ws.delete_rows(row)
    return redirect(url_for("index"))


if __name__ == "__main__":
    # 自分のパソコンで試すとき： python app.py → http://127.0.0.1:5000
    app.run(debug=True)
