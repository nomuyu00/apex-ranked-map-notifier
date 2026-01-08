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


ALS_ALL_MODES_URL = "https://apexlegendsstatus.com/current-map"  # ← これを定数エリアに追加してOK

def _normalize_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    lines: list[str] = []
    for ln in text.splitlines():
        # NBSPなど「見た目同じ空白」を普通の空白へ
        ln = ln.replace("\xa0", " ").replace("\u202f", " ").strip()
        if ln:
            lines.append(ln)
    return lines


def fetch_ranked_rotation():
    """
    1) ranked専用ページから抽出（一覧の先頭＝現在、2番目＝次）
    2) 取れない場合は /current-map の BR Ranked セクションから抽出（保険）
    """
    # --- (1) ranked専用ページ ---
    r = requests.get(
        ALS_RANKED_URL,
        timeout=25,
        headers={"User-Agent": USER_AGENT},
    )
    r.raise_for_status()

    lines = _normalize_lines(r.text)

    entries = []
    for i, ln in enumerate(lines):
        if i == 0:
            continue

        # ★ここが修正点： "From " ではなく "from" で始まるか（スペース不要）
        if ln.lower().startswith("from"):
            name = lines[i - 1].strip()
            name = re.sub(r"^#+\s*", "", name).strip()  # 念のため
            entries.append({"name": name, "detail": ln})

    if entries:
        current = entries[0]
        next_map = entries[1] if len(entries) >= 2 else None
        return current, next_map

    # --- (2) 保険：/current-map の BR Ranked から拾う ---
    r = requests.get(
        ALS_ALL_MODES_URL,
        timeout=25,
        headers={"User-Agent": USER_AGENT},
    )
    r.raise_for_status()
    lines = _normalize_lines(r.text)

    idx = next((i for i, l in enumerate(lines) if l.lower() == "br ranked"), None)
    if idx is None or idx + 1 >= len(lines):
        sample = "\n".join(lines[:80])
        raise RuntimeError("BR Ranked セクションが見つかりませんでした。\n---DEBUG---\n" + sample)

    current_name = lines[idx + 1]
    current_from = next((l for l in lines[idx + 2 : idx + 20] if l.lower().startswith("from")), "")

    next_line = next((l for l in lines[idx + 2 : idx + 30] if "next map is" in l.lower()), "")
    m = re.search(r"next map is\s+(.*?),\s*from\s+(.*)$", next_line, re.I)

    if not (current_from and m):
        sample = "\n".join(lines[idx : idx + 50])
        raise RuntimeError("BR Ranked の抽出に失敗しました。\n---DEBUG---\n" + sample)

    next_name = m.group(1).strip()
    next_detail = "From " + m.group(2).strip()

    return {"name": current_name, "detail": current_from}, {"name": next_name, "detail": next_detail}



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
    cands = _slug_candidates(map_name)
    if not cands:
        return None
    # 先頭候補をそのまま使う（Discordが取得して表示する）
    return f"{ALS_ASSET_BASE}{cands[0]}.png"



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
