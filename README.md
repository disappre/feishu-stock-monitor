# Feishu Stock Monitor · 基于飞书的A股智能监测预警系统

> 规则引擎 + K线形态聚类模型双引擎监测，LLM 自动生成解读卡片，飞书机器人实时推送，多维表格量化复盘。
>
> **仅供学习研究，不构成任何投资建议。系统只推送事实与统计信息，不提供任何买卖指令。**

## 项目动机

在使用自研的[股票技术形态聚类分析系统](https://github.com/disappre/stock-clustering-system)过程中，我发现"能识别形态"和"能及时知道形态出现"之间差着一个监控与触达的断层。本项目把聚类模型改造成在线信号源，并补齐企业协作场景的最后一公里：

```
行情采集 ──→ 双引擎监测 ──→ LLM解读 ──→ 飞书推送 ──→ 多维表格复盘
 akshare     规则 + 模型      解读卡片     机器人卡片      胜率统计
```

## 功能特性

- **双引擎监测**
  - 规则引擎：均线金叉/死叉、涨跌幅异动、成交量异动、RSI 超买超卖，阈值全部配置化
  ② 模型引擎：桥接 [stock-clustering-system](https://github.com/disappre/stock-clustering-system) 的
  `TechnicalPatternRecognizer`，双底、头肩底、红三兵、长阳不破、平台突破等形态自动识别作为信号源
- **LLM 智能解读**：信号触发后自动生成"触发原因 + 形态统计 + 风险提示"解读卡片
- **飞书触达**：自定义机器人 webhook 推送交互式消息卡片（含迷你K线图），签名校验
- **量化复盘**：信号流水自动写入飞书多维表格，回填 N 日后走势，统计各信号胜率
- **交互查询**（规划中）：群内 @机器人 查询个股实时行情与近期形态

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                      调度层 APScheduler                   │
│         盘中低频轮询(5min) · 收盘后全量扫描(15:30)          │
└──────────────┬──────────────────────────────────────────┘
               ↓
┌──────────────────────────┐   ┌──────────────────────────┐
│  数据层 data/             │   │  模型引擎 engine/pattern  │
│  akshare 行情/K线采集     │──→│  形态聚类识别(复用自研)    │
└──────────────┬───────────┘   └──────────┬───────────────┘
               ↓                          ↓
┌─────────────────────────────────────────────────────────┐
│              规则引擎 engine/ (金叉死叉/异动/RSI)           │
└──────────────┬──────────────────────────────────────────┘
               ↓ Signal 事件
┌──────────────────────────┐   ┌──────────────────────────┐
│  解读层 llm/              │   │  触达层 notify/           │
│  LLM 生成解读卡片         │──→│  飞书 webhook 消息卡片     │
└──────────────────────────┘   │  多维表格信号流水回写      │
                               └──────────────────────────┘
```

## 快速开始

### 环境要求

- Python 3.10+
- 一个飞书群 + 自定义机器人 webhook（[官方教程](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)，开启签名校验）

### 安装

```bash
git clone https://github.com/disappre/feishu-stock-monitor.git
cd feishu-stock-monitor
pip install -r requirements.txt

# 形态识别引擎：本地已部署 stock-api（聚类系统）时，在 .env 中指定路径即可
#   CLUSTER_REPO_PATH=C:\...\stock-api-master
# 未部署也可 clone: git clone https://github.com/disappre/stock-clustering-system.git ../stock-clustering-system

cp .env.example .env   # 填入 FEISHU_WEBHOOK / FEISHU_SECRET，其余凭证按需
```

### 运行

```bash
# 单次扫描自选股（盘中/调试，允许请求实时行情）
python -m stock_monitor.main --once

# 收盘后两阶段流程（推荐）：先并发同步至 SQLite，再纯本地筛选并推送飞书
python -m stock_monitor.main --sync-pool --workers 4
python -m stock_monitor.main --screen-local

# 仅测试股票池前25只
python -m stock_monitor.main --sync-pool --pool-limit 25 --workers 4
python -m stock_monitor.main --screen-local --pool-limit 25

# 常驻调度：盘中轮询自选股；收盘后自动执行“同步 → 本地筛选”
python -m stock_monitor.main
```

> 日线缓存固定采用 `daily + qfq`（前复权）口径，存放于 `data/kline_cache.db`。收盘同步会刷新最近250根**已收盘**K线，防止前复权调整导致技术指标口径漂移；本地筛选全程不访问外部行情源，缓存缺失或不足的股票会在飞书摘要中单独标记。

### 回测与阈值寻优

所有规则的信号质量都有历史数据背书（方法见 `knowledge/strategies/python-trade-simulator.md`）：

```bash
# 1. 基线回测：当前 watchlist.yaml 配置 × 全池300只 × 250根日线
#    输出各信号类型的样本数/胜率/前瞻均值（data/backtest_report.json）
python tools/backtest_report.py

# 2. 阈值寻优：按规则独立网格搜索（零新依赖）
#    训练期(前70%日期)选参 → 测试期(后30%)样本外验证 → data/threshold_tuning.json
python tools/tune_rule_thresholds.py --min-samples 20
```

回测语义：信号在第i根收盘触发 → 入场=次日开盘 → 出场=第N日收盘（N默认取 `review.forward_days`）；电平触发信号按冷却去重；胜负按信号方向判定。**寻优结果不自动写回配置**——样本外胜率回撤>15%的参数视为过拟合，需人工决策。

### 配置说明

**选股清单**：编辑 `data/my_picks.txt`，把东方财富的选股结果粘贴进去，每行一条，保存即生效（每次扫描前自动重读，无需重启）：

```
# 注释行
600519              （只写代码，自动查名称）
600519,贵州茅台     （代码+名称）
```

> 东方财富不开放个人自选股API，标准做法是从东财导出代码清单：**App自选股**可逐个复制代码，**网页选股器**（xuanyuan.eastmoney.com 条件选股）支持把选股结果导出后直接粘贴到 `my_picks.txt`。

其余信号阈值集中在 `config/watchlist.yaml`，无需改代码即可调整监测标的与灵敏度：

```yaml
watchlist:
  - code: "600519"
    name: 贵州茅台
  - code: "300750"
    name: 宁德时代

rules:
  ma_cross:            # 均线金叉死叉
    short: 5
    long: 20
  price_surge:         # 涨跌幅异动
    pct_threshold: 5.0
  volume_surge:        # 成交量放大倍数
    ratio_threshold: 2.0
  rsi:
    period: 14
    overbought: 80
    oversold: 20
  pattern:             # 聚类形态识别
    enabled: true
    min_confidence: 0.7
```

## 目录结构

```
feishu-stock-monitor/
├── config/watchlist.yaml      # 自选股 + 规则阈值（全部可配置）
├── stock_monitor/
│   ├── main.py                # 调度入口：盘中轮询 + 收盘复盘
│   ├── data/feed.py           # akshare 行情采集（带重试与频控）
│   ├── engine/
│   │   ├── base.py            # Signal 数据类 + RuleEngine 调度
│   │   ├── rules_ma.py        # 均线金叉/死叉
│   │   ├── rules_price.py     # 涨跌幅 / 成交量 / RSI
│   │   └── pattern.py         # 形态识别适配层（桥接聚类系统）
│   ├── llm/interpreter.py     # LLM 解读卡片生成（Prompt 工程）
│   ├── notify/feishu_bot.py   # 飞书 webhook 推送（签名 + 消息卡片）
│   ├── notify/bitable.py      # 多维表格信号流水 / 胜率回填
│   └── utils/kline_plot.py    # matplotlib 迷你K线图
└── tests/test_rules.py        # 规则引擎单元测试
```

## 推送效果

<!-- TODO: 补充真实推送截图（飞书消息卡片 1-2 张）与多维表格截图 -->

| 消息卡片 | 多维表格复盘 |
|---------|-------------|
| （截图占位） | （截图占位） |

## 信号复盘统计

<!-- TODO: 项目运行满一个月后，用多维表格导出数据填写下表 -->

| 信号类型 | 触发次数 | N日胜率 | 备注 |
|---------|---------|--------|------|
| 均线金叉 | — | — | — |
| 形态识别（头肩顶/双底） | — | — | — |

## Roadmap

- [x] 规则引擎（金叉死叉 / 涨跌幅 / 量能 / RSI）
- [x] 飞书 webhook 卡片推送（含签名校验）
- [x] 迷你K线图生成与卡片嵌图（无凭证自动降级纯文本）
- [x] 多维表格信号流水写入与胜率统计（lark-oapi）
- [x] 聚类形态识别接入（桥接 stock-clustering-system 的 TechnicalPatternRecognizer）
- [x] LLM 解读卡片生成（禁止荐股约束 + 失败降级）
- [x] 群内 @机器人 交互查询（长连接模式，无需公网）
- [ ] 信号胜率月报自动生成

## 机器人交互查询

群里 @机器人（或私聊）发送 **6位A股代码**（如 `600519`），机器人回复一张个股体检卡片：
现价快照 + 均线/量能/RSI 概览 + 当前触发的规则与形态信号。

```bash
python -m stock_monitor.bot.server
```

一次性配置（飞书开放平台 [open.feishu.cn](https://open.feishu.cn)）：

1. 创建**企业自建应用** → 添加「机器人」能力；
2. 事件与回调 → 事件配置 → 订阅方式选 **「使用长连接接收事件」**，添加事件 `im.message.receive_v1`（接收消息）；
3. 权限管理开通：`im:message.group_at_msg`（读取群@消息）、`im:message.p2p_msg`（读取私聊）、`im:message:send_as_bot`（发送消息）；
4. 创建版本并发布 → 把机器人拉进群或私聊，@它 发送股票代码；
5. 把应用的 **App ID / App Secret** 填入 `.env` 的 `FEISHU_APP_ID / FEISHU_APP_SECRET`（与多维表格共用同一套凭证）。

## 免责声明

本项目所有输出（含 LLM 生成内容）仅为技术学习与数据统计演示，不构成任何证券投资建议。作者不对任何人因使用本系统而产生的损失承担责任。股市有风险，投资需谨慎。

### 盘中定时扫描（午盘/尾盘）

每个交易日两次实时扫描自选股并推飞书，重点看盘面温度与持仓状态：

```bash
python tools/intraday_scan.py --session noon    # 午盘速览（11:35，上午收盘后）
python tools/intraday_scan.py --session close   # 尾盘速览（14:50，收盘前10分钟）
```

卡片内容：涨跌家数/均涨幅/防狼术危险区占比 + 异动榜Top5 + 五步法持仓状态 + 主力看涨标的。

**三种定时方式（择一即可）**：

1. **Windows 计划任务（推荐，不依赖任何常驻进程）**：
   右键管理员运行 `setup_schedule.bat`，注册两个任务（周一至周五 11:35 / 14:50）；
2. **项目调度器**：`python -m stock_monitor.main` 常驻，内置 11:35/14:50 两个 job
   （时间可在 `config/watchlist.yaml` 的 `noon_scan_time`/`tail_scan_time` 调整）；
3. **ZCode 自动化**：在当前会话已创建"每交易日11:35午盘扫描"任务；
   尾盘任务需**新开一个会话**创建（同一会话只能绑定一个定时任务）。

### Web前端

```bash
python tools/web_server.py        # 启动后访问 http://localhost:8787
```

功能：多级别K线（笔/线段/中枢/背驰/买卖点/主力色带全部同图叠加）、
级别切换（日线/30分钟/5分钟）、**实时搜索**（代码/名称/拼音首字母，带实时价格）、
**实时刷新**（盘中30秒自动重取）、K线hover逐根解释、URL参数分享（`?code=600519&level=m30`）。

搜索示例：`600519` / `茅台` / `gzmt`（贵州茅台拼音首字母）/ `payh`（平安银行）。
