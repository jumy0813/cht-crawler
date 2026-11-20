from collections import Counter
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
    "channel_access_token": os.environ.get("LINE_TOKEN", "").strip(),
    "user_id": os.environ.get("LINE_USER_ID", "").strip(),
}

# 只抓取「幾個小時內」發布的新文章？（預設 24 小時內）
MAX_ARTICLE_AGE_HOURS = 24

# 是否在每次發現新文章時都附帶發送戰情圖卡？
ALWAYS_SEND_DASHBOARD_ON_NEW = True

# 本次新增幾篇「技術障礙」以上，戰情圖卡自動切換為紅色警戒風格？
ALERT_THRESHOLD = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "cht_all_platforms.csv")
DASHBOARD_IMG = os.path.join(BASE_DIR, "dashboard.png")
ALERT_CARD_IMG = os.path.join(BASE_DIR, "alert_card.png")
STATE_FILE = os.path.join(BASE_DIR, "last_run_state.json")

# ==========================================
# 2. 自訂關鍵字分類字典（純化技術障礙與一般客訴）
# ==========================================
CATEGORY_KEYWORDS = {
    "技術障礙": [
        "斷線", "斷網", "沒訊號", "無訊號", "網速慢", "很慢", "變慢", "卡頓",
        "連不上", "故障", "當機", "基地台", "收訊差", "ping高", "不穩", "瞬斷",
        "障礙", "亮紅燈", "紅燈", "沒網路", "無網路", "掛掉", "癱瘓", "海纜",
        "連外", "dns", "中華炸了", "中華掛了", "轉圈圈",
    ],
    "客服/帳務抱怨": [
        "客訴", "爛", "維修", "賠償", "客服", "打不通", "態度差", "帳單",
        "多收", "解約", "退費", "申訴", "ncc",
    ],
    "資費優惠": [
        "資費", "方案", "優惠", "吃到飽", "499", "599", "699", "999", "1399",
        "攜碼", "np", "續約", "新辦", "違約金", "月租", "折扣", "退傭", "加碼",
        "限速", "流量", "熱點", "雙11",
    ],
    "企業新聞": [
        "財報", "營收", "股價", "配息", "股利", "法說會", "董事長", "總經理",
        "簡志誠", "esg", "永續", "合作", "結盟", "投資", "衛星", "6g",
        "標案", "子公司", "獲利", "eps",
    ],
}

# 障礙受災地區萃取字典
REGION_KEYWORDS = {
    "台北/新北": ["台北", "臺北", "新北", "雙北", "板橋", "三重", "中和", "永和", "新莊", "新店", "淡水", "汐止", "內湖", "信義", "大安", "士林", "北投"],
    "桃園": ["桃園", "中壢", "平鎮", "八德", "蘆竹", "龜山", "楊梅"],
    "新竹/苗栗": ["新竹", "竹北", "苗栗", "頭份", "竹南"],
    "台中": ["台中", "臺中", "西屯", "北屯", "南屯", "豐原", "大里", "太平"],
    "彰化/南投": ["彰化", "員林", "南投", "草屯", "埔里"],
    "雲林/嘉義": ["雲林", "斗六", "虎尾", "嘉義", "民雄"],
    "台南": ["台南", "臺南", "永康", "安南", "東區", "善化", "新營"],
    "高雄": ["高雄", "左營", "三民", "鳳山", "鼓山", "楠梓", "前鎮", "苓雅", "小港", "岡山"],
    "屏東": ["屏東", "潮州", "東港", "恆春"],
    "宜花東/離島": ["宜蘭", "羅東", "花蓮", "台東", "臺東", "澎湖", "金門", "馬祖"],
}

# 影響業務類別萃取字典
SERVICE_KEYWORDS = {
    "光世代/家用固網": ["光世代", "固網", "寬頻", "小烏龜", "數據機", "wifi", "家裡網路", "家用", "光纖", "pppoe"],
    "行動網路(4G/5G)": ["行動", "5g", "4g", "手機網路", "門號", "基地台", "訊號", "volte", "漫遊"],
    "MOD/Hami影音": ["mod", "hami", "hamivideo", "機上盒", "愛爾達", "轉播"],
    "國際海纜/連外": ["海纜", "連外", "國外網站", "延遲", "ping", "遊戲", "海底電纜"],
}


def get_tw_now():
    return datetime.now(timezone(timedelta(hours=8)))


def parse_to_tw_datetime(date_str):
    """將 RSS 或 PTT 時間字串統一轉換為台灣時區 datetime 物件"""
    if not date_str or pd.isna(date_str):
        return None
    s = str(date_str).strip()
    tw_tz = timezone(timedelta(hours=8))

    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tw_tz)
    except Exception:
        pass

    try:
        dt_naive = datetime.strptime(s, "%a %b %d %H:%M:%S %Y")
        return dt_naive.replace(tzinfo=tw_tz)
    except Exception:
        pass

    try:
        dt_naive = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt_naive.replace(tzinfo=tw_tz)
    except Exception:
        pass

    return None


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


def extract_hotspots(df_alerts):
    """從技術障礙貼文中自動萃取受災地區與影響業務統計"""
    region_counter = Counter()
    service_counter = Counter()

    for _, row in df_alerts.iterrows():
        title = str(row.get("文章標題", ""))
        summary = str(row.get("內文摘要", ""))
        if summary == "（請點擊連結查看完整貼文）":
            summary = ""
        text = f"{title} {summary}".lower()

        for region, kws in REGION_KEYWORDS.items():
            if any(kw.lower() in text for kw in kws):
                region_counter[region] += 1

        for service, kws in SERVICE_KEYWORDS.items():
            if any(kw.lower() in text for kw in kws):
                service_counter[service] += 1

    region_str = (
        "、".join([f"{k} ({v}篇)" for k, v in region_counter.most_common(4)])
        if region_counter
        else "尚無明顯單一縣市集中（或貼文未註明地點）"
    )
    service_str = (
        "、".join([f"{k} ({v}篇)" for k, v in service_counter.most_common(3)])
        if service_counter
        else "未明確指明單一業務"
    )
    return region_str, service_str


def generate_dashboards(df_all, new_count, alert_count, keyword="中華電信"):
    """方案 B：生成兩張高留白、大字體獨立圖卡 (dashboard.png + alert_card.png)"""
    configure_chinese_font()

    is_emergency = alert_count >= ALERT_THRESHOLD
    bg_color = "#FEF2F2" if is_emergency else "#F4F6F9"
    title_color = "#991B1B" if is_emergency else "#1E293B"
    title_prefix = "🚨 【緊急障礙警戒】" if is_emergency else "📊 "
    now_dt = get_tw_now()
    now_str = now_dt.strftime("%Y-%m-%d %H:%M")

    # =================================================================
    # 圖卡 1：dashboard.png（全網數據總覽：KPI + 平台長條圖 + 24H 趨勢圖）
    # =================================================================
    fig1 = plt.figure(figsize=(12, 6.8), dpi=160, facecolor=bg_color)
    fig1.suptitle(
        f"{title_prefix}【{keyword}】全網輿情數據總覽（圖 1/2）",
        fontsize=20,
        fontweight="bold",
        color=title_color,
        y=0.95,
    )

    kpi_specs = [
        ("累積監控總聲量", f"{len(df_all)} 篇", "#2563EB", 0.18),
        ("本次新增貼文", f"+{new_count} 篇", "#059669", 0.50),
        (
            "技術障礙/斷網 (本次)",
            f"{alert_count} 篇",
            "#DC2626" if alert_count > 0 else "#475569",
            0.82,
        ),
    ]
    for label, value, color, x_pos in kpi_specs:
        fig1.text(x_pos, 0.84, label, ha="center", fontsize=11.5, color="#475569", fontweight="bold")
        fig1.text(x_pos, 0.775, value, ha="center", fontsize=23, color=color, fontweight="bold")

    fig1.text(
        0.93,
        0.025,
        f"資料更新時間：{now_str} (台灣時間) ｜ 累積總收錄：{len(df_all)} 篇",
        ha="right",
        fontsize=10,
        color="#64748B",
    )

    if df_all.empty:
        fig1.text(0.5, 0.45, "目前尚無足夠資料可供繪製圖表", ha="center", fontsize=14, color="#94A3B8")
    else:
        # 左圖：各平台累積聲量
        ax1 = fig1.add_axes([0.08, 0.13, 0.38, 0.55], facecolor="white")
        platform_counts = df_all["平台分類"].value_counts().sort_values(ascending=True)
        bars = ax1.barh(platform_counts.index, platform_counts.values, color="#3B82F6", height=0.55)
        ax1.set_title("各平台累積聲量分布", fontsize=13.5, fontweight="bold", color="#1E293B", pad=12)
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
                fontsize=10.5,
                fontweight="bold",
                color="#334155",
            )
        ax1.set_xlim(0, max(platform_counts.values) * 1.18)

        # 右圖：近 24 小時分時段趨勢折線圖
        ax2 = fig1.add_axes([0.53, 0.13, 0.40, 0.55], facecolor="white")
        hours_list = [(now_dt - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0) for i in range(23, -1, -1)]
        hour_labels = [h.strftime("%H:%M") for h in hours_list]
        total_by_hour = {h: 0 for h in hours_list}
        alert_by_hour = {h: 0 for h in hours_list}

        for _, row in df_all.iterrows():
            dt = parse_to_tw_datetime(row.get("發布時間")) or parse_to_tw_datetime(row.get("抓取時間"))
            if dt:
                dt_hour = dt.replace(minute=0, second=0, microsecond=0)
                if dt_hour in total_by_hour:
                    total_by_hour[dt_hour] += 1
                    if "技術障礙" in str(row.get("主題分類", "")):
                        alert_by_hour[dt_hour] += 1

        y_total = [total_by_hour[h] for h in hours_list]
        y_alert = [alert_by_hour[h] for h in hours_list]

        ax2.plot(range(24), y_total, color="#3B82F6", linewidth=2.2, marker="o", markersize=4, label="全網總聲量")
        ax2.plot(range(24), y_alert, color="#DC2626", linewidth=2.8, marker="s", markersize=5, label="技術障礙/斷網")
        ax2.fill_between(range(24), y_alert, color="#FCA5A5", alpha=0.35)

        ax2.set_title("近 24 小時分時段聲量趨勢", fontsize=13.5, fontweight="bold", color="#1E293B", pad=12)
        ax2.set_xticks(range(0, 24, 4))
        ax2.set_xticklabels([hour_labels[i] for i in range(0, 24, 4)], fontsize=9.5)
        ax2.yaxis.get_major_locator().set_params(integer=True)
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_visible(False)
        ax2.spines["left"].set_color("#CBD5E1")
        ax2.spines["bottom"].set_color("#CBD5E1")
        ax2.grid(axis="y", linestyle="--", alpha=0.4)
        ax2.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="#E2E8F0", fontsize=10)

    plt.savefig(DASHBOARD_IMG, bbox_inches="tight", facecolor=fig1.get_facecolor())
    plt.close(fig1)

    # =================================================================
    # 圖卡 2：alert_card.png（障礙熱點定位 ＋ 最新 5 則技術障礙時間軸）
    # =================================================================
    fig2 = plt.figure(figsize=(11, 7.2), dpi=160, facecolor=bg_color)
    fig2.suptitle(
        f"🚨 【{keyword}】障礙熱點定位與即時快訊（圖 2/2）",
        fontsize=20,
        fontweight="bold",
        color="#991B1B" if is_emergency else "#1E293B",
        y=0.95,
    )

    all_alerts_df = df_all[df_all["主題分類"].str.contains("技術障礙", na=False)] if not df_all.empty else pd.DataFrame()
    recent_alerts = []
    cutoff_24h = now_dt - timedelta(hours=24)
    for _, row in all_alerts_df.iterrows():
        dt = parse_to_tw_datetime(row.get("發布時間")) or parse_to_tw_datetime(row.get("抓取時間"))
        if dt and dt >= cutoff_24h:
            recent_alerts.append(row)
    df_hotspot_source = pd.DataFrame(recent_alerts) if recent_alerts else all_alerts_df.head(10)
    region_str, service_str = extract_hotspots(df_hotspot_source)

    # 上方熱點定位資訊框
    ax_top = fig2.add_axes([0.06, 0.69, 0.88, 0.19], facecolor="#FFF1F2")
    ax_top.set_xticks([])
    ax_top.set_yticks([])
    for spine in ax_top.spines.values():
        spine.set_edgecolor("#FCA5A5")
        spine.set_linewidth(1.5)

    ax_top.text(
        0.03, 0.68,
        f"📍 疑似受災地區熱點：  {region_str}",
        fontsize=13, fontweight="bold", color="#991B1B", va="center"
    )
    ax_top.text(
        0.03, 0.28,
        f"📡 主要影響業務類別：  {service_str}",
        fontsize=13, fontweight="bold", color="#1E293B", va="center"
    )

    # 下方最新 5 則技術障礙時間軸清單
    ax_list = fig2.add_axes([0.06, 0.08, 0.88, 0.57], facecolor="white")
    ax_list.set_xticks([])
    ax_list.set_yticks([])
    for spine in ax_list.spines.values():
        spine.set_edgecolor("#CBD5E1")
        spine.set_linewidth(1.2)

    ax_list.text(
        0.03, 0.91,
        "🕒 最新「技術障礙 / 斷網」貼文時間軸（Top 5）",
        fontsize=14, fontweight="bold", color="#1E293B", va="center"
    )
    ax_list.plot([0.03, 0.97], [0.83, 0.83], color="#E2E8F0", linewidth=1.2, transform=ax_list.transAxes)

    top5_alerts = all_alerts_df.head(5)
    if top5_alerts.empty:
        ax_list.text(
            0.5, 0.45,
            "✅ 目前資料庫中尚無任何網路斷線或技術障礙紀錄。",
            ha="center", va="center", fontsize=14, color="#64748B"
        )
    else:
        y_slots = [0.71, 0.55, 0.39, 0.23, 0.07]
        for idx, (_, row) in enumerate(top5_alerts.iterrows()):
            platform = str(row.get("平台分類", "未知平台")).replace(" (直抓完整版)", "")
            title = str(row.get("文章標題", "")).strip()
            dt = parse_to_tw_datetime(row.get("發布時間")) or parse_to_tw_datetime(row.get("抓取時間"))
            time_tag = dt.strftime("%m/%d %H:%M") if dt else "時間未知"

            if len(title) > 36:
                title = title[:35] + "..."

            ax_list.text(
                0.03, y_slots[idx],
                f"{idx + 1}.  [{time_tag}]  【{platform}】  {title}",
                fontsize=12.5, color="#1E293B", va="center"
            )
            if idx < len(top5_alerts) - 1:
                line_y = y_slots[idx] - 0.08
                ax_list.plot([0.03, 0.97], [line_y, line_y], color="#F1F5F9", linewidth=1, transform=ax_list.transAxes)

    fig2.text(
        0.94,
        0.025,
        f"資料更新時間：{now_str} (台灣時間) ｜ 累積技術障礙：{len(all_alerts_df)} 篇",
        ha="right",
        fontsize=10,
        color="#64748B",
    )

    plt.savefig(ALERT_CARD_IMG, bbox_inches="tight", facecolor=fig2.get_facecolor())
    plt.close(fig2)
    print(f"[儀表板] 已成功生成雙戰情室圖卡：{DASHBOARD_IMG} 與 {ALERT_CARD_IMG}")


def is_recent_article(pub_date_str, max_hours=MAX_ARTICLE_AGE_HOURS):
    if not pub_date_str:
        return True
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(hours=max_hours)

    dt_tw = parse_to_tw_datetime(pub_date_str)
    if dt_tw is not None:
        return dt_tw.astimezone(timezone.utc) >= cutoff_utc
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
    """第一階段：執行全網爬蟲、更新 CSV、繪製雙戰情室圖卡並暫存本次告警狀態"""
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
        df_filtered[df_filtered["主題分類"].str.contains("技術障礙", na=False)]
        if not df_filtered.empty
        else pd.DataFrame(columns=column_order)
    )

    generate_dashboards(
        df_final,
        new_count=len(df_filtered),
        alert_count=len(alert_articles),
        keyword=keyword,
    )

    region_str, service_str = extract_hotspots(alert_articles)

    state = {
        "keyword": keyword,
        "new_count": len(df_filtered),
        "total_count": len(df_final),
        "region_hotspot": region_str,
        "service_hotspot": service_str,
        "alerts": alert_articles.head(10).to_dict(orient="records"),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def step_notify():
    """第二階段：讀取暫存告警狀態，將文字訊息 + 2 張戰情圖卡一併發送到 LINE"""
    if not LINE_CONFIG["enabled"] or not os.path.exists(STATE_FILE):
        return

    token = LINE_CONFIG["channel_access_token"]
    user_id = LINE_CONFIG["user_id"]
    print(f"[診斷資訊] 讀取到的 LINE_TOKEN 長度為：{len(token)} 字元")

    if not token or not user_id:
        print("[LINE 錯誤] LINE_TOKEN 或 LINE_USER_ID 為空值！請確認 GitHub Secrets 設定。")
        return

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    alerts = state.get("alerts", [])
    new_count = state.get("new_count", 0)
    total_count = state.get("total_count", 0)
    keyword = state.get("keyword", "中華電信")
    region_str = state.get("region_hotspot", "")
    service_str = state.get("service_hotspot", "")

    is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if (
        not alerts
        and not is_manual
        and not (ALWAYS_SEND_DASHBOARD_ON_NEW and new_count > 0)
    ):
        print("[LINE 略過] 本次無新技術障礙且無需發送戰情圖。")
        return

    now_str = get_tw_now().strftime("%Y-%m-%d %H:%M")
    repo = os.environ.get("GITHUB_REPO", "")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    cache_buster = int(time.time())

    img1_url = f"https://raw.githubusercontent.com/{repo}/{branch}/dashboard.png?t={cache_buster}" if repo else ""
    img2_url = f"https://raw.githubusercontent.com/{repo}/{branch}/alert_card.png?t={cache_buster}" if repo else ""

    if alerts:
        lines = [
            "🚨 【雲端技術障礙告警】",
            f"偵測到 {len(alerts)} 篇「{keyword} - 技術障礙/斷網」最新貼文！",
            f"📍 地區熱點：{region_str}",
            f"📡 影響業務：{service_str}",
            f"🕒 時間：{now_str} (台灣時間)",
            "━━━━━━━━━━━━━━",
        ]
        for idx, row in enumerate(alerts, start=1):
            dt = parse_to_tw_datetime(row.get("發布時間")) or parse_to_tw_datetime(row.get("抓取時間"))
            time_tag = dt.strftime("%H:%M") if dt else "--:--"
            lines.append(f"\n{idx}. [{time_tag}] [{row['平台分類']}] {row['文章標題']}")
            lines.append(f"🔗 {row['文章連結']}")
    else:
        lines = [
            f"📊 【{keyword} 障礙與輿情戰情快報】",
            f"本次新增 {new_count} 篇最新討論（無新增技術障礙/斷網）。",
            f"累積總收錄：{total_count} 篇｜時間：{now_str}",
        ]

    messages = [{"type": "text", "text": "\n".join(lines)}]

    if img1_url:
        messages.append({
            "type": "image",
            "originalContentUrl": img1_url,
            "previewImageUrl": img1_url,
        })
    if img2_url:
        messages.append({
            "type": "image",
            "originalContentUrl": img2_url,
            "previewImageUrl": img2_url,
        })

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "to": user_id,
        "messages": messages,
    }

    res = requests.post(url, headers=headers, json=payload, timeout=15)
    if res.status_code == 200:
        print("[LINE 通知] 已成功發送雙戰情室圖卡！")
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
