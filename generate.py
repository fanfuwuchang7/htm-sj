#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
随机链接静态生成器（SEO 版）
------------------------------------------------
- 生成纯静态 HTML，所有链接以 <a href> 暴露在源码中，便于搜索引擎抓取
- 输出到 dot/ 文件夹（dot/index.html）
- 使用 Set 去重，保证每条链接唯一
- 仅依赖 Python 标准库
"""

import os
import random
import string
from datetime import datetime

# ============ 配置区（按需修改） ============
DOMAIN_SUFFIX = ".v642.com"
SUBDOMAIN_LEN = 5          # 随机子域名字母位数
LINK_COUNT = 100           # 生成链接数量

OUTPUT_DIR = "dot"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "index.html")

# SEO 相关
SITE_TITLE = "随机链接列表 - v642 域名集合"
SITE_DESCRIPTION = "自动生成的随机子域名链接列表，共 {count} 条，适合测试与索引。"
SITE_KEYWORDS = "v642, 随机链接, 子域名, SEO, 静态生成"
# ============================================


def random_subdomain(length: int = SUBDOMAIN_LEN) -> str:
    """生成指定长度的随机小写字母子域名。"""
    return "".join(random.choices(string.ascii_lowercase, k=length))


def generate_links(n: int) -> list:
    """使用 Set 去重，生成 n 条唯一链接并排序返回。"""
    links = set()
    while len(links) < n:
        links.add(f"http://{random_subdomain()}{DOMAIN_SUFFIX}")
    return sorted(links)


def build_html(links: list, generated_at: str) -> str:
    """根据链接列表拼装完整静态 HTML。"""
    link_items = "\n".join(
        f'''        <div class="card">
            <span class="num">{i + 1}</span>
            <a class="link" href="{link}" target="_blank" rel="nofollow noreferrer">
                {link}
            </a>
        </div>'''
        for i, link in enumerate(links)
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{SITE_TITLE}</title>
<meta name="description" content="{SITE_DESCRIPTION.format(count=len(links))}">
<meta name="keywords" content="{SITE_KEYWORDS}">
<style>
body {{
    margin: 0;
    font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    background: linear-gradient(135deg, #0f172a, #0c4a6e);
    color: #e0f2fe;
}}
.container {{
    max-width: 1200px;
    margin: auto;
    padding: 2rem;
}}
h1 {{
    text-align: center;
    background: linear-gradient(90deg, #38bdf8, #22d3ee);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
}}
.meta {{
    text-align: center;
    opacity: 0.8;
    margin-bottom: 2rem;
}}
.grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1rem;
}}
.card {{
    background: rgba(255,255,255,0.05);
    padding: 0.8rem 1rem;
    border-radius: 12px;
    display: flex;
    align-items: center;
    gap: 0.8rem;
}}
.num {{
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: #38bdf8;
    color: #0f172a;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.85rem;
    font-weight: bold;
    flex-shrink: 0;
}}
.link {{
    color: #7dd3fc;
    text-decoration: none;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.link:hover {{
    text-decoration: underline;
}}
footer {{
    text-align: center;
    margin-top: 3rem;
    opacity: 0.6;
    font-size: 0.85rem;
}}
</style>
</head>
<body>
<div class="container">
    <h1>随机链接索引</h1>
    <div class="meta">
        共生成 {len(links)} 条链接 · 更新时间 {generated_at}
    </div>
    <div class="grid">
{link_items}
    </div>
    <footer>
        Auto generated · SEO Static HTML · {DOMAIN_SUFFIX.strip('.')}
    </footer>
</div>
</body>
</html>
"""


def main():
    random.seed()  # 保证每次运行结果不同
    links = generate_links(LINK_COUNT)
    html = build_html(links, datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Generated {OUTPUT_FILE} with {len(links)} unique links")


if __name__ == "__main__":
    main()
