import os
import re
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

ALS_RANKED_URL = "https://apexlegendsstatus.com/current-map/battle_royale/ranked"
ALS_ASSET_BASE = "https://apexlegendsstatus.com/assets/maps/"

# 英語マップ名 → 日本語表記（好みで増やしてOK）
JA_MAP = {
    "Olympus": "オリンパス",
    "Storm Point": "ストームポイント",
    "World's Edge": "ワールズエッジ",
    "Worlds Edge": "ワールズエッジ",
    "Broken Moon": "ブロークンムーン",
    "Kings Canyon": "キングスキャニオン",
    "E-District": "E-ディストリクト",
}

USER_AGENT = "Mozilla/5.0 (compatible; ApexRankMapDiscordNotifier/1.0; +https://github.com/)"


def fetch_ranked_rotation():
    """
    ApexLegendsStatusのランクマップローテページを取得し、
    先頭(現在)と次(次のマップ)を抽出します。
    """
    r = requests.get(
        ALS_RANKED_URL,
        timeout=25,
        headers={"User-Agent": USER_AGENT},
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n")

    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    entries = []
    for i, ln in enumerate(lines):
        if ln.startswith("From "):
            if i == 0:
                continue
            name = lines[i - 1].strip()
            name = re.sub(r"^#+\s*", "", name).strip()  # 念のため "### " を除去

            # 変な見出しを除外（保険）
            if len(name) > 60:
                continue
            if name.lower().startswith("from"):
                continue

            entries.append({"name": name, "detail": ln})

    if not entries:
        raise RuntimeError("マップ情報を抽出できませんでした（ページ構造が変わった可能性）")

    current = entries[0]
    next_map = entries[1] if len(entries) >= 2 else None
    return current, next_map


def _slug_candidates(map_name: str):
    base = map_name.strip()
    base = base.replace("’", "").replace("'", "")  # アポストロフィ除去
    base = re.sub(r"\s+", " ", base)

    c1 = base.replace(" ", "_")  # スペース→_
    c2 = c1.replace("-", "_")    # -→_
    c3 = c1.replace("-", "")     # -除去
    c4 = c2.replace("_", "")     # _除去（EDistrictみたいな形）

    candidates = [c1, c2, c3, c4]

    cleaned = []
    seen = set()
    for c in candidates:
        c = re.sub(r"[^A-Za-z0-9_\-]", "", c)
        if c and c not in seen:
            seen.add(c)
            cleaned.append(c)

    return cleaned


def find_map_image_url(map_name: str):
    """
    ALSの /assets/maps/ にある画像URLを推測して実在確認し、見つかったURLを返す。
    見つからなければ None。
    """
    for slug in _slug_candidates(map_name):
        url = f"{ALS_ASSET_BASE}{slug}.png"
        try:
            rr = requests.get(url, timeout=15, stream=True, headers={"User-Agent": USER_AGENT})
            if rr.status_code == 200 and rr.headers.get("Content-Type", "").startswith("image"):
                rr.close()
                return url
            rr.close()
        except Exception:
            continue
    return None


def post_to_discord(webhook_url: str, current: dict, next_map: dict | None, image_url: str | None):
    now_jst = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))

    cur_en = current["name"]
    cur_ja = JA_MAP.get(cur_en, cur_en)

    desc = f"**{cur_ja}**（{cur_en}）\n{current['detail']}"
    embed = {
        "title": "🗺️ Apex ランク（BR） 現在のマップ",
        "url": ALS_RANKED_URL,
        "description": desc,
        "timestamp": now_jst.isoformat(),
        "footer": {"text": "Data: Apex Legends Status"},
    }

    if next_map:
        nxt_en = next_map["name"]
        nxt_ja = JA_MAP.get(nxt_en, nxt_en)
        embed["fields"] = [
            {
                "name": "次のマップ",
                "value": f"**{nxt_ja}**（{nxt_en}）\n{next_map['detail']}",
                "inline": False,
            }
        ]

    if image_url:
        embed["image"] = {"url": image_url}

    payload = {
        "username": "Apex Ranked Map",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

    res = requests.post(webhook_url, json=payload, timeout=25)
    res.raise_for_status()


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("環境変数 DISCORD_WEBHOOK_URL が設定されていません（GitHub Secrets を確認）")

    current, next_map = fetch_ranked_rotation()
    image_url = find_map_image_url(current["name"])

    post_to_discord(webhook_url, current, next_map, image_url)


if __name__ == "__main__":
    main()
