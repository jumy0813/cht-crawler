from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import os
import sys
import time
import urllib.parse
from bs4 import BeautifulSoup
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd
import requests

# ==========================================
# 1. LINE Messaging API 與時間過濾設定區
# ==========================================
LINE_CONFIG = {
    "enabled": True,
    "channel_access_token": os.environ.get("fVGM5SgXQ/CZ4fmgr9hbY5swo2q0Z56bPGDn6zj9sAG8jh6xv53drabuVerZ7Saq9flPj/oCqLKrlPFt/UQCapgh5Gx9hc3ZCBDKScp1Z6Y9H4o2D79+9IuhMO3a8eMnTS6SCcDmQ9XmuqaJv5YwsAdB04t89/1O/w1cDnyilFU=", ""),
    "user_id": os.environ.get("U6bcc27b5c9c059a25e296cc3285c84ff", ""),
}

# 只抓取「幾個小時內」發布的新文章？
MAX_ARTICLE_AGE_HOURS = 03

# 是否在每次發現新文章時都附帶發送儀表板圖片？（True = 只要有新文章就發戰情圖；False = 只有發生客訴/斷線才發）
ALWAYS_SEND_DASHBOARD_ON_NEW = True

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "cht_all_platforms.csv")
DASHBOARD_IMG = os.path.join(BASE_DIR, "dashboard.png")
STATE_FILE = os.path.join(BASE_DIR, "last_run_state.json")

# ==========================================
# 2. 自訂關鍵字分類字典
# ==========================================
CATEGORY_KEYWORDS = {
    "網路斷線/客訴": [
        "斷線", "斷網", "沒訊號", "無訊號", "網速慢", "很慢", "變慢", "卡頓",
        "連不上", "故障", "當機", "基地台", "客訴", "爛", "收訊差", "ping高",
        "不穩", "瞬斷", "障礙", "維修", "賠償", "客服", "轉圈圈",
    ],
    "資費優惠": [
        "資費", "方案", "優惠", "吃到飽", "499", "599", "699", "999", "1399",
        "攜碼", "NP", "續約", "新辦", "違約金", "月租", "折扣", "退傭", "加碼",
        "限速", "流量", "熱點", "光世代", "雙11", "MOD", "Hamivideo",
    ],
    "企業新聞": [
        "財報", "營收", "股價", "配息", "股利", "法說會", "董事長", "總經理",
        "簡志誠", "ESG", "永續", "合作", "結盟", "投資", "AI", "衛星", "5G",
        "6G", "標案", "子公司", "中華電", "獲利", "EPS",
    ],
}


def get_tw_now():
    return datetime.now(timezone(timedelta(hours=8)))


def configure_chinese_font():
    """自動尋找並設定 Linux/Windows 系統的中文字型"""
    font_candidates = [
        "Noto Sans CJK TC",
        "Noto Sans CJK JP",
        "Microsoft JhengHei",
        "PingFang TC",
        "SimHei",
    ]
    available_fonts = {f.name for f in fm.fontManager.ttflist}
    for font_name in font_candidates:
        if font_name in available_fonts:
            plt.rcParams["font.sans-serif"] = [font_name]
            break
    plt.rcParams["axes.unicode_minus"] = False


def generate_dashboard(df_all, new_count, alert_count, keyword="中華電信"):
    """將 CSV 資料繪製成高解析度戰情室儀表板圖片 (dashboard.png)"""
    configure_chinese_font()

    fig = plt.figure(figsize=(12, 6.8), dpi=160, facecolor="#F4F6F9")
    now_str = get_tw_now().strftime("%Y-%m-%d %H:%M")

    # 頂部主標題
    fig.suptitle(
        f"📊 【{keyword}】全網輿情監控戰情室",
        fontsize=20,
        fontweight="bold",
        color="#1E293B",
        y=0.95,
    )
    fig.text(
        0.5,
        0.895,
        f"資料更新時間：{now_str} (台灣時間) ｜ 累積總收錄：{len(df_all)} 篇",
        ha="center",
        fontsize=11,
        color="#64748B",
    )

    # 頂部 3 張 KPI 數據卡片
    kpi_specs = [
        ("累積監控總聲量", f"{len(df_all)} 篇", "#2563EB", 0.18),
        ("本次新增貼文", f"+{new_count} 篇", "#059669", 0.50),
        (
            "網路斷線/客訴 (本次)",
            f"{alert_count} 篇",
            "#DC2626" if alert_count > 0 else "#475569",
            0.82,
        ),
    ]
    for label, value, color, x_pos in kpi_specs:
        fig.text(
            x_pos, 0.81, label, ha="center", fontsize=11, color="#475569", fontweight="bold"
        )
        fig.text(
            x_pos, 0.75, value, ha="center", fontsize=22, color=color, fontweight="bold"
        )

    if df_all.empty:
        fig.text(0.5, 0.42, "目前尚無足夠資料可供繪製圖表", ha="center", fontsize=14, color="#94A3B8")
        plt.savefig(DASHBOARD_IMG, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return

    # 左下圖表：各平台聲量分布 (橫向長條圖)
    ax1 = fig.add_axes([0.08, 0.12, 0.40, 0.54], facecolor="white")
    platform_counts = df_all["平台分類"].value_counts().sort_values(ascending=True)
    bars = ax1.barh(platform_counts.index, platform_counts.values, color="#3B82F6", height=0.55)
    ax1.set_title("各平台輿情聲量分布", fontsize=13, fontweight="bold", color="#1E293B", pad=12)
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    ax1.spines["left"].set_color("#CBD5E1")
    ax1.spines["bottom"].set_color("#CBD5E1")
    ax1.grid(axis="x", linestyle="--", alpha=0.4)
    for bar in bars:
        width = bar.get_width()
        ax1.text(
            width + max(platform_counts.values) * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"{int(width)}",
            va="center",
            fontsize=10,
            fontweight="bold",
            color="#334155",
        )
    ax1.set_xlim(0, max(platform_counts.values) * 1.18)

    # 右下圖表：輿情主題分類佔比 (圓餅圖)
    ax2 = fig.add_axes([0.55, 0.12, 0.38, 0.54], facecolor="white")
    # 拆解複合分類 (例如 "網路斷線/客訴 / 資費優惠") 以便精準統計
    exploded_cats = df_all["主題分類"].dropna().str.split(" / ").explode()
    cat_counts = exploded_cats.value_counts()

    color_map = {
        "網路斷線/客訴": "#EF4444",
        "資費優惠": "#10B981",
        "企業新聞": "#3B82F6",
        "一般討論/其他": "#94A3B8",
    }
    pie_colors = [color_map.get(c, "#F59E0B") for c in cat_counts.index]

    wedges, texts, autotexts = ax2.pie(
        cat_counts.values,
        labels=cat_counts.index,
        autopct="%1.1f%%",
        startangle=140,
        colors=pie_colors,
        pctdistance=0.75,
        textprops={"fontsize": 10, "color": "#1E293B"},
    )
    for autotext in autotexts:
        autotext.set_color("white")
        autotext.set_fontweight("bold")
    ax2.set_title("輿情主題關鍵字佔比", fontsize=13, fontweight="bold", color="#1E293B", pad=12)

    plt.savefig(DASHBOARD_IMG, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"[儀表板] 已成功生成戰情室圖表：{DASHBOARD_IMG}")


def is_recent_article(pub_date_str, max_hours=MAX_ARTICLE_AGE_HOURS):
    if not pub_date_str:
        return True
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(hours=max_hours)

    try:
        dt = parsedate_to_datetime(pub_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff_utc
    except Exception:
        pass

    try:
        dt_naive = datetime.strptime(pub_date_str.strip(), "%a %b %d %H:%M:%S %Y")
        dt_tw = dt_naive.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt_tw.astimezone(timezone.utc) >= cutoff_utc
    except Exception:
        pass

    return True


def classify_article(title, content=""):
    clean_content = "" if content == "（請點擊連結查看完整貼文）" else str(content)
    text_to_check = f"{title} {clean_content}".lower()

    matched_categories = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw.lower() in text_to_check for kw in keywords):
            matched_categories.append(category)

    return " / ".join(matched_categories) if matched_categories else "一般討論/其他"


def fetch_google_rss(query, platform_label, max_results=20):
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
    base_url = "https://www.ptt.cc"
    encoded_kw = urllib.parse.quote(keyword)
    current_url = f"{base_url}/bbs/MobileComm/search?q={encoded_kw}"
    cookies = {"over18": "1"}

    results = []
    for _ in range(pages):
        try:
            res = requests.get(current_url, headers=HEADERS, cookies=cookies, timeout=10)
            if res.status_code != 200:
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

                art_res = requests.get(link, headers=HEADERS, cookies=cookies, timeout=10)
                art_soup = BeautifulSoup(art_res.text, "html.parser")
                main_content = art_soup.find("div", id="main-content")

                pub_time, clean_content = "", ""
                if main_content:
                    metas = main_content.find_all("span", class_="article-meta-value")
                    pub_time = metas[3].text.strip() if len(metas) >= 4 else ""

                    if pub_time and not is_recent_article(pub_time):
                        stop_paging = True
                        continue

                    for tag in main_content.find_all(
                        "div", class_=["article-metaline", "article-metaline-right", "push"]
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
            prev_btn = paging_div.find_all("a", class_="btn")[1] if paging_div else None
            if prev_btn and "href" in prev_btn.attrs:
                current_url = base_url + prev_btn["href"]
            else:
                break
        except Exception as e:
            print(f"[PTT] 抓取發生錯誤: {e}")
            break

    return results


def step_crawl_and_plot(keyword="中華電信"):
    """第一階段：執行全網爬蟲、更新 CSV、繪製儀表板圖片並暫存本次告警狀態"""
    new_data = []
    print(f"=== 開始執行雲端全網輿情爬蟲（關鍵字：{keyword}）===\n")

    new_data.extend(fetch_google_rss(f"{keyword}", "新聞媒體", max_results=30))
    new_data.extend(fetch_ptt_direct(keyword=keyword, pages=2))

    social_targets = [
        ("Dcard", f"{keyword} site:dcard.tw"),
        ("Mobile01", f"{keyword} site:mobile01.com"),
        ("Threads", f"{keyword} site:threads.net"),
        ("Facebook", f"{keyword} site:facebook.com"),
    ]
    for platform_name, search_query in social_targets:
        new_data.extend(fetch_google_rss(search_query, platform_name, max_results=20))
        time.sleep(1)

    column_order = [
        "抓取時間", "主題分類", "平台分類", "來源網站",
        "文章標題", "發布時間", "文章連結", "內文摘要",
    ]

    df_new = pd.DataFrame(new_data, columns=column_order)
    if not df_new.empty:
        df_new.drop_duplicates(subset=["文章連結"], inplace=True)
        df_new.drop_duplicates(subset=["文章標題"], inplace=True)

    if os.path.exists(OUTPUT_FILE):
        df_old = pd.read_csv(OUTPUT_FILE, encoding="utf-8-sig")
        old_links = set(df_old["文章連結"].dropna().astype(str))
        old_titles = set(df_old["文章標題"].dropna().astype(str))

        mask_new = (~df_new["文章連結"].astype(str).isin(old_links)) & (
            ~df_new["文章標題"].astype(str).isin(old_titles)
        )
        df_filtered = df_new[mask_new]
        df_final = pd.concat([df_filtered, df_old], ignore_index=True)
    else:
        df_filtered = df_new
        df_final = df_new

    df_final = df_final[column_order]
    df_final.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    alert_articles = (
        df_filtered[df_filtered["主題分類"].str.contains("網路斷線/客訴", na=False)]
        if not df_filtered.empty
        else pd.DataFrame(columns=column_order)
    )

    # 繪製最新戰情室儀表板圖表
    generate_dashboard(
        df_final,
        new_count=len(df_filtered),
        alert_count=len(alert_articles),
        keyword=keyword,
    )

    # 將本次告警明細存入暫存檔，供推送圖片到 GitHub 後發送 LINE
    state = {
        "keyword": keyword,
        "new_count": len(df_filtered),
        "total_count": len(df_final),
        "alerts": alert_articles.head(10).to_dict(orient="records"),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def step_notify():
    """第二階段：讀取暫存告警狀態，將文字訊息 + 儀表板圖片一併發送到 LINE"""
    if not LINE_CONFIG["enabled"] or not os.path.exists(STATE_FILE):
        return

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    alerts = state.get("alerts", [])
    new_count = state.get("new_count", 0)
    total_count = state.get("total_count", 0)
    keyword = state.get("keyword", "中華電信")

    if not alerts and not (ALWAYS_SEND_DASHBOARD_ON_NEW and new_count > 0):
        print("[LINE 略過] 本次無新客訴且無需發送戰情圖。")
        return

    now_str = get_tw_now().strftime("%Y-%m-%d %H:%M")
    repo = os.environ.get("GITHUB_REPO", "")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    # 加上時間戳記參數避免 LINE 快取舊圖片
    cache_buster = int(time.time())
    image_url = (
        f"https://raw.githubusercontent.com/{repo}/{branch}/dashboard.png?t={cache_buster}"
        if repo
        else ""
    )

    if alerts:
        lines = [
            "🚨 【雲端輿情緊急告警】",
            f"偵測到 {len(alerts)} 篇「{keyword} - 網路斷線/客訴」最新貼文！",
            f"時間：{now_str} (台灣時間)",
            "━━━━━━━━━━━━━━",
        ]
        for idx, row in enumerate(alerts, start=1):
            lines.append(f"\n{idx}. [{row['平台分類']}] {row['文章標題']}")
            lines.append(f"🔗 {row['文章連結']}")
    else:
        lines = [
            f"📊 【{keyword} 每日輿情戰情快報】",
            f"本次新增 {new_count} 篇最新討論（無重大斷線/客訴）。",
            f"累積總收錄：{total_count} 篇｜時間：{now_str}",
        ]

    messages = [{"type": "text", "text": "\n".join(lines)}]

    if image_url:
        messages.append({
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url,
        })

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CONFIG['channel_access_token']}",
    }
    payload = {
        "to": LINE_CONFIG["user_id"],
        "messages": messages,
    }

    res = requests.post(url, headers=headers, json=payload, timeout=15)
    if res.status_code == 200:
        print("[LINE 通知] 已成功發送文字與儀表板圖片！")
    else:
        print(f"[LINE 失敗] 狀態碼：{res.status_code}，錯誤：{res.text}")


if __name__ == "__main__":
    if "--step" in sys.argv:
        step_name = sys.argv[sys.argv.index("--step") + 1]
        if step_name == "crawl_and_plot":
            step_crawl_and_plot(keyword="中華電信")
        elif step_name == "notify":
            step_notify()
    else:
        step_crawl_and_plot(keyword="中華電信")
