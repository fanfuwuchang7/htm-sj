#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 题材风口扫描器
功能：
1. 拉取全市场场内 ETF，并按题材/行业分类
2. 今日风口
3. 核心风口（30 日）
4. 低位启动信号（30 日未大涨 + 今日放量突破）
5. 题材温度计（0-100）
6. 盘中 / 盘后扫描
7. 通过 SMTP 发送 HTML 报告

运行方式：
    python etf_theme_scanner.py --mode intraday
    python etf_theme_scanner.py --mode close
    python etf_theme_scanner.py --mode both

邮箱配置：
    默认直接修改本文件顶部 EMAIL_CONFIG 即可（单文件配置，无需外部文件）。
    下列环境变量优先级更高，可用于部署/临时切换（不设置则忽略）：
    SMTP_HOST      SMTP 服务器地址
    SMTP_PORT      SMTP 端口，默认 465
    SMTP_USER      SMTP 账号
    SMTP_PASSWORD  SMTP 密码或授权码
    SMTP_USE_SSL   是否使用 SSL，默认 true
    EMAIL_FROM     发件人地址，缺省使用 SMTP_USER
    EMAIL_TO       收件人地址，多个地址用英文逗号分隔
    配置校验：python etf_theme_scanner.py --test-email
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import smtplib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import akshare as ak
import numpy as np
import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ETFThemeScanner")

# ============ 题材规则 ============
# 分类口径：行情 App「板块总览」82 个板块（见 板块名称.txt），dict 键即最终题材名。
# 匹配规则：dict 顺序 = 优先级，名称包含关键词即命中，因此按「跨境→细分行业→宽泛行业→宽基指数
# →固收/现金」重排，避免「恒生科技」被「科技」抢走、「红利低波」被「红利」抢走等问题。
THEME_RULES: dict[str, list[str]] = {
    # ===== 跨境（须先于境内行业） =====
    "恒生科技": ["恒生科技", "港股科技", "香港科技"],
    "港股互联": ["港股互联", "中概", "恒生互联网", "港股互联网", "中国互联", "香港互联网"],
    "港创新药": ["港股创新药", "港创新药", "恒生创新药", "香港创新药", "港股医药", "恒生医药", "港股医疗"],
    "港股红利": ["港股红利", "恒生红利", "港股通红利", "港股高股息"],
    "海外医药": ["海外医药", "海外医疗", "全球医疗"],
    "沪港深消费": ["沪港深消费", "沪港深龙头", "沪港深"],
    "恒生": ["恒生", "港股通", "香港", "港股"],
    "纳指": ["纳指", "纳斯达克", "标普", "美股", "道琼", "日经", "日本", "德国", "法国", "亚太",
           "沙特", "印度", "东南亚", "全球", "海外", "新兴", "巴西"],
    # ===== 大宗商品/周期 =====
    "黄金股": ["黄金股"],
    "黄金": ["黄金", "白银", "贵金属", "金ETF", "金矿"],
    "稀土永磁": ["稀土", "永磁"],
    "有色金属": ["有色", "稀有金属", "铜", "铝", "铅锌", "矿业", "金属", "资源", "大宗商品", "商品",
               "材料ETF", "!新材料"],
    "煤炭": ["煤炭"],
    "钢铁": ["钢铁"],
    "油气资源": ["油气", "石油", "原油", "石化"],
    "化工": ["化工", "能源化工", "碳纤维", "钛"],
    # ===== TMT（细分器件 → 板块 → 宽泛） =====
    "存储芯片": ["存储", "内存", "闪存"],
    "半导体材料设备": ["半导体材料", "半导体设备", "光刻", "刻蚀", "硅片", "科创新材料"],
    "MLCC": ["MLCC", "被动元件"],
    "PCB": ["PCB", "印制电路"],
    "CPO": ["CPO", "光模块", "光通信"],
    "算力租赁": ["算力租赁"],
    "国产算力": ["国产算力", "算力"],
    "AI应用": ["AI应用", "AIGC"],
    "脑机接口": ["脑机"],
    "人工智能": ["人工智能", "AI", "大模型"],
    "云计算": ["云计算", "大数据", "数据中心", "数据要素", "数字经济"],
    "通信": ["通信", "5G", "电信", "物联网", "工业互联网", "卫星"],
    "消费电子": ["消费电子", "消电", "光学", "面板", "VR", "虚拟现实", "苹果", "智能穿戴", "电子"],
    "半导体": ["半导体", "芯片", "集成电路", "晶圆"],
    "传媒游戏": ["传媒", "游戏", "影视", "动漫", "文娱", "电竞", "广告", "出版"],
    # ===== 制造 / 军工 =====
    "机器人": ["机器人", "人形机器人"],
    "工程机械": ["工程机械", "挖机"],
    "先进制造": ["先进制造", "智能制造", "工业母机", "机床", "自动化", "高端装备", "专精特新",
               "船舶", "产业升级", "工业4", "制造", "机械", "工业"],
    "商业航天": ["商业航天", "航天"],
    "可控核聚变": ["核聚变"],
    "军工": ["军工", "国防", "航空", "兵器", "大飞机", "低空", "军民融合"],
    # ===== 新能源链 =====
    "固态电池": ["固态电池", "固态"],
    "锂矿": ["锂矿", "锂业"],
    "储能": ["储能", "抽水蓄能"],
    "电网设备": ["电网", "特高压", "电力设备"],
    "光伏": ["光伏", "太阳能", "风电", "风能"],
    "汽车整车": ["汽车", "整车", "新能源车", "智能车", "新能车", "智能驾驶", "电动车"],
    "电力": ["电力", "水电", "核电", "绿电", "公用事业", "能源"],
    "新能源": ["新能源", "锂电", "电池", "碳中和", "清洁能源", "钠离子", "环保", "低碳", "绿色", "可持续",
             "新材料", "!科创新材料"],
    # ===== 医药消费 =====
    "CXO": ["CXO", "医药外包"],
    "创新药": ["创新药"],
    "医疗": ["医疗", "医美", "健康"],
    "医药": ["医药", "生物", "中药", "疫苗", "制药", "药ETF"],
    "养老产业": ["养老", "银发"],
    "白酒": ["白酒", "酒ETF", "酿酒"],
    "食品饮料": ["食品", "饮料", "乳业", "啤酒"],
    "家用电器": ["家电", "家用电器"],
    "消费": ["消费", "零售", "免税", "国货", "教育", "旅游", "酒店", "休闲"],
    "农林牧渔": ["农业", "养殖", "猪肉", "种业", "渔业", "畜牧", "粮食", "豆粕", "农牧", "农林"],
    # ===== 金融 / 地产 / 交运 =====
    "金融科技": ["金融科技", "金融IT", "互金", "金融"],
    "证券保险": ["证券", "券商", "保险"],
    "银行": ["银行"],
    "房地产": ["地产", "房地产"],
    "基建": ["基建", "建筑", "建材", "工程", "一带一路"],
    "交通运输": ["交通运输", "交运", "物流", "公路", "铁路", "港口", "机场"],
    # ===== 风格 / 策略 =====
    "现金流": ["现金流"],
    "红利低波": ["红利低波", "低波", "分红"],
    "红利": ["红利", "高股息", "股息"],
    "小微盘量化": ["小微盘", "小盘量化"],
    "微盘股": ["微盘", "小盘", "中证1000", "国证2000", "1000", "2000"],
    "量化": ["量化", "指增", "增强"],
    "大科技": ["科技", "TMT", "信息技术", "信息安全", "信息", "新经济", "互联网", "软件", "计算机", "信创"],
    "蓝筹": ["蓝筹", "基本面", "价值", "成长", "质量", "治理", "责任", "ESG", "综指", "A股",
           "大盘", "深证", "深成", "国证", "中创", "湾创", "央视", "MSCI", "中小",
           "央企", "国企", "国资", "民企", "指数", "区域", "成渝", "长江", "张江", "之江",
           "G60", "湾区", "珠三角", "湖北", "杭州", "四川", "长三角", "京津冀", "粤港澳", "经济圈"],
    # ===== 宽基指数 =====
    "北证": ["北证", "北交所"],
    "双创50": ["双创", "科创创业"],
    "科创板": ["科创板", "科创50", "科创100", "科创200", "科创综"],
    "创业板": ["创业板", "创业"],
    "中证500": ["中证500", "中证800", "500ETF"],
    "上证50": ["上证50", "50ETF", "上证"],
    "沪深300": ["沪深300", "300ETF", "沪深", "中证A", "A500", "A100", "A50", "中证", "300", "500",
              "800", "400", "88", "100"],
    # ===== 固收 / 现金 =====
    "可转债": ["转债"],
    "短债": ["短债", "短融", "同业存单", "超短债"],
    "固收+": ["固收", "偏债", "二级债", "含权"],
    "中长债": ["国债", "政金债", "信用债", "地方债", "中长债", "利率债", "债券", "债ETF", "纯债"],
    "货币基金": ["货币", "现金", "日利", "快钱", "快线", "添益", "添利", "日日鑫", "财富宝"],
}

# ============ 参数 ============
class Params:
    min_amount_today = 5e7          # 今日风口最低成交额
    ret30_threshold = 0.08          # 低位启动：30 日涨幅低于 8%
    breakout_pct = 0.025            # 低位启动：今日涨幅至少 2.5%
    breakout_amount = 2e8           # 低位启动：今日成交额至少 2 亿
    volume_ratio = 1.5              # 低位启动：量能至少放大 1.5 倍
    top_n = 15                      # 榜单展示数量
    core_etf_sample = 8             # 每个题材参与 30 日统计的代表 ETF 数量
    history_days = 60               # 日线回溯天数
    max_workers = 8                # 历史行情并发拉取线程数
    hist_retries = 3               # 单只 ETF 历史行情失败重试次数
    hist_retry_sleep = 0.5         # 重试退避基数（秒），第 n 次等待 n*基数
    cache_dir = "."                # 行情快照缓存目录
    spot_cache_file = "etf_spot_cache.json"   # 行情快照缓存文件
    universe_file = "etf_codes.json" # ETF 代码清单缓存（供腾讯/天天基金等需代码源的回退使用）
    hist_cache_dir = "etf_hist_cache"        # 历史行情缓存目录
    prefer_source = "auto"         # 行情数据源：auto / em / sina / tiantian / tencent
    allow_cache_fallback = True    # 实时源全部失败时回退本地缓存
    docs_dir = "docs"              # HTML 看板输出目录
    dashboard_file = "index.html"  # 看板文件名（固定单份，覆盖更新）


# ============ 邮箱配置（直接改这里，单文件配置，无需外部文件） ============
# QQ邮箱授权码获取：QQ邮箱 → 设置 → 账户 → 开启「IMAP/SMTP服务」→ 短信验证得到 16 位授权码
# 常见服务商 host：QQ=smtp.qq.com、163=smtp.163.com、126=smtp.126.com、
#                  Gmail=smtp.gmail.com、腾讯企业邮=smtp.exmail.qq.com、Outlook=smtp.office365.com
EMAIL_CONFIG: dict = {
    "host": "smtp.qq.com",
    "port": 465,
    "use_ssl": True,
    "user": "783109755@qq.com",
    # 授权码留空：改由环境变量注入（本地用系统环境变量，GitHub Actions 用仓库 Secrets）
    # 这样脚本入库不会泄露授权码。本地一次性设置：
    #   setx SMTP_PASSWORD 你的授权码
    "password": "",
    "from": "783109755@qq.com",
    "to": ["809080777@qq.com", "1279885368@qq.com", "120957262@qq.com", "1013562640@qq.com"],
}


@dataclass
class ScanResult:
    generated_at: str
    mode: str
    spot: pd.DataFrame
    today_rank: pd.DataFrame
    core_rank: pd.DataFrame
    low_level: pd.DataFrame
    temperature: pd.DataFrame
    stale: bool = False            # 数据是否来自本地缓存回退（可能非最新）


# ============ 数据获取与分类 ============
# 数据源状态：fetch_spot 在回退本地缓存时置为 True，供报告标注
_SPOT_STALE = False
# 离线模式：True 时完全不联网，仅使用本地缓存（行情快照 + 历史行情）
_OFFLINE = False

# 各行情源的原始列名 -> 统一字段 映射
_EM_SPOT_MAP = {
    "代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "pct_change",
    "涨跌额": "price_change", "成交量": "volume", "成交额": "amount",
    "换手率": "turnover", "昨收": "pre_close",
}
_SINA_SPOT_MAP = {
    "代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "pct_change",
    "涨跌额": "price_change", "成交量": "volume", "成交额": "amount",
    "换手率": "turnover", "昨收": "pre_close",
}
_SPOT_NUMERIC = ["pct_change", "price_change", "volume", "amount", "turnover", "price", "pre_close"]


def _normalize_spot(df: pd.DataFrame, rename_map: dict) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    for col in _SPOT_NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["theme"] = df["name"].fillna("").apply(classify_theme)
    return df


def _ensure_spot_schema(df: pd.DataFrame) -> pd.DataFrame:
    """统一行情字段：各数据源覆盖度不同（如新浪无换手率、天天基金无成交量），
    缺失列补 NaN，保证下游聚合不会因缺列崩溃。"""
    required = ["code", "name", "price", "pct_change", "price_change",
                "volume", "amount", "turnover", "pre_close"]
    for col in required:
        if col not in df.columns:
            df[col] = float("nan")
    for col in _SPOT_NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "theme" not in df.columns:
        df["theme"] = df["name"].fillna("").apply(classify_theme)
    return df


def _spot_from_em() -> Optional[pd.DataFrame]:
    if not hasattr(ak, "fund_etf_spot_em"):
        return None
    return _normalize_spot(ak.fund_etf_spot_em(), _EM_SPOT_MAP)


def _spot_from_sina() -> Optional[pd.DataFrame]:
    """新浪 ETF 全市场行情（直接请求，绕过 akshare 易失效的封装）。
    节点 etf_hq_fund 返回全部 ETF 的实时行情，同时充当 universe 代码清单来源。"""
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/jsonp.php/"
           "IO.XSRV2.CallbackList['da_yPT46_Ll7K6WD']/Market_Center.getHQNodeDataSimple")
    params = {"page": "1", "num": "6000", "sort": "symbol", "asc": "0",
              "node": "etf_hq_fund", "[object HTMLDivElement]": "qvvne"}
    try:
        r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        text = r.text
        # JSONP 形如 前缀([{...}])：取首个 '(' 与末个 ')' 之间的 JSON 数组
        i, j = text.find("("), text.rfind(")")
        if i < 0 or j <= i:
            logger.warning("新浪 ETF 列表解析失败：未找到 JSON 数组")
            return None
        data = json.loads(text[i + 1:j])
    except Exception as exc:
        logger.warning("新浪 ETF 行情获取失败: %s", exc)
        return None

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return float("nan")

    rows = []
    for it in data:
        sym = str(it.get("symbol", ""))
        if len(sym) < 2:
            continue
        code = sym[2:] if sym[:2] in ("sh", "sz") else sym
        rows.append({
            "code": code,
            "name": it.get("name", ""),
            "price": _f(it.get("trade")),
            "pct_change": _f(it.get("changepercent")),
            "price_change": _f(it.get("pricechange")),
            "pre_close": _f(it.get("settlement")),
            "volume": _f(it.get("volume")),
            "amount": _f(it.get("amount")),
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["theme"] = df["name"].fillna("").apply(classify_theme)
    return df if not df.empty else None


# ---------- ETF 代码清单缓存（universe） ----------
# 腾讯/天天基金等接口需要“先有代码清单再补行情”，故把 em/sina 成功返回的
# 代码+名称落盘，供后续仅提供行情的源在 em/sina 不可用时仍能工作。
def _universe_path() -> str:
    return os.path.join(Params.cache_dir, Params.universe_file)


def _save_universe(df: pd.DataFrame) -> None:
    if "code" not in df.columns or "name" not in df.columns:
        return
    try:
        os.makedirs(Params.cache_dir, exist_ok=True)
        df[["code", "name"]].dropna(subset=["code"]).to_json(
            _universe_path(), orient="records", force_ascii=False)
    except Exception as exc:
        logger.debug("代码清单缓存写入失败: %s", exc)


def _load_universe() -> dict[str, str]:
    path = _universe_path()
    if not os.path.exists(path):
        return {}
    try:
        u = pd.read_json(path)
        return dict(zip(u["code"].astype(str), u["name"].astype(str)))
    except Exception as exc:
        logger.debug("代码清单缓存读取失败: %s", exc)
        return {}


# ---------- 腾讯财经（qt.gtimg.cn，独立于东方财富/新浪） ----------
def _tencent_quotes(codes: list[str]) -> dict[str, dict]:
    """批量获取腾讯实时行情，返回 code -> 行情字段。需 sh/sz 前缀。"""
    out: dict[str, dict] = {}
    if not codes:
        return out
    sess = requests.Session()
    batch = 60
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        q = ",".join(_to_sina_symbol(c) for c in chunk)
        try:
            r = sess.get("https://qt.gtimg.cn/q=" + q, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            text = r.content.decode("gbk", errors="ignore")
        except Exception as exc:
            logger.warning("腾讯行情请求失败: %s", exc)
            continue
        for line in text.split(";"):
            line = line.strip()
            if not line.startswith("v_"):
                continue
            _, _, val = line.partition("=")
            val = val.strip().strip('"')
            if not val:
                continue
            f = val.split("~")
            if len(f) < 37:
                continue
            code = f[2]
            try:
                price = float(f[3])
                chg = float(f[31])
                pct = float(f[32])
                amount = float(f[36])
                volume = float(f[6]) * 100  # 腾讯返回“手”，换算为“股”
            except ValueError:
                continue
            out[code] = {"price": price, "price_change": chg, "pct_change": pct,
                         "amount": amount, "volume": volume}
    return out


def _spot_from_tencent() -> Optional[pd.DataFrame]:
    universe = _load_universe()
    if not universe:
        logger.debug("腾讯源缺少 ETF 代码清单（需先由 em/sina 成功运行一次）")
        return None
    quotes = _tencent_quotes(list(universe.keys()))
    if not quotes:
        return None
    rows = [{"code": c, "name": universe.get(c, c), **q} for c, q in quotes.items()]
    df = pd.DataFrame(rows)
    df["theme"] = df["name"].fillna("").apply(classify_theme)
    return df if not df.empty else None


# ---------- 天天基金（fundmobapi.eastmoney.com，东财系但不同子域/接口） ----------
def _spot_from_tiantian() -> Optional[pd.DataFrame]:
    """天天基金 ETF 排行接口（best-effort）。返回字段随接口变动，若结构与预期不符
    会自动返回 None 触发下游回退；如需精确字段请按实际返回微调映射。"""
    try:
        r = requests.get(
            "https://fundmobapi.eastmoney.com/FundMNFundRank",
            params={"pageIndex": "1", "pageSize": "3000", "appType": "Android",
                    "product": "EFund", "plat": "Android", "deviceid": "ETFScanner",
                    "version": "1", "fundType": "4"},
            timeout=15, headers={"User-Agent": "Mozilla/5.0",
                                 "Referer": "https://fund.eastmoney.com/"},
        )
        data = r.json()
    except Exception as exc:
        logger.warning("天天基金获取失败: %s", exc)
        return None
    datas = data.get("Datas") or data.get("data") or []
    rows = []
    for it in datas:
        code = it.get("FCODE") or it.get("fundcode") or it.get("code")
        name = it.get("SHORTNAME") or it.get("name") or it.get("shortname")
        if not code or not name:
            continue
        try:
            price = float(it.get("ESTVAL") or it.get("NAV") or it.get("PRICE"))
            pct = float(it.get("ESTVALCHG") or it.get("NAVCHG") or it.get("CHG"))
        except (TypeError, ValueError):
            continue
        rows.append({"code": str(code), "name": name, "price": price, "pct_change": pct})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["theme"] = df["name"].fillna("").apply(classify_theme)
    return df if not df.empty else None


# 可插拔行情源：auto 模式下按此顺序尝试，全部失败再回退本地缓存
SPOT_PROVIDERS = {
    "em": _spot_from_em,
    "sina": _spot_from_sina,
    "tiantian": _spot_from_tiantian,
    "tencent": _spot_from_tencent,
}


def _spot_cache_path() -> str:
    return os.path.join(Params.cache_dir, Params.spot_cache_file)


def _save_spot_cache(df: pd.DataFrame) -> None:
    try:
        os.makedirs(Params.cache_dir, exist_ok=True)
        df.to_json(_spot_cache_path(), orient="records", force_ascii=False, date_format="iso")
    except Exception as exc:
        logger.debug("行情缓存写入失败: %s", exc)


def _load_spot_cache() -> Optional[pd.DataFrame]:
    path = _spot_cache_path()
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_json(path)
        for col in _SPOT_NUMERIC:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "theme" not in df.columns and "name" in df.columns:
            df["theme"] = df["name"].fillna("").apply(classify_theme)
        return df
    except Exception as exc:
        logger.debug("行情缓存读取失败: %s", exc)
        return None


def fetch_spot(prefer: Optional[str] = None) -> pd.DataFrame:
    """拉取全市场 ETF 行情。auto 模式按 sina -> em -> tiantian -> tencent 顺序尝试，
    任一处成功即采用并落盘缓存；实时源全部失败时回退本地快照（标记 _SPOT_STALE）。
    离线模式（_OFFLINE=True）下完全不联网，仅读取本地快照缓存。"""
    global _SPOT_STALE
    _SPOT_STALE = False
    if _OFFLINE:
        cached = _load_spot_cache()
        if cached is not None and not cached.empty:
            logger.info("离线模式：使用本地行情缓存 %d 只", len(cached))
            _SPOT_STALE = True
            return cached
        logger.error("离线模式但本地行情缓存为空（请先联网运行一次以生成 etf_spot_cache.json）")
        return pd.DataFrame()
    prefer = prefer or Params.prefer_source
    order = ["sina", "em", "tiantian", "tencent"] if prefer == "auto" else [prefer]
    for name in order:
        prov = SPOT_PROVIDERS.get(name)
        if prov is None:
            continue
        try:
            df = prov()
        except Exception as exc:
            logger.warning("行情源 %s 获取失败: %s", name, exc)
            continue
        if df is not None and not df.empty:
            df = _ensure_spot_schema(df)
            logger.info("行情源 %s 获取成功：%d 只", name, len(df))
            _save_spot_cache(df)
            _save_universe(df)   # 记录代码清单，供腾讯/天天基金等需代码的源后续回退
            return df

    if Params.allow_cache_fallback:
        cached = _load_spot_cache()
        if cached is not None and not cached.empty:
            logger.warning("实时行情源全部失败，回退本地缓存（数据可能非最新）")
            _SPOT_STALE = True
            return cached
    logger.error("行情获取失败且无可用缓存")
    return pd.DataFrame()



def classify_theme(name: str) -> str:
    """按 THEME_RULES 顺序匹配，返回题材名。
    支持排除式关键词：以 '!' 开头表示「名称含该词则不归入本题材」，
    用于解决 '材料ETF' 是 '新材料ETF' 子串这类包含冲突。"""
    for theme, keywords in THEME_RULES.items():
        positive = [kw for kw in keywords if not kw.startswith("!")]
        negative = [kw[1:] for kw in keywords if kw.startswith("!")]
        if any(kw in name for kw in positive) and not any(kw in name for kw in negative):
            return theme
    return "其他"


# 进程内历史行情缓存：避免 core_hotspot / low_level_breakout 重复拉取同一只 ETF
_HIST_CACHE: dict[tuple[str, int], Optional[pd.DataFrame]] = {}
_HIST_LOCK = threading.Lock()


def _to_sina_symbol(code: str) -> str:
    """ETF 6 位代码转新浪代码：沪市(5/6 开头) 用 sh，深市(1 开头) 用 sz。"""
    return ("sh" if code[:1] in ("5", "6") else "sz") + code


def _coerce_hist(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["收盘", "开盘", "最高", "最低", "成交量"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _hist_cache_path(code: str, days: int) -> str:
    try:
        os.makedirs(Params.hist_cache_dir, exist_ok=True)
    except OSError:
        pass
    return os.path.join(Params.hist_cache_dir, f"{code}_{days}.json")


def _save_history_cache(code: str, days: int, df: pd.DataFrame) -> None:
    try:
        df.to_json(_hist_cache_path(code, days), orient="records",
                   force_ascii=False, date_format="iso")
    except Exception as exc:
        logger.debug("历史缓存写入 %s 失败: %s", code, exc)


def _load_history_cache(code: str, days: int) -> Optional[pd.DataFrame]:
    path = _hist_cache_path(code, days)
    if not os.path.exists(path):
        return None
    try:
        return _coerce_hist(pd.read_json(path))
    except Exception as exc:
        logger.debug("历史缓存读取 %s 失败: %s", code, exc)
        return None


def _fetch_history_one(code: str, days: int, source: str) -> Optional[pd.DataFrame]:
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    if source == "em":
        if not hasattr(ak, "fund_etf_hist_em"):
            return None
        return ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
    if source == "sina":
        if not hasattr(ak, "fund_etf_hist_sina"):
            return None
        # 新浪历史接口仅接受 symbol，返回全量日线且为英文字段，无日期区间/复权参数
        raw = ak.fund_etf_hist_sina(symbol=_to_sina_symbol(code))
        if raw is None or raw.empty:
            return None
        raw = raw.rename(columns={
            "date": "日期", "open": "开盘", "high": "最高",
            "low": "最低", "close": "收盘", "volume": "成交量",
        })
        if "日期" in raw.columns:
            raw["日期"] = pd.to_datetime(raw["日期"], errors="coerce")
            raw = raw.sort_values("日期")
        return raw.tail(days)
    if source == "tiantian":
        # 天天基金 NAV 历史（东财系，缺成交量）：净值 -> 收盘，成交量置 NaN。
        # 放量信号因此不可用，low_level_breakout 会自动跳过此类标的。
        if not hasattr(ak, "fund_open_fund_info_em"):
            return None
        try:
            nav = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值")
        except Exception as exc:
            logger.debug("天天基金历史 %s 失败: %s", code, exc)
            return None
        if nav is None or nav.empty:
            return None
        nav = nav.rename(columns={"净值日期": "日期", "单位净值": "收盘"})
        if "收盘" in nav.columns:
            nav["收盘"] = pd.to_numeric(nav["收盘"], errors="coerce")
        nav["成交量"] = np.nan
        return nav
    if source == "tencent":
        # 腾讯日 K（无复权）：param 形如 sh510500,day,,,320,  —— 用空日期段请求最近 N 根
        sym = _to_sina_symbol(code)
        count = max(int(days * 1.5), 60)
        try:
            r = requests.get(
                "http://web.ifzq.gtimg.cn/appstock/app/kline/kline",
                params={"_var": "kline_day", "param": f"{sym},day,,,{count},",
                        "r": str(random.random())},
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/84.0",
                         "Host": "web.ifzq.gtimg.cn", "Referer": f"http://gu.qq.com/{sym}/gp"},
            )
            txt = r.text
            payload = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            rows = ((payload.get("data") or {}).get(sym) or {}).get("day") or []
        except Exception as exc:
            logger.debug("腾讯历史 %s 失败: %s", code, exc)
            return None
        if not rows:
            return None
        df = pd.DataFrame(rows)
        if df.shape[1] < 6:
            return None
        df = df.iloc[:, :6]
        df.columns = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
        return df.tail(days)
    return None


def _fetch_history_raw(code: str, days: int) -> Optional[pd.DataFrame]:
    """单只 ETF 历史行情：实时源 tencent -> em -> tiantian（各带重试），
    全部失败回退本地文件缓存。
    注意：不使用 sina——akshare 的 sina 历史依赖 py_mini_racer 执行 JS 解密，
    在部分环境会原生崩溃（无法被 try/except 捕获，直接带走整个进程）。"""
    last_exc: Optional[Exception] = None
    for source in ("tencent", "em", "tiantian"):
        for attempt in range(1, Params.hist_retries + 1):
            try:
                raw = _fetch_history_one(code, days, source)
            except Exception as exc:  # 网络抖动、接口限频，按指数退避重试
                last_exc = exc
                logger.warning("历史 %s[%s] 第 %d/%d 次失败: %s",
                               code, source, attempt, Params.hist_retries, exc)
                if attempt < Params.hist_retries:
                    time.sleep(Params.hist_retry_sleep * attempt)
                continue
            if raw is None or raw.empty:
                break
            hist = _coerce_hist(raw)
            _save_history_cache(code, days, hist)
            return hist
    cached = _load_history_cache(code, days)
    if cached is not None:
        logger.debug("历史 %s 回退本地缓存", code)
        return cached
    logger.debug("历史 %s 最终失败: %s", code, last_exc)
    return None


def etf_history(code: str, days: int = Params.history_days, offline: Optional[bool] = None) -> Optional[pd.DataFrame]:
    offline = _OFFLINE if offline is None else offline
    key = (code, days)
    with _HIST_LOCK:
        if key in _HIST_CACHE:
            return _HIST_CACHE[key]
    # 离线模式：仅从本地文件缓存读取，完全不联网
    hist = _load_history_cache(code, days) if offline else _fetch_history_raw(code, days)
    with _HIST_LOCK:
        _HIST_CACHE[key] = hist
    return hist


def fetch_histories(codes: list[str], offline: Optional[bool] = None) -> dict[str, Optional[pd.DataFrame]]:
    """并发拉取多只 ETF 历史行情，返回 code -> DataFrame（或 None）。"""
    result: dict[str, Optional[pd.DataFrame]] = {}
    if not codes:
        return result
    with ThreadPoolExecutor(max_workers=Params.max_workers) as pool:
        future_to_code = {pool.submit(etf_history, code, Params.history_days, offline): code for code in codes}
        for fut in as_completed(future_to_code):
            code = future_to_code[fut]
            try:
                result[code] = fut.result()
            except Exception as exc:
                logger.debug("历史任务 %s 异常: %s", code, exc)
                result[code] = None
    return result


# ============ 分析模块 ============
# 固收/现金类板块：不是「题材」，不参与风口与温度排名
FIXED_INCOME_THEMES = {"货币基金", "中长债", "短债", "可转债", "固收+"}


def _norm01(s: pd.Series) -> pd.Series:
    """归一化到 0-100（相对最大值）：负值截断为 0，避免下跌题材算出负分；
    全负/全零时返回全 0。"""
    mx = s.max()
    if not mx or mx <= 0:
        return s * 0.0
    return (s / mx * 100).round(0).clip(lower=0)


def today_hotspot(df: pd.DataFrame) -> pd.DataFrame:
    data = df[(df["amount"] >= Params.min_amount_today)
              & (~df["theme"].isin(FIXED_INCOME_THEMES)) & (df["theme"] != "其他")].copy()
    if data.empty:
        return pd.DataFrame()

    group = data.groupby("theme").agg(
        ETF数量=("code", "count"),
        平均涨幅=("pct_change", "mean"),
        最大涨幅=("pct_change", "max"),
        上涨占比=("pct_change", lambda x: (x > 0).mean()),
        总成交额=("amount", "sum"),
        平均换手=("turnover", "mean"),
    )
    # 今日风口只保留平均涨幅为正的题材：平均下跌的题材不是“风口”，
    # 其高排名纯粹由成交额量纲贡献（如巨量下跌的宽基/行业）
    group = group[group["平均涨幅"] > 0]
    # 涨幅与成交额各自归一化到 0-100 再加权，避免成交额量纲碾压涨幅
    group["今日风口分"] = (_norm01(group["平均涨幅"]) * 0.6
                        + _norm01(group["总成交额"]) * 0.4).round(0).astype(int)
    return group.sort_values("今日风口分", ascending=False)


def core_hotspot(df: pd.DataFrame) -> pd.DataFrame:
    candidates = []
    for theme, group in df.groupby("theme"):
        # 排除固收/现金类板块（货币基金/中长债/短债/可转债/固收+）与其他：
        # 核心风口衡量的是权益题材的 30 日动能，债券基金的稳定净值会污染排名
        if theme in FIXED_INCOME_THEMES or theme in {"货币", "债券", "其他"}:
            continue
        group = group.sort_values("amount", ascending=False).head(Params.core_etf_sample)
        for _, row in group.iterrows():
            candidates.append((theme, row))

    if not candidates:
        return pd.DataFrame()

    hist_map = fetch_histories([row["code"] for _, row in candidates])

    rows = []
    for theme, row in candidates:
        hist = hist_map.get(row["code"])
        if hist is None or len(hist) < 22:
            continue
        close = hist["收盘"].dropna().values
        if len(close) < 22:
            continue
        ret30 = close[-1] / close[-22] - 1
        vol_recent = hist["成交量"].dropna().iloc[-5:].mean()
        vol_prev = hist["成交量"].dropna().iloc[-10:-5].mean()
        # 缺成交量（如天天基金 NAV 历史）时量能比记 1.0（中性），不参与放大/缩量判定
        vol_ratio = vol_recent / vol_prev if (vol_prev > 0 and np.isfinite(vol_recent)) else 1.0
        rows.append({
            "theme": theme,
            "code": row["code"],
            "name": row["name"],
            "amount": row["amount"],
            "ret_30d": ret30,
            "vol_ratio": vol_ratio,
        })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    group = result.groupby("theme").agg(
        代表ETF数=("code", "count"),
        平均30日涨幅=("ret_30d", "mean"),
        最大涨幅=("ret_30d", "max"),
        平均量能比=("vol_ratio", "mean"),
        样本总成交额=("amount", "sum"),
    )
    # 各分项归一化到 0-100 再加权（0.5 涨幅 / 0.3 量能 / 0.2 成交额），
    # 消除量纲碾压，核心风口分落在 0-100，可直接与今日风口分横向比较
    # 核心风口只保留 30 日涨幅为正的题材：下跌题材不是“风口”，
    # 放量下跌属于资金异动而非核心趋势（此类机会由低位启动信号表捕捉）
    group = group[group["平均30日涨幅"] > 0]
    group["核心风口分"] = (
        _norm01(group["平均30日涨幅"]) * 0.5
        + _norm01(group["平均量能比"].clip(upper=5)) * 0.3
        + _norm01(group["样本总成交额"]) * 0.2
    ).round(0).astype(int)
    return group.sort_values("核心风口分", ascending=False)


def low_level_breakout(df: pd.DataFrame) -> pd.DataFrame:
    candidates = []
    for code in df["code"].unique():
        row = df[df["code"] == code].iloc[0]
        if _meets_basic(row):
            candidates.append((code, row))

    if not candidates:
        return pd.DataFrame()

    hist_map = fetch_histories([code for code, _ in candidates])

    rows = []
    for code, row in candidates:
        hist = hist_map.get(code)
        if hist is None or len(hist) < 35:
            continue
        close = hist["收盘"].dropna().values
        if len(close) < 35:
            continue
        ret30 = close[-1] / close[-22] - 1
        if ret30 >= Params.ret30_threshold:
            continue

        vol_recent = hist["成交量"].dropna().iloc[-5:].mean()
        vol_prev = hist["成交量"].dropna().iloc[-10:-5].mean()
        if vol_prev <= 0:
            continue
        vol_ratio = vol_recent / vol_prev
        if not np.isfinite(vol_ratio):   # 缺少成交量（如天天基金 NAV 历史）则无法判定放量
            continue
        if vol_ratio < Params.volume_ratio:
            continue

        ma20 = close[-20:].mean()
        ma10 = close[-10:].mean()
        rows.append({
            "theme": row["theme"],
            "code": code,
            "name": row["name"],
            "pct_change": row["pct_change"],
            "amount": row["amount"],
            "ret_30d": ret30,
            "vol_ratio": vol_ratio,
            "ma10": ma10,
            "ma20": ma20,
            "break_ma10": bool(close[-1] > ma10),
            "break_ma20": bool(close[-1] > ma20),
        })

    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows)
    result["突破强度"] = (
        result["pct_change"] * 0.4
        + (result["vol_ratio"] - 1) * 10 * 0.4
        + (1 / (result["ret_30d"] + 0.2)) * 2 * 0.2
    )
    return result.sort_values("突破强度", ascending=False)


def _meets_basic(row: pd.Series) -> bool:
    if pd.isna(row.get("pct_change")) or row.get("pct_change", 0) < Params.breakout_pct * 100:
        return False
    if pd.isna(row.get("amount")) or row.get("amount", 0) < Params.breakout_amount:
        return False
    return True


def temperature(today: pd.DataFrame, core: pd.DataFrame) -> pd.DataFrame:
    if today.empty and core.empty:
        return pd.DataFrame()

    today = today.copy()
    core = core.copy()

    # 今日风口分/核心风口分在上游（today_hotspot/core_hotspot）已各自归一化到 0-100，
    # 这里直接使用，不再二次归一化——保证温度计表的分数与下面两个表完全一致，
    # 0.4 / 0.6 权重直接作用于同一量纲的分数。

    today_score = today["今日风口分"] if "今日风口分" in today.columns else pd.Series(dtype=float, name="今日风口分")
    core_score = core["核心风口分"] if "核心风口分" in core.columns else pd.Series(dtype=float, name="核心风口分")
    joined = pd.concat([today_score, core_score], axis=1).fillna(0)
    # concat / fillna 会把整数列变回浮点，统一转回整数，避免显示成 60.0
    for col in ("今日风口分", "核心风口分"):
        if col in joined.columns:
            joined[col] = joined[col].fillna(0).round(0).astype(int)
    joined["原始分"] = joined.get("今日风口分", 0) * 0.4 + joined.get("核心风口分", 0) * 0.6
    max_score = joined["原始分"].max()
    if max_score <= 0:
        joined["温度计"] = 0
    else:
        joined["温度计"] = (joined["原始分"] / max_score * 100).round(0)
    joined["温度计"] = joined["温度计"].clip(0, 100).astype(int)
    return joined.sort_values("温度计", ascending=False)


# ============ 报告渲染 ============
def render_report(result: ScanResult) -> str:
    css = """
    <style>
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;padding:20px;color:#222;background:#f7f8fa}
    h1{font-size:20px;margin:0 0 5px} .meta{color:#888;font-size:12px;margin-bottom:20px}
    h2{font-size:16px;border-left:4px solid #1677ff;padding-left:8px;margin:24px 0 10px}
    table{width:100%;border-collapse:collapse;background:#fff;font-size:13px}
    th{background:#1677ff;color:#fff;text-align:left;padding:8px}
    td{padding:8px;border-bottom:1px solid #eee}
    tr:nth-child(even){background:#f2f6fc}
    .up{color:#e5484d;font-weight:bold} .down{color:#2ba471;font-weight:bold}
    .hot{display:inline-block;min-width:40px;text-align:center;color:#fff;border-radius:4px;padding:2px 5px}
    </style>
    """
    html = [f"<html><head><meta charset='utf-8'>{css}</head><body>"]
    html.append(f"<h1>ETF 题材风口扫描报告</h1>")
    meta = f"生成时间：{result.generated_at} ｜ 模式：{result.mode.upper()} ｜ 样本：{len(result.spot)} 只"
    if result.stale:
        meta += " ｜ <span style='color:#e5484d'>数据来自本地缓存，可能非最新</span>"
    html.append(f"<div class='meta'>{meta}</div>")

    html.append("<h2>一、题材温度计（Top 10）</h2>")
    html.append(_render_temperature(result.temperature.head(10)))

    html.append("<h2>二、今日风口</h2>")
    html.append(_render_today(result.today_rank.head(Params.top_n)))

    html.append("<h2>三、核心风口（30 日）</h2>")
    html.append(_render_core(result.core_rank.head(Params.top_n)))

    html.append("<h2>四、低位启动信号</h2>")
    html.append(_render_low_level(result.low_level.head(Params.top_n)))

    html.append("<p class='meta'>说明：数据为公开市场数据，存在延迟与口径差异；跨境 ETF 受溢价、额度和汇率影响，不构成投资建议。</p>")
    html.append("</body></html>")
    return "".join(html)


# ============ HTML 看板落盘 ============
def save_dashboard(html: str, result: ScanResult) -> Optional[str]:
    """将报告 HTML 写入 docs/index.html，每次运行覆盖为最新一份。"""
    try:
        os.makedirs(Params.docs_dir, exist_ok=True)
    except OSError as exc:
        logger.error("无法创建看板目录 %s：%s", Params.docs_dir, exc)
        return None

    latest_path = os.path.join(Params.docs_dir, Params.dashboard_file)
    try:
        with open(latest_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("HTML 看板已更新：%s", latest_path)
        return latest_path
    except OSError as exc:
        logger.error("HTML 看板写入失败：%s", exc)
        return None


def _render_temperature(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p>暂无数据</p>"
    df = df.rename_axis("题材").reset_index()
    return _table(
        df,
        ["题材", "今日风口分", "核心风口分", "温度计"],
        temp=lambda v: (
            lambda bg, fg: f'<span class="hot" style="background:{bg};color:{fg}">{v}</span>'
        )(*_heat_color(v)),
    )


def _render_today(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p>当前市场缺乏满足流动性要求的题材。</p>"
    df = df.rename_axis("题材").reset_index()
    # 上涨占比显示为整数百分比（如 100%、82%）
    df["上涨占比"] = (df["上涨占比"] * 100).round(0).astype(int).astype(str) + "%"
    return _table(
        df,
        ["题材", "ETF数量", "平均涨幅", "最大涨幅", "上涨占比", "总成交额", "今日风口分"],
        pct=lambda v: f"{v:+.2f}%",
        amount=lambda v: f"{v/1e8:.2f}亿",
    )


def _render_core(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p>暂未取得足够日线数据。</p>"
    df = df.rename_axis("题材").reset_index()
    df["平均30日涨幅"] = (df["平均30日涨幅"] * 100).round(2)
    df["最大涨幅"] = (df["最大涨幅"] * 100).round(2)
    # 量能比是比值，按行业惯例用倍数表示，并附放量/缩量状态（1.47倍 · 温和放量）
    ratio = df["平均量能比"]
    df["平均量能比"] = ratio.round(2).astype(str) + "倍 · " + ratio.apply(_vol_state)
    return _table(
        df,
        ["题材", "代表ETF数", "平均30日涨幅", "最大涨幅", "平均量能比", "核心风口分"],
        pct=lambda v: f"{v:+.2f}%",
    )


def _render_low_level(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p>当前未发现同时满足“30 日低位 + 放量突破”的标的。</p>"
    df = df.copy()
    df["pct_change"] = df["pct_change"].round(2)
    df["ret_30d"] = (df["ret_30d"] * 100).round(2)
    vratio = df["vol_ratio"]
    df["vol_ratio"] = vratio.round(2).astype(str) + "倍 · " + vratio.apply(_vol_state)
    df["突破强度"] = df["突破强度"].round(2)
    # 成交额带单位（亿）
    df["amount"] = (df["amount"] / 1e8).round(2).astype(str) + "亿"
    return _table(
        df,
        ["theme", "code", "name", "pct_change", "ret_30d", "vol_ratio", "amount", "突破强度"],
        rename={
            "theme": "题材", "code": "代码", "name": "名称", "pct_change": "今日涨幅%",
            "ret_30d": "30日涨幅%", "vol_ratio": "量能比", "amount": "成交额",
            "突破强度": "突破强度",
        },
        pct=lambda v: f"{v:+.2f}%",
    )


def _table(df: pd.DataFrame, columns: list[str], rename: Optional[dict] = None,
           pct=None, amount=None, temp=None) -> str:
    rename = rename or {}
    df = df[[c for c in columns if c in df.columns]].rename(columns=rename)
    html = ["<table><tr>"]
    headers = list(df.columns)
    for h in headers:
        html.append(f"<th>{h}</th>")
    html.append("</tr>")
    for _, row in df.iterrows():
        html.append("<tr>")
        for h in headers:
            value = row[h]
            if pct and ("涨幅" in h or "%" in str(h)):
                value = pct(value)
            if amount and "成交额" in h:
                value = amount(value)
            if temp and h == "温度计":
                value = temp(value)
            html.append(f"<td>{value}</td>")
        html.append("</tr>")
    html.append("</table>")
    return "".join(html)


def _vol_state(ratio) -> str:
    """量能比对应的放量/缩量状态文字。"""
    if ratio is None or pd.isna(ratio) or ratio <= 0:
        return ""
    if ratio < 0.7:
        return "明显缩量"
    if ratio < 0.9:
        return "温和缩量"
    if ratio <= 1.1:
        return "持平"
    if ratio < 1.5:
        return "温和放量"
    if ratio <= 3.0:
        return "显著放量"
    return "巨量异动"


def _heat_color(temp: float) -> tuple[str, str]:
    """返回 (背景色, 文字色)：全部色阶统一使用深色文字，保证徽章文字清晰可读。"""
    if temp >= 80:
        return "#e5484d", "#5c0f0f"
    if temp >= 60:
        return "#f0a14b", "#5c3a00"
    if temp >= 40:
        return "#f5d64a", "#5c4a00"
    if temp >= 20:
        return "#73a9d8", "#0d3b5c"
    return "#9aa5b1", "#2b2b2b"


# ============ 邮件发送 ============
# 常见邮箱 SMTP 预设（port / use_ssl）：EMAIL_CONFIG 只填 host 时也能自动匹配端口与加密方式
SMTP_PRESETS = {
    "smtp.qq.com": {"port": 465, "use_ssl": True},
    "smtp.163.com": {"port": 465, "use_ssl": True},
    "smtp.126.com": {"port": 465, "use_ssl": True},
    "smtp.gmail.com": {"port": 465, "use_ssl": True},
    "smtp.exmail.qq.com": {"port": 465, "use_ssl": True},
    "smtp.office365.com": {"port": 587, "use_ssl": False},
}


def _load_email_config() -> dict:
    """读取邮件配置：以脚本顶部 EMAIL_CONFIG 为准，环境变量可覆盖（便于部署/临时切换）。"""
    cfg: dict = dict(EMAIL_CONFIG)

    env_map = {
        "host": "SMTP_HOST", "port": "SMTP_PORT", "user": "SMTP_USER",
        "password": "SMTP_PASSWORD", "use_ssl": "SMTP_USE_SSL",
        "from": "EMAIL_FROM", "to": "EMAIL_TO",
    }
    for key, env in env_map.items():
        val = os.getenv(env)
        if val:
            cfg[key] = val
    return cfg


def send_email(subject: str, html: str, attachments: Optional[list[str]] = None) -> bool:
    cfg = _load_email_config()
    host = (cfg.get("host") or "").strip()
    if not host:
        logger.warning("未配置 SMTP 服务器（设置环境变量 SMTP_HOST 或脚本顶部 EMAIL_CONFIG['host']），跳过邮件发送")
        return False

    preset = SMTP_PRESETS.get(host, {})
    port = int(cfg.get("port") or preset.get("port") or 465)
    user = (cfg.get("user") or "").strip()
    password = cfg.get("password") or ""
    use_ssl = str(cfg.get("use_ssl") if cfg.get("use_ssl") is not None
                  else preset.get("use_ssl", True)).lower() == "true"
    from_addr = (cfg.get("from") or user).strip()

    to_raw = cfg.get("to") or ""
    if isinstance(to_raw, str):
        to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    else:
        to_addrs = [str(a).strip() for a in to_raw if str(a).strip()]

    if not (user and password and to_addrs):
        logger.error("邮件配置不完整：需要 user / password / to（收件人）")
        return False

    msg = MIMEMultipart("mixed")
    # 主题含中文必须用 Header 做 RFC2047 编码，否则 smtplib 按 ascii 编码会报错
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(html, "html", "utf-8"))

    for path in attachments or []:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment",
                            filename=("utf-8", "", os.path.basename(path)))
            msg.attach(part)
        except Exception as exc:
            logger.warning("附件 %s 读取失败：%s", path, exc)

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(user, password)
        server.sendmail(from_addr, to_addrs, msg.as_string())
        server.quit()
        logger.info("邮件已发送至 %s", to_addrs)
        return True
    except Exception as exc:
        logger.error("邮件发送失败：%s", exc)
        return False


# ============ 主流程 ============
def scan(mode: str, source: Optional[str] = None, offline: Optional[bool] = None) -> Optional[ScanResult]:
    global _OFFLINE
    _OFFLINE = bool(offline)
    logger.info("开始拉取全市场 ETF 行情（数据源：%s，离线：%s）",
                source or Params.prefer_source, _OFFLINE)
    spot = fetch_spot(source)
    if spot.empty:
        logger.error("行情数据为空")
        return None
    logger.info("获取到 %d 只 ETF%s", len(spot), "（本地缓存回退）" if _SPOT_STALE else "")

    today_rank = today_hotspot(spot)
    core_rank = core_hotspot(spot)
    low_level = low_level_breakout(spot)
    temp = temperature(today_rank, core_rank)

    return ScanResult(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        mode=mode,
        spot=spot,
        today_rank=today_rank,
        core_rank=core_rank,
        low_level=low_level,
        temperature=temp,
        stale=_SPOT_STALE,
    )


def run(mode: str, do_print: bool = False, source: Optional[str] = None, offline: Optional[bool] = None,
        no_dashboard: bool = False) -> int:
    result = scan(mode, source, offline)
    if result is None:
        return 1

    html = render_report(result)
    if do_print:
        print(html)
    dash_path = None
    if not no_dashboard:
        dash_path = save_dashboard(html, result)
    # 看板文件作为附件一并发送，收件人可直接打开完整报告
    attach = [dash_path] if dash_path else None
    if mode in {"intraday", "both"}:
        send_email(f"[盘中] ETF题材扫描 {result.generated_at}", html, attachments=attach)
    if mode in {"close", "both"}:
        send_email(f"[盘后] ETF题材扫描 {result.generated_at}", html, attachments=attach)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETF 题材风口扫描器")
    parser.add_argument("--mode", choices=["intraday", "close", "both"], default="close")
    parser.add_argument("--print", action="store_true", help="在终端打印完整 HTML 报告")
    parser.add_argument("--source", choices=["auto", "em", "sina", "tiantian", "tencent"],
                        default=Params.prefer_source,
                        help="行情数据源：auto=em→sina→天天基金→腾讯依次尝试，失败回退本地缓存")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式：完全不联网，仅使用本地缓存（需先联网运行过一次生成缓存）")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="不生成 HTML 看板文件（仅发送邮件/打印）")
    parser.add_argument("--test-email", action="store_true",
                        help="仅发送一封测试邮件验证邮箱配置，不扫描行情")
    args = parser.parse_args()

    if args.test_email:
        ok = send_email(
            "[测试] ETF题材扫描器 邮箱配置验证",
            "<html><body><h3>ETF 题材风口扫描器</h3>"
            "<p>邮箱配置验证成功，后续扫描报告将发送至本邮箱。</p></body></html>",
        )
        sys.exit(0 if ok else 1)

    sys.exit(run(args.mode, args.print, args.source, args.offline, args.no_dashboard))
