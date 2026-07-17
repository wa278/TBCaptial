# TBCaptial 第一阶段详细设计

文档状态：Latest / 第一阶段实施依据

版本：v1.0-draft

日期：2026-07-17
当前状态：环境、AKShare Raw 小切片和 AKQuant `v0.3.2` 最小 adapter 已验收；Silver、正式 snapshot、执行守卫和完整产物链路待实现

## 1. 本文件的范围

本文件是第一阶段的唯一执行计划，只设计三部分：

1. Conda 开发与部署环境；
2. AKShare 主数据采集、Tushare 辅助补充、存储、快照和本地查询；
3. A 股日频回测内核。

第一阶段结束时，应能在一台空机器上从 `environment.yml` 创建环境，导入或读取本地指定数据快照，在不访问 AKShare/Tushare 的情况下运行一个诊断策略，并得到可重复、可对账的回测产物。

本阶段不建设因子平台、策略研究平台、参数搜索、机器学习、Web 页面、模拟盘、实盘、分钟/Tick、分布式计算或微服务。为了验收回测，仅提供最小的固定目标持仓/买入持有诊断策略，不把它扩展为策略框架。

## 2. 第一阶段固定决策

| 项目 | 决策 |
| --- | --- |
| 仓库名 | `TBCaptial` |
| Python 包名 | `tbcaptial` |
| 环境 | Conda，Python 3.11 |
| 依赖源 | `conda-forge`，strict channel priority；确无 Conda 包时才用锁定的 pip 子依赖 |
| 环境声明 | 人工维护 `environment.yml`；不生成多平台 lock |
| 平台验收 | 当前开发机 `osx-64`；其他机器使用时再执行同一 smoke test |
| 系统形态 | Python 模块化单体，一个仓库、一个发布单元、一个 CLI |
| 数据源 | AKShare 为主、Tushare 为辅；只允许采集模块访问 |
| 权威存储 | 单机本地文件系统；不使用云存储或外部存储服务 |
| 数据格式 | Raw/Silver 使用 Parquet，manifest 使用 JSON，hash 使用 SHA-256 |
| 本地查询 | 本机 DuckDB catalog 直接查询本地 Parquet |
| 写入模型 | 单写者发布，多读者消费已发布快照 |
| 回测频率 | A 股现货日频、只做多、不加杠杆 |
| 时间语义 | T 日收盘后产生目标，T+1 日开盘尝试成交 |
| 价格语义 | 未复权价格用于成交和账户；复权因子用于研究价格和公司行为交叉检查 |
| 结果要求 | 同一代码、实际 Conda 包清单、配置、随机种子和数据快照得到相同事件序列与产物 |

## 3. 第一阶段架构图

```text
                         +----------------------+
                         | AKShare / Tushare    |
                         | primary / auxiliary  |
                         +----------+-----------+
                                    |
                             ingestion only
                                    v
+-----------------------------------------------------------------------------+
| TBCaptial Modular Monolith: one repo / one release / one job process        |
| CLI / Config -> Application Orchestrator                                    |
|   ingest:  Provider -> Raw -> Normalize -> Quality -> Snapshot Publish      |
|   backtest: Snapshot View -> Clock -> Target -> Orders -> Execute -> Ledger |
| Domain: time, bar, action, order, fill, position, cash ledger, run manifest |
| Adapters: AKShare / Tushare / AKQuant / local FS / Parquet / DuckDB         |
+---------------------------------------+-------------------------------------+
                                       |
                         snapshots and run artifacts
                                       v
       +----------------------------------------------------------------+
       |             Authoritative Local Data Directory                 |
       | raw / silver / manifests / snapshots / artifacts / quarantine  |
       +------------------------------+---------------------------------+
                                      |
                              snapshot_id + hashes
                                      v
       +----------------------------------------------------------------+
       |                    Local Runner Process                        |
       | Conda environment -> snapshot manifest -> local DuckDB views  |
       +----------------------------------------------------------------+
```

约束：

- 数据文件和 DuckDB 均位于本机，不依赖云服务器、NAS 或网盘；
- 采集和回测是同一单体的两个命令用例；
- 回测不持有 AKShare/Tushare client；
- 跨机器只通过显式导出/复制/校验快照包迁移数据，不共享 DuckDB 文件；
- 正式回测必须指定 `snapshot_id`，不读取“最新目录”。

## 4. Conda 开发与部署环境

### 4.1 Conda 引导程序

统一使用 Miniforge 作为推荐 Conda 发行入口，因为它默认以 `conda-forge` 为唯一 channel。`scripts/create_conda_env.sh` 先发现现有 Conda；macOS/Linux 未发现时，下载固定版本安装器、核验官方 SHA-256 后安装到独立前缀。Windows 先按 Miniforge 官方方式安装，再从仓库根目录创建环境。项目依赖不装进 `base`。

当前开发机已经安装并验收 Miniforge `26.3.2-2`，环境位于 `/Users/wa/miniforge3/envs/tbcaptial`。验收事实单独记录在 `ENVIRONMENT_ACCEPTANCE.md`；设计文档不把该绝对路径当作其他机器的约定。

### 4.2 需要创建的环境文件

| 文件 | 是否手工编辑 | 作用 |
| --- | --- | --- |
| `environment.yml` | 是 | 环境名、channel、Python 和直接依赖的源声明 |
| `pyproject.toml` | 是 | Python 包元数据、构建系统、Ruff/Mypy/Pytest 配置；不再维护第二套依赖解析 |
| `.env.example` | 是 | 只列变量名和说明，不包含 token/secret |
| `scripts/create_conda_env.sh` | 是 | 发现/引导 Conda，幂等创建或更新环境并执行 smoke test |
| `scripts/activate_conda_env.sh` | 是 | 通过 `source` 发现 Conda 并激活项目环境 |
| `scripts/verify_conda_env.py` | 是 | 离线验证解释器、核心包、Parquet 和本地 DuckDB |
| `.gitmodules` / `scripts/init_submodules.sh` | 是 | 绑定 AKQuant fork 与固定 gitlink，初始化并校验 remote/commit |
| `scripts/install_akquant_backend.sh` | 是 | 校验 AKQuant submodule commit，固定构建时间，构建/安装 wheel 并输出 SHA-256 |
| `Makefile` | 是 | 封装环境创建和验收命令；业务行为以后由 Python CLI 承担 |

`environment.yml` 是唯一依赖源声明。按当前决策不生成或提交多平台 lock，也不维护 `requirements.txt`。为使正式运行可审计，run manifest 保存 `environment.yml` hash，另外保存当次 `conda list --explicit`、Conda/Python/关键包版本和平台；该记录描述实际运行环境，不反向替代源声明。

### 4.3 环境名和平台

- 环境名固定为 `tbcaptial`；
- Python 固定在 3.11 小版本系列；AKShare 官方安装文档推荐 Python 3.11.x；
- `environment.yml` 不写平台专属路径，目标是在 Conda 支持的 64 位 macOS/Linux/Windows 主机上使用；
- 当前只验收实际开发机 `osx-64`，不进行四平台求解或试装；
- 新机器首次部署必须运行同一个离线 smoke test，通过后才加入已验收平台记录；
- Linux 可以作为以后长期运行平台，但在真正部署和验收前不宣称已支持。

### 4.4 第一阶段直接依赖

| 类别 | 依赖 | 用途 |
| --- | --- | --- |
| 运行时 | Python 3.11 | 解释器；优先满足 AKShare 官方推荐版本 |
| 数值/表格 | NumPy、pandas | AKShare/Tushare DataFrame 接口、标准化和小规模研究计算 |
| 文件 | PyArrow | Parquet schema、读写和元数据 |
| 查询 | DuckDB Python | 本地 catalog、Parquet SQL 查询和质量检查 |
| 主数据源 | AKShare | 优先实现的财经数据接口；进入 pip 子段并受兼容版本范围约束 |
| 辅助数据源 | Tushare | 用于字段补充和交叉校验；进入 pip 子段并受兼容版本范围约束 |
| 回测 backend | AKQuant | 固定 `v0.3.2` commit；环境声明 Rust/Maturin、Polars/Plotly 等依赖，wheel 由受检源码本地构建 |
| 配置 | Pydantic、pydantic-settings、PyYAML | 配置校验和环境变量注入 |
| CLI | Typer | 单一命令入口 |
| 重试 | Tenacity | AKShare/Tushare 请求有边界的重试和退避 |
| 开发 | Pytest、pytest-cov、Hypothesis | 单元、覆盖率和账户不变量测试 |
| 质量 | Ruff、Mypy、pandas-stubs | 格式、lint 和静态类型检查 |

直接依赖在 `environment.yml` 中约束兼容版本范围。没有 lock 时，不同日期重新求解可能得到不同的间接依赖版本；正式运行通过实际包清单留痕，发现环境差异时才能准确复建和比较。引入新依赖必须说明为什么标准库或现有依赖不够。

### 4.5 安装与更新规则

- 科学计算和原生依赖优先使用 `conda-forge` 包；
- 不混用 `defaults` 和 `conda-forge`；
- 项目开发安装使用 editable + `--no-deps`，避免 pip 再解析依赖；
- 部署安装使用 CI 构建的 wheel + `--no-deps`，记录 wheel hash；
- 临时安装依赖后必须更新 `environment.yml` 并在当前目标平台从声明重新更新环境、执行 smoke test，否则不得合并；
- 依赖更新必须列出 Python、NumPy、pandas、PyArrow、DuckDB、AKShare、Tushare 的实际版本变化；
- 新部署机器从声明创建，不能依赖某台开发机 `base` 中的隐式包。

### 4.6 配置和密钥

第一阶段配置分为三个文件域：

- `configs/base.yml`：本地数据目录、日志和通用默认值；
- `configs/data.yml`：端点、日期、修订窗口、质量阈值；
- `configs/backtest.yml`：snapshot、日期、资金、费用、撮合和诊断目标。

覆盖顺序固定为：代码默认值 < 配置文件 < 环境变量 < CLI 显式参数。最终解析配置去除秘密后写入回测产物。

秘密只从环境变量读取：`TUSHARE_TOKEN`。本地数据根目录使用普通配置，不属于秘密。

日志只记录凭证是否存在，不记录值、长度、前后缀或请求 Authorization 内容。

### 4.7 环境验收

- 当前开发机从空环境安装成功，重复执行创建脚本能幂等更新；
- Bash/Zsh 均能通过 `source scripts/activate_conda_env.sh` 激活，直接执行会给出明确错误；
- 核心依赖 import smoke test 通过；
- 本地 Parquet 写入后由 DuckDB 读取，schema 与行数一致；
- 本地临时目录完成 ZSTD Parquet 和 DuckDB 文件写入/读取，不访问云服务；
- 环境记录能输出 Conda 版本、`environment.yml` hash、实际包清单、平台、Python 和关键包版本；
- 项目测试不依赖用户 base 环境中的隐式包。

本章环境部分和当前平台 AKQuant 构建基线已经完成；当前开发机的实测结果见 `ENVIRONMENT_ACCEPTANCE.md`。按用户决定，本阶段不执行其他平台试装。

## 5. 数据系统详细设计

### 5.1 数据系统目标

- 历史行情优先从 AKShare 回填并永久保留 Raw，Tushare 只补充 AKShare 缺失的标准字段或做交叉校验；
- 日常仅增量抓取，并定期检测上游修订；
- 任一支持平台可以在导入同一快照包后通过 snapshot id 得到相同数据；
- AKShare/Tushare 暂时不可用时，本地已有快照仍可回测；
- 任一 Silver 行可以追溯到 Raw batch、请求参数和转换版本；
- 数据缺失、权限不足、停牌和非交易日不能互相混淆；
- 同一逻辑分区只能有一个明确 provider；fallback 必须产生新分区版本，禁止逐行静默拼接两个来源；
- 旧实验引用的数据永不被新数据静默覆盖。

### 5.2 第一阶段数据接口与优先顺序

| 实现顺序 | 标准数据集 | AKShare 主接口 | Tushare 辅助接口 | 决策 |
| --- | --- | --- | --- | --- |
| 1 | `trade_calendar` | `tool_trade_date_hist_sina` | `trade_cal` | AKShare 发布，Tushare 只做日期交叉检查 |
| 2 | `instrument` | `stock_info_a_code_name` + 交易所上市信息接口 | `stock_basic` 的 L/P/D 状态 | AKShare 建当前清单；历史上市/退市状态缺口由 Tushare 显式补充 |
| 3 | `daily_bar` | `stock_zh_a_hist(adjust="")` | `daily` | AKShare 是正式主源；Tushare 仅做抽样对账或整分区 fallback |
| 4 | `benchmark_bar` | `stock_zh_index_daily` | `index_daily` | AKShare 主源 |
| 5 | `suspension` | `stock_tfp_em` | `suspend_d` | AKShare 主源；空结果和接口失败必须区分 |
| 6 | `corporate_action` | `stock_history_dividend_detail` | `dividend` | AKShare 主源，Tushare 补缺并保留行级血缘 |
| 7 | `adjustment_factor` | 先做 AKShare 能力契约探针 | `adj_factor` | 未确认稳定 AKShare 契约前由 Tushare 提供独立辅助分区 |
| 8 | `daily_price_limit` | 不使用仅覆盖近期的涨停池代替历史全量 | `stk_limit` | 第一阶段正式回测由 Tushare 辅助分区提供；无该分区时只能跑合成黄金夹具 |

AKShare 适配器先完成前六项的固定响应契约、单位映射和小切片回填，再开始 Tushare 辅助适配器。AKShare 接口可能来自不同上游站点，Raw manifest 必须记录 AKShare 版本、函数名、非敏感参数、抓取时间、原始列和响应 hash；接口列变化不能被宽松重命名悄悄吞掉。

`stock_zh_a_hist` 按股票代码和日期区间拉取，因此 P0 先用 3–20 只验收股票控制调用量，并做有界并发、重试和断点续传。Tushare 的全市场日线 fallback 按交易日拉取。两个 provider 的抓取粒度不同，但发布后的 Silver schema 相同；provider 选择写入分区 manifest，不由策略决定。

单位转换按接口分别定义：AKShare `stock_zh_a_hist` 的成交量由手乘 100 转为股，成交额按人民币元保留；Tushare `daily` 的成交量由手乘 100、成交额由千元乘 1000。禁止把一套供应商单位公式套到另一个接口。

### 5.3 存储拓扑

权威仓就是本机文件系统中的数据根目录。该目录不提交 Git、不放在网盘同步目录，也不依赖远端挂载。目录布局：

```text
tbcaptial-data/
├── raw/source=<akshare|tushare>/endpoint=<name>/ingest_date=<date>/batch=<uuid>/
├── silver/dataset=<name>/schema_version=1/year=<yyyy>/month=<mm>/
├── manifests/partitions/<dataset>/<logical_partition>/<version>.json
├── snapshots/<snapshot_id>/manifest.json
├── quality/<quality_run_id>/
├── artifacts/<run_id>/
├── quarantine/<transaction_id>/
└── staging/<writer_id>/<transaction_id>/
```

原则：

- Raw、已发布 Silver 和 snapshot 文件是不可变的；
- 日频表按年月组织，文件内包含全市场，不按 symbol 分目录；
- 当日小文件允许临时存在，合并时生成新文件和新 manifest，不原地覆盖；
- Parquet 使用 ZSTD，row group 和目标文件大小由数据量测试后写入 schema 契约；
- manifest 列出确切相对文件路径，不用 glob 作为正式快照边界；
- Raw、snapshot 和正式 artifacts 可定期复制到另一块本地磁盘；备份目录不得作为运行时读写目录。

### 5.4 本地数据目录与 DuckDB

```text
var/
├── data/                    # 权威本地数据根目录
├── catalog/catalog.duckdb   # 可从 manifest 重建
├── work/                    # staging 和临时文件
└── runs/<run_id>/           # 本地实验产物
```

读取流程：

1. 读取本地 snapshot manifest；
2. 校验 manifest hash、状态和所有相对路径；
3. 校验 manifest 所列 Parquet 文件的 SHA-256；
4. 全部文件就绪后，从 manifest 建立 DuckDB 只读视图；
5. 回测期间固定 snapshot id，不自动读取后来写入的分区。

DuckDB 文件只属于当前机器，可以随时删除重建。禁止放到 NAS、网盘同步目录或由多个进程跨机器写入。运行中的 snapshot 和正式实验引用的 snapshot 必须 pin，本地清理任务不得删除其引用文件。

### 5.5 Raw 契约

每个 Raw batch 包含：

- provider 原始字段的 Parquet；
- `request.json`：provider、endpoint、去密参数、SDK 版本、请求开始/结束时间、重试次数；
- `response.json`：行数、列、响应 hash、是否空、错误分类；
- batch manifest：每个相对文件路径、字节数和 SHA-256。

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

1. 为任务创建 `transaction_id`，取得本机排他文件锁；
2. provider 响应先写对应 source 的 Raw，并计算 hash；
3. 转换到 staging Silver；
4. 做 schema、行数、主键和跨表质量检查；
5. 写 partition manifest；
6. 在同一文件系统内原子重命名已验证目录，并以排他创建方式写发布标记；
7. 将一组分区写入 snapshot manifest；
8. snapshot 最后变为 `PUBLISHED`，研究端才可见。

采集中断不会产生半个可见快照。重复任务发现相同内容 hash 时复用现有版本；内容变化时创建新版本和父子 snapshot，不覆盖旧对象。

### 5.8 回填、日更和修订

首次回填：

- 先用 AKShare 保存交易日历、当前股票清单和交易所上市信息；
- 按 symbol/日期区间抓取 AKShare 未复权日线，随后抓基准、停复牌和公司行为；
- 对固定验收切片做 schema/单位/日期契约验证并发布 AKShare 主分区；
- 再按需用 Tushare 保存历史 L/P/D 状态、复权因子和每日涨跌停，或对 AKShare 主分区做抽样对账；
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
- provider-specific 单位转换：AKShare 量乘 100、额保持元；Tushare 量乘 100、额乘 1000；
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
- 相对文件路径、字节数、行数、SHA-256；
- Raw batch 和转换代码 commit；
- 质量报告相对路径和结果；
- 已知限制；
- manifest 自身 hash。

snapshot id 使用 `YYYYMMDDTHHMMSSZ-<manifest_hash_prefix>`。回测产物保存完整 snapshot id 和 manifest hash。

## 6. 日频回测系统详细设计

### 6.1 AKQuant 接口参考与取舍

本章接口和实现边界参考 `third_party/akquant` submodule，重点检查了 `run_backtest`、`Strategy`、`StrategyContext`、`DataFeedAdapter`、`BacktestResult`、目标权重示例、T+1、多标的一致性、自定义撮合器和公司行为实现。检查基线为 `git@github.com:wa278/akquant.git` 的固定 commit `2924e0cff36669a3563ffb5cb139da0ba9254045`，构建后包版本为 `0.3.2`，许可证为 MIT；不依赖 fork 中存在 Git tag。

固定决策：**TBCaptial 不从零重写事件循环、订单状态机和基础账户引擎；第一阶段以 AKQuant 作为进程内嵌的底层回测引擎，并通过 `AkQuantBacktestEngine` 适配器封装。** 领域、策略和应用层只能依赖 TBCaptial 自己的端口，不能 import、继承或返回 AKQuant 类型。这样既复用 Rust/Python 引擎，也保留将来替换后端、升级版本和修正 A 股语义的边界。

```text
TBCaptial public API: run_backtest(BacktestRequest, StrategySpec)
          |
          +--> SnapshotFeedAdapter ------> pinned local snapshot
          |
          +--> TBCaptialStrategyBridge --> internal AKQuant Strategy
          |        AKQuant on_bar stream -> bucket -> one BarSlice/on_bars
          |        target intent          -> rebalance_weights
          |
          +--> AShareExecutionGuard ------> suspension / price-limit semantics
          |
          +--> AkQuantBacktestEngine -----> aq.run_backtest (same process)
          |
          +<-- ResultTranslator ---------- AKQuant result/events
                     |
                     v
              immutable artifacts + TBCaptial BacktestResult
```

| 能力 | 归属 | 说明 |
| --- | --- | --- |
| 快照选择、hash、as-of、历史窗口 | TBCaptial | AKQuant 不得自行发现“最新数据”或在线拉取数据 |
| Rust 事件循环、基础订单/成交/持仓/现金 | AKQuant | 通过单个 adapter 调用，不向上泄露 engine 实例 |
| `BarSlice`、策略 context、目标组合命令 | TBCaptial | 内部 bridge 转换到 AKQuant `on_bar/rebalance_weights` |
| T+1、买入整手、基础佣金/印花税 | AKQuant + adapter 配置 | TBCaptial 契约测试锁定行为，不直接信任默认值 |
| 停牌、涨跌停、历史可交易状态 | TBCaptial A 股执行守卫 | 数据来自 snapshot；不得只用 `volume=0` 猜停牌 |
| 公司行为、带生效日费用、账本审计 | TBCaptial 契约 + AKQuant 扩展点 | 未达到本章语义时必须 fail-fast，不能静默降级 |
| 结果 schema、manifest、hash、对账 | TBCaptial | 不使用推测性 fallback，也不返回可变 strategy/engine |

已经识别三个接口缺口，其中制品固定问题已经关闭，另外两项仍是进入正式回测前的门禁：

1. **已关闭（BT-000）**：AKQuant submodule 固定到 `git@github.com:wa278/akquant.git` commit `2924e0cff36669a3563ffb5cb139da0ba9254045`；安装脚本拒绝未知/脏源码，以 commit 时间固定 `SOURCE_DATE_EPOCH`，并校验构建后包版本为 `0.3.2`。当前 `osx-64` 连续构建 wheel hash 一致，并验证导入和最小真实回测。
2. 当前 Python 自定义 matcher 对“无成交的拒单”不能完整回写订单状态，而默认 `volume <= 0` 只让订单继续挂起；需要在固定 AKQuant 版本上补齐拒单扩展或使用可证明等价的执行守卫，黄金测试必须看到稳定 `SUSPENDED/LIMIT_*` 拒单事件。
3. AKQuant 简单公司行为只按一个日期处理 split/dividend，费用也主要是运行级费率；TBCaptial 的 record/ex/pay/list 多日期账本和跨费率生效日回测需要明确扩展。能力未完成时，实际持仓跨越相关事件或费用切换日期必须拒绝运行。

| AKQuant 设计 | TBCaptial 对应设计 | 第一阶段取舍 |
| --- | --- | --- |
| `run_backtest(data, strategy, ..., config)` | `AkQuantBacktestEngine.run(request, strategy)` | 只由 adapter 展开参数，外部仍是单一严格入口 |
| `Strategy` 生命周期回调 | `TBCaptialStrategyBridge` + 精简公开 `Strategy` | bridge 是唯一允许继承 AKQuant Strategy 的类型 |
| `StrategyContext.history/position/cash` | 只读 `StrategyContext` 查询面 | 所有读取强制绑定 `as_of` 和 snapshot |
| `order_target*`、`rebalance_weights` | `set_target_positions/weights` | P0 只允许目标组合，不向策略暴露 `buy/sell` |
| `DataFeedAdapter` | `SnapshotFeedAdapter` | 正式运行只读 manifest 文件；DataFrame 仅用于测试夹具 |
| `BacktestResult` 的曲线和 DataFrame 属性 | `ResultTranslator` + TBCaptial `BacktestResult` | 复制并校验必需字段，不暴露可变 engine/strategy 实例 |
| `on_order → on_trade → on_bar` 顺序契约 | 带序号的 order/fill/reject 事件 | 顺序写入引擎语义版本和黄金测试 |

明确偏离：

- 正式接口不接受裸 `DataFrame`、数据目录 glob 或“最新数据”，只接受 `snapshot_id`；
- `run_backtest` 不提供 `**kwargs` 兼容层，未知配置字段立即失败；
- 多标的日频策略收到同一交易日的完整 `BarSlice`，而不是逐 symbol 调用 `on_bar`。这避免横截面策略手工聚合回调和依赖 symbol 到达顺序；
- T+1、整手和 T 日收盘决策/T+1 开盘执行是 A 股 P0 固定语义，不是可随手关闭的布尔开关；
- P0 adapter 固定 `next open`；即使 AKQuant 支持也不开放同周期成交、限价/止损/OCO、tick、timer、动态策略源码、多策略 slot 或实盘 broker 接口。

### 6.2 唯一公开运行入口

Python 公开接口固定为：

```python
def run_backtest(
    request: BacktestRequest,
    strategy: StrategySpec,
) -> BacktestResult:
    ...
```

调用形态：

```python
result = run_backtest(
    request=BacktestRequest(
        data=SnapshotSelection(
            snapshot_id="20260717T090000Z-ab12cd34",
            symbols=("000001.SZ", "600000.SH"),
            start_date="2024-01-01",
            end_date="2024-06-30",
            benchmark="000300.SH",
        ),
        account=AccountConfig(initial_cash="1000000.00"),
        execution=ExecutionConfig(),
        costs=CostConfig.from_file("configs/fees.yml"),
        random_seed=0,
    ),
    strategy=StrategySpec(
        strategy_type=MomentumStrategy,
        parameters={"lookback": 20, "top_n": 5},
    ),
)
```

`BacktestRequest`、所有子配置和 `StrategySpec` 均使用严格 Pydantic 模型：冻结实例、`extra="forbid"`、禁止 NaN/Inf、日期和金额在进入引擎前规范化。CLI 只是加载 YAML/环境变量并构造同一请求，不另建一套执行路径。

`BacktestRequest` 分为：

| 配置对象 | 关键字段 | 约束 |
| --- | --- | --- |
| `SnapshotSelection` | snapshot、symbols/universe、日期、benchmark | 快照存在且 hash 通过；日期必须是其子区间 |
| `AccountConfig` | initial cash、cash shortfall policy | P0 只做多、不加杠杆、现金不得为负 |
| `ExecutionConfig` | 决策/成交阶段、滑点、容量、排序 | P0 固定 `T_CLOSE → T+1_OPEN`，只能调参数不能改因果顺序 |
| `CostConfig` | 带生效日期的佣金、印花税、过户/经手费 | 覆盖整个回测区间，不允许缺口 |
| `ArtifactConfig` | 本地输出根目录、日志级别、是否保存调试事件 | 输出目录必须在本地，运行目录不可预先存在 |

输入验证通过后生成 `run_id` 和 resolved request hash。运行中任何异常都必须落一个状态为 `FAILED` 的 manifest；不得只抛异常而丢失已发生事件。

`AkQuantBacktestEngine` 使用白名单映射，不把请求对象直接 `**dict` 展开：

| TBCaptial 输入 | AKQuant 内部调用 |
| --- | --- |
| `SnapshotSelection` | `SnapshotFeedAdapter`、明确 symbols/start/end；禁用默认 catalog |
| `T_CLOSE → T+1_OPEN` | `fill_policy={price_basis: open, bar_offset: 1, temporal: next_event}` |
| A 股现货 | `t_plus_one=True`、每标的 `lot_size/tick_size`、`Asia/Shanghai` |
| `CostConfig` | 明确 commission/stamp/transfer/minimum；不使用 AKQuant 随版本变化的默认费率 |
| `StrategySpec` | 内部 `TBCaptialStrategyBridge` 实例；`strict_strategy_params=True` |
| 事件审计 | `on_event` 转换为带稳定序号的 TBCaptial 事件并立即写暂存产物 |

日线 feed 为每个交易日、每个 expected symbol 生成确定性顺序。未上市、退市和停牌状态通过 `Bar.extra` 的内部编码及独立 tradability 表传给执行守卫；公开策略只看到类型化 `TradabilityView`。adapter 不调用 AKShare/Tushare，不允许 AKQuant 默认 catalog、动态策略源码、broker profile 或兼容模式改变已固定语义。

### 6.3 Strategy 生命周期接口

```python
class Strategy(ABC):
    def initialize(self, ctx: StrategyContext) -> None: ...
    def on_start(self, ctx: StrategyContext) -> None: ...
    def on_before_trading(self, ctx: StrategyContext, session: TradingSession) -> None: ...
    def on_bars(self, ctx: StrategyContext, bars: BarSlice) -> None: ...
    def on_order(self, ctx: StrategyContext, event: OrderEvent) -> None: ...
    def on_fill(self, ctx: StrategyContext, event: FillEvent) -> None: ...
    def on_reject(self, ctx: StrategyContext, event: RejectEvent) -> None: ...
    def on_after_trading(self, ctx: StrategyContext, portfolio: PortfolioView) -> None: ...
    def on_stop(self, ctx: StrategyContext, reason: StopReason) -> None: ...
```

生命周期约束：

- 框架创建策略实例后先绑定 context，再调用一次 `initialize`；构造函数只保存参数，不读取市场或账户；
- warmup 历史准备完成后调用一次 `on_start`；
- `on_before_trading`、订单/成交/拒单回调和 `on_after_trading` 默认只读，P0 只有 `on_bars` 可以提交目标组合；
- `on_stop` 无论成功或失败都最多调用一次，不能继续下单；
- 回调异常在 P0 一律 fail-fast，保存回调名、交易日和最后事件序号，不提供“记录后继续”的模糊模式；
- 同一 strategy 类型和参数必须能生成稳定 `strategy_id`；参数未知、不可 JSON 序列化或构造函数不匹配时在运行前失败。

### 6.4 StrategyContext：只读查询与受控命令

`StrategyContext` 是框架拥有的门面，不把 engine、ledger、DuckDB connection 或未来 DataFrame 暴露给策略。

只读属性/方法：

```python
ctx.now: datetime
ctx.trade_date: date
ctx.phase: TradingPhase
ctx.snapshot_id: str
ctx.cash: Decimal
ctx.equity: Decimal
ctx.position(symbol) -> PositionView
ctx.positions() -> Mapping[str, PositionView]
ctx.instrument(symbol) -> InstrumentView
ctx.history(symbol, fields, count, *, adjusted=True) -> HistoryWindow
ctx.open_orders() -> tuple[OrderView, ...]
```

受控命令：

```python
ctx.set_target_positions(
    targets: Mapping[str, int],
    *,
    liquidate_unmentioned: bool,
    reason: str,
) -> IntentReceipt

ctx.set_target_weights(
    targets: Mapping[str, float],
    *,
    liquidate_unmentioned: bool,
    tolerance: float = 0.0,
    reason: str,
) -> IntentReceipt

ctx.record_metric(name: str, value: JsonScalar) -> None
```

命令约束：

- 同一策略每个交易日最多提交一个目标组合；第二次提交拒绝为 `DUPLICATE_TARGET_INTENT`，避免隐式覆盖；
- `liquidate_unmentioned` 必须显式填写，防止遗漏 symbol 时意外保留或清仓；
- 权重必须有限、非负且总和不超过 1；目标股数必须是非负整数；P0 不允许杠杆和做空；
- `IntentReceipt` 只表示意图被引擎接收，字段为 `intent_id/accepted/reason/submitted_at`，不代表会成交；
- 策略不能直接改持仓、现金、费用、可卖量或订单状态。

### 6.5 多标的 BarSlice 与历史窗口

AKQuant 的 `on_bar(bar)` 按 symbol 触发；其横截面目标权重示例需要按 timestamp 手工收集全部 symbol。内部 `TBCaptialStrategyBridge` 负责该收集，按 snapshot 中的 `expected_symbols` 判定切片完整后只向公开策略发一次 `BarSlice`；用户策略不接触 bucket 和到达顺序：

```python
@dataclass(frozen=True)
class BarSlice:
    trade_date: date
    as_of: datetime
    bars: Mapping[str, DailyBar]
    tradability: Mapping[str, TradabilityView]
    expected_symbols: tuple[str, ...]
```

- `on_bars` 每个交易日只调用一次，`bars` 按 symbol 字典序冻结；
- bridge 在下一个交易日到来或引擎结束时发现 bucket 不完整必须失败，不能把半个横截面交给策略；
- 停牌/未上市/退市通过 `tradability` 明确表达，不能只靠缺行推断；
- 应有行情却缺失时在进入策略前由质量门失败；
- `ctx.history(..., adjusted=True)` 返回截至当前 `as_of` 的只读连续窗口，默认包含当前 T 日收盘数据；不足 `count` 时返回明确的实际长度，不向前偷取数据；
- 历史窗口携带 `start/end/as_of/snapshot_id/adjustment_definition_hash`，策略无法改变 as-of 边界；
- `SnapshotFeedAdapter` 可以按 AKQuant 的输入协议形成内部 DataFrame，但 bridge 和公开策略永远拿不到完整未来区间。单元测试可以使用专用 `InMemoryMarketDataFixture`，但该类型不属于正式运行入口。

### 6.6 目标意图、订单事件和结果接口

接口流向：

```text
Strategy.on_bars
      |
      v
TargetPortfolioIntent --validate--> IntentReceipt
      |
      v
OrderPlanner --> Order(new/planned) --> next-open Matcher
                                      | fill / reject
                                      v
                       Ledger --> OrderEvent / FillEvent / RejectEvent
                                      |
                                      v
                               Strategy callbacks
```

所有事件均为冻结对象并包含 `sequence_id`、`event_time`、`trade_date`、`strategy_id` 和关联 id。`OrderEvent` 的状态至少为 `PLANNED/ACCEPTED/FILLED/REJECTED/EXPIRED`；成交先完成账本更新，再发 `on_fill`，因此回调中看到的 cash/position 已包含该成交。

`BacktestResult` 是运行完成后的只读索引，不暴露可变 engine 或 strategy：

```python
result.run_id: str
result.status: RunStatus
result.summary: BacktestSummary
result.artifact_dir: Path
result.portfolio() -> DataFrame
result.positions() -> DataFrame
result.orders() -> DataFrame
result.fills() -> DataFrame
result.rejects() -> DataFrame
result.metrics() -> Mapping[str, JsonValue]
```

访问器从已落盘 Parquet/JSON 读取并返回副本。正式 API 不用“缺数据时从另一个字段猜测”的 fallback；必需产物缺失、schema 不符或 hash 失败时抛 `ArtifactIntegrityError`。

### 6.7 时间和数据可见性

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

### 6.8 生命周期与每日事件顺序 ASCII 图

```text
initialize(ctx) -> on_start(ctx)
                        |
                        v
+---------------------------------------------------------------+
| Advance to trade day T                                       |
+-------------------------------+-------------------------------+
                                v
  apply actions/status -> unlock T+1 -> on_before_trading(read-only)
                                |
                                v
              execute pending orders at T open
                                |
                                v
        ledger update -> on_order/on_fill/on_reject callbacks
                                |
                                v
                  mark positions at T close
                                |
                                v
       publish frozen BarSlice + StrategyContext(as_of=T close)
                                |
                                v
              on_bars -> TargetPortfolioIntent
                                |
                                v
       validate/plan deterministic orders for T+1 open
                                |
                                v
       invariant checks -> persist -> on_after_trading(read-only)
+---------------------------------------------------------------+
                        |
                 next trading day
                        |
                        v
                  on_stop(ctx, reason)
```

事件顺序属于公开回测语义。每个箭头阶段都有单调 `sequence_id`；改动必须升级引擎语义版本并更新黄金结果。

### 6.9 领域对象

| 对象 | 核心字段/职责 |
| --- | --- |
| `TradingSession` | 交易日、开盘/收盘阶段、前后交易日 |
| `Instrument` | symbol、交易所、板块、上市/退市、lot/tick 规则 |
| `DailyBar` | 未复权 OHLC、昨收、股数成交量、人民币成交额 |
| `PriceLimit` | 当日涨停价、跌停价 |
| `CorporateAction` | 登记、除权、支付/上市日期，现金/股票比例 |
| `BarSlice` | 同一交易日完整 bar/tradability/as-of 只读切片 |
| `TargetPortfolioIntent` | 决策时点、完整目标组合、清仓语义、原因 |
| `IntentReceipt` | 意图是否被接受及稳定 intent id；不承诺成交 |
| `TargetPosition` | 决策时点、symbol、目标股数、原因 |
| `Order` | id、创建/执行时间、方向、数量、剩余量、状态 |
| `Fill` | order id、成交时间、股数、价格、费用拆分 |
| `Position` | 总数量、可卖数量、待上市红股、成本和市值 |
| `CashLedgerEntry` | 时间、类型、金额、关联 order/action、余额 |
| `PortfolioSnapshot` | 当日现金、持仓、市值、权益和对账字段 |
| `BacktestRequest` | 已解析且冻结的数据、账户、执行、费用和产物配置 |
| `BacktestRun` | run id、状态、输入 hash、引擎版本和产物位置 |
| `BacktestResult` | 已校验产物的只读索引和结构化访问器 |

所有 id 和排序规则必须稳定。相同时间的订单按 `side_priority + created_at + symbol + order_id` 确定顺序，禁止依赖 dict/set 的偶然顺序。

### 6.10 订单规划

第一阶段订单规划优先委托 AKQuant 的 `order_target/rebalance_weights`，TBCaptial adapter 负责输入约束、显式排序和结果校验，不再实现第二套可分叉的账户规划器。对外必须满足以下契约：

1. 比较目标股数和当前总持仓；
2. 卖出量不超过可卖数量；
3. 买入量向下取 100 股整数手；
4. 不足 100 股的存量零股只能一次性卖出；
5. 使用 T 日已知价格和费用预估检查现金；
6. 使用 trailing ADV 和配置比例限制订单名义规模；
7. 先规划卖出再规划买入；
8. 现金不足默认按稳定顺序逐单缩量到可买整手，不能产生负现金；
9. 生成 `execute_not_before=T+1 open` 的订单。

若 AKQuant 某项行为与上述契约不同，先通过固定适配参数解决；仍无法满足时只在 adapter/上游扩展，不在策略层打补丁。第一阶段只支持市价意图在下一开盘按模拟价格成交，不实现限价单、撤改单和跨日部分成交队列。

### 6.11 撮合和拒单规则

`AShareExecutionGuard` 以 snapshot 中的状态和价格限制增强 AKQuant 股票撮合。拒单必须成为可观察事件并终结对应订单，不能利用默认“本 bar 未成交、下个 bar 再试”的挂起语义模拟停牌或涨跌停。

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

只有日线无法知道涨跌停排队、开盘瞬间深度和盘中路径，因此规则有意保守。报告必须标记 `daily_open_approximation_v1` 和固定的 AKQuant backend version/commit。

### 6.12 T+1 和持仓状态

Position 至少分为：

- `total_quantity`；
- `sellable_quantity`；
- `today_bought_quantity`；
- `pending_bonus_quantity`。

买入成交增加总数量和今日买入量，但不增加当日可卖量；下一交易日开盘前结转为可卖。卖出只扣可卖量。红股在 `div_listdate` 入账并按明确规则进入可卖量。

每日结束检查：数量均为非负整数，`sellable <= total`，各分量能还原总数量。

### 6.13 公司行为

第一阶段优先从 AKShare `stock_history_dividend_detail` 构建标准化 `corporate_action`；只有 AKShare 字段缺失时才显式使用 Tushare `dividend` 补充，并在行级血缘记录 provider。只处理状态为已实施且关键日期完整的两类事件：

- 现金分红：在 record date 收盘记录权益，在 ex date 建立应收股利并计入总权益，在 pay date 按标准字段 `cash_per_share_after_tax` 由应收转为现金；
- 送股/转增：在 record date 记录权益，在 ex date 建立待上市红股并按未复权市价计入总权益，在 list date 按标准字段 `stock_per_share` 转为正式持仓，整数化差异写入事件日志。

除权日使用未复权价格估值；公司行为入账使账户经济价值连续。复权因子变化用于交叉检查是否存在未覆盖事件。

第一阶段不模拟个体持有期红利税、配股选择、增发、合并和复杂权利。遇到持仓期间无法映射的复权因子跳变，默认立即失败 `UNSUPPORTED_CORPORATE_ACTION`，不能带着错误净值继续。

### 6.14 费用模型

费用规则是带生效日期的配置表，不散落在撮合代码：

- 券商佣金：买卖双向，支持最低单笔佣金；
- 证券交易印花税：卖出侧；
- 过户费/经手费：按市场、方向和生效日期配置；
- 滑点：独立于法定费用，使用 bps；
- 每笔 Fill 保存各费用分项和总额。

黄金测试使用固定测试费率，不依赖“当前默认费率”。真实回测配置在实现时根据实际券商和回测区间核对官方规则。

### 6.15 账户账本和不变量

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

### 6.16 回测产物布局

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

`manifest.json` 包含：run id、状态、开始/结束时间、TBCaptial Git commit、工作区是否干净、项目 wheel hash、`environment.yml` hash、实际 Conda 包清单 hash、平台、AKQuant version/commit/wheel hash、snapshot id/hash、配置 hash、随机种子、引擎语义版本和所有产物 hash。

第一阶段 summary 只需要总收益、基准收益、最大回撤、交易次数、拒单分布、总费用和最终对账状态；不建设完整绩效报告系统。

## 7. 第一阶段计划目录

```text
TBCaptial/
├── README.md
├── PLAN.md
├── PLAN_LATEST.md
├── ARCHITECTURE.txt
├── ENVIRONMENT_ACCEPTANCE.md
├── .gitmodules
├── environment.yml
├── pyproject.toml
├── scripts/
│   ├── create_conda_env.sh
│   ├── activate_conda_env.sh
│   ├── verify_conda_env.py
│   ├── init_submodules.sh
│   └── install_akquant_backend.sh
├── third_party/
│   └── akquant/          # fixed Git submodule; source only
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
│   │   └── adapters/akquant/
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
└── var/                  # 本地数据、catalog 与运行目录，不提交 Git
```

## 8. 开发任务和依赖顺序

任务按依赖顺序执行，不在本文件分配人员。

### 8.1 环境基线

| ID | 状态 | 任务 | 完成条件 |
| --- | --- | --- | --- |
| ENV-000 | 完成 | 安装并校验 Miniforge | Conda 可用，安装器版本/hash 有记录，base 不装项目依赖 |
| ENV-001 | 完成 | 创建 `environment.yml` | 直接依赖、Python 和 channel 声明完整，无云 SDK |
| ENV-002 | 完成 | 创建/激活脚本 | 首次创建、重复更新、Bash/Zsh source 激活行为通过 |
| ENV-003 | 完成 | 建立 `pyproject.toml` 和开发工具配置 | Ruff/Mypy/Pytest 使用同一配置；未提前创建业务代码 |
| ENV-004 | 完成 | 当前机器离线 smoke test | 核心导入、ZSTD Parquet、本地 DuckDB 通过并有验收记录 |

### 8.2 数据最小切片

| ID | 任务 | 依赖 | 完成条件 |
| --- | --- | --- | --- |
| DATA-001 | 配置和秘密加载 | ENV-003 | 可选 token/secret 不泄露，配置可验证 |
| DATA-002 | 本地 StorageBackend | DATA-001 | 写入、hash、原子发布和读取测试通过 |
| DATA-003 | AKShare adapter 和接口契约探针 | DATA-001 | 前六类主接口成功或返回明确分类错误，原始列已冻结到夹具 |
| DATA-004 | provider-neutral Raw batch writer | DATA-002/003 | AKShare 原始响应和请求元数据可追溯 |
| DATA-005 | Silver schema/AKShare normalizer | DATA-004 | 单位、主键、日期、symbol 和类型契约通过 |
| DATA-006 | 质量门 | DATA-005 | PASS/WARN/FAIL 和隔离行为通过 |
| DATA-007 | partition/snapshot publisher | DATA-002/006 | 半发布不可见、重复执行幂等 |
| DATA-008 | 本地数据目录和 DuckDB catalog | DATA-007 | 从 snapshot manifest 重建并查询 |
| DATA-009 | AKShare 小切片回填 | DATA-008 | 3–20 只股票，主源数据通过质量门 |
| DATA-010 | Tushare 辅助 adapter | DATA-009 | 历史状态、复权因子、涨跌停分区或明确权限错误 |
| DATA-011 | 正式 P0 snapshot | DATA-010 | 主/辅来源边界清晰，特殊事件数据齐全且全部 PASS |
| DATA-012 | 增量与修订检测 | DATA-011 | 新日追加、历史修订不覆盖旧快照 |

### 8.3 回测内核

| ID | 任务 | 依赖 | 完成条件 |
| --- | --- | --- | --- |
| BT-000（完成） | 固定 AKQuant backend 制品 | ENV-004 | `v0.3.2` commit 对应的可重复本地 wheel 构建方案、SHA-256、导入和最小回测通过 |
| BT-001 | `BacktestRequest/StrategySpec/BacktestResult` 端口 | BT-000 | 不暴露 AKQuant 类型，严格配置、稳定 hash 和只读结果 schema 通过 |
| BT-002 | `SnapshotFeedAdapter` | DATA-008/BT-001 | 只读指定 manifest，固定 symbol/date 顺序，不访问在线源 |
| BT-003 | `TBCaptialStrategyBridge` | BT-001/002 | AKQuant 多 symbol 回调只产生一份完整 `BarSlice`，as-of 截断正确 |
| BT-004 | 目标组合命令映射 | BT-003 | intent/receipt 映射到 target/rebalance，单日单意图及确定性排序通过 |
| BT-005 | `AShareExecutionGuard` | BT-004 | 正常、停牌、涨跌停、缺行情均生成正确成交或终态拒单 |
| BT-006 | T+1、整手、费用、账户兼容层 | BT-005 | AKQuant backend 行为与手算账本逐事件一致，跨费用切换能力有明确门禁 |
| BT-007 | 公司行为扩展 | BT-006 | record/ex/pay/list 语义通过；未知或 backend 不支持时 fail-fast |
| BT-008 | 事件/结果转换和确定性 | BT-003/006/007 | `order/fill/reject → bars` 顺序及同输入事件流逐字段一致 |
| BT-009 | 产物 writer | BT-008 | manifest、backend 身份和全部 Parquet/hash 完整 |

### 8.4 集成验收

| ID | 任务 | 依赖 | 完成条件 |
| --- | --- | --- | --- |
| INT-001 | 人工黄金市场夹具 | DATA-005/BT-008 | 手算订单、费用、持仓、现金和净值 |
| INT-002 | snapshot 到回测 E2E | DATA-011/BT-009 | 一条命令产出完整 run |
| INT-003 | 快照包迁移 | INT-002 | 新的本地临时数据根目录离线导入后结果 hash 一致 |
| INT-004 | 故障注入 | INT-002 | API 中断、磁盘写入失败、半写和坏 hash 均安全失败 |
| INT-005 | 第一阶段发布门 | 全部 | 完成定义全部通过 |

## 9. 测试设计

### 9.1 必须测试的层次

- 单元：日期、单位、舍入、整手、费用、T+1、订单状态和账本；
- 接口契约：未知配置失败、context 只读、BarSlice 同步边界、单日单意图、result 产物完整性；
- 属性：任意合法事件序列下现金/持仓不变量和确定性；
- 数据契约：固定 AKShare 主响应、Tushare 辅助响应分别映射到 Raw/Silver，不在线访问 API；
- backend 契约：固定 AKQuant wheel/version 下的参数映射、回调顺序、T+1、整手、费用和结果字段；
- 存储集成：本地文件写入、原子发布、读取和校验行为正确；
- 黄金：人工 10–20 个交易日、3 只股票逐字段对照；
- E2E：固定 Raw → Silver → snapshot → 回测 → artifacts；
- 故障：坏 hash、缺文件、重复发布、磁盘空间不足、网络中断和权限不足。

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
- 同日多 symbol 输入顺序变化不改变 `BarSlice`、目标、订单和结果；
- `on_order/on_fill/on_reject` 在 `on_bars` 前按 sequence id 触发；
- snapshot 中本地文件被篡改后的 hash 失败。

普通测试不调用实时 AKShare/Tushare。在线接口探针和增量采集是单独的手工/计划任务；当前阶段不执行其他平台试装。

## 10. 第一阶段完成定义

以下条件必须全部满足：

- [x] 当前开发机能从 `environment.yml` 创建环境，核心包、本地 Parquet/DuckDB smoke test 通过；
- [x] 创建/激活/验收脚本和当前机器验收记录已提交工作树；
- [x] AKQuant 固定 backend 制品、commit、SHA-256 和兼容性测试已建立；
- [ ] AKShare 前六类主接口契约已探测并记录；
- [ ] 正式 P0 所需的 Tushare 辅助权限或等价完整数据分区已明确；
- [ ] 小数据切片的 Raw、Silver、quality、partition manifest 和 snapshot 全部发布；
- [ ] 导出的快照包能在新的本地数据根目录离线导入并得到相同数据 hash；
- [ ] 回测路径没有 AKShare/Tushare 调用，也不读取 snapshot 范围之外的数据；
- [x] 合成行情下 T 日决策、下一根开盘成交和 T+1 可卖数量契约测试通过；
- [ ] T+1、整手、零股、停牌、涨跌停、现金、费用和 trailing ADV cap 已进入撮合路径；
- [ ] 现金分红和送转进入账户；未知公司行为会安全失败；
- [ ] 订单、成交、持仓、现金、公司行为和权益可以逐日对账；
- [ ] 黄金测试、属性测试、集成测试和 E2E 全部通过；
- [ ] 相同代码、实际环境包清单、AKQuant 制品、snapshot、配置和种子重复运行，事件和产物 hash 一致；
- [ ] 采集中断、重复采集、历史修订和损坏文件不会污染已发布 snapshot；
- [ ] run manifest 记录所有环境、数据、代码、配置和产物 hash；
- [ ] 仓库文档与实际命令、目录和语义一致。

第一阶段完成前，不扩展到本文件范围之外。

## 11. 实现前需要提供的外部配置

这些值不影响架构，但实现和验收前必须配置：

- AKShare 不需要 token；需要记录实际访问网络、代理策略和接口探针日期；
- 可选的 `TUSHARE_TOKEN` 及辅助接口权限；没有 token 时，真实数据回测不得伪造复权因子或历史涨跌停；
- 新平台按固定 AKQuant `v0.3.2` commit 本地构建 wheel，并追加该平台的路径、工具链和 SHA-256 验收记录；
- 本地权威数据根目录和可选的第二块本地磁盘备份目录；
- 验收股票集合、日期范围和基准指数；
- 初始资金；
- 实际券商佣金、最低佣金及需要计入的其他费用；
- 默认滑点 bps、trailing ADV 窗口和最大占比。

秘密值不得提交 Git。非秘密值写入版本化配置并进入 run manifest。

## 12. 官方资料

- [AKShare A 股数据](https://akshare.akfamily.xyz/data/stock/stock.html)
- [AKShare 工具箱/交易日历](https://akshare.akfamily.xyz/data/tool/tool.html)
- [AKQuant 仓库](https://github.com/akfamily/akquant)
- [AKQuant PyPI](https://pypi.org/project/akquant/)
- [Tushare A 股日线](https://tushare.pro/document/1?doc_id=27)
- [Tushare 股票基础信息](https://tushare.pro/document/1?doc_id=25)
- [Tushare 交易日历](https://tushare.pro/document/2?doc_id=26)
- [Tushare A 股复权说明](https://tushare.pro/document/2?doc_id=146)
- [Tushare 分红送股](https://tushare.pro/document/2?doc_id=103)
- [Conda 环境管理](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html)
- [Miniforge](https://github.com/conda-forge/miniforge)
- [DuckDB Parquet](https://duckdb.org/docs/stable/data/parquet/overview)
- [DuckDB 并发模型](https://duckdb.org/docs/stable/connect/concurrency)
- [上海证券交易所交易规则（2026 年修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)
- [深圳证券交易所交易规则（2026 年修订）](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)
- [证券交易印花税减半公告](https://shanghai.chinatax.gov.cn/zcfw/zcfgk/yhs/202308/t468451.html)

实现开始当天再次核对 AKShare/Tushare 接口、AKQuant 固定版本和交易规则，并将核对日期写入数据契约、backend 兼容矩阵和费用配置。
