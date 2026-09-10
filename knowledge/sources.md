# 原文档案索引

只存"哪里有什么"，不存全文。需要原文时按 URL 重新抓取。

| 日期 | 来源 | 三句话摘要 | 提炼产出 |
|------|------|-----------|---------|
| 2026-09-08 | [MQL5:螃蟹谐波形态](https://www.mql5.com/zh/articles/19099) | MQL5实现XABCD螃蟹形态识别；枢轴点左右各5根确认+斐波那契比例校验(0.618/0.382-0.886/1.618延伸,容差10%)；防重绘=形态锁定K线下根确认；多级TP与3倍反向止损 | [patterns/crab-harmonic.md](patterns/crab-harmonic.md)；`rules_harmonic.py` 待实现 |
| 2026-09-08 | [MQL5:针形线吞没形态+RSI背离](https://www.mql5.com/zh/articles/17962) | Pin Bar(影线>2倍实体等4条件)/吞没形态精确定义；RSI背离=价格新低+RSI低点抬高+超卖区(窗口5-15)；形态与背离双确认才出信号 | [patterns/pinbar-engulfing-rsi-divergence.md](patterns/pinbar-engulfing-rsi-divergence.md) + `engine/rules_candlestick.py` 已实现 |
| 2026-09-09 | [MQL5:双EMA分形突破策略](https://www.mql5.com/zh/articles/18297) | 威廉姆斯分形(左右各2根确认)定关键位；看涨=上穿分形高点+价>EMA14>EMA200；只在趋势方向确认突破；分形右翼未走完不得用(防重绘) | [patterns/fractal-ema-breakout.md](patterns/fractal-ema-breakout.md) + `engine/rules_fractal.py` 已实现 |
| 2026-09-09 | [MQL5文章库批量下载(40篇)](https://www.mql5.com/zh/articles) | 浏览器下载归档至 knowledge/archive/；共提炼11张卡片：协整套利/灰色模型/A3优化/Python回测器/DRL网格警示/TimeFound/Gator-AD假突破过滤/Parafrac/Mamba/高斯过程分类/Mantis；MQL5编程系列等留档不提炼 | archive/ + knowledge/{strategies,indicators}/ 共11卡片，见 reading-list.md |
| 2026-09-09 | [MQL5:协整股票统计套利(第二部分)](https://www.mql5.com/zh/articles/19052) | EG两两检验→Johansen篮子检验→ADF+KPSS平稳性→特征向量权重→价差z-score(2.0σ/0.3σ)开平仓；初版失败教训=持仓1-2秒 | [strategies/cointegration-pairs-trading.md](strategies/cointegration-pairs-trading.md) |
| 2026-09-09 | [MQL5:灰色模型交易预测](https://www.mql5.com/zh/articles/19012) | AGO累加降噪+离散递推GM(1,1)；小样本(≥4正数)无分布假设 | [indicators/grey-model-gm11.md](indicators/grey-model-gm11.md) |
| 2026-09-09 | [MQL5:A3人工原子算法](https://www.mql5.com/zh/articles/18958) | 元启发式参数寻优(原子=解,共价键=开发,离子键=探索)；警示:噪声目标函数过拟合 | [strategies/metaheuristic-optimization-a3.md](strategies/metaheuristic-optimization-a3.md) |
| 2026-09-09 | [MQL5:Python策略测试器(第一部分)](https://www.mql5.com/zh/articles/18971) | Python自研交易模拟器三账本(持仓/挂单/成交)架构 | [strategies/python-trade-simulator.md](strategies/python-trade-simulator.md)（已实施为backtest.py+回测实证） |
| 2026-09-09 | [MQL5:深度强化学习优化Ilan](https://www.mql5.com/zh/articles/17455) | DRL+动态Q-table改造网格马丁格尔——警示卡:尾部风险不因AI化消失 | [strategies/drl-grid-ean-warning.md](strategies/drl-grid-ean-warning.md) |
| 2026-09-09 | [MQL5:TimeFound时序基础模型](https://www.mql5.com/zh/articles/18414) | 跨域预训练Transformer零样本预测；适用新股无历史场景 | [indicators/time-series-foundation-model.md](indicators/time-series-foundation-model.md) |
| 2026-09-09 | [MQL5:鳄鱼振荡器与AD指标韧性策略](https://www.mql5.com/zh/articles/18992) | 趋势信号无AD量能确认=假突破；双重确认思想可给fractal_break加过滤 | [indicators/gator-ad-fakeout-filter.md](indicators/gator-ad-fakeout-filter.md) |
| 2026-09-09 | [MQL5:Parafrac振荡器](https://www.mql5.com/zh/articles/19100) | SAR×分形组合；与fractal_break能力域重叠，低优先级 | [indicators/parafrac-sar-fractal.md](indicators/parafrac-sar-fractal.md) |
| 2026-09-09 | [MQL5:Mamba选择性状态空间模型](https://www.mql5.com/zh/articles/19047) | O(N)线性复杂度长序列架构，2024-25时序预测从注意力转向SSM | [indicators/mamba-ssm-long-sequence.md](indicators/mamba-ssm-long-sequence.md) |
| 2026-09-09 | [MQL5:高斯过程分类](https://www.mql5.com/zh/articles/18875) | 概率预测("75%概率上涨")天然适合信号置信度过滤 | [indicators/gaussian-process-classification.md](indicators/gaussian-process-classification.md) |
| 2026-09-09 | [MQL5:Mantis轻量时序分类](https://www.mql5.com/zh/articles/18246) | 对比预训练+局部/全局token；核心卖点=概率校准 | [indicators/mantis-calibrated-classification.md](indicators/mantis-calibrated-classification.md) |
| 2026-09-10 | 回测与寻优实施（Python回测器卡+A3卡落地） | 全池300只×250根回放：ma_death_cross 55.5%最高、ma_golden_cross 47.5%、fractal_break_up均值-1.65%实锤假突破；6规则网格寻优样本外全部回落46-47%，candle_div过拟合回撤19%被自动标记；参数不写回配置 | `stock_monitor/backtest.py` + `tools/backtest_report.py` + `tools/tune_rule_thresholds.py` + data/{backtest_report,threshold_tuning}.json；卡片python-trade-simulator.md与metaheuristic-optimization-a3.md已更新实证 |
| 2026-09-08 | MQL5文章库（首页浏览） | 精选5篇高相关文章待处理：双EMA分形突破(18297)/流动性扫单(18379)/ARIMA(18253)/CNN识别K线(17981)/Python统计建模(18035)；本地直连mql5.com超时，需走服务端读取 | [reading-list.md](reading-list.md) |
| 2026-09-08 | [sngyai/Sequoia-X](https://github.com/sngyai/Sequoia-X) | A股自动选股，技术形态扫描后收盘推送飞书 | 监测系统架构参考 |
| 2026-09-08 | [datawhalechina/self-dify](https://github.com/datawhalechina/self-dify) | Dify中文教程：智能体→工作流→知识库→MCP | 知识库接入路线参考 |
