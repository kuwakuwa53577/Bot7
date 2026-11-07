import asyncio
import json
import os
import datetime
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask, render_template_string, request, jsonify, redirect, session
import requests

import firebase_admin
from firebase_admin import credentials, firestore

import logging

# ログ設定
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------
# 設定 & 環境変数
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", 10000))

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = f"{WEB_URL}/oauth/callback" if WEB_URL else f"http://localhost:{PORT}/oauth/callback"

# --------------------------------------------------
# Firebase 初期化
# --------------------------------------------------
firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS")

if firebase_creds_json:
    try:
        cred_dict = json.loads(firebase_creds_json)
        if "private_key" in cred_dict:
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin SDK の初期化に成功しました")
    except Exception as e:
        print(f"❌ Firebase 初期化エラー: {e}")
elif os.path.exists("serviceAccountKey.json"):
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    print("✅ ローカルファイルで Firebase の初期化に成功しました")
else:
    print("⚠️ 警告: FIREBASE_CREDENTIALS または serviceAccountKey.json が見つかりません。")

try:
    db = firestore.client()
except Exception as e:
    db = None
    print(f"⚠️ Firestoreクライアントの取得に失敗しました: {e}")

# --------------------------------------------------
# データベース設定ヘルパー（Firestoreでサーバー設定を動的管理）
# --------------------------------------------------
def get_server_config(guild_id):
    """Firestoreからサーバーごとの設定を取得する"""
    if not db:
        return {}
    try:
        doc = db.collection("server_configs").document(str(guild_id)).get()
        if doc.exists:
            return doc.to_dict()
    except Exception as e:
        print(f"❌ 設定取得エラー: {e}")
    return {}

def save_server_config(guild_id, config_dict):
    """Firestoreへサーバーごとの設定を保存する"""
    if not db:
        return
    try:
        db.collection("server_configs").document(str(guild_id)).set(config_dict, merge=True)
    except Exception as e:
        print(f"❌ 設定保存エラー: {e}")

def save_user_verification(user_id, data_dict):
    if not db:
        return
    try:
        db.collection("verifications").document(str(user_id)).set(data_dict, merge=True)
    except Exception as e:
        print(f"❌ ユーザーデータ保存エラー: {e}")

# --------------------------------------------------
# Flask Webサーバー
# --------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "kuwakuwa_secret_key_change_this")

@app.route("/ping")
def ping():
    return "pong", 200

# 認証前ページ（長押し演出・セキュリティチェック付き）
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>安全な認証システム</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: sans-serif; color: #fff; height: 100vh; display: flex; justify-content: center; align-items: center; background: #0f1115; }
    .container { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); padding: 40px; border-radius: 16px; text-align: center; width: 90%; max-width: 420px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); }
    h1 { font-size: 1.4rem; margin-bottom: 10px; color: #fff; }
    p { color: #8a8f98; font-size: 0.88rem; margin-bottom: 30px; line-height: 1.5; }
    .verify-btn { width: 100%; padding: 15px; font-size: 1rem; color: #fff; background: #5865F2; border: none; border-radius: 10px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; transition: all 0.2s; user-select: none; }
    .verify-btn:hover { background: #4752C4; transform: translateY(-1px); }
    .verify-btn:active { transform: scale(0.98); }
  </style>
</head>
<body>
  <div class="container">
    <h1>Discord 認証</h1>
    <p>サーバーの安全性を保つため、アカウント認証を行います。ボタンをクリックして認証を進めてください。</p>
    <button id="verify-btn" class="verify-btn" onclick="startAuth()">Discordで認証を開始</button>
  </div>
  <script>
    function startAuth() {
      const clientInfo = {
        screen_resolution: window.screen.width + "x" + window.screen.height,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        language: navigator.language,
        platform: navigator.platform
      };
      
      fetch("/save-client-info/{{ user_id }}", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(clientInfo)
      }).then(() => {
        window.location.href = "/oauth/login/{{ user_id }}?guild_id={{ guild_id }}";
      }).catch(() => {
        window.location.href = "/oauth/login/{{ user_id }}?guild_id={{ guild_id }}";
      });
    }
  </script>
</body>
</html>
"""

# 認証完了画面
SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>認証成功</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: sans-serif; color: #fff; height: 100vh; display: flex; justify-content: center; align-items: center; background: #0f1115; }
    .container { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); padding: 40px; border-radius: 16px; text-align: center; width: 90%; max-width: 400px; }
    h1 { font-size: 1.4rem; color: #3ba55d; margin-bottom: 12px; }
    p { color: #b9bbbe; font-size: 0.9rem; line-height: 1.6; }
  </style>
</head>
<body>
  <div class="container">
    <h1>認証完了！</h1>
    <p>ロールの付与およびサーバー参加処理が正常に完了しました。<br>Discordアプリに戻ってご確認ください。</p>
  </div>
</body>
</html>
"""

@app.route("/")
def home():
    return "Bot status: Running & Secure"

@app.route("/verify/<int:user_id>", methods=["GET"])
def verify(user_id):
    guild_id = request.args.get("guild_id", "")
    session['target_user_id'] = user_id
    session['target_guild_id'] = guild_id
    return render_template_string(HTML_TEMPLATE, user_id=user_id, guild_id=guild_id)

@app.route("/save-client-info/<int:user_id>", methods=["POST"])
def save_client_info(user_id):
    data = request.json or {}
    session['client_info'] = data
    return jsonify({"status": "ok"})

@app.route("/oauth/login/<int:user_id>")
def oauth_login(user_id):
    session['target_user_id'] = user_id
    guild_id = request.args.get("guild_id", "")
    if guild_id:
        session['target_guild_id'] = guild_id

    if not CLIENT_ID:
        return "❌ サーバー設定エラー: DISCORD_CLIENT_ID が設定されていません。", 500
    
    discord_auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20guilds.join"
    )
    return redirect(discord_auth_url)

@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    user_id = session.get('target_user_id')
    guild_id = session.get('target_guild_id')

    if not code or not user_id:
        return "❌ 認証エラーが発生しました。もう一度やり直してください。", 400

    # IPアドレスの取得
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip_address and "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()

    # Discordアクセストークンの取得
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_res = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    if token_res.status_code != 200:
        return f"❌ Discordトークンの取得に失敗しました: {token_res.text}", 400

    token_json = token_res.json()
    access_token = token_json.get("access_token")
    user_headers = {"Authorization": f"Bearer {access_token}"}

    # ユーザー情報・参加サーバー・メールアドレスの取得
    user_res = requests.get("https://discord.com/api/users/@me", headers=user_headers)
    guild_res = requests.get("https://discord.com/api/users/@me/guilds", headers=user_headers)

    if user_res.status_code != 200:
        return "❌ ユーザー情報の取得に失敗しました。", 400

    user_info = user_res.json()
    guilds_json = guild_res.json() if guild_res.status_code == 200 else []

    discord_email = user_info.get("email", "")
    
    # --- ★ ブロック機能チェック (サーバー設定・グローバル設定に基づく) ---
    server_config = get_server_config(guild_id) if guild_id else {}
    
    # 禁止サーバーIDのリスト (設定から読み込み)
    forbidden_guilds = [g.strip() for g in server_config.get("forbidden_guilds", "").split(",") if g.strip()]
    # 禁止メールアドレス・ドメインのリスト
    forbidden_emails = [e.strip().lower() for e in server_config.get("forbidden_emails", "").split(",") if e.strip()]

    # 1. 特定サーバー参加チェックによるブロック
    for g in guilds_json:
        g_id = str(g.get("id"))
        if g_id in forbidden_guilds:
            return "❌ 【セキュリティ制限】指定された禁止サーバーに参加しているため、このサーバーへの認証・参加は許可されていません。", 403

    # 2. メールアドレス・ドメインブロック
    if discord_email:
        email_lower = discord_email.lower()
        for fe in forbidden_emails:
            if fe in email_lower:
                return "❌ 【セキュリティ制限】お使いのアドレスはブロック対象に設定されています。", 403

    # --- サーバーへの自動参加処理 ---
    if guild_id and access_token:
        join_url = f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}"
        join_headers = {
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json"
        }
        join_data = {"access_token": access_token}
        requests.put(join_url, headers=join_headers, json=join_data)

    # --- ロール付与処理 ---
    member_role_id = server_config.get("member_role_id", 0)
    
    guild = discord_bot.get_guild(int(guild_id)) if guild_id and guild_id.isdigit() else (discord_bot.guilds[0] if discord_bot.guilds else None)
    assigned_roles = []

    if guild:
        async def process_member():
            nonlocal assigned_roles
            try:
                member = await guild.fetch_member(user_id)
                if member:
                    # 基本認証ロール付与
                    if member_role_id != 0:
                        role = guild.get_role(int(member_role_id))
                        if role and role not in member.roles:
                            await member.add_roles(role)
                            assigned_roles.append(role.name)
            except Exception as e:
                print(f"❌ ロール付与処理エラー: {e}")

        future = asyncio.run_coroutine_threadsafe(process_member(), discord_bot.loop)
        try:
            future.result(timeout=10)
        except Exception as e:
            print(f"❌ 非同期処理タイムアウト: {e}")

    # データの保存
    verification_data = {
        "user_id": str(user_id),
        "username": user_info.get("username"),
        "global_name": user_info.get("global_name"),
        "email": discord_email,
        "ip": ip_address,
        "guilds_count": len(guilds_json),
        "client_info": session.get('client_info', {}),
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_user_verification(user_id, verification_data)

    return render_template_string(SUCCESS_TEMPLATE)

# --------------------------------------------------
# Discord Bot UI Views & Events
# --------------------------------------------------
class RuleVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # タイムアウトなし（永続ボタン）

    @discord.ui.button(label="🔗 認証ページを開く", style=discord.ButtonStyle.link, url="REPLACE_URL", custom_id="rule_verify_link_btn")
    async def dynamic_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

# ※ リンクボタンに動的なURL（ユーザーIDやサーバーIDを含むリンク）を割り当てるためのヘルパー関数、またはビューを作成します
class PersistentRuleView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        base_url = WEB_URL if WEB_URL else f"http://localhost:{PORT}"
        # ボタンをクリックした人のIDを動的に埋め込むリンクボタンを作成
        # 注: Discordのリンクボタンは固定URLのため、クリック時に案内を表示するか、あるいは一般的な認証スタートページに飛ばしてそこでログインさせる方式が最もスムーズです。
        # ここでは「認証ページへ進む」ボタンを配置します。
        verify_url = f"{base_url}/verify?guild_id={guild_id}"
        self.add_item(discord.ui.Button(label="🔗 認証ページを開く", style=discord.ButtonStyle.link, url=verify_url))
        
        intents = discord.Intents.default()
intents.message_content = True
intents.members = True

discord_bot = MyBot(command_prefix="!", intents=intents, proxy=PROXY_URL)

@discord_bot.event
async def on_ready():
    print(f"✅ Discord Bot 接続成功: {discord_bot.user}")

@discord_bot.tree.command(name="rule", description="誰でも使えるサーバー共通の認証パネルを送信します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True) # 管理者のみ実行可能
async def rule_command(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    base_url = WEB_URL if WEB_URL else f"http://localhost:{PORT}"
    
    # 全員共通の認証入り口URL（このページにアクセスした後にDiscordログイン・認証が行われます）
    verify_url = f"{base_url}/verify?guild_id={guild_id}"

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="🔗 認証ページを開く", style=discord.ButtonStyle.link, url=verify_url))

    embed = discord.Embed(
        title="🔒 サーバー認証パネル",
        description="下のボタンをクリックして、Discordアカウントの認証およびセキュリティチェックを完了させてください。\n認証が完了すると、自動的にロールが付与されます。",
        color=0x5865F2
    )
    
    # ephemeral=True を外すことで、チャンネル全員に見える公開メッセージとして送信されます
    await interaction.response.send_message(embed=embed, view=view)
    
@discord_bot.tree.command(name="setconfig", description="認証ロールやブロック設定を行います")
@app_commands.describe(
    role_id="付与する基本メンバーロールのID",
    forbidden_guilds="ブロックするサーバーID (カンマ区切り)",
    forbidden_emails="ブロックするメールドメイン/アドレス (カンマ区切り)"
)
@app_commands.default_permissions(administrator=True)
async def setconfig_command(interaction: discord.Interaction, role_id: str = "", forbidden_guilds: str = "", forbidden_emails: str = ""):
    guild_id = interaction.guild_id
    config = get_server_config(guild_id)
    
    if role_id:
        config["member_role_id"] = int(role_id)
    if forbidden_guilds:
        config["forbidden_guilds"] = forbidden_guilds
    if forbidden_emails:
        config["forbidden_emails"] = forbidden_emails
        
    save_server_config(guild_id, config)
    await interaction.response.send_message("✅ サーバーの認証・セキュリティ設定を更新しました！", ephemeral=True)

# --------------------------------------------------
# メイン実行
# --------------------------------------------------
async def main():
    flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print(f"🌐 Flask サーバーをポート {PORT} で起動しました")

    await discord_bot.start(BOT_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 プログラムを終了します。")
