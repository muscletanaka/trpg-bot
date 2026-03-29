"""
TRPG シナリオ検索Bot - コアロジックサンプル
----------------------------------------------
必要なパッケージ:
    pip install discord.py openai

環境変数（.envなどで管理）:
    DISCORD_TOKEN      : Discord BotのToken
    OPENAI_API_KEY     : OpenAI APIキー
    SOURCE_CHANNEL_ID  : タイトルリストを投稿するチャンネルID
    OUTPUT_CHANNEL_ID  : 検索結果を投稿するチャンネルID
"""

import os
import asyncio
import discord
from openai import AsyncOpenAI

# ── クライアント初期化 ──────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # メッセージ内容の読み取りに必要

discord_client = discord.Client(intents=intents)
openai_client  = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

SOURCE_CHANNEL_ID = int(os.environ["SOURCE_CHANNEL_ID"])
OUTPUT_CHANNEL_ID = int(os.environ["OUTPUT_CHANNEL_ID"])


# ── OpenAI Web検索でシナリオ情報を取得 ────────────────────────────
async def search_scenario_info(title: str) -> dict:
    """
    GPT-4o + web_search_preview を使い、
    シナリオ提供元URL とトレーラー画像URL を返す。

    返却例:
        {
            "title": "クトゥルフの呼び声",
            "url":   "https://booth.pm/ja/items/xxxxxx",
            "image": "https://example.com/trailer.jpg",
            "description": "シナリオの短い説明文"
        }
    """
    prompt = f"""
以下のTRPGシナリオタイトルについて、Web検索で情報を調べてください。

タイトル: {title}

以下の情報をJSON形式のみで返答してください（余分なテキスト不要）:
{{
  "title": "タイトル（正式名称）",
  "url": "シナリオ配布・販売ページのURL（BOOTH / DLsite / itch.io / 公式サイト など）",
  "image": "トレーラー画像またはシナリオ表紙画像のURL（見つからない場合はnull）",
  "description": "シナリオの短い説明（1〜2文）"
}}
"""

    response = await openai_client.responses.create(
        model="gpt-4o",
        tools=[{"type": "web_search_preview"}],  # Web検索ツールを有効化
        input=prompt,
    )

    # レスポンスからテキスト部分を抽出
    result_text = ""
    for item in response.output:
        if hasattr(item, "content"):
            for block in item.content:
                if hasattr(block, "text"):
                    result_text += block.text

    # JSONパース
    import json, re
    match = re.search(r"\{.*\}", result_text, re.DOTALL)
    if match:
        return json.loads(match.group())

    # パース失敗時のフォールバック
    return {
        "title": title,
        "url": None,
        "image": None,
        "description": "情報を取得できませんでした。",
    }


# ── Discord Embed を生成して送信 ──────────────────────────────────
async def post_result(channel: discord.TextChannel, info: dict) -> None:
    """検索結果を整形してDiscordのEmbedで投稿する。"""

    embed = discord.Embed(
        title=info.get("title", "不明"),
        url=info.get("url") or discord.Embed.Empty,
        description=info.get("description", ""),
        color=0x7B68EE,  # ミディアムスレートブルー
    )

    if info.get("image"):
        embed.set_image(url=info["image"])

    if info.get("url"):
        embed.add_field(name="🔗 シナリオ提供元", value=info["url"], inline=False)
    else:
        embed.add_field(name="⚠️ 提供元URL", value="見つかりませんでした", inline=False)

    embed.set_footer(text="Powered by OpenAI web_search_preview")

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
            info = await search_scenario_info(title)
            await post_result(output_channel, info)
            # API負荷軽減のため少し待機
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"[ERROR] '{title}' の処理中にエラー: {e}")
            await output_channel.send(f"⚠️ `{title}` の情報取得に失敗しました: {e}")

    # 処理完了をリアクションで通知
    await message.add_reaction("✅")


# ── 起動 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    discord_client.run(os.environ["DISCORD_TOKEN"])
