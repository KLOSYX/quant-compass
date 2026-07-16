# China Investment Screener Skill

一个面向中国个人投资者的可审计候选池筛选 Skill。第一版覆盖 A 股、场内 ETF 和主要公募基金类别，优先适配蚂蚁购买、兼顾证券账户。

关键原则：渠道可售状态不伪造、硬过滤先于评分、只做同类排名、累计净值优先、保留数据失败和淘汰原因。

Skill 入口见 `SKILL.md`，离线计算入口见 `scripts/analyze_candidates.py`。
