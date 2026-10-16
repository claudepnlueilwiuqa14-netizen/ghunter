#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ghunter.radar —— 持续灵感雷达（GitHub 全域巡检 + 意外发现）

解决的是这句原话：
  「你 GitHub 都搜索不完全 … 不能替我想到很多我之前不了解的提前用上 …
    不能帮我每一步都思考都检索全部 GitHub 超级聪明剑走偏锋，彻底解决」

四个真手段（都不是"多搜几个关键词"这种表面功夫）：
  1. **兴趣图 BFS**：从用户目标种子出发 → 仓库 → 该仓库的 topics → 新 topics → 新仓库。
     顺着 topic 图走到用户**从没提过**的邻域。
  2. **跨域桥接**：同一仓库出现在多个不同域的检索结果里 = 稀有交叉点，优先看。
  3. **隐藏宝石**：可用性 A/B 级 但 star < 300 —— 好东西没人知道的那一类。
  4. **新发现差分**：与 seen 库对比，只推"第一次见到"的，噪音归零。

用法：
  python radar.py once                跑一轮巡检，出新发现报告
  python radar.py daemon --hours 2    常驻，每 2 小时巡检一轮
  python radar.py report              看最近一轮报告
"""
import argparse, json, os, sys, time, hashlib, random
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ghunter as G

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN = os.path.join(HERE, "radar_seen.json")
FEED = os.path.join(HERE, "radar_feed.jsonl")
BUS = r"C:/Users/qq/SharedKB/_bus"

# 用户的真实目标 → 种子域（这些是"用户已经知道的"）
# ⚠️ intent 保持 3-5 个短词：GitHub repo 搜索是 AND 语义，长句必 0 结果；
#    expand_intent() 会再炸成变体，这里只给种子。
SEED_DOMAINS = [
    # —— 原有 12 域（用户明确提出过的方向）——
    ("媒体控制", "photoshop premiere after effects davinci resolve scripting api automation"),
    ("审美设计", "frontend design system aesthetic ui guidelines color palette tokens"),
    ("热点短视频", "douyin tiktok kuaishou weibo zhihu hot search trending crawler"),
    ("免费大模型", "free llm api gateway proxy unlimited token relay router"),
    ("查人定位", "osint people lookup username enumeration phone email identity"),
    ("外贸", "cross border ecommerce shopify amazon aliexpress dropshipping scraper"),
    ("电话卡分销", "esim sim card reseller affiliate referral platform white label"),
    ("渗透绕过", "pentest bypass payload poc exploitation anti bot"),
    ("多智能体", "multi agent orchestration swarm bus collaboration"),
    ("漫剧视频", "motion comic ai story short video automatic generation"),
    ("全栈工程", "self hosted web app full stack deployment database"),
    ("自动化运维", "automation scheduler windows linux background daemon task"),
    # —— 2026-10-16 扩域 25 个（8 大方向全覆盖 + 工具层 + 邻域）——
    ("逆向工程", "reverse engineering binary decompile disassembler analysis"),
    ("游戏安全", "game hack memory esp aimbot anti cheat"),
    ("爬虫框架", "web scraper playwright undetected browser stealth"),
    ("爬虫对抗", "anti bot captcha solver fingerprint evasion"),
    ("数据可视化", "data visualization dashboard chart d3 analytics"),
    ("机器学习", "machine learning pytorch fine tune training"),
    ("本地大模型", "local llm inference ollama vllm quantization gguf"),
    ("数据库内核", "database engine sqlite postgres internals orm"),
    ("网络协议", "network protocol packet analysis proxy tunnel"),
    ("编译原理", "compiler interpreter llvm bytecode vm assembler"),
    ("容器云原生", "kubernetes docker container devops helm"),
    ("安全扫描", "security scanner nuclei vulnerability recon"),
    ("区块链Web3", "blockchain smart contract solidity web3 defi"),
    ("移动开发", "android ios react native flutter cross platform"),
    ("浏览器扩展", "browser extension chrome firefox addon manifest"),
    ("桌面应用", "desktop app electron tauri rust gui"),
    ("游戏开发", "game engine unity godot indie 2d 3d"),
    ("嵌入式IoT", "embedded iot firmware stm32 esp32 rtos"),
    ("音视频处理", "audio video ffmpeg transcoding streaming"),
    ("文档处理", "document pdf ocr parsing converter extract"),
    ("邮件营销", "email marketing smtp deliverability campaign automation"),
    ("支付集成", "payment gateway stripe integration billing"),
    ("建站部署", "static site generator deploy cdn hosting"),
    ("监控告警", "monitoring alerting observability metrics tracing"),
    ("知识管理", "knowledge base obsidian note taking second brain"),
]


def load_seen():
    if os.path.exists(SEEN):
        try:
            return set(json.load(open(SEEN, encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(s):
    json.dump(sorted(s), open(SEEN, "w", encoding="utf-8"), ensure_ascii=False)


def _repo_topics(full_name, limit=8):
    """拿仓库 topics，用于兴趣图 BFS 的下一步。"""
    st, body, _ = G._req("/repos/" + full_name)
    if st != 200:
        return []
    try:
        return [t for t in (json.loads(body).get("topics") or [])][:limit]
    except Exception:
        return []


def _search_topic(topic, per=8):
    """topic 页搜索（比关键词搜索更容易捞到没听说过的项目）。"""
    items = G.search_repos("topic:%s" % topic, per, sort="updated")
    return [i.get("full_name") for i in items if i.get("full_name")]


def bfs_domain(name, intent, depth=1, per=6, maxv=6):
    """兴趣图 BFS：intent -> repos -> topics -> new repos

    ⚠️ GitHub repo 搜索是 AND 语义：整句长查询会返回 0 条。
    必须先炸成短查询变体再搜（这就是"搜不全"的根因之一）。
    """
    found = {}
    frontier = set(G.expand_intent(intent, maxv=maxv))
    visited = set()
    for d in range(depth):
        nxt = set()
        def one(q):
            out = []
            for s in ("", "updated"):
                out.extend(G.search_repos(q, per, s))
            return [i.get("full_name") for i in out if i.get("full_name")]
        with ThreadPoolExecutor(6) as ex:
            futs = [ex.submit(G.cached, "r|" + q, (lambda qq: lambda: one(qq))(q), ttl_h=24)
                    for q in list(frontier)]
            for f in as_completed(futs):
                for fn in (f.result() or []):
                    found.setdefault(fn, {"from": name, "depth": d})
        # 从本轮找到的仓库抽 topics
        sample = random.sample(list(found.keys()), min(8, len(found)))
        with ThreadPoolExecutor(8) as ex:
            for tps in ex.map(lambda fn: _repo_topics(fn), sample):
                for t in tps:
                    if t not in visited and len(t) >= 3:
                        visited.add(t)
                        nxt.add("topic:%s" % t)
        frontier = nxt - visited
        if not frontier:
            break
        if len(frontier) > 8:
            frontier = set(random.sample(list(frontier), 8))
    return found


def cached_names(q, fn):
    return G.cached("r|" + q, fn, ttl_h=24)


def cross_bridge(all_results):
    """同一仓库出现在多个域 = 稀有交叉点。"""
    from collections import defaultdict
    appear = defaultdict(set)
    for dom, repos in all_results.items():
        for fn in repos:
            appear[fn].add(dom)
    return {fn: sorted(doms) for fn, doms in appear.items() if len(doms) >= 2}


def run_once(top=25, depth=1, maxv=6, per=6, domains=None):
    seen = load_seen()
    doms = domains or [d[0] for d in SEED_DOMAINS]
    intents = {n: i for n, i in SEED_DOMAINS}
    all_results = {}
    for name in doms:
        print("[i] 巡检域：%s" % name, file=sys.stderr)
        all_results[name] = bfs_domain(name, intents[name], depth=depth, per=per, maxv=maxv)
        print("    命中 %d 个仓库" % len(all_results[name]), file=sys.stderr)

    bridges = cross_bridge(all_results)
    print("[i] 跨域交叉点 %d 个" % len(bridges), file=sys.stderr)

    # 汇总 + 验证 + 差分
    cand = {}
    for dom, repos in all_results.items():
        for fn, meta in repos.items():
            cand.setdefault(fn, {"domains": set(), "depth": meta["depth"]})
            cand[fn]["domains"].add(dom)
    # 交叉点优先
    cand = {fn: v for fn, v in cand.items() if len(v["domains"]) >= 1}
    ordered = sorted(cand.items(), key=lambda kv: (-len(kv[1]["domains"]), kv[1]["depth"]))
    ordered = ordered[:120]

    def enrich(item):
        fn, v = item
        m = G.cached("m|" + fn, lambda: G.repo_meta(fn), ttl_h=48)
        if not m:
            return None
        tr = G.cached("t|" + fn, lambda: G.repo_tree(fn, m.get("default_branch", "main")), ttl_h=48)
        g, sc, why = G.usability_score(m, tr, "")
        return {"full_name": fn, "domains": sorted(v["domains"]), "depth": v["depth"],
                "stars": m.get("stars", 0), "lang": m.get("lang"),
                "pushed_at": m.get("pushed_at"), "desc": (m.get("desc") or "")[:220],
                "grade": g, "score": sc, "why": why[:4],
                "url": "https://github.com/" + fn}

    print("[i] 验证 %d 个候选…" % len(ordered), file=sys.stderr)
    out = []
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(enrich, ordered):
            if r:
                out.append(r)

    new = [r for r in out if r["full_name"] not in seen]
    hidden = [r for r in out if r["grade"] in ("A", "B") and r["stars"] < 300]
    # 排序：交叉域多 > 隐藏宝石 > 可用性
    def rank(r):
        return (-len(r["domains"]), 1 if (r["grade"] in "AB" and r["stars"] < 300) else 0,
                -r["score"], -r["stars"])
    out.sort(key=rank)
    new.sort(key=rank)

    seen.update(r["full_name"] for r in out)
    save_seen(seen)

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    rep = {"ts": ts, "domains": {d: len(v) for d, v in all_results.items()},
           "cross_bridges": len(bridges), "total": len(out), "new": len(new),
           "hidden_gems": len(hidden), "top": out[:top], "new_top": new[:top]}
    jp = os.path.join(HERE, "radar_%s.json" % ts.replace(":", "").replace("-", "").replace(" ", "_"))
    json.dump(rep, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    with open(os.path.join(HERE, "radar_report.md"), "w", encoding="utf-8") as f:
        f.write("# 灵感雷达 · %s\n\n" % ts)
        f.write("- 巡检域 %d 个，命中仓库 %d，跨域交叉点 %d，隐藏宝石 %d\n"
                % (len(all_results), len(out), len(bridges), len(hidden)))
        f.write("- **本轮新发现 %d 个**（已排除历史见过的）\n\n" % len(new))
        f.write("## 一、新发现（第一次见到）\n\n")
        for r in new[:top]:
            f.write("- **[%s]** [%s](%s) ★%d · %s\n  - 域：%s\n  - %s\n" % (
                r["grade"], r["full_name"], r["url"], r["stars"], r["lang"] or "-",
                "、".join(r["domains"]), r["desc"]))
        f.write("\n## 二、隐藏宝石（A/B 级但 star<300，好东西没人知道）\n\n")
        for r in hidden[:top]:
            f.write("- **[%s]** [%s](%s) ★%d · %s\n  - 域：%s\n  - %s\n" % (
                r["grade"], r["full_name"], r["url"], r["stars"], r["lang"] or "-",
                "、".join(r["domains"]), r["desc"]))
        f.write("\n## 三、跨域交叉点（一个项目同时踩中多个域，最可能有意外用法）\n\n")
        for fn, doms in list(bridges.items())[:20]:
            f.write("- `%s` ← %s\n" % (fn, "、".join(doms)))

    # 把新发现写进总线，让其他端看到
    try:
        lines = []
        for r in new[:15]:
            lines.append("- [%s] %s ★%d 域:%s %s" % (r["grade"], r["full_name"], r["stars"],
                                                     "、".join(r["domains"]), r["desc"][:90]))
        if lines:
            with open(os.path.join(BUS, "radar_latest.txt"), "w", encoding="utf-8") as f:
                f.write("# GitHub 灵感雷达 新发现 %d 个  %s\n\n" % (len(new), ts) + "\n".join(lines))
    except Exception as e:
        print("[!] 写总线失败: %s" % e, file=sys.stderr)

    print("\n[✓] 巡检完成：命中 %d，新发现 %d，隐藏宝石 %d，交叉点 %d"
          % (len(out), len(new), len(hidden), len(bridges)))
    print("[✓] 报告: %s" % jp)
    return rep


def daemon(hours=2):
    print("[i] 灵感雷达常驻，每 %d 小时巡检一轮" % hours, file=sys.stderr)
    while True:
        try:
            run_once()
        except Exception as e:
            print("[!] 本轮失败: %s" % e, file=sys.stderr)
        time.sleep(int(hours * 3600))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="once", choices=["once", "daemon", "report"])
    ap.add_argument("--hours", type=float, default=2)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    if a.cmd == "daemon":
        daemon(a.hours)
    elif a.cmd == "report":
        p = os.path.join(HERE, "radar_report.md")
        print(open(p, encoding="utf-8").read() if os.path.exists(p) else "还没有报告")
    else:
        run_once(a.top)
