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
