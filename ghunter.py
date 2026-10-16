#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ghunter - GitHub 全量猎取引擎  (2026-10-16 重写版)

解决过去 "GitHub 搜不全 / 搜不到我想不到的东西" 的根因：
  1. 单条 query 只覆盖一个说法 -> 本引擎对同一个意图自动扇出 N 条变体（中/英/黑话/拼写/近义）
  2. 只搜 repo        -> 本引擎同时跑 repo / code / topic / 组合过滤，并去重合并
  3. 只看 star 排序   -> 本引擎对 stars / updated / best-match 三种排序各取一轮，再按活跃度重排
  4. 没有验证          -> 本引擎抓 README + 文件树，判断"能不能真跑起来"，给出可用性分级
  5. 每次重头来        -> 结果落盘 JSON 缓存，可增量累积

用法：
  python ghunter.py --intent "抖音/TikTok 全自动发布与热点抓取" 
  python ghunter.py --topic "davinci resolve api" --deep
  python ghunter.py --recon            # 跑内置全域侦察（媒体/热榜/LLM/OSINT/外贸/电话卡/红队/审美）
"""
import argparse, json, os, sys, time, hashlib, re, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache")
OUT = os.path.join(ROOT, "out")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

TOKEN_CANDIDATES = [
    r"C:/Users/qq/.workbuddy/skills/github-upload-via-api/pw/gh_token.txt",
    os.path.expanduser("~/.gh_token"),
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ghunter/2.0 (+local-research-agent)"
API = "https://api.github.com"


def _load_token():
    for p in TOKEN_CANDIDATES:
        if os.path.exists(p):
            t = open(p, "r", encoding="utf-8", errors="ignore").read().strip()
            if t:
                return t
    return None


TOKEN = _load_token()


def _req(path, raw=False, timeout=30, accept="application/vnd.github+json"):
    """带重试的 GitHub API 请求。返回 (status, body_str_or_None, headers)"""
    if path.startswith("http"):
        url = path
    else:
        url = API + path
    hdr = {"User-Agent": UA, "Accept": accept}
    if TOKEN:
        hdr["Authorization"] = "Bearer " + TOKEN
    last = None
    for attempt in range(3):
        try:
            rq = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(rq, timeout=timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
                return r.status, body, dict(r.headers)
        except urllib.error.HTTPError as e:
            last = e
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code in (403, 429):
                # secondary rate limit / abuse -> 退避
                time.sleep(2.5 * (attempt + 1))
                continue
            if e.code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            return e.code, body, dict(e.headers or {})
        except Exception as e:
            last = e
            time.sleep(1.2 * (attempt + 1))
    return 0, (str(last) if last else ""), {}


# ---------------- 查询变体展开：搜不全的头号原因就是"说法太单一" ----------------

SYN = {
    "抖音": ["douyin", "tiktok", "Dy", "aweme", "douyin-downloader", "douyin-bot"],
    "tiktok": ["tiktok-api", "TikTokPy", "tiktok-scraper", "tiktok-bot"],
    "快手": ["kuaishou", "kuaishou-scraper", "ks", "kwai"],
    "微博": ["weibo", "weibo-crawler", "weibo-spider", "weibo-hot-search"],
    "知乎": ["zhihu", "zhihu-hot", "zhihu-crawler", "zhihuDaily"],
    "热榜": ["hot-search", "hotlist", "hots easing", "trending", "hot-board", "热榜聚合", "hotsearch", "toutiao-hot"],
    "小红书": ["xiaohongshu", "rednote", "xhs", "xiaohongshu-crawler"],
    "哔哩哔哩": ["bilibili", "bilibili-api", "bili", "bilibili-downloader"],
    "photoshop": ["photoshop-scripting", "photoshop-api", "jsx", "ExtendScript", "CEP", "UXP", "ps-automation"],
    "premiere": ["premiere-pro", "premiere-script", "pr-automation", "extendscript"],
    "after effects": ["after-effects", "ae-script", "aep", "aescripts", "jsx"],
    "达芬奇": ["davinci-resolve", "resolve-api", "DaVinciResolveScript", "davinci-resolve-api", "resolve-python"],
    "剪映": ["jianying", "capcut-automation", "capcut"],
    "免费大模型": ["free-llm-api", "free-api", "llm-proxy", "one-api", "new-api", "gpt-free", "free-gpt", "unlimited-gpt"],
    "api网关": ["api-gateway", "openai-proxy", "llm-router", "api-relay", "reverse-proxy"],
    "osint": ["osint", "osint-framework", "people-search", "person-lookup", "identity-resolution", "phone-lookup", "email-osint"],
    "查人": ["dox", "people-finder", "username-enumeration", "sherlock", "social-analyzer", "holehe", "maigret"],
    "外贸": ["cross-border-ecommerce", "shopify-tool", "dropshipping", "amazon-scraper", "alibaba-scraper", "trade-tool", "export-business", "外贸"],
    "跨境电商": ["shopify", "woocommerce-scraper", "ebay-api", "etsy-scraper", "temu-scraper", "aliexpress-api"],
    "电话卡": ["sim-card", "esim", "virtual-number", "sms-activate", "phone-number-api", "voip"],
    "推广返利": ["affiliate-platform", "referral-system", "reseller-panel", "cpa-platform", "联盟营销", "affiliate-network"],
    "渗透": ["pentest", "red-team", "attack-surface", "payloads", "exploit-db", "cve-poc"],
    "绕过": ["bypass", "waf-bypass", "anti-bot-bypass", "detection-evasion", "mitm-bypass", "cloudflare-bypass"],
    "审美": ["design-system", "frontend-design", "ui-kit", "design-tokens", "web-design-guidelines", "aesthetic", "color-palette"],
    "多智能体": ["multi-agent", "agent-orchestration", "autogen", "crewai", "agent-swarm", "llm-swarm", "agent-bus"],
    "浏览器自动化": ["playwright", "puppeteer", "cdp", "chrome-devtools-protocol", "browser-use", "selenium-cdp"],
    "漫剧": ["comic-video", "motion-comic", "ai-story-video", "automatic-video-generation", "short-video-automation", "text-to-video-pipeline"],
    "数据抓取": ["crawler", "scraper", "harvester", "collector", "spider"],
    "图集注册": ["bulk-account", "account-generator", "phone-verification", "temp-mail", "catch-all-email"],
}

STOP_HINT = [" viral ", "awesome-list-only"]


def expand_intent(intent, maxv=26):
    """把一句大白话炸成多条互补 query。这是'搜得到你没想到的东西'的核心。"""
    buckets = set()
    base = intent.strip()
    buckets.add(base)
    # 中英/黑话替换
    for zh, ens in SYN.items():
        if zh in intent or zh.lower() in intent.lower():
            for e in ens:
                buckets.add(intent.replace(zh, e))
                buckets.add(e)
    low = intent.lower()
    for kw in list(SYN.keys()):
        if kw.lower() in low or kw in low:
            for e in SYN[kw]:
                buckets.add(e)
    # 通用强检索模板（往往能捞出没听说过的项目）
    templates = [
        "{} python",
        "{} automation",
        "{} api",
        "{} open source",
        "{} tool",
        "{} framework",
        "{} script",
        "{} 2026",
    ]
    core = re.sub(r"[，,。;；、/]+", " ", intent)
    core = " ".join(core.split()[:4])
    for t in templates:
        buckets.add(t.format(core))
    for e in ["awesome-{}".format(core), "{} alternatives".format(core), "self-hosted {}".format(core)]:
        buckets.add(e)
    out = []
    seen = set()
    for b in buckets:
        b = " ".join(b.split())
        if not b or len(b) > 90:
            continue
        k = b.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(b)
    return out[:maxv]


# ---------------- 三种搜索通道 ----------------

def search_repos(q, per=15, sort=""):
    p = "/search/repositories?per_page=%d&q=%s" % (per, urllib.parse.quote(q))
    if sort:
        p += "&sort=" + sort
    st, body, _ = _req(p)
    if st != 200:
        return []
    try:
        return json.loads(body).get("items", [])
    except Exception:
        return []


def search_code(q, per=10):
    p = "/search/code?per_page=%d&q=%s" % (per, urllib.parse.quote(q))
    st, body, _ = _req(p)
    if st != 200:
        return []
    try:
        return json.loads(body).get("items", [])
    except Exception:
        return []


def topic_repos(topic, per=15):
    return search_repos("topic:%s" % topic, per, sort="stars")


# ---------------- 可用性验证（关键：别再拿一堆跑不起来的东西糊弄） ----------------

def repo_meta(full_name):
    st, body, _ = _req("/repos/" + full_name)
    if st != 200:
        return None
    try:
        d = json.loads(body)
    except Exception:
        return None
    return {
        "full_name": d.get("full_name"),
        "stars": d.get("stargazers_count", 0),
        "forks": d.get("forks_count", 0),
        "open_issues": d.get("open_issues_count", 0),
        "lang": d.get("language"),
        "license": (d.get("license") or {}).get("spdx_id"),
        "pushed_at": d.get("pushed_at"),
        "size_kb": d.get("size", 0),
        "desc": d.get("description"),
        "archived": d.get("archived"),
        "topics": d.get("topics", []),
        "default_branch": d.get("default_branch", "main"),
    }


def repo_tree(full_name, branch="main", limit=400):
    st, body, _ = _req("/repos/%s/git/trees/%s?recursive=1" % (full_name, branch))
    if st != 200:
        return []
    try:
        d = json.loads(body)
    except Exception:
        return []
    return [t["path"] for t in d.get("tree", []) if t.get("type") == "blob"][:limit]


READMES = ["README.md", "readme.md", "README.MD", "README.rst", "README", "readme.txt"]


def repo_readme(full_name, tree=None):
    if tree is None:
        tree = repo_tree(full_name)
    cand = [p for p in tree if os.path.basename(p) in READMES and p.count("/") <= 1]
    if not cand:
        return ""
    cand.sort(key=lambda p: (p.count("/"), len(p)))
    for c in cand[:2]:
        st, body, _ = _req("https://raw.githubusercontent.com/%s/HEAD/%s" % (full_name, c), accept="text/plain")
        if st == 200 and body:
            return body[:14000]
    return ""


INSTALL_HINT = [
    "pip install", "npm install", "uv pip", "pnpm i", "yarn add", "docker-compose", "docker run",
    "cargo build", "go install", "python -m", "make install", "pnpm install", "poetry install",
]
RUN_HINT = ["python ", "node ", "uv run", "docker compose", "npm run", "streamlit run", "uvicorn"]
ENTRY_EXT = {".py": 1.0, ".js": 0.9, ".ts": 0.9, ".jsx": 0.6, ".jsx": 0.6, ".sh": 0.5, ".ps1": 0.5}


def usability_score(meta, tree, readme):
    """返回 (分级, 分数, 理由)。A=可直接落地 B=要改 C=只可参考"""
    if not meta:
        return "C", 0, ["取不到元信息"]
    score = 0.0
    why = []
    # 活跃度
    try:
        pushed = meta.get("pushed_at") or ""
        yr = int(pushed[:4])
        mo = int(pushed[5:7])
        age_month = (2026 - yr) * 12 + (10 - mo)
        if age_month <= 6:
            score += 3
            why.append("近半年有提交")
        elif age_month <= 24:
            score += 1.5
        elif age_month > 60:
            score -= 2
            why.append("超过5年没动")
    except Exception:
        pass
    if meta.get("stars", 0) >= 1000:
        score += 2.5; why.append("star>=1000")
    elif meta.get("stars", 0) >= 100:
        score += 1.5
    if meta.get("archived"):
        score -= 3; why.append("已归档")
    if meta.get("license") not in (None, "NOASSERTION"):
        score += 0.5
    # 结构：有明确的依赖清单/入口
    low = [t.lower() for t in tree]
    deps = [p for p in low if os.path.basename(p) in
            ("requirements.txt", "pyproject.toml", "package.json", "cargo.toml", "go.mod", "environment.yml", "setup.py")]
    if deps:
        score += 1.5; why.append("有依赖清单:" + os.path.basename(deps[0]))
    ent = [p for p in low if os.path.splitext(p)[1] in ENTRY_EXT and p.count("/") <= 1]
    if ent:
        score += 1.0; why.append("有根目录入口")
    if any(p.lower() in ("dockerfile", "docker-compose.yml", "compose.yaml") for p in low):
        score += 1.0; why.append("可容器化")
    if readme:
        rl = readme.lower()
        if any(h in rl for h in INSTALL_HINT):
            score += 1.5; why.append("README 有安装命令")
        if any(h in rl for h in RUN_HINT):
            score += 1.0; why.append("README 有运行命令")
        if len(readme) > 2500:
            score += 0.5
    else:
        score -= 1
    grade = "A" if score >= 7 else ("B" if score >= 4 else "C")
    return grade, round(score, 2), why


# ---------------- 主流程 ----------------

_cache_index = {}


def _cache_path(key):
    return os.path.join(CACHE, hashlib.md5(key.encode("utf-8")).hexdigest() + ".json")


def cached(key, fn, ttl_h=72):
    p = _cache_path(key)
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < ttl_h * 3600:
        try:
            return json.load(open(p, "r", encoding="utf-8"))
        except Exception:
            pass
    v = fn()
    try:
        json.dump(v, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return v


def hunt(intent, per=12, sorts=("", "stars", "updated"), deep=True, workers=8):
    queries = expand_intent(intent)
    raw = {}
    print("[i] 展开 %d 条查询变体" % len(queries), file=sys.stderr)

    def _one(q):
        res = []
        for s in sorts:
            res.extend(search_repos(q, per, s))
        return res

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(cached, "s|" + q, lambda q=q: _one(q)): q for q in queries}
        for f in as_completed(futs):
            q = futs[f]
            try:
                items = f.result() or []
            except Exception:
                items = []
            for it in items:
                fn = it.get("full_name")
                if not fn or fn in raw:
                    continue
                raw[fn] = it

    print("[i] 初筛去重后 %d 个仓库，开始验证可用性…" % len(raw), file=sys.stderr)

    def enrich(fn):
        m = cached("m|" + fn, lambda: repo_meta(fn), ttl_h=48)
        if not m:
            return None
        tr = cached("t|" + fn, lambda: repo_tree(fn, m.get("default_branch", "main")), ttl_h=48) if deep else []
        rd = cached("r|" + fn, lambda: repo_readme(fn, tr), ttl_h=48) if deep else ""
        g, sc, why = usability_score(m, tr, rd)
        return {
            "full_name": fn,
            "url": m.get("html_url") or ("https://github.com/" + fn),
            "stars": m.get("stars", 0),
            "lang": m.get("lang"),
            "license": m.get("license"),
            "pushed_at": m.get("pushed_at"),
            "desc": (m.get("desc") or "")[:300],
            "topics": (m.get("topics") or [])[:8],
            "grade": g,
            "score": sc,
            "why": why,
            "entry": [p for p in (tr or []) if p.count("/") <= 1 and os.path.splitext(p)[1] in ENTRY_EXT][:6],
            "readme_head": (rd or "")[:1600],
        }

    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(enrich, list(raw.keys())):
            if r:
                out.append(r)
    order = {"A": 0, "B": 1, "C": 2}
    out.sort(key=lambda x: (order[x["grade"]], -x["score"], -x["stars"]))
    return {"intent": intent, "queries": queries, "count": len(out), "items": out}


RECON = [
    ("media-control", "photoshop premiere after effects davinci resolve scripting automation API"),
    ("aesthetic-design", "frontend design system aesthetic ui guidelines color palette"),
    ("hotboard-video", "douyin tiktok kuaishou weibo zhihu 热榜 hot search trending aggregator crawler"),
    ("free-llm-gateway", "free llm api proxy gateway unlimited token relay router"),
    ("osint-people", "osint people lookup username enumeration phone email identity resolution"),
    ("foreign-trade", "跨境电商 外贸 shopify amazon aliexpress dropshipping scraper tool"),
    ("sim-affiliate", "sim card esim affiliate platform referral reseller panel virtual number"),
    ("redteam-bypass", "pentest bypass payload poc exploitation toolkit"),
    ("multi-agent", "multi agent orchestration swarm bus collaboration framework"),
    ("comic-video", "漫剧 短视频 automatic comic story video generation pipeline"),
]


def recon_all():
    allr = {}
    for name, intent in RECON:
        print("\n===== [%s] %s =====" % (name, intent), file=sys.stderr)
        r = hunt(intent, per=12, workers=8)
        allr[name] = r
        print("    %d 个结果，A级 %d / B级 %d" % (
            r["count"],
            sum(1 for i in r["items"] if i["grade"] == "A"),
            sum(1 for i in r["items"] if i["grade"] == "B")), file=sys.stderr)
    ts = time.strftime("%Y%m%d-%H%M%S")
    jp = os.path.join(OUT, "recon_%s.json" % ts)
    json.dump(allr, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    mp = os.path.join(OUT, "recon_%s.md" % ts)
    with open(mp, "w", encoding="utf-8") as f:
        f.write("# GitHub 全域侦察报告\n\n> %s · token=%s\n\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), "已认证" if TOKEN else "匿名"))
        for name, r in allr.items():
            f.write("\n## %s\n\n> 意图：%s\n> 命中 %d，A级 %d\n\n" % (
                name, r["intent"], r["count"], sum(1 for i in r["items"] if i["grade"] == "A")))
            for i in r["items"][:18]:
                f.write("- **[%s]** [%s](%s) ★%s · %s · %s\n  - %s\n  - 可用性: %s(%s) %s\n" % (
                    i["grade"], i["full_name"], i["url"], i["stars"], i["lang"] or "-",
                    (i["pushed_at"] or "")[:10], (i["desc"] or "")[:180],
                    i["grade"], i["score"], "; ".join(i["why"][:4])))
    print("\n[✓] JSON: %s\n[✓] MD  : %s" % (jp, mp), file=sys.stderr)
    return jp, mp


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--intent")
    ap.add_argument("--topic")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--per", type=int, default=12)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.recon:
        recon_all()
        sys.exit(0)
    q = a.intent or a.topic
    if not q:
        print(__doc__)
        sys.exit(1)
    r = hunt(q, per=a.per)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print("\n" + "=" * 70)
        print("意图: %s  |  查询变体 %d 条  |  命中 %d" % (r["intent"], len(r["queries"]), r["count"]))
        print("=" * 70)
        for i in r["items"][:a.top]:
            print("\n[%s] %s  ★%s  %s  push:%s" % (
                i["grade"], i["full_name"], i["stars"], i["lang"], (i["pushed_at"] or "")[:10]))
            print("   %s" % i["url"])
            print("   %s" % (i["desc"] or "")[:200])
            print("   可用性%s(%s): %s" % (i["grade"], i["score"], "; ".join(i["why"][:5])))
