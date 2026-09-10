# MQL5 精选文章待读书单

> 本地网络直连 mql5.com 超时，逐篇处理时用服务端读取（WebFetch/web_reader），
> 按 knowledge-intake 流程提炼：能写成判定条件的进 engine/代码，其余进知识卡片。
> 每处理完一篇，把条目移到 sources.md 并从本清单删除。

## 2026-09-09 批量下载40篇处置结果

**已提炼（11张卡片）**：协整套利(19052)/灰色模型(19012)/A3优化(18958)/
Python回测器(18971)/DRL网格警示(17455)/TimeFound(18414)/Gator-AD假突破过滤(18992)/
Parafrac(19100)/Mamba(19047)/高斯过程分类(18875)/Mantis(18246)

**不再提炼（留archive备查）**：
- MQL5编程系列×10（对象/事件/日志/仪表盘/新闻按钮等，与Python项目无关）
- 市场模拟系列×3（MT5仓位视图/SQL，同理）
- Firebase数据集成（技术栈不符）
- 螃蟹谐波(19099)/针形K线加仓(19087)已各有卡片，新下载仅存档
- 量子神经网络×3(18759/18890/19043)、协整第一部分、神经网络特征提取续篇(18307/18329)、
  TimeFound结论(18447)、多币种EA(17608)、动态多品种EA(18165)、风控头文件(18918)、
  最小二乘趋势线(19077)、Python读MT5行情流(19065)、MQL5向导技巧(18975)、
  ML平台回顾(22768)：ML类后续按需提炼，其余与项目弱相关

## 旧队列（服务端读取失败，已由浏览器批量下载替代）

- [ ] [第27部分：流动性扫单（清算）](https://www.mql5.com/zh/articles/18379)
      ⚠️ 未包含在本次40篇下载中，如需要请从浏览器保存到 inbox/

## 已处理

- [x] [第25部分：双EMA分形突破策略](https://www.mql5.com/zh/articles/18297)
      → patterns/fractal-ema-breakout.md + engine/rules_fractal.py（2026-09-09，feed拉取长度60→250）
- [x] [第26部分：针形线、吞没形态与RSI背离](https://www.mql5.com/zh/articles/17962)
      → patterns/pinbar-engulfing-rsi-divergence.md + engine/rules_candlestick.py（2026-09-08）
