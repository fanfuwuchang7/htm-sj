import random
import string
from datetime import datetime

DOMAIN_SUFFIX = ".v642.com"
LINK_COUNT = 100
OUTPUT_FILE = "index.html"


def random_subdomain(length=5):
    return "".join(random.choices(string.ascii_lowercase, k=length))


def generate_links(n):
    links = set()
    while len(links) < n:
        links.add(f"http://{random_subdomain()}{DOMAIN_SUFFIX}")
    return sorted(links)


def build_html(links):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    link_items = "\n".join(
        f'''
        <div class="card">
            <span class="num">{i+1}</span>
            <a class="link" href="{link}" target="_blank" rel="nofollow noreferrer">
                {link}
            </a>
        </div>
        '''
        for i, link in enumerate(links)
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>随机链接列表 - v642 域名集合</title>
<meta name="description" content="自动生成的随机子域名链接列表，共 {len(links)} 条，适合测试与索引。">
<meta name="keywords" content="v642, 随机链接, 子域名, SEO, 静态生成">
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
        共生成 {len(links)} 条链接 · 更新时间 {now}
    </div>
    <div class="grid">
        {link_items}
    </div>
    <footer>
        Auto generated · SEO Static HTML · v642.com
    </footer>
</div>
</body>
</html>
"""


def main():
    links = generate_links(LINK_COUNT)
    html = build_html(links)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Generated {OUTPUT_FILE} with {len(links)} links")


if __name__ == "__main__":
    main()