# rps — A股情绪周期 RPS 量化系统

基于 tdx2db 写入的 DuckDB 数据，计算个股/板块 RPS 排名与三线红榜单。

## 环境要求

- Python 3.10+
- 已有 tdx2db 同步好的 DuckDB 数据库文件

## 安装

在 `rps/` 目录下安装：

```bash
cd rps
pip install -e .
```

## 使用

**所有命令从仓库根目录执行**（即 `tdx2db/` 目录下）：

### 第一次初始化

```bash
python -m rps.cli.run_daily --db /path/to/your.duckdb --init-history
```

自动回填近 2 年历史数据，包含股票池刷新、RPS 计算、三线红（strict + loose 两个版本）。

### 每日更新（收盘后）

```bash
python -m rps.cli.run_daily --db /path/to/your.duckdb
```

不指定 `--date` 则自动取 `raw_kline_daily` 最新日期。每次运行自动刷新股票池（ST 变更、新股上市当天生效）。

### 补算中断的日期

```bash
python -m rps.cli.run_daily --db /path/to/your.duckdb --init-history --start 2026-06-09 --end 2026-06-11
```

`--start/--end` 指定区间重算，不影响区间外历史数据。

## 选项

| 选项 | 说明 |
|------|------|
| `--db` | DuckDB 文件路径（必填） |
| `--date` | 指定计算日期，默认取 `raw_kline_daily` 最新日期 |
| `--init-history` | 回填历史，默认从 end_date 往前 2 年 |
| `--start` | 配合 `--init-history` 指定起始日期 |
| `--end` | 配合 `--init-history` 指定结束日期 |
| `--refresh-blocks` | 仅刷新板块成员数缓存后退出（一般无需单独使用） |
| `--skip-sanxianhong` | 跳过三线红榜单计算 |
| `--cfg` | 指定配置文件路径，默认 `config/thresholds.yaml` |

## 输出表

| 表 | 说明 |
|----|------|
| `rps_stock_daily` | 个股每日 RPS（5/10/20/50/120/250 周期），各周期独立排名 |
| `rps_block_daily` | 板块每日 RPS（5/10/15/20 周期） |
| `sanxianhong_daily` | 三线红榜单（strict + loose），含连续天数、60日在榜、上榜次数、本轮入榜、上次离场 |

## 股票池过滤规则

每次运行自动重建，排除以下股票：

| 规则 | 说明 |
|------|------|
| 不在 `raw_symbol_name` 中 | 退市股（TDX 数据中退市股无名称记录） |
| 名称含 `ST` | ST、\*ST、S\*ST 等 |
| 名称含 `退` | 退市整理期 |
| 代码以 `8` 开头 | 北交所 |
| 代码以 `4` 开头 | 三板 |
| 代码以 `9` 开头 | B股 |

## 三线红版本

| 版本 | RPS 条件 | 近高比 |
|------|---------|--------|
| `strict` | rps50 **且** rps120 **且** rps250 各自达标 | `close/hhv150` ≥ 0.85 |
| `loose` | rps50 **或** rps120 **或** rps250 **任一**达标 | `close/hhv250` ≥ 0.6 |

阈值在 `config/thresholds.yaml` 中调整。

## 可视化

```bash
streamlit run rps/ui/streamlit_app.py -- --db /path/to/your.duckdb
```

功能：
- 日期选择、版本切换（strict / loose）、行业筛选
- 排序列：连续天数、60日在榜、上榜次数、RPS50/120/250 等
- 显示所属行业（行业二级分类）、本轮入榜、上次离场
- 汇总指标：在榜股票数、平均连续天数、平均 RPS50
- 一键下载 CSV

## 参数配置

```yaml
sanxianhong:
  strict:
    rps50_min: 90
    rps120_min: 93
    rps250_min: 95
    hhv_ratio_min: 0.85
  loose:
    rps_any_min: 95
    hhv_period: 250
    hhv_ratio_min: 0.6

block_rps:
  max_member_count: 100   # 成员超过此数的板块排除出 RPS 排名
```
