# ghunter — GitHub 全量检索引擎 + 持续灵感雷达

**解决两件事：① GitHub 搜不全 ② 想不到用户没提过的东西。**

两个入口：
- `ghunter.py` —— **意图 → 全量检索**：一句大白话炸成 10–26 条查询，三通道搜索，A/B/C 可用性分级
- `radar.py` —— **持续灵感雷达**：兴趣图 BFS 走到用户没提过的邻域，只报新发现，捞出「隐藏宝石」

## 为什么以前的搜索「搜不全」（四个真根因）

1. **单条 query 只覆盖一种说法** → `ghunter.py` 对同一意图自动扇出变体（中英/黑话/近义，内置 30+ 组同义词表）
2. **整句长查询返回 0 条** → GitHub repo 搜索是 **AND 语义**！`photoshop premiere after effects davinci resolve scripting api automation` 这种长句必然 0 结果。**必须先炸成短查询。**
3. **只看 star 排序** → 同时跑 `""` / `stars` / `updated` 三种排序，再按活跃度重排
4. **不验证可用性** → 抓 README + 文件树，判「近半年有提交/有依赖清单/有入口/README 有装跑命令」，输出 A/B/C 分级。**只采信 A/B 级。**

## 用法

```bash
# 全量检索一个意图
python ghunter.py --intent "抖音 热点 爬虫" --per 12 --top 20
python ghunter.py --intent "photoshop automation" --json     # 机器可读
python ghunter.py --recon                                     # 跑全部内置意图，出 JSON+MD 报告

# 灵感雷达
python radar.py once --top 60        # 一轮巡检（37 个种子域）
python radar.py daemon --hours 2     # 常驻，每 2 小时一轮
python radar.py report               # 看最新一轮报告
```

token 自动从 `TOKEN_CANDIDATES`（含 `gh_token` 环境变量与常见路径）加载；
加载不到就匿名跑（10 req/min，会被限流，报告里会标注「匿名」）。

## 灵感雷达的四个真手段

- **兴趣图 BFS**：用户目标 → 仓库 → 该仓库 topics → 新 topics → 新仓库（走到用户从没提过的邻域）
- **隐藏宝石**：可用性 A/B 级但 star < 300（好东西没人知道的那一类）
- **新发现差分**：与 `radar_seen.json` 对比，只报第一次见到的
- **跨域交叉点**：一个仓库同时出现在多个域 = 稀有交叉点，排最前

## 首轮实战成果（此前全部漏掉）

| 仓库 | star | 捞到了什么 |
|---|---|---|
| `alisaitteke/photoshop-mcp` | 655 | **118 个工具**控制 Photoshop（此前只知道 ExtendScript） |
| `leancoderkavy/premiere-pro-mcp` | 384 | PR 的 MCP 控制通道 |
| `DialmasterOrg/Youtarr` | 1751 | YouTube 自动归档（"下载视频"需求的现成答案） |
| `swarmcllaw/swarmcllaw` | 689 | 多智能体总线 |
| `jivoi/awesome-osint` | 30260 | 查人工具总索引 |

## 种子域（37 个）

媒体控制 / 审美设计 / 热点短视频 / 免费大模型 / 查人定位 / 外贸 / 电话卡分销 / 渗透绕过 /
多智能体 / 漫剧视频 / 全栈工程 / 自动化运维 / 逆向工程 / 游戏安全 / 爬虫框架 / 爬虫对抗 /
数据可视化 / 机器学习 / 本地大模型 / 数据库内核 / 网络协议 / 编译原理 / 容器云原生 / 安全扫描 /
区块链Web3 / 移动开发 / 浏览器扩展 / 桌面应用 / 游戏开发 / 嵌入式IoT / 音视频处理 / 文档处理 /
邮件营销 / 支付集成 / 建站部署 / 监控告警 / 知识管理

（`radar.py` 的 `SEED_DOMAINS`，加域就往里加一行；intent 保持 3–5 个短词。）

## 已知限制

- GitHub API 未认证 10 req/min、认证 5000 req/h；本机用 token，见 `gh_token`（环境变量或 `--token-file`）
- `usability_score` 是启发式，判 B 级的仓库值得人工扫一眼再采信
- BFS depth=1 是性能与覆盖的折中；跑全量 37 域约需几分钟，建议常驻模式

## 许可

MIT
