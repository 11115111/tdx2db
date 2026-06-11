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
# 1. 刷新静态缓存（股票池 + 板块成员数）
python -m rps.cli.run_daily --db /path/to/your.duckdb --refresh-blocks

# 2. 回填近 2 年历史数据
python -m rps.cli.run_daily --db /path/to/your.duckdb --init-history
```

### 每日更新（收盘后）

```bash
python -m rps.cli.run_daily --db /path/to/your.duckdb --date 2026-06-11
```

不指定 `--date` 则自动取 `raw_kline_daily` 最新日期。

### 板块/股票数据变更后（新股上市、ST 变更等）

```bash
python -m rps.cli.run_daily --db /path/to/your.duckdb --refresh-blocks
```

平时无需每天执行，每周一次即可。

## 选项

| 选项 | 说明 |
|------|------|
| `--db` | DuckDB 文件路径（必填） |
| `--date` | 指定计算日期，默认取最新 |
| `--init-history` | 回填历史，默认从 end_date 往前 2 年 |
| `--start` | 配合 `--init-history` 指定起始日期 |
| `--end` | 配合 `--init-history` 指定结束日期 |
| `--refresh-blocks` | 刷新静态缓存后退出 |
| `--skip-sanxianhong` | 跳过三线红榜单计算 |
| `--cfg` | 指定配置文件路径，默认 `config/thresholds.yaml` |

## 输出表

| 表 | 说明 |
|----|------|
| `rps_stock_daily` | 个股每日 RPS（5/10/20/50/120/250 周期） |
| `rps_block_daily` | 板块每日 RPS（5/10/15/20 周期） |
| `sanxianhong_daily` | 三线红榜单，含连续在榜天数 |

## 参数配置

所有阈值在 `config/thresholds.yaml` 中调整，无需改代码：

```yaml
sanxianhong:
  strict:
    rps50_min: 90
    rps120_min: 93
    rps250_min: 95
    hhv_ratio_min: 0.85

block_rps:
  max_member_count: 100   # 成员超过此数的板块排除
```
