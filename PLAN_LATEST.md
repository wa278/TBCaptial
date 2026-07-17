# TBCaptial 第一阶段详细设计

文档状态：Latest / 第一阶段实施依据

版本：v1.0-draft

日期：2026-07-17
当前状态：仅设计，尚未创建环境文件或实现代码

## 1. 本文件的范围

本文件是第一阶段的唯一执行计划，只设计三部分：

1. Conda 开发与部署环境；
2. Tushare 数据采集、存储、快照和本地查询；
3. A 股日频回测内核。

第一阶段结束时，应能在一台空机器上从 Conda 锁文件创建环境，从共享存储取得指定数据快照，在不访问 Tushare 的情况下运行一个诊断策略，并得到可重复、可对账的回测产物。

本阶段不建设因子平台、策略研究平台、参数搜索、机器学习、Web 页面、模拟盘、实盘、分钟/Tick、分布式计算或微服务。为了验收回测，仅提供最小的固定目标持仓/买入持有诊断策略，不把它扩展为策略框架。

## 2. 第一阶段固定决策

| 项目 | 决策 |
| --- | --- |
| 仓库名 | `TBCaptial` |
| Python 包名 | `tbcaptial` |
| 环境 | Conda，Python 3.12 |
| 依赖源 | `conda-forge`，strict channel priority；确无 Conda 包时才用锁定的 pip 子依赖 |
| 环境声明 | 人工维护 `environment.yml`，机器生成并提交 `conda-lock.yml` |
| 支持平台 | `linux-64`、`osx-64`、`osx-arm64`、`win-64` |
| 系统形态 | Python 模块化单体，一个仓库、一个发布单元、一个 CLI |
| 数据源 | Tushare Pro，只允许采集模块访问 |
| 权威存储 | S3 兼容对象存储 |
| 数据格式 | Raw/Silver 使用 Parquet，manifest 使用 JSON，hash 使用 SHA-256 |
| 本地查询 | 每台机器独立的 DuckDB catalog + 内容寻址 Parquet 缓存 |
| 写入模型 | 单写者发布，多读者消费已发布快照 |
| 回测频率 | A 股现货日频、只做多、不加杠杆 |
| 时间语义 | T 日收盘后产生目标，T+1 日开盘尝试成交 |
| 价格语义 | 未复权价格用于成交和账户；复权因子用于研究价格和公司行为交叉检查 |
| 结果要求 | 同一代码、Conda lock、配置、随机种子和数据快照得到相同事件序列与产物 |

## 3. 第一阶段架构图

```text
                         +----------------------+
                         |     Tushare Pro      |
                         +----------+-----------+
                                    |
                             ingestion only
                                    v
+-----------------------------------------------------------------------------+
| TBCaptial Modular Monolith: one repo / one release / one job process        |
| CLI / Config -> Application Orchestrator                                    |
|   ingest:  Tushare -> Raw -> Normalize -> Quality -> Snapshot Publish       |
|   backtest: Snapshot View -> Clock -> Target -> Orders -> Execute -> Ledger |
| Domain: time, bar, action, order, fill, position, cash ledger, run manifest |
| Adapters: Tushare / local FS / S3 / Parquet / DuckDB / artifact writer      |
+---------------------------------------+-------------------------------------+
                                       |
                         snapshots and run artifacts
                                       v
       +----------------------------------------------------------------+
       |       Authoritative S3-compatible Object Storage               |
       | raw / silver / manifests / snapshots / artifacts / quarantine  |
       +------------------------------+---------------------------------+
                                      |
                              snapshot_id + hashes
                                      v
       +----------------------------------------------------------------+
       |                       Any Runner Machine                       |
       | Conda locked env -> local content cache -> local DuckDB views  |
       +----------------------------------------------------------------+
```

约束：

- 对象存储是基础设施，不是拆出的量化微服务；
- 采集和回测是同一单体的两个命令用例；
- 回测不持有 Tushare client；
- DuckDB 文件不在两台机器之间共享；
- 正式回测必须指定 `snapshot_id`，不读取“最新目录”。

## 4. Conda 开发与部署环境

### 4.1 Conda 引导程序

统一使用 Miniforge 作为 Conda 发行入口，因为它默认以 `conda-forge` 为唯一 channel，并提供 macOS Intel/Apple Silicon、Linux x86-64 和 Windows x86-64 安装器。安装器版本和 SHA-256 写入 bootstrap 说明，开发环境不使用 `base`。

当前开发机检查结果：2026-07-17 时 `conda` 不在 PATH，因此实现的第一项工作是安装并验证 Miniforge，而不是假设系统已有可用 Conda。

### 4.2 需要创建的环境文件

| 文件 | 是否手工编辑 | 作用 |
| --- | --- | --- |
| `environment.yml` | 是 | 环境名、channel、Python 和直接依赖的源声明 |
| `conda-lock.yml` | 否 | 四个平台完整依赖解析、包版本、构建和来源 |
| `pyproject.toml` | 是 | Python 包元数据、构建系统、Ruff/Mypy/Pytest 配置；不再维护第二套依赖解析 |
| `.env.example` | 是 | 只列变量名和说明，不包含 token/secret |
| `Makefile` 或等价任务入口 | 是 | 封装环境检查、测试、数据和回测命令，行为跨平台时由 Python CLI 承担 |

`environment.yml` 是依赖意图，`conda-lock.yml` 是可复现结果。禁止用某台机器的完整 `conda list` 直接替代源声明，也禁止手工编辑锁文件。

### 4.3 环境名和平台

- 环境名固定为 `tbcaptial`；
- Python 固定在 3.12 小版本系列，精确版本由 lock 决定；
- 必须生成 `linux-64`、`osx-64`、`osx-arm64`、`win-64` 解析；
- Linux 是推荐的持续集成和长期运行平台；
- 每个平台至少验证环境创建、包导入、DuckDB/Parquet 读写和黄金回测；
- 平台未通过 smoke test 时不得宣称支持，即使锁文件生成成功。

### 4.4 第一阶段直接依赖

| 类别 | 依赖 | 用途 |
| --- | --- | --- |
| 运行时 | Python 3.12 | 解释器 |
| 数值/表格 | NumPy、pandas | Tushare DataFrame 接口、标准化和小规模研究计算 |
| 文件 | PyArrow | Parquet schema、读写和元数据 |
| 查询 | DuckDB Python | 本地 catalog、Parquet SQL 查询和质量检查 |
| 数据源 | Tushare | Pro SDK；若 Conda 无合适包则进入 pip 子段 |
| 对象存储 | boto3 | S3 兼容对象读写、条件请求和凭证链 |
| 配置 | Pydantic、pydantic-settings、PyYAML | 配置校验和环境变量注入 |
| CLI | Typer | 单一命令入口 |
| 重试 | Tenacity | Tushare/S3 有边界的重试和退避 |
| 开发 | Pytest、pytest-cov、Hypothesis | 单元、覆盖率和账户不变量测试 |
| 质量 | Ruff、Mypy、pandas-stubs | 格式、lint 和静态类型检查 |

直接依赖在 `environment.yml` 中约束兼容的主版本范围，所有直接和间接依赖在 lock 中精确固定。引入新依赖必须说明为什么标准库或现有依赖不够。

### 4.5 安装与更新规则

- 科学计算和原生依赖优先使用 `conda-forge` 包；
- 不混用 `defaults` 和 `conda-forge`；
- 项目开发安装使用 editable + `--no-deps`，避免 pip 再解析依赖；
- 部署安装使用 CI 构建的 wheel + `--no-deps`，记录 wheel hash；
- 临时安装依赖后必须更新 `environment.yml`、重建四平台 lock 并跑完整测试，否则不得合并；
- lock 更新 PR 必须列出 Python、NumPy、pandas、PyArrow、DuckDB、Tushare 的版本变化；
- CI 定期从空环境创建，不能只在长期使用的本地环境验证。

### 4.6 配置和密钥

第一阶段配置分为三个文件域：

- `configs/base.yml`：目录、对象存储非敏感位置、日志、通用默认值；
- `configs/data.yml`：端点、日期、修订窗口、质量阈值；
- `configs/backtest.yml`：snapshot、日期、资金、费用、撮合和诊断目标。

覆盖顺序固定为：代码默认值 < 配置文件 < 环境变量 < CLI 显式参数。最终解析配置去除秘密后写入回测产物。

秘密只从环境变量或系统凭证链读取：

- `TUSHARE_TOKEN`；
- S3 endpoint/region；
- S3 access key/secret/session token 或角色凭证。

日志只记录凭证是否存在，不记录值、长度、前后缀或请求 Authorization 内容。

### 4.7 环境验收

- 四个平台 lock 生成成功；
- 当前开发机和 Linux 从空环境安装成功；
- 核心依赖 import smoke test 通过；
- 本地 Parquet 写入后由 DuckDB 读取，schema 与行数一致；
- 测试 S3 前缀完成上传、HEAD、下载、条件创建和删除；
- 环境 manifest 能输出 Conda 版本、lock hash、平台、Python 和关键包版本；
- 项目测试不依赖用户 base 环境中的隐式包。

## 5. 数据系统详细设计

### 5.1 数据系统目标

- 历史数据只需从 Tushare 完整回填一次；
- 日常仅增量抓取，并定期检测上游修订；
- 任何一台支持平台的机器可以通过 snapshot id 得到相同数据；
- Tushare 暂时不可用时，已缓存快照仍可回测；
- 任一 Silver 行可以追溯到 Raw batch、请求参数和转换版本；
- 数据缺失、权限不足、停牌和非交易日不能互相混淆；
- 旧实验引用的数据永不被新数据静默覆盖。

### 5.2 第一阶段 Tushare 端点

| 优先级 | Tushare 接口 | 拉取方式 | 标准数据集 | 用途 |
| --- | --- | --- | --- | --- |
| 必需 | `trade_cal` | 按年度/交易所 | `trade_calendar` | 回测时钟和缺失判断 |
| 必需 | `stock_basic` | 分 L/P/D 状态低频全量 | `instrument` | 代码、市场、上市/退市状态 |
| 必需 | `daily` | 按交易日全市场 | `daily_bar` | 未复权 OHLCV 与成交额 |
| 必需 | `adj_factor` | 按交易日全市场 | `adjustment_factor` | 研究价格与公司行为交叉检查 |
| 必需 | `stk_limit` | 按交易日全市场 | `daily_price_limit` | 涨跌停可成交判断 |
| 必需 | `suspend_d` | 按交易日 | `suspension` | 区分停牌和数据缺失 |
| 必需 | `dividend` | 固定验收股票按 symbol 回填 | `corporate_action` | 现金分红与送转事件 |
| 必需 | `index_daily` | 指定基准按区间 | `benchmark_bar` | 回测输出对照和日期交叉检查 |

实现前先做权限探针：每个端点用最小请求确认 token 的积分、字段、频率和返回上限。权限不满足时停止 M1，不使用网页抓取或另一个未授权数据源偷偷补齐。

Tushare `daily` 的成交量单位是手、成交额单位是千元；Silver 统一为股和人民币元。全市场日线按交易日拉取，不按几千个 symbol 循环。

### 5.3 存储拓扑

权威仓使用 S3 兼容对象存储，本地文件系统实现只用于测试和离线开发。对象布局：

```text
tbcaptial-data/
├── raw/source=tushare/endpoint=<name>/ingest_date=<date>/batch=<uuid>/
├── silver/dataset=<name>/schema_version=1/year=<yyyy>/month=<mm>/
├── manifests/partitions/<dataset>/<logical_partition>/<version>.json
├── snapshots/<snapshot_id>/manifest.json
├── quality/<quality_run_id>/
├── artifacts/<run_id>/
├── quarantine/<transaction_id>/
└── staging/<writer_id>/<transaction_id>/
```

原则：

- Raw、已发布 Silver 和 snapshot 是不可变对象；
- 日频表按年月组织，文件内包含全市场，不按 symbol 分目录；
- 当日小文件允许临时存在，合并时生成新文件和新 manifest，不原地覆盖；
- Parquet 使用 ZSTD，row group 和目标文件大小由数据量测试后写入 schema 契约；
- manifest 列出确切对象，不用 glob 作为正式快照边界；
- 权威 bucket 启用版本保护，并复制 Raw、snapshot 和正式 artifacts 到独立故障域。

### 5.4 本地缓存与 DuckDB

```text
var/
├── cache/objects/<sha256>/data.parquet
├── cache/snapshots/<snapshot_id>/manifest.json
├── catalog/catalog.duckdb
├── work/
└── runs/<run_id>/
```

缓存算法：

1. 下载 snapshot manifest；
2. 校验 manifest hash 和状态；
3. 根据 SHA-256 找出本地缺失对象；
4. 下载到临时文件并逐个校验；
5. 原子移动到内容寻址缓存；
6. 全部对象就绪后标记本地快照完整；
7. 从 manifest 重建 DuckDB 只读视图。

DuckDB 文件只属于当前机器，可以随时删除重建。禁止放到 NAS、网盘同步目录或由多个进程跨机器写入。运行中的 snapshot 和正式实验引用的 snapshot 必须 pin，LRU 清理不得删除。

### 5.5 Raw 契约

每个 Raw batch 包含：

- Tushare 原始字段的 Parquet；
- `request.json`：endpoint、去密参数、SDK 版本、请求开始/结束时间、重试次数；
- `response.json`：行数、列、响应 hash、是否空、错误分类；
- batch manifest：每个对象 URI、字节数和 SHA-256。

Raw 不做业务修正。若供应商返回重复行、异常单位或空值，原样保存，由 Silver 转换和质量报告处理。

### 5.6 Silver 数据契约

所有表公共列：

- `source`、`source_batch_id`、`source_row_hash`；
- `schema_version`；
- `event_time`、`available_time`、`ingested_at`；
- `quality_status`。

关键表：

| 数据集 | 主键 | 第一阶段关键字段 |
| --- | --- | --- |
| `trade_calendar` | `exchange, calendar_date` | `is_open, previous_trade_date` |
| `instrument` | `symbol, valid_from` | `exchange, board, list_status, list_date, delist_date` |
| `daily_bar` | `symbol, trade_date` | `open, high, low, close, pre_close, volume_shares, amount_cny` |
| `adjustment_factor` | `symbol, trade_date` | `factor` |
| `daily_price_limit` | `symbol, trade_date` | `up_limit, down_limit` |
| `suspension` | `symbol, trade_date, suspend_time` | `suspend_type, resume_time` |
| `corporate_action` | `symbol, ex_date, action_id` | `record_date, pay_date, list_date, cash_per_share, stock_per_share, status` |
| `benchmark_bar` | `index_code, trade_date` | `open, high, low, close, pre_close` |

数值约定：

- 市场矩阵用 float64；数量统一为整数股；
- Silver 价格保留供应商有效精度，不提前转整数分；
- 账户现金和费用使用 Decimal，并在明确边界按人民币分舍入；
- 日期存逻辑 date，时间存带 `Asia/Shanghai` 语义的 timestamp；
- symbol 统一为 `000001.SZ`、`600000.SH`、`*.BJ` 格式。

### 5.7 采集和发布状态机

```text
DISCOVER -> FETCH_RAW -> NORMALIZE -> VALIDATE -> PUBLISH_PARTITION
                                  |              |
                                  | FAIL         v
                                  +--------> QUARANTINE

PUBLISHED_PARTITIONS -> BUILD_SNAPSHOT -> PUBLISHED_SNAPSHOT
```

发布协议：

1. 为任务创建 `transaction_id`，取得带过期时间的 writer lease；
2. Tushare 响应先写 Raw，并计算 hash；
3. 转换到 staging Silver；
4. 做 schema、行数、主键和跨表质量检查；
5. 写 partition manifest；
6. 使用对象存储条件创建发布标记；
7. 将一组分区写入 snapshot manifest；
8. snapshot 最后变为 `PUBLISHED`，研究端才可见。

采集中断不会产生半个可见快照。重复任务发现相同内容 hash 时复用现有版本；内容变化时创建新版本和父子 snapshot，不覆盖旧对象。

### 5.8 回填、日更和修订

首次回填：

- 先保存股票状态和交易日历；
- 根据日历按交易日抓取全市场 daily、adj_factor、stk_limit、suspend_d；
- 固定验收股票抓取 dividend；
- 抓取基准 index_daily；
- 每个 Raw batch 成功后记录进度，Silver 失败不重新消耗 API 配额；
- 完成一个小时间切片并通过验收后，再扩大历史范围。

日常增量：

- 只有交易日运行；
- 接口到达预期更新时间后触发，但以数据完整性而不是固定时钟判断就绪；
- 当日所有必需数据集通过质量门才发布新 snapshot；
- 任一必需端点缺失时 snapshot 保持未发布，已有 snapshot 不受影响。

修订检测：

- 每日重查最近可配置数量的交易日；
- 周期任务使用更长回看窗口；
- 比较 Raw 和 Silver 内容 hash、行数及关键字段；
- 有差异时保存新 Raw、生成字段级摘要、发布子 snapshot；
- 正式实验仍指向原 snapshot，除非显式重跑。

### 5.9 数据质量门

发布前必须通过：

- schema、列类型、必填字段和枚举；
- 主键唯一；
- `low <= open/close <= high`，价格和量额非负；
- 交易日期属于开市日；
- 行情日期在上市/退市范围内；
- `volume_shares = raw_vol * 100`、`amount_cny = raw_amount * 1000` 的转换测试；
- `pre_close`、`close`、涨跌幅在容差内一致；
- OHLC 不越过当日涨跌停边界；
- 缺失行情必须能由停牌、未上市、已退市或质量错误解释；
- 复权因子缺失和跳变有报告；
- corporate action 的实施状态与关键日期完整；
- 当日全市场行数与近期分布差异不超过配置阈值；
- Raw 到 Silver 的每条丢弃/合并都有原因；
- 文件 hash 与 manifest 一致。

质量级别为 `PASS/WARN/FAIL`。第一阶段正式回测只接受 `PASS` snapshot；暂不允许 WARN 白名单，减少隐式例外。

### 5.10 snapshot manifest

manifest 至少包含：

- snapshot id、父 id、创建时间、状态；
- 数据覆盖起止日期；
- 各 dataset/schema/逻辑分区；
- 对象 URI、字节数、行数、SHA-256；
- Raw batch 和转换代码 commit；
- 质量报告 URI 和结果；
- 已知限制；
- manifest 自身 hash。

snapshot id 使用 `YYYYMMDDTHHMMSSZ-<manifest_hash_prefix>`。回测产物保存完整 snapshot id 和 manifest hash。

## 6. 日频回测系统详细设计

### 6.1 回测输入和输出

输入：

- `snapshot_id`；
- 开始/结束交易日；
- 初始现金；
- 固定验收股票集合；
- 诊断目标持仓计划；
- 费用、滑点、订单容量和现金不足规则；
- 随机种子，即使首版没有随机逻辑也固定记录。

输出：

- run manifest；
- 每日现金、总持仓、可卖持仓、市值、总权益；
- 订单、成交、拒单和原因；
- 逐笔费用拆分和现金流水；
- 公司行为应收/实收流水；
- 每日净值和基准净值；
- 数据/配置/代码/环境 hash；
- 结构化日志和最终对账结果。

### 6.2 时间和数据可见性

默认时间线：

```text
Trading day T                                    Trading day T+1

open -------------------------- close            open ---------------- close
                                   |               |
                                   | as-of T       | execute order
                                   v               v
                              build target ---> pending order
```

规则：

- T 日日线只有收盘事件后才进入 `as_of=T close` 视图；
- 目标在 T 收盘后产生，订单最早在 T+1 开盘执行；
- 策略/诊断目标不能取得 T+1 bar、成交量或任何未来 corporate action 公告；
- 回测循环收到的是按日期切片的只读 market view，不接收包含完整未来区间的裸 DataFrame；
- 开盘执行的容量上限使用 T 日之前可见的 trailing ADV，不使用 T+1 全日成交量决定开盘是否成交；T+1 全日成交量只能用于事后诊断。

### 6.3 每日事件顺序 ASCII 图

```text
+---------------------------+
| Advance to trade day T    |
+-------------+-------------+
              v
+---------------------------+
| Apply due corporate action|
| and security status       |
+-------------+-------------+
              v
+---------------------------+
| Unlock T+1 sellable shares|
+-------------+-------------+
              v
+---------------------------+
| Execute pending orders at |
| T open; fill or reject    |
+-------------+-------------+
              v
+---------------------------+
| Update fills, fees, cash, |
| positions and ledger      |
+-------------+-------------+
              v
+---------------------------+
| Mark positions at T close |
+-------------+-------------+
              v
+---------------------------+
| Publish read-only as-of T |
| market and account view   |
+-------------+-------------+
              v
+---------------------------+
| Read diagnostic target    |
| and build T+1 orders      |
+-------------+-------------+
              v
+---------------------------+
| Validate invariants and   |
| persist daily snapshot    |
+---------------------------+
```

事件顺序属于公开回测语义，改动必须升级引擎语义版本并更新黄金结果。

### 6.4 领域对象

| 对象 | 核心字段/职责 |
| --- | --- |
| `TradingSession` | 交易日、开盘/收盘阶段、前后交易日 |
| `Instrument` | symbol、交易所、板块、上市/退市、lot/tick 规则 |
| `DailyBar` | 未复权 OHLC、昨收、股数成交量、人民币成交额 |
| `PriceLimit` | 当日涨停价、跌停价 |
| `CorporateAction` | 登记、除权、支付/上市日期，现金/股票比例 |
| `TargetPosition` | 决策时点、symbol、目标股数、原因 |
| `Order` | id、创建/执行时间、方向、数量、剩余量、状态 |
| `Fill` | order id、成交时间、股数、价格、费用拆分 |
| `Position` | 总数量、可卖数量、待上市红股、成本和市值 |
| `CashLedgerEntry` | 时间、类型、金额、关联 order/action、余额 |
| `PortfolioSnapshot` | 当日现金、持仓、市值、权益和对账字段 |
| `BacktestRun` | run id、状态、输入 hash、引擎版本和产物位置 |

所有 id 和排序规则必须稳定。相同时间的订单按 `side_priority + created_at + symbol + order_id` 确定顺序，禁止依赖 dict/set 的偶然顺序。

### 6.5 订单规划

第一阶段诊断目标直接给出目标股数。订单规划：

1. 比较目标股数和当前总持仓；
2. 卖出量不超过可卖数量；
3. 买入量向下取 100 股整数手；
4. 不足 100 股的存量零股只能一次性卖出；
5. 使用 T 日已知价格和费用预估检查现金；
6. 使用 trailing ADV 和配置比例限制订单名义规模；
7. 先规划卖出再规划买入；
8. 现金不足默认按稳定顺序逐单缩量到可买整手，不能产生负现金；
9. 生成 `execute_not_before=T+1 open` 的订单。

第一阶段只支持市价意图在下一开盘按模拟价格成交，不实现限价单、撤改单和跨日部分成交队列。

### 6.6 撮合和拒单规则

| 场景 | 第一阶段规则 |
| --- | --- |
| 正常交易 | `open * (1 + side * slippage_bps)`，再按 tick 舍入并限制在涨跌停内 |
| 停牌 | 拒单，原因 `SUSPENDED` |
| 应有行情但 bar 缺失 | 回测失败，原因是数据质量，不当作停牌 |
| 买入开盘价达到涨停 | 保守拒单 `LIMIT_UP_LOCKED` |
| 卖出开盘价达到跌停 | 保守拒单 `LIMIT_DOWN_LOCKED` |
| 价格越过当日限制 | 回测失败，说明数据或规则不一致 |
| T+1 可卖不足 | 拒绝或缩量，记录 `T1_NOT_SETTLED` |
| 买入不足 100 股 | 不生成订单 |
| 现金不足 | 按整手确定性缩量；一手仍不足则拒绝 |
| trailing ADV 超限 | 在下单阶段缩量；不使用当日收盘后才知道的成交量 |
| 已退市/未上市 | 拒绝，原因 `NOT_LISTED` |

只有日线无法知道涨跌停排队、开盘瞬间深度和盘中路径，因此规则有意保守。报告必须标记 `daily_open_approximation_v1`。

### 6.7 T+1 和持仓状态

Position 至少分为：

- `total_quantity`；
- `sellable_quantity`；
- `today_bought_quantity`；
- `pending_bonus_quantity`。

买入成交增加总数量和今日买入量，但不增加当日可卖量；下一交易日开盘前结转为可卖。卖出只扣可卖量。红股在 `div_listdate` 入账并按明确规则进入可卖量。

每日结束检查：数量均为非负整数，`sellable <= total`，各分量能还原总数量。

### 6.8 公司行为

第一阶段处理 Tushare `dividend` 中状态为已实施且关键日期完整的两类事件：

- 现金分红：在 record date 收盘记录权益，在 ex date 建立应收股利并计入总权益，在 pay date 按 `cash_div`（税后字段）由应收转为现金；
- 送股/转增：在 record date 记录权益，在 ex date 建立待上市红股并按未复权市价计入总权益，在 div list date 按 `stk_div` 转为正式持仓，整数化差异写入事件日志。

除权日使用未复权价格估值；公司行为入账使账户经济价值连续。复权因子变化用于交叉检查是否存在未覆盖事件。

第一阶段不模拟个体持有期红利税、配股选择、增发、合并和复杂权利。遇到持仓期间无法映射的复权因子跳变，默认立即失败 `UNSUPPORTED_CORPORATE_ACTION`，不能带着错误净值继续。

### 6.9 费用模型

费用规则是带生效日期的配置表，不散落在撮合代码：

- 券商佣金：买卖双向，支持最低单笔佣金；
- 证券交易印花税：卖出侧；
- 过户费/经手费：按市场、方向和生效日期配置；
- 滑点：独立于法定费用，使用 bps；
- 每笔 Fill 保存各费用分项和总额。

黄金测试使用固定测试费率，不依赖“当前默认费率”。真实回测配置在实现时根据实际券商和回测区间核对官方规则。

### 6.10 账户账本和不变量

现金变动全部产生 ledger entry：入金、买入本金、卖出本金、佣金、印花税、其他费用、现金分红。禁止直接修改余额而不留流水。

每个事件后检查：

- 只做多时持仓、现金和可卖量不为负；
- 成交数量为正且不超过订单剩余；
- 买入现金变化 = `-(成交额 + 费用)`；
- 卖出现金变化 = `成交额 - 费用`；
- 当前余额 = 初始现金 + 全部 cash ledger entry；
- 总权益 = 现金 + 持仓市值 + 明确的应收项；
- 当日权益变化可以拆为市场盈亏、交易现金流、公司行为和费用；
- 所有订单最终处于 filled/rejected/expired 中的明确状态。

账本使用 Decimal，费用按配置舍入到分；行情和分析数组使用 float64。转换只发生在成交/估值边界并有单元测试。

### 6.11 回测产物布局

```text
artifacts/<run_id>/
├── manifest.json
├── resolved_config.yml
├── environment.json
├── daily_portfolio.parquet
├── positions.parquet
├── orders.parquet
├── fills.parquet
├── cash_ledger.parquet
├── corporate_actions.parquet
├── rejects.parquet
├── benchmark.parquet
├── summary.json
└── run.log.jsonl
```

`manifest.json` 包含：run id、状态、开始/结束时间、Git commit、工作区是否干净、wheel hash、Conda lock hash、平台、snapshot id/hash、配置 hash、随机种子、引擎语义版本和所有产物 hash。

第一阶段 summary 只需要总收益、基准收益、最大回撤、交易次数、拒单分布、总费用和最终对账状态；不建设完整绩效报告系统。

## 7. 第一阶段计划目录

```text
TBCaptial/
├── README.md
├── PLAN.md
├── PLAN_LATEST.md
├── ARCHITECTURE.txt
├── environment.yml
├── conda-lock.yml
├── pyproject.toml
├── configs/
│   ├── base.yml
│   ├── data.yml
│   └── backtest.yml
├── src/tbcaptial/
│   ├── application/
│   ├── bootstrap/
│   ├── domain/
│   ├── data_source/
│   ├── storage/
│   ├── data_quality/
│   ├── market_data/
│   ├── backtest/
│   ├── execution/
│   ├── accounting/
│   ├── artifacts/
│   └── cli/
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── golden/
│   └── e2e/
└── var/                  # 本地缓存与运行目录，不提交 Git
```

## 8. 开发任务和依赖顺序

任务按依赖顺序执行，不在本文件分配人员。

### 8.1 环境基线

| ID | 任务 | 完成条件 |
| --- | --- | --- |
| ENV-000 | 安装并校验 Miniforge | conda/mamba 可用，安装器版本和 hash 有记录，base 不装项目依赖 |
| ENV-001 | 创建 `environment.yml` | 直接依赖、Python、channel、平台声明完整 |
| ENV-002 | 生成 `conda-lock.yml` | 四平台解析成功，文件提交 Git |
| ENV-003 | 建立 `pyproject.toml` 和包骨架 | editable 安装、CLI 空入口、包导入成功 |
| ENV-004 | 配置 Ruff/Mypy/Pytest | 本地和 CI 使用相同配置 |
| ENV-005 | 建立空环境 smoke test | 当前平台和 Linux 从零安装并运行 |

### 8.2 数据最小切片

| ID | 任务 | 依赖 | 完成条件 |
| --- | --- | --- | --- |
| DATA-001 | 配置和秘密加载 | ENV-003 | token/secret 不泄露，配置可验证 |
| DATA-002 | StorageBackend 本地/S3 | DATA-001 | 上传、hash、条件发布、下载测试通过 |
| DATA-003 | Tushare adapter 和权限探针 | DATA-001 | 必需端点成功或明确权限错误 |
| DATA-004 | Raw batch writer | DATA-002/003 | 原始响应和请求元数据可追溯 |
| DATA-005 | Silver schema/normalizer | DATA-004 | 单位、主键、日期和类型契约通过 |
| DATA-006 | 质量门 | DATA-005 | PASS/WARN/FAIL 和隔离行为通过 |
| DATA-007 | partition/snapshot publisher | DATA-002/006 | 半发布不可见、重复执行幂等 |
| DATA-008 | 本地 cache 和 DuckDB catalog | DATA-007 | 空机器按 snapshot 恢复并查询 |
| DATA-009 | 小切片回填 | DATA-008 | 3–20 只股票、含特殊事件、数据 PASS |
| DATA-010 | 增量与修订检测 | DATA-009 | 新日追加、历史修订不覆盖旧快照 |

### 8.3 回测内核

| ID | 任务 | 依赖 | 完成条件 |
| --- | --- | --- | --- |
| BT-001 | 领域对象和引擎语义版本 | ENV-003 | 类型、不变量和序列化测试通过 |
| BT-002 | 交易日时钟/as-of market view | BT-001 | 未来数据访问测试失败得可解释 |
| BT-003 | 订单规划 | BT-001 | 整手、零股、现金、ADV cap 测试通过 |
| BT-004 | 撮合与拒单 | BT-003 | 正常/停牌/涨跌停/缺行情测试通过 |
| BT-005 | 账户、费用和 T+1 | BT-004 | 现金与持仓逐事件对账 |
| BT-006 | 公司行为 | BT-005 | 现金分红、送转、未知事件 fail-fast |
| BT-007 | 日循环与确定性 | BT-002/005/006 | 同输入事件流逐字段一致 |
| BT-008 | 产物 writer | BT-007 | manifest 和全部 Parquet/hash 完整 |

### 8.4 集成验收

| ID | 任务 | 依赖 | 完成条件 |
| --- | --- | --- | --- |
| INT-001 | 人工黄金市场夹具 | DATA-005/BT-007 | 手算订单、费用、持仓、现金和净值 |
| INT-002 | snapshot 到回测 E2E | DATA-009/BT-008 | 一条命令产出完整 run |
| INT-003 | 跨机器恢复 | INT-002 | 空缓存机器不访问 Tushare，结果 hash 一致 |
| INT-004 | 故障注入 | INT-002 | API 中断、S3 中断、半写和坏 hash 均安全失败 |
| INT-005 | 第一阶段发布门 | 全部 | 完成定义全部通过 |

## 9. 测试设计

### 9.1 必须测试的层次

- 单元：日期、单位、舍入、整手、费用、T+1、订单状态和账本；
- 属性：任意合法事件序列下现金/持仓不变量和确定性；
- 契约：固定 Tushare 响应映射到 Raw/Silver，不在线访问 API；
- 存储集成：本地和 S3 后端行为一致；
- 黄金：人工 10–20 个交易日、3 只股票逐字段对照；
- E2E：固定 Raw → Silver → snapshot → 回测 → artifacts；
- 跨平台 smoke：四平台环境创建和黄金回测；
- 故障：坏 hash、缺对象、重复发布、网络中断、权限不足。

### 9.2 黄金场景必须覆盖

- 正常买入和卖出；
- 当日买入后尝试卖出；
- 买入数量不足一手；
- 一次性卖出零股；
- 停牌无行情；
- 应有行情却缺失；
- 涨停买入失败和跌停卖出失败；
- 现金不足导致确定性缩量；
- 最低佣金、卖出印花税和其他费用；
- 现金分红和送转；
- 不支持的公司行为 fail-fast；
- 多订单竞争现金的稳定顺序；
- snapshot 中对象被篡改后的 hash 失败。

CI 不调用实时 Tushare。在线权限探针和增量采集是单独的手工/计划任务。

## 10. 第一阶段完成定义

以下条件必须全部满足：

- [ ] 四平台 Conda lock 已提交，当前平台和 Linux 可从空环境创建；
- [ ] 核心包导入、Parquet/DuckDB 和 S3 smoke test 通过；
- [ ] 必需 Tushare 权限已探测并记录；
- [ ] 小数据切片的 Raw、Silver、quality、partition manifest 和 snapshot 全部发布；
- [ ] 一台机器采集后，另一台空缓存机器只凭 snapshot id 恢复相同数据 hash；
- [ ] 回测路径没有 Tushare 调用，也不读取 snapshot 范围之外的数据；
- [ ] T 日收盘决策、T+1 开盘成交的时间测试通过；
- [ ] T+1、整手、零股、停牌、涨跌停、现金、费用和 trailing ADV cap 已进入撮合路径；
- [ ] 现金分红和送转进入账户；未知公司行为会安全失败；
- [ ] 订单、成交、持仓、现金、公司行为和权益可以逐日对账；
- [ ] 黄金测试、属性测试、集成测试和 E2E 全部通过；
- [ ] 相同代码、lock、snapshot、配置和种子重复运行，事件和产物 hash 一致；
- [ ] 采集中断、重复采集、历史修订和损坏对象不会污染已发布 snapshot；
- [ ] run manifest 记录所有环境、数据、代码、配置和产物 hash；
- [ ] 仓库文档与实际命令、目录和语义一致。

第一阶段完成前，不扩展到本文件范围之外。

## 11. 实现前需要提供的外部配置

这些值不影响架构，但实现和验收前必须配置：

- 可用的 `TUSHARE_TOKEN` 及必需接口权限；
- S3 兼容 endpoint、region、bucket 和最小权限凭证；
- 独立备份位置；
- 验收股票集合、日期范围和基准指数；
- 初始资金；
- 实际券商佣金、最低佣金及需要计入的其他费用；
- 默认滑点 bps、trailing ADV 窗口和最大占比。

秘密值不得提交 Git。非秘密值写入版本化配置并进入 run manifest。

## 12. 官方资料

- [Tushare A 股日线](https://tushare.pro/document/1?doc_id=27)
- [Tushare 股票基础信息](https://tushare.pro/document/1?doc_id=25)
- [Tushare 交易日历](https://tushare.pro/document/2?doc_id=26)
- [Tushare A 股复权说明](https://tushare.pro/document/2?doc_id=146)
- [Tushare 分红送股](https://tushare.pro/document/2?doc_id=103)
- [Conda 环境管理](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html)
- [Miniforge](https://github.com/conda-forge/miniforge)
- [conda-lock](https://conda.github.io/conda-lock/)
- [DuckDB Parquet](https://duckdb.org/docs/stable/data/parquet/overview)
- [DuckDB S3 API](https://duckdb.org/docs/stable/core_extensions/httpfs/s3api)
- [DuckDB 并发模型](https://duckdb.org/docs/stable/connect/concurrency)
- [上海证券交易所交易规则（2026 年修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)
- [深圳证券交易所交易规则（2026 年修订）](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)
- [证券交易印花税减半公告](https://shanghai.chinatax.gov.cn/zcfw/zcfgk/yhs/202308/t468451.html)

实现开始当天再次核对接口权限、字段和交易规则，并将核对日期写入数据契约和费用配置。
