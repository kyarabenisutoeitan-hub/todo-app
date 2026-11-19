"""Todo リスト（課題1）

やること（タイトル・内容・期日）を Google スプレッドシートに保存する Flask アプリ。
ページは「一覧」「登録」「編集」の3つ。
課題2で「完了と削除」「期日前の LINE 通知」「重要度と並べ替え」を追加。
"""

import json
import os
import secrets
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone

import gspread
from flask import Flask, abort, redirect, render_template, request, url_for

app = Flask(__name__)

# スプレッドシートのID（アドレスの /d/ と /edit の間の文字）
SHEET_ID = os.environ.get("SHEET_ID", "")

# 1行目の見出し。列の順番はこの通り
# 完了日：空なら未完了、日付が入っていれば完了（課題2で追加）
# 重要度：高・中・低（空のときは「中」として扱う）（課題2で追加）
HEADER = ["ID", "タイトル", "内容", "期日", "登録日", "完了日", "重要度"]

# 重要度の選び方と、重要度順に並べるときの順番
PRIORITIES = ["高", "中", "低"]
DEFAULT_PRIORITY = "中"

_client = None

# 日本時間（Render のサーバーはイギリス時間で動くため）
JST = timezone(timedelta(hours=9))


def today_jst():
    """日本時間の今日の日付を「2026-11-16」の形で返す"""
    return datetime.now(JST).date().isoformat()


# ---- 課題2：期日前の LINE 通知 ----
# 通知専用 LINE から自分に送る。鍵は Render の環境変数に入れておく
#   LINE_CHANNEL_TOKEN：通知専用 LINE のチャネルアクセストークン
#   LINE_TO：送り先（自分）の LINE のユーザーID
#   NOTIFY_KEY：/notify を開くための合言葉（知らない人に送らせないため）
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
APP_URL = "https://todo-app-nfnq.onrender.com"


def short_date(iso):
    """「2026-11-07」を「11/7」にする"""
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def build_notice(todos, today):
    """未完了で、期日が過ぎている・今日・明日のものを1通の文にする（なければ None）"""
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    groups = {"over": [], "today": [], "tomorrow": []}
    for t in todos:
        if t["done"] or not t["due"]:
            continue
        line = f"・{t['title']}（{short_date(t['due'])}）"
        if t["due"] < today:
            groups["over"].append(line)
        elif t["due"] == today:
            groups["today"].append(line)
        elif t["due"] == tomorrow:
            groups["tomorrow"].append(line)
    if not any(groups.values()):
        return None
    parts = ["【Todo】今日・明日のやること"]
    for key, label in [("over", "⚠ 期日を過ぎています"), ("today", "📌 今日"),
                       ("tomorrow", "🗓 明日")]:
        if groups[key]:
            parts.append(label + "\n" + "\n".join(groups[key]))
    parts.append(APP_URL)
    return "\n\n".join(parts)


def push_line(text):
    """通知専用 LINE から自分に1通送る"""
    body = {"to": os.environ["LINE_TO"], "messages": [{"type": "text", "text": text}]}
    req = urllib.request.Request(
        LINE_PUSH_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["LINE_CHANNEL_TOKEN"],
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20)


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
             "done": r[5], "priority": r[6] if r[6] in PRIORITIES else DEFAULT_PRIORITY}
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
        "priority": request.form.get("priority", DEFAULT_PRIORITY),
    }
    errors = []
    if todo["priority"] not in PRIORITIES:
        errors.append("重要度は 高・中・低 から選んでください")
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
    # 並べ方：?sort=priority なら重要度順（同じ重要度の中は期日順のまま）、それ以外は期日順
    sort = request.args.get("sort", "due")
    if sort == "priority":
        open_todos.sort(key=lambda t: PRIORITIES.index(t["priority"]))
    else:
        sort = "due"
    return render_template("index.html", todos=open_todos, done_todos=done_todos,
                           today=today_jst(), sort=sort)


@app.route("/new", methods=["GET", "POST"])
def new():
    """登録のページ"""
    if request.method == "POST":
        todo, errors = read_form()
        if errors:
            return render_template("form.html", todo=todo, errors=errors, mode="new",
                                   priorities=PRIORITIES)
        ws = get_sheet()
        ws.append_row(
            [uuid.uuid4().hex[:8], todo["title"], todo["body"], todo["due"],
             today_jst(), "", todo["priority"]],
            value_input_option="RAW",  # 日付を文字のまま保存する
        )
        return redirect(url_for("index"))
    return render_template("form.html", todo={"priority": DEFAULT_PRIORITY}, errors=[],
                           mode="new", priorities=PRIORITIES)


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
            return render_template("form.html", todo=todo, errors=errors, mode="edit",
                                   priorities=PRIORITIES)
        # タイトル・内容・期日（B〜D列）と重要度（G列）を、1回でまとめて書く
        ws.batch_update(
            [
                {"range": f"B{row}:D{row}",
                 "values": [[todo["title"], todo["body"], todo["due"]]]},
                {"range": f"G{row}", "values": [[todo["priority"]]]},
            ],
            value_input_option="RAW",
        )
        return redirect(url_for("index"))
    r = (ws.row_values(row) + [""] * len(HEADER))[: len(HEADER)]
    todo = {"id": r[0], "title": r[1], "body": r[2], "due": r[3],
            "priority": r[6] if r[6] in PRIORITIES else DEFAULT_PRIORITY}
    return render_template("form.html", todo=todo, errors=[], mode="edit",
                           priorities=PRIORITIES)


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


@app.route("/notify")
def notify():
    """毎朝、目覚まし役（Apps Script）がこのページを開くと、LINE で通知を送る
    ?dry=1 を付けると送らずに、送る文だけを画面に出す（確かめる用）"""
    key = os.environ.get("NOTIFY_KEY", "")
    if not key or not secrets.compare_digest(request.args.get("key", ""), key):
        abort(403)
    text = build_notice(load_todos(get_sheet()), today_jst())
    if text is None:
        return "送るものはありません"
    if request.args.get("dry"):
        return text, 200, {"Content-Type": "text/plain; charset=utf-8"}
    push_line(text)
    return "送りました"


if __name__ == "__main__":
    # 自分のパソコンで試すとき： python app.py → http://127.0.0.1:5000
    app.run(debug=True)
