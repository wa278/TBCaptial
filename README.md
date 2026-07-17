# TBCaptial

TBCaptial 是一个面向 A 股日频研究、回测与策略迭代的 Python 量化系统。

项目采用**模块化单体**：所有能力位于同一个代码库、同一个发布单元和默认单进程中，通过清晰的内部模块边界协作，不拆微服务。这个选择优先服务双人团队的开发速度、调试效率和回测可复现性。

> 当前状态：**环境与 AKQuant backend 基线已验收**。Conda、AKShare Raw 小切片脚本、固定 AKQuant `v0.3.2` wheel、最小回测 adapter 和离线契约测试已经落地；Silver、正式 snapshot 与完整回测产物链路仍待实现。

## 要解决的问题

第一阶段只解决一条最重要的闭环：

**AKShare 主数据采集（Tushare 补充）→ 数据校验与快照 → 策略产生目标持仓 → A 股规则回测 → 指标与产物落盘 → 可复现实验。**

目标是让两个人尽快稳定地迭代日频策略，而不是一次性建设完整交易平台。

## 设计原则

- **正确性优先于性能**：先消灭未来函数、幸存者偏差、复权错误和不可成交假设，再优化速度。
- **研究与交易语义一致**：策略输出目标持仓，撮合、费用、T+1、整手、停牌和涨跌停由统一回测内核处理。
- **数据可追溯**：每次回测绑定数据快照、参数、代码版本和运行标识。
- **模块化单体**：保持单仓库、单发布单元；模块只通过明确接口依赖，不共享隐式全局状态。
- **环境可迁移**：使用平台无关的 `environment.yml` 管理解释器与直接依赖；正式运行额外记录实际解析出的包清单。
- **先日频后高频**：首版只支持 A 股现货日频、多标的、只做多；分钟线、实盘和分布式计算延后。
- **先基线后复杂模型**：先用简单策略证明整条链路正确，再引入多因子、优化器和机器学习。

## 系统边界

首版范围：

- AKShare 优先的数据适配与增量同步，Tushare 用于显式补充和交叉校验；
- 股票主数据、交易日历、未复权日线、复权因子、涨跌停、停复牌和基准行情；
- 本地 Parquet 数据集、DuckDB 查询和不可变数据快照；
- 日频事件驱动回测；
- AKQuant `v0.3.2` 作为进程内底层回测引擎，由 TBCaptial adapter 隔离；
- A 股 T+1、100 股整数手、停牌、涨跌停、佣金和印花税；
- 策略、股票池、目标持仓、组合记账、绩效归因和实验产物；
- 命令行运行、自动化测试和本地/CI 开发流程。
- Conda 环境声明、创建/激活脚本和当前机器环境一致性检查。

首版明确不做：

- 微服务、消息队列、Kubernetes 或分布式任务系统；
- 实盘下单、券商柜台接入和高可用交易服务；
- 分钟/Tick 回测、高频撮合和盘口重建；
- 融资融券、期权、期货、港美股；
- Web 管理后台和多人权限系统；
- 在未经验证前自研通用数据库、DataFrame 或分布式计算框架。

## 目标架构

数据流按以下方向单向推进：

`AKShare（主）/ Tushare（辅）→ 原始数据层 → 标准数据层 → 数据快照 → 特征/股票池 → TBCaptial 策略/目标组合 → AKQuant backend + A 股执行守卫 → 报告/实验产物`

模块依赖遵循以下方向：

`入口与应用编排 → 研究/回测用例 → 领域接口与模型 ← 数据和基础设施适配器`

核心领域和用户策略不直接依赖 AKShare、Tushare、AKQuant、DuckDB 或命令行。只有内部 `AkQuantBacktestEngine`/strategy bridge 能接触 AKQuant 类型；backend 升级由固定兼容测试和黄金结果控制。

## 存储系统选择

存储采用“**本地文件系统为权威仓 + Parquet 为事实格式 + DuckDB 本地查询**”的组合，而不是把 AKShare/Tushare 当数据库，也不引入云存储服务。

- **权威数据仓**：在单机本地数据根目录保存不可变 Raw、标准化 Silver、可重建 Gold、数据快照清单和正式实验产物。数据根目录必须位于本地磁盘，不放入 Git、网盘同步目录或远端挂载目录。
- **文件格式**：大表使用带压缩的 Parquet；小型 manifest 使用 JSON，批量元数据使用 Parquet。行情按日期分区，不按股票代码制造海量小目录。
- **查询引擎**：DuckDB 直接查询本地 Parquet，并维护可重建的本地 catalog；DuckDB 文件不是事实数据的权威副本。
- **快照读取**：回测只读 `snapshot_id` 对应 manifest 中列出的本地文件。已有快照就绪后无需连接任何数据源。
- **写入模型**：只有本机数据采集进程能写数据仓，研究和回测只读已经发布的快照，即“单写者、多读者”。新数据或上游修订产生新分区版本，不覆盖旧实验引用的数据。
- **数据源使用方式**：只有采集模块访问 AKShare/Tushare。AKShare 是默认主源并优先实现；Tushare 只用于主源缺少的字段、交叉校验或显式故障切换。禁止在同一数据分区内静默混源，manifest 必须记录实际来源和接口版本；策略、因子和回测禁止调用任何在线数据源。
- **恢复能力**：Raw 和 snapshot manifest 长期保留，并可复制到另一块本地磁盘做离线备份；Silver 可以从 Raw 重建，Gold 可以从 Silver 重建，DuckDB catalog 可以从 manifest 重建。

这个方案明确以单机本地运行作为第一阶段边界，不依赖 S3、NAS、数据库服务器或任何外部云服务。需要在另一台机器运行时，显式导出并复制完整快照包，目标机校验 manifest 和 SHA-256 后再导入。详细目录、版本、发布、备份和保留策略见 `PLAN.md` 的“数据架构”章节。

## 开发优先级

| 优先级 | 交付结果 | 判断标准 |
| --- | --- | --- |
| P0 | 最小可信回测闭环 | 固定小股票池优先从 AKShare 同步并完成无未来函数回测，结果可重复 |
| P1 | 策略迭代效率 | 支持全市场日频、特征缓存、历史股票池、批量实验、报告对比 |
| P2 | A 股仿真可信度 | 公司行为、历史 ST/上市状态、容量、滑点和有效期费用模型完善 |
| P3 | 模拟盘与实盘准备 | 统一时钟、实时数据、券商适配、风控、监控和恢复机制 |
| P4 | 性能扩展 | 根据真实 profiling 结果做并行、Polars/Numba/C++ 热点优化 |

P0 不是“能算出一条收益曲线”就结束。它必须同时满足：时间边界明确、输入数据通过质量门、订单可成交性受约束、交易账本可对账、运行产物可复现。

## 首个验收场景

第一条基线策略建议使用简单的日频横截面动量或均线策略，目的不是追求收益，而是暴露系统错误。验收场景应具备：

- 使用固定、很小的股票集合和一段可人工核对的历史数据；
- 收盘后计算信号，下一交易日开盘成交；
- 包含一次涨停买入失败、一次跌停卖出失败、一次停牌、一次 T+1 卖出拒绝和一次零股处理；
- 输出订单、成交、持仓、现金、每日净值、费用与拒单原因；
- 相同代码、配置和数据快照重复运行，结果逐字段一致；
- 至少有一个人工计算的黄金样例与引擎结果完全一致。

## 文档导航

- [AGENTS.md](./AGENTS.md)：仓库目录职责、Agent 工作边界、脚本入口和交付检查清单。
- [OPERATIONS.md](./OPERATIONS.md)：环境、AKQuant 构建/验收、AKShare 下载、产物校验和故障处理操作指南。
- [PLAN_LATEST.md](./PLAN_LATEST.md)：第一阶段唯一执行计划，只包含 Conda 环境、AKShare 优先的本地数据系统和 A 股日频回测详细设计，并内嵌 ASCII 架构图。
- [PLAN.md](./PLAN.md)：长期架构背景、数据设计、回测语义、模块边界、优先级和里程碑。
- [ARCHITECTURE.txt](./ARCHITECTURE.txt)：系统拓扑、本地存储、数据发布、回测事件流和可复现边界的 ASCII 图。
- [ENVIRONMENT_ACCEPTANCE.md](./ENVIRONMENT_ACCEPTANCE.md)：当前开发机的 Conda 创建、激活、依赖和本地存储 smoke test 验收记录。

## 关键假设

- 首个可用版本使用 Conda 管理的 Python 3.11 环境，日频批处理在单机运行；
- AKShare 是主数据源并优先实现，Tushare 是辅助源；两者都不成为领域层直接依赖；
- 回测复用固定 AKQuant backend，但 TBCaptial 保有公开接口、`BarSlice`、A 股执行语义和不可变产物契约；
- Parquet 是主要数据文件格式，DuckDB 用于本地查询和元数据管理；
- 研究价格、成交价格和公司行为分开建模；
- 默认信号时点为交易日收盘后，默认成交时点为下一交易日开盘；
- 首版只做多，不使用杠杆，现金不足时不成交或按明确规则缩量；
- 所有费率与交易规则按生效日期配置，不把当前规则散落硬编码在策略中。

这些假设不是永久承诺。改变假设时，需要在计划中的决策记录里说明原因和迁移影响。

## Conda 环境与部署约定

Conda 是项目唯一支持的开发和部署环境入口。当前已经提供：

- `environment.yml`：人工维护的唯一依赖声明，固定环境名、Python 3.11、`conda-forge` 和直接依赖；
- `scripts/create_conda_env.sh`：自动发现 Conda；未安装时在 macOS/Linux 自动安装并校验固定版本 Miniforge，然后创建或更新环境并执行 smoke test；
- `scripts/activate_conda_env.sh`：必须通过 `source` 使用，自动发现 Conda 并激活 `tbcaptial`；
- `scripts/verify_conda_env.py`：验证 Python、关键包、ZSTD Parquet 和本地 DuckDB 文件；
- `scripts/install_akquant_backend.sh`：校验固定 tag/commit，以 commit 时间构建可重复 wheel，安装并输出 SHA-256；
- `pyproject.toml`：保存项目包元数据与 Ruff/Mypy/Pytest 配置，不维护第二套依赖解析流程。

```bash
make setup
source scripts/activate_conda_env.sh
make acceptance
```

AKShare、Tushare 放在 `environment.yml` 的 pip 子段，其余科学计算与原生依赖优先来自 `conda-forge`。按当前决策不生成多平台 lock，也不做四个平台试装；`environment.yml` 不写机器绝对路径或平台专属直接依赖，新增依赖只在实际使用的平台上验收。正式运行记录 `environment.yml` hash、`conda list --explicit`、Conda/Python/关键包版本、操作系统和 CPU 架构，因此能够知道某次运行的实际环境；这不承诺不同日期重新求解会得到逐包完全相同的间接依赖。

AKQuant 固定为 `v0.3.2`、commit `2924e0cff36669a3563ffb5cb139da0ba9254045`。`environment.yml` 声明其运行与 Rust/Maturin 构建依赖，`make akquant-backend` 从经过身份和干净工作区检查的源码构建 release wheel；源码绝对路径不会进入部署声明。本机 `osx-64` 连续构建已得到一致 wheel SHA-256，具体记录见 `ENVIRONMENT_ACCEPTANCE.md`。

## 官方资料基线

设计以官方资料为语义基线，并在实现期固定访问日期与字段契约：

- [AKShare 安装指导](https://akshare.akfamily.xyz/installation.html)：AKShare 支持 64 位 Python 3.8 及以上，官方推荐 Python 3.11.x，因此项目环境采用 Python 3.11。
- [AKShare A 股数据](https://akshare.akfamily.xyz/data/stock/stock.html)：主源优先使用 `stock_zh_a_hist` 的不复权日线，并对股票列表、停复牌和分红接口建立固定契约。
- [AKShare 工具箱](https://akshare.akfamily.xyz/data/tool/tool.html)：交易日历主源使用 `tool_trade_date_hist_sina` 并保存原始响应。
- [AKQuant 仓库](https://github.com/akfamily/akquant)：底层回测实现参考并固定本地 `v0.3.2` commit，通过 TBCaptial adapter 隔离。
- [AKQuant PyPI](https://pypi.org/project/akquant/)：用于核对公开制品版本和平台 wheel；不能用未固定的最新版替代已验收 backend。
- [Tushare A 股日线行情](https://tushare.pro/document/1?doc_id=27)：作为辅助源交叉校验行情单位与缺失情况。
- [Tushare 股票基础信息](https://tushare.pro/document/1?doc_id=25)：包含上市、退市及市场信息，单次返回数量和调用频次受权限约束。
- [Tushare 交易日历](https://tushare.pro/document/2?doc_id=26)：提供开闭市状态和上一交易日。
- [Tushare A 股复权行情说明](https://tushare.pro/document/2?doc_id=146)：前复权结果与查询结束日相关，因此系统保存原始行情和复权因子，自行构造可复现价格序列。
- [上海证券交易所交易规则（2026 年修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)：作为交易、交收、申报数量及价格单位的规则来源之一。
- [财政部、税务总局关于减半征收证券交易印花税的公告](https://shanghai.chinatax.gov.cn/zcfw/zcfgk/yhs/202308/t468451.html)：费用模型采用带生效日期的规则表，不假设费率永久不变。
- [Conda 环境管理](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html)：区分跨平台环境声明和精确复现需要的锁定表示。
- [DuckDB 读写 Parquet](https://duckdb.org/docs/stable/data/parquet/overview)：支持直接查询 Parquet 以及过滤、列裁剪下推。
- [DuckDB 并发说明](https://duckdb.org/docs/stable/connect/concurrency)：共享目录中的 DuckDB 文件需要谨慎，因此本项目不把它用作跨机器多写者数据库。

## 免责声明

本项目用于研究与工程验证，不构成投资建议。回测结果不代表未来收益；任何进入模拟盘或实盘的策略都必须重新完成数据授权、规则核对、容量评估、风险审查和小资金验证。
