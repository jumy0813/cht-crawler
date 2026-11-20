from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import os
import time
import urllib.parse
from bs4 import BeautifulSoup
import pandas as pd
import requests

# ==========================================
# 1. LINE Messaging API 與時間過濾設定區
# ==========================================
LINE_CONFIG = {
    "enabled": True,
    "channel_access_token": os.environ.get("LINE_TOKEN", ""),
    "user_id": os.environ.get("LINE_USER_ID", ""),
}

# 只抓取「幾個小時內」發布的新文章？（預設 24 小時內，可依需求改成 12 或 6）
MAX_ARTICLE_AGE_HOURS = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "cht_all_platforms.csv")

# ==========================================
# 2. 自訂關鍵字分類字典
# ==========================================
CATEGORY_KEYWORDS = {
    "網路斷線/客訴": [
        "斷線",
        "斷網",
        "沒訊號",
        "無訊號",
        "網速慢",
        "很慢",
        "變慢",
        "卡頓",
        "連不上",
        "故障",
        "當機",
        "基地台",
        "客訴",
        "爛",
        "收訊差",
        "ping高",
        "不穩",
        "瞬斷",
        "障礙",
        "維修",
        "賠償",
        "客服",
        "轉圈圈",
    ],
    "資費優惠": [
        "資費",
        "方案",
        "優惠",
        "吃到飽",
        "499",
        "599",
        "699",
        "999",
        "1399",
        "攜碼",
        "NP",
        "續約",
        "新辦",
        "違約金",
        "月租",
        "折扣",
        "退傭",
        "加碼",
        "限速",
        "流量",
        "熱點",
        "光世代",
        "雙11",
        "MOD",
        "Hamivideo",
    ],
    "企業新聞": [
        "財報",
        "營收",
        "股價",
        "配息",
        "股利",
        "法說會",
        "董事長",
        "總經理",
        "簡志誠",
        "ESG",
        "永續",
        "合作",
        "結盟",
        "投資",
        "AI",
        "衛星",
        "5G",
        "6G",
        "標案",
        "子公司",
        "中華電",
        "獲利",
        "EPS",
    ],
}


def get_tw_now():
    """取得台灣標準時間 (UTC+8)"""
    return datetime.now(timezone(timedelta(hours=8)))


def is_recent_article(pub_date_str, max_hours=MAX_ARTICLE_AGE_HOURS):
    """檢查文章發布時間是否在指定小時數內，過濾掉舊文章"""
    if not pub_date_str:
        return True  # 若平台未提供明確時間字串則先保留

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(hours=max_hours)

    try:
        # 1. 解析 Google News RSS 標準時間 (例如: Fri, 20 Nov 2026 03:30:00 GMT)
        dt = parsedate_to_datetime(pub_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff_utc
    except Exception:
        pass

    try:
        # 2. 解析 PTT 內文時間格式 (例如: Fri Nov 20 11:30:00 2026)
        dt_naive = datetime.strptime(pub_date_str.strip(), "%a %b %d %H:%M:%S %Y")
        dt_tw = dt_naive.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt_tw.astimezone(timezone.utc) >= cutoff_utc
    except Exception:
        pass

    return True


def send_line_alert(alert_df, keyword):
    """透過 LINE Messaging API 發送緊急客訴/斷線通知"""
    if not LINE_CONFIG["enabled"] or alert_df.empty:
        return

    if not LINE_CONFIG["channel_access_token"] or not LINE_CONFIG["user_id"]:
        print(
            "\n[LINE 略過] 尚未偵測到 LINE_TOKEN 或 LINE_USER_ID 環境變數，請確認 GitHub Secrets 設定。"
        )
        return

    count = len(alert_df)
    now_str = get_tw_now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "🚨 【雲端輿情緊急告警】",
        f"偵測到 {count} 篇「{keyword} - 網路斷線/客訴」最新貼文！",
        f"告警時間：{now_str} (台灣時間)",
        "━━━━━━━━━━━━━━",
    ]

    for idx, (_, row) in enumerate(alert_df.head(10).iterrows(), start=1):
        lines.append(f"\n{idx}. [{row['平台分類']}] {row['文章標題']}")
        lines.append(f"🕒 發布：{row['發布時間']}")
        lines.append(f"🔗 {row['文章連結']}")

    if count > 10:
        lines.append(f"\n...還有 {count - 10} 篇，請至 GitHub 下載 CSV 總表。")

    message_text = "\n".join(lines)

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CONFIG['channel_access_token']}",
    }
    payload = {
        "to": LINE_CONFIG["user_id"],
        "messages": [{"type": "text", "text": message_text}],
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            print(
                f"\n[LINE 通知] 已成功發送 LINE 告警訊息（共 {count} 筆最新客訴/斷線文章）！"
            )
        else:
            print(
                f"\n[LINE 失敗] 狀態碼：{res.status_code}，錯誤訊息：{res.text}"
            )
    except Exception as e:
        print(f"\n[LINE 失敗] 連線發生錯誤：{e}")


def classify_article(title, content=""):
    """根據標題與內文關鍵字，自動標記文章主題分類"""
    clean_content = (
        "" if content == "（請點擊連結查看完整貼文）" else str(content)
    )
    text_to_check = f"{title} {clean_content}".lower()

    matched_categories = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw.lower() in text_to_check for kw in keywords):
            matched_categories.append(category)

    if matched_categories:
        return " / ".join(matched_categories)
    return "一般討論/其他"


def fetch_google_rss(query, platform_label, max_results=20):
    """透過 Google 索引抓取最近 24 小時內的新聞與各社群平台公開文章"""
    # 加上 when:1d 強制 Google 只回傳最近 1 天內的索引結果
    time_bounded_query = f"{query} when:1d"
    encoded_q = urllib.parse.quote(time_bounded_query)
    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"

    results = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.content, "xml")
        items = soup.find_all("item")

        for item in items[:max_results]:
            title = item.title.text.strip() if item.title else ""
            link = item.link.text.strip() if item.link else ""
            pub_date = item.pubDate.text.strip() if item.pubDate else ""
            source = item.source.text.strip() if item.source else platform_label

            # 嚴格檢查時間，超過 MAX_ARTICLE_AGE_HOURS 的舊文直接跳過
            if not is_recent_article(pub_date):
                continue

            summary = "（請點擊連結查看完整貼文）"
            category = classify_article(title, summary)

            results.append({
                "抓取時間": get_tw_now().strftime("%Y-%m-%d %H:%M:%S"),
                "主題分類": category,
                "平台分類": platform_label,
                "來源網站": source,
                "文章標題": title,
                "發布時間": pub_date,
                "文章連結": link,
                "內文摘要": summary,
            })
    except Exception as e:
        print(f"[{platform_label}] 抓取發生錯誤: {e}")

    return results


def fetch_ptt_direct(keyword="中華電信", pages=2):
    """直接深入 PTT MobileComm 看板抓取最新完整標題與內文"""
    base_url = "https://www.ptt.cc"
    encoded_kw = urllib.parse.quote(keyword)
    current_url = f"{base_url}/bbs/MobileComm/search?q={encoded_kw}"
    cookies = {"over18": "1"}

    results = []
    for _ in range(pages):
        try:
            res = requests.get(
                current_url, headers=HEADERS, cookies=cookies, timeout=10
            )
            if res.status_code != 200:
                print(f"[PTT] 連線受限或狀態異常：{res.status_code}")
                break

            soup = BeautifulSoup(res.text, "html.parser")
            entries = soup.find_all("div", class_="r-ent")
            stop_paging = False

            for entry in entries:
                a_tag = (
                    entry.find("div", class_="title").find("a")
                    if entry.find("div", class_="title")
                    else None
                )
                if not a_tag:
                    continue

                title = a_tag.text.strip()
                link = base_url + a_tag["href"]

                art_res = requests.get(
                    link, headers=HEADERS, cookies=cookies, timeout=10
                )
                art_soup = BeautifulSoup(art_res.text, "html.parser")
                main_content = art_soup.find("div", id="main-content")

                pub_time, clean_content = "", ""
                if main_content:
                    metas = main_content.find_all(
                        "span", class_="article-meta-value"
                    )
                    pub_time = metas[3].text.strip() if len(metas) >= 4 else ""

                    # 若該篇 PTT 文章已超過設定時數，直接略過且無需再往更舊頁面翻頁
                    if pub_time and not is_recent_article(pub_time):
                        stop_paging = True
                        continue

                    for tag in main_content.find_all(
                        "div",
                        class_=[
                            "article-metaline",
                            "article-metaline-right",
                            "push",
                        ],
                    ):
                        tag.decompose()
                    raw_text = main_content.text.strip()
                    clean_content = (
                        raw_text.split("※ 發信站:")[0].strip()
                        if "※ 發信站:" in raw_text
                        else raw_text
                    )

                category = classify_article(title, clean_content)

                results.append({
                    "抓取時間": get_tw_now().strftime("%Y-%m-%d %H:%M:%S"),
                    "主題分類": category,
                    "平台分類": "PTT (直抓完整版)",
                    "來源網站": "PTT MobileComm",
                    "文章標題": title,
                    "發布時間": pub_time,
                    "文章連結": link,
                    "內文摘要": clean_content,
                })
                time.sleep(0.5)

            if stop_paging:
                break

            paging_div = soup.find("div", class_="btn-group-paging")
            prev_btn = (
                paging_div.find_all("a", class_="btn")[1]
                if paging_div
                else None
            )
            if prev_btn and "href" in prev_btn.attrs:
                current_url = base_url + prev_btn["href"]
            else:
                break
        except Exception as e:
            print(f"[PTT] 抓取發生錯誤: {e}")
            break

    return results


def run_all_crawlers(keyword="中華電信"):
    new_data = []
    print(
        f"=== 開始執行雲端全網輿情爬蟲（關鍵字：{keyword}｜僅收錄最近 {MAX_ARTICLE_AGE_HOURS} 小時發文）===\n"
    )

    print("1. 正在收集【各大新聞媒體】...")
    new_data.extend(fetch_google_rss(f"{keyword}", "新聞媒體", max_results=30))

    print("2. 正在深入爬取【PTT 論壇】完整內文...")
    new_data.extend(fetch_ptt_direct(keyword=keyword, pages=2))

    social_targets = [
        ("Dcard", f"{keyword} site:dcard.tw"),
        ("Mobile01", f"{keyword} site:mobile01.com"),
        ("Threads", f"{keyword} site:threads.net"),
        ("Facebook", f"{keyword} site:facebook.com"),
    ]

    for idx, (platform_name, search_query) in enumerate(social_targets, start=3):
        print(f"{idx}. 正在收集【{platform_name}】公開貼文...")
        new_data.extend(
            fetch_google_rss(search_query, platform_name, max_results=20)
        )
        time.sleep(1)

    column_order = [
        "抓取時間",
        "主題分類",
        "平台分類",
        "來源網站",
        "文章標題",
        "發布時間",
        "文章連結",
        "內文摘要",
    ]

    df_new = pd.DataFrame(new_data, columns=column_order)
    if not df_new.empty:
        df_new.drop_duplicates(subset=["文章連結"], inplace=True)
        df_new.drop_duplicates(subset=["文章標題"], inplace=True)

    file_existed = os.path.exists(OUTPUT_FILE)
    if file_existed:
        df_old = pd.read_csv(OUTPUT_FILE, encoding="utf-8-sig")

        if "主題分類" not in df_old.columns:
            df_old["主題分類"] = df_old.apply(
                lambda row: classify_article(
                    str(row.get("文章標題", "")), str(row.get("內文摘要", ""))
                ),
                axis=1,
            )

        old_links = set(df_old["文章連結"].dropna().astype(str))
        old_titles = set(df_old["文章標題"].dropna().astype(str))

        mask_new = (~df_new["文章連結"].astype(str).isin(old_links)) & (
            ~df_new["文章標題"].astype(str).isin(old_titles)
        )
        df_filtered = df_new[mask_new]

        print(
            f"\n比對完成：本次共掃描 {len(df_new)} 筆近期文章，過濾掉"
            f" {len(df_new) - len(df_filtered)} 筆已收錄文章。"
        )
        print(f"實際新增：{len(df_filtered)} 筆最新文章！")

        df_final = pd.concat([df_filtered, df_old], ignore_index=True)
    else:
        df_filtered = df_new
        df_final = df_new
        print(f"\n首次執行建立檔案！共寫入 {len(df_final)} 筆最新文章。")

    df_final = df_final[column_order]
    df_final.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"檔案已更新：{OUTPUT_FILE}（目前累積總筆數：{len(df_final)} 筆）")

    # 檢查「本次新增文章」是否包含「網路斷線/客訴」
    if not df_filtered.empty:
        alert_articles = df_filtered[
            df_filtered["主題分類"].str.contains("網路斷線/客訴", na=False)
        ]
        if not alert_articles.empty:
            send_line_alert(alert_articles, keyword)
        else:
            print(
                "\n[告警檢查] 本次新增文章中未發現「網路斷線/客訴」相關內容，無需通知。"
            )


if __name__ == "__main__":
    run_all_crawlers(keyword="中華電信")
