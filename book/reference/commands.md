# 命令速查

所有 Python 命令都在项目根目录执行。

## 主命令

| 命令 | 作用 | 是否改写本地数据 |
|---|---|---|
| `python main.py env` | 查看最终配置，不打印密钥 | 否 |
| `python main.py init` | 重建业务库、元数据中心并采集 | 是 |
| `python main.py index` | 增量构建检索文档和向量索引 | 是 |
| `python main.py index --force` | 强制全量重建向量索引 | 是 |
| `python main.py show` | 展示元数据、文档和向量记录 | 否 |
| `python main.py check` | 检查文档、向量数与模型一致性 | 否（线上模式会探测模型） |
| `python main.py ask "问题"` | 单次问答，默认 LLM 生成 | 查询业务库 |
| `python main.py ask "问题" --rule` | 单次问答，规则生成 SQL | 查询业务库 |
| `python main.py chat` | 交互式连续提问 | 查询业务库 |
| `python main.py all` | 初始化、索引、检查并回答默认问题 | 是 |

## 常用工作流

### 第一次离线体验

```bash
NL2SQL_EMBEDDER=hash python main.py all --rule
```

### 修改业务语义后

当前 Demo 的 `init` 会重建元数据，因此运行：

```bash
python main.py init
python main.py index
python main.py check
```

### 只修改检索文档模板后

```bash
python main.py index
```

内容指纹会识别变化；无需 `--force`。

### 切换 Embedding 模型后

```bash
python main.py index
python main.py check
```

Indexer 检测到模型名变化会自动全量重建。

### 比较规则与 LLM

```bash
python main.py ask "今年哪个区域卖得最好" --rule
python main.py ask "今年哪个区域卖得最好"
```

比较两次 Trace、最终 SQL 与结果，而不只是代码格式。

## 推荐测试问题

```text
查询 2026 年 8 月华南区销售额最高的 5 个产品
华南区成交额最高的商品
今年哪个区域卖得最好
2026年8月华东区销售额最高的3个产品
华北区销量最高的产品
去年华南区的销售额
华东区卖了多少钱
```

它们分别覆盖标准问法、全同义词、无值过滤的分组、LIMIT、第二个 Metric、整体聚合和口语指标。

## 文档站

在 `book` 目录执行：

| 命令 | 作用 |
|---|---|
| `npm install` | 安装 VitePress |
| `npm run docs:dev` | 启动开发服务器 |
| `npm run docs:build` | 生成静态站点 |
| `npm run docs:preview` | 预览构建结果 |

构建产物位于 `book/.vitepress/dist`。
