# PostgreSQL Demo 数据库设计

## 背景

项目当前业务库使用 MySQL，但数据源与 ChatBI 需要补充 PostgreSQL 支持。为便于后续开发和联调，先准备一套稳定、可重复初始化的 PostgreSQL demo 数据库。

## 目标

- 创建独立数据库 `nanzi_demo`。
- 创建 `demo` schema 及一组适合数据源元数据、关联查询和聚合分析的业务表。
- 写入固定且可理解的测试数据，支持后续重复执行初始化脚本。
- 不删除已有数据库或已有业务数据。
- 初始化完成后输出数据量和基础连通性验证结果。

## 非目标

- 不修改项目当前 MySQL 业务库配置。
- 不启动 `./dev.sh` 或其他项目服务。
- 不把 PostgreSQL demo 表直接接入生产数据源配置。
- 不实现 PostgreSQL 数据源适配器本身。

## 方案

新增 `scripts/init_postgres_demo.py`，使用 PostgreSQL Python 驱动连接数据库：

1. 从用户提供的连接串读取主机、端口、账号和密码。
2. 将数据库名切换到维护库 `postgres`，检查并创建 `nanzi_demo`。
3. 连接 `nanzi_demo`，创建 `demo` schema、表、约束、索引和字段注释。
4. 使用固定主键和 `ON CONFLICT DO NOTHING` 写入测试数据，保证重复执行不会产生重复记录且不覆盖已有相同主键数据。
5. 执行计数和聚合查询，打印初始化结果。

脚本默认使用以下源连接串，并从中派生目标数据库连接：

```text
源连接：postgresql://postgres:postgres123@localhost:5432/appdb
目标库：nanzi_demo
```

也支持通过命令行参数传入完整连接串和数据库名；为避免误写业务库，目标数据库名必须使用 `nanzi_demo` 前缀。

## 数据模型

### `demo.customers`

客户基础信息，包括客户名称、区域、行业和创建时间。

### `demo.products`

商品基础信息，包括商品名称、分类和单价。

### `demo.orders`

订单事实数据，关联客户和商品，包括订单日期、数量、金额和订单状态。该表用于验证 PostgreSQL 的关联查询、日期过滤、分组聚合和金额计算。

数据规模保持在小型 demo 范围：客户、商品各约 5 条，订单约 12 条，覆盖多个区域、分类、月份和订单状态。

## 幂等与安全

- 数据库创建使用存在性检查；数据库已存在时继续初始化，不执行删除或重建。
- 表使用 `CREATE TABLE IF NOT EXISTS`。
- 测试数据使用固定主键并通过 `ON CONFLICT DO NOTHING` 补齐，重复执行不会追加重复数据或覆盖已有记录。
- 所有 SQL 使用参数绑定传递数据值；数据库名和 schema 名只接受脚本内部生成的安全标识符。
- 如果账号没有 `CREATEDB` 权限，脚本明确报错并提示先手动创建数据库。

## 验证

实现后执行：

- Python 编译检查。
- 针对初始化逻辑的测试，覆盖连接串解析、目标库派生和幂等 SQL 关键片段。
- 使用用户本机 PostgreSQL 实际运行初始化脚本。
- 通过 `information_schema`/聚合查询确认三张表存在、行数正确、订单关联查询可用。
- `git diff --check`。

不运行项目服务启动脚本，不进行 git 提交。
