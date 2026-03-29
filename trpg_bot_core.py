"""
TRPG シナリオ検索Bot - コアロジック
----------------------------------------------
必要なパッケージ:
    pip install discord.py anthropic

環境変数（Railwayの Variables に設定）:
    DISCORD_TOKEN      : Discord BotのToken
    ANTHROPIC_API_KEY  : AnthropicのAPIキー
    SOURCE_CHANNEL_ID  : タイトルリストを投稿するチャンネルID
    OUTPUT_CHANNEL_ID  : 検索結果を投稿するチャンネルID
"""

import os
import json
import re
import asyncio
import discord
import anthropic

# ── クライアント初期化 ──────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # メッセージ内容の読み取りに必要

discord_client   = discord.Client(intents=intents)
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SOURCE_CHANNEL_ID = int(os.environ["SOURCE_CHANNEL_ID"])
OUTPUT_CHANNEL_ID = int(os.environ["OUTPUT_CHANNEL_ID"])


# ── Claude Web検索でシナリオ情報を取得 ────────────────────────────
def search_scenario_info(title: str) -> dict:
    """
    Claude Sonnet 4.6 + web_search ツールを使い、
    シナリオ詳細情報を返す。
    """
    prompt = f"""
以下のTRPGシナリオタイトルについて、Web検索で情報を調べてください。

タイトル: {title}

以下の情報をJSON形式のみで返答してください（前置き・説明文・コードブロック不要）。
情報が見つからない項目は "不明" としてください。

{{
  "title": "タイトル（正式名称）",
  "url": "シナリオ配布・販売ページのURL（BOOTH / DLsite / itch.io / 公式サイト など。見つからない場合はnull）",
  "image": "トレーラー画像またはシナリオ表紙画像のURL（見つからない場合はnull）",
  "author": "作者名（見つからない場合は「不明」）",
  "players": "プレイ人数（例：「1人」「2〜4人」。見つからない場合は「不明」）",
  "playtime": "プレイ時間（例：「2〜3時間」。見つからない場合は「不明」）",
  "difficulty": "難易度（「高」「中」「低」のいずれか。判断できない場合は「不明」）",
  "edition": "版（例：「CoC6版」「CoC7版」「SW2.5」。見つからない場合は「不明」）",
  "summary": "ネタバレなしのシナリオ概要（舞台・雰囲気を2〜3文で。日本語で）"
}}
"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        tools=[
            {
                "type": "web_search_20250305",  # Claude Web検索ツール
                "name": "web_search",
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    # レスポンスからテキストブロックを抽出
    result_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            result_text += block.text

    # JSONパース（コードブロックが含まれる場合も考慮）
    match = re.search(r"\{.*\}", result_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # パース失敗時のフォールバック
    return {
        "title": title,
        "url": None,
        "image": None,
        "author": "不明",
        "players": "不明",
        "playtime": "不明",
        "difficulty": "不明",
        "edition": "不明",
        "summary": "情報を取得できませんでした。",
    }


# ── Discord Embed を生成して送信 ──────────────────────────────────
async def post_result(channel: discord.TextChannel, info: dict) -> None:
    """検索結果を整形してDiscordのEmbedで投稿する。"""

    embed = discord.Embed(
        title=info.get("title", "不明"),
        url=info.get("url") or None,
        color=0x7B68EE,  # ミディアムスレートブルー
    )

    # トレーラー・表紙画像
    if info.get("image"):
        embed.set_image(url=info["image"])

    # 各項目をフィールドで表示
    embed.add_field(name="作者",       value=info.get("author",     "不明"), inline=True)
    embed.add_field(name="プレイ人数", value=info.get("players",    "不明"), inline=True)
    embed.add_field(name="プレイ時間", value=info.get("playtime",   "不明"), inline=True)
    embed.add_field(name="難易度",     value=info.get("difficulty", "不明"), inline=True)
    embed.add_field(name="版",         value=info.get("edition",    "不明"), inline=True)

    # 提供元URL
    if info.get("url"):
        embed.add_field(name="🔗 シナリオ提供元", value=info["url"], inline=False)
    else:
        embed.add_field(name="🔗 シナリオ提供元", value="不明", inline=False)

    # シナリオ概要
    embed.add_field(name="シナリオ概要", value=info.get("summary", "不明"), inline=False)

    embed.set_footer(text="Powered by Claude Sonnet 4.6 + Web Search")

    await channel.send(embed=embed)


# ── メインイベント: タイトルリスト投稿を検知 ─────────────────────
@discord_client.event
async def on_ready():
    print(f"Bot起動完了: {discord_client.user}")


@discord_client.event
async def on_message(message: discord.Message):
    # Bot自身のメッセージは無視
    if message.author.bot:
        return

    # 対象チャンネル以外は無視
    if message.channel.id != SOURCE_CHANNEL_ID:
        return

    # 改行区切りでタイトルを分割（空行・空白行を除外）
    titles = [t.strip() for t in message.content.splitlines() if t.strip()]
    if not titles:
        return

    output_channel = discord_client.get_channel(OUTPUT_CHANNEL_ID)
    if output_channel is None:
        print(f"出力チャンネル(ID:{OUTPUT_CHANNEL_ID})が見つかりません")
        return

    # 処理開始をリアクションで通知
    await message.add_reaction("🔍")

    for title in titles:
        try:
            # anthropicは同期クライアントのため、スレッドで実行してブロッキングを回避
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, search_scenario_info, title)
            await post_result(output_channel, info)
            # API負荷軽減のため待機
            await asyncio.sleep(60.0)
        except Exception as e:
            print(f"[ERROR] '{title}' の処理中にエラー: {e}")
            await output_channel.send(f"⚠️ {title}：検索失敗")

    # 処理完了をリアクションで通知
    await message.add_reaction("✅")


# ── 起動 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    discord_client.run(os.environ["DISCORD_TOKEN"])