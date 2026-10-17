#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ghunter.deep —— 解决「GitHub 搜不全 / 想不到」的四个真缺口。

2026-10-17 补。ghunter.py 已有：查询变体展开 + 三排序 + 代码/topic 通道。
缺的四件事（都是实测能显著提高覆盖率的）：

1. **awesome 列表深挖**：`awesome <topic>` 是人工 curated 的金矿，
   一个列表里常有 50-200 个相关仓库，且带一句话说明。单靠关键词搜不到。
2. **字段限定搜**：GitHub 默认搜 name+description+readme，
   但 `in:name` / `in:description` / `in:readme` 各自能捞出不同的集合
   （有的项目名很土但描述很准，有的反之）。
3. **star 分层**：`stars:20..20000` 把「太明星没信息量」和「没人用已死」
   两头都切掉，中间的才是能直接用的。
4. **新鲜度**：`pushed:>2026-01-01` 排除僵尸仓库；`created:>2025-06-01` 找新秀。

用法：
    python deep.py mega "抖音 tiktok 下载"      # 全通道合并检索
    python deep.py awesome "osint"              # 只挖 awesome 列表
    python deep.py field "esim affiliate"       # 字段限定对比
"""
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ghunter import (_req, expand_intent, search_repos, repo_meta, repo_tree,
                     repo_readme, usability_score, cached)

# awesome 列表仓库名模式
AWESOME_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)")


def awesome_lists(topic, per=10):
    """找 topic 相关的 awesome 列表仓库。"""
    out = []
    for q in ("awesome-%s" % topic, "awesome %s" % topic,
              "awesome-%s list" % topic):
        for it in search_repos(q, per=per, sort="stars"):
            nm = (it.get("full_name") or "")
            if "awesome" in nm.lower() or "awesome" in (it.get("description") or "").lower():
                out.append({"full_name": nm, "stars": it.get("stargazers_count", 0),
                            "desc": (it.get("description") or "")[:120]})
    seen, uniq = set(), []
    for x in out:
        if x["full_name"] not in seen:
            seen.add(x["full_name"])
            uniq.append(x)
    return uniq[:per]


def dig_awesome(full_name, limit=120):
    """读一个 awesome 列表的 README，抽出里面推荐的仓库 + 说明。"""
    tree = repo_tree(full_name, limit=400)
    readme = repo_readme(full_name, tree)
    if not readme:
        return []
    found = []
    for m in AWESOME_RE.finditer(readme):
        repo = m.group(1)
        if repo.lower().startswith(("awesome", "sindresorhus/awesome")):
            continue
        # 取链接前后的说明文字
        tail = readme[m.end():m.end() + 220]
        desc = re.split(r"[\n\r\]\[]", tail)[0].strip(" -–—:>|*")
        found.append({"repo": repo, "desc": desc[:110]})
    seen, uniq = set(), []
    for x in found:
        if x["repo"] not in seen:
            seen.add(x["repo"])
            uniq.append(x)
    return uniq[:limit]


def field_search(q, per=20):
    """字段限定：name / description / readme 三路各搜一次，取并集。

    实测意义：同一个词在三处的命中集合差异很大。
    """
    out = []
    for f in ("in:name", "in:description", "in:readme"):
        for it in search_repos("%s %s" % (q, f), per=per):
            out.append({"full_name": it.get("full_name"),
                        "stars": it.get("stargazers_count", 0),
                        "desc": (it.get("description") or "")[:120],
                        "field": f,
                        "url": it.get("html_url")})
    seen, uniq = set(), []
    for x in out:
        if x["full_name"] not in seen:
            seen.add(x["full_name"])
            uniq.append(x)
    return uniq


def band_search(q, per=20):
    """star 分层 + 新鲜度，切掉明星仓库和僵尸仓库。"""
    out = []
    bands = ["stars:20..2000", "stars:2000..20000", "stars:20000..200000"]
    for b in bands:
        for it in search_repos("%s %s" % (q, b), per=per, sort="updated"):
            out.append({"full_name": it.get("full_name"),
                        "stars": it.get("stargazers_count", 0),
                        "desc": (it.get("description") or "")[:120],
                        "band": b,
                        "url": it.get("html_url")})
    # 新鲜度：最近半年有推送
    try:
        for it in search_repos("%s pushed:>2026-04-01" % q, per=per, sort="updated"):
            out.append({"full_name": it.get("full_name"),
                        "stars": it.get("stargazers_count", 0),
                        "desc": (it.get("description") or "")[:120],
                        "band": "fresh",
                        "url": it.get("html_url")})
    except Exception:
        pass
    seen, uniq = set(), []
    for x in out:
        if x["full_name"] not in seen:
            seen.add(x["full_name"])
            uniq.append(x)
    return uniq


def mega_hunt(intent, per=8, workers=4, budget_sec=150):
    """全通道合并：变体展开 × 三排序 × 字段限定 × star 分层 × awesome 深挖。

    budget_sec：总时间预算。本机代理抖动严重，不限时会被重试拖死
    （实测 20 变体 × 3 通道 = 60+ 请求，代理一抖就 5 分钟出不来）。
    """
    t0 = time.time()
    queries = expand_intent(intent, maxv=8)
    repos = {}

    def absorb(items):
        for it in items or []:
            nm = it.get("full_name")
            if not nm:
                continue
            if nm not in repos:
                repos[nm] = {"full_name": nm,
                             "stars": it.get("stars") or it.get("stargazers_count") or 0,
                             "desc": it.get("desc") or (it.get("description") or "")[:120],
                             "url": it.get("url") or it.get("html_url"),
                             "hits": 0}
            repos[nm]["hits"] += 1

    # 通道 1：三排序
    def ch_repos(q):
        out = []
        for s in ("", "stars", "updated"):
            out.extend(search_repos(q, per=per, sort=s))
        return out

    # 通道 2：字段限定（name/description/readme 三路并集）
    def ch_field(q):
        return field_search(q, per=per)

    # 通道 3：star 分层 + 新鲜度
    def ch_band(q):
        return band_search(q, per=per)

    # 通道 4：awesome 列表深挖（最贵，只对前 2 个 query 做）
    def ch_awesome(q):
        out = []
        core = " ".join(q.split()[:2])
        for lst in awesome_lists(core, per=3):
            for x in dig_awesome(lst["full_name"], limit=30):
                out.append({"full_name": x["repo"], "stars": 0,
                            "desc": x["desc"],
                            "url": "https://github.com/" + x["repo"]})
        return out

    jobs = []
    for q in queries:
        jobs += [(ch_repos, q), (ch_field, q)]
    for q in queries[:4]:
        jobs.append((ch_band, q))
    for q in queries[:2]:
        jobs.append((ch_awesome, q))

    with ThreadPoolExecutor(workers) as ex:
        for items in ex.map(lambda j: j[0](j[1]), jobs):
            absorb(items)
            if time.time() - t0 > budget_sec:
                break

    # awesome 深挖出来的仓库补 star 数（限 15 个，控成本）
    no_star = [r for r in repos.values() if not r["stars"]][:15]

    def fill(r):
        m = repo_meta(r["full_name"])
        if m:
            r["stars"] = m.get("stargazers_count", 0)
        return r

    if no_star:
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(fill, no_star))

    # 重排：命中通道多 > star 适中 > 有描述
    for r in repos.values():
        s = r["hits"] * 10
        s += min(r["stars"], 20000) / 20000.0 * 6
        if r["desc"]:
            s += 2
        r["score"] = round(s, 2)
    ranked = sorted(repos.values(), key=lambda x: -x["score"])
    return {"intent": intent, "queries": queries, "total": len(ranked),
            "sec": round(time.time() - t0, 1), "repos": ranked}


def _cli():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "mega":
        r = mega_hunt(arg)
        print("意图：%s" % r["intent"])
        print("变体 %d 条，命中仓库 %d 个，耗时 %.1fs\n" % (len(r["queries"]), r["total"], r["sec"]))
        for x in r["repos"][:40]:
            print("  ★%-6d hits=%-2d %-46s %s" % (x["stars"], x["hits"], x["full_name"], x["desc"][:60]))
        json.dump(r, open("mega_%s.json" % re.sub(r"\W+", "_", arg)[:30], "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)
    elif cmd == "awesome":
        core = " ".join(arg.split()[:2])
        for lst in awesome_lists(core):
            print("列表 %-46s ★%d  %s" % (lst["full_name"], lst["stars"], lst["desc"]))
            for x in dig_awesome(lst["full_name"], limit=25):
                print("    %-52s %s" % (x["repo"], x["desc"][:70]))
    elif cmd == "field":
        for x in field_search(arg):
            print("  %-14s ★%-7d %-46s %s" % (x["field"], x["stars"], x["full_name"], x["desc"][:60]))


if __name__ == "__main__":
    _cli()
