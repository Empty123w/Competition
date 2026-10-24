# A → D 知识问答 API 说明（阶段 K）

供前端（D）接入项目知识库问答。**只读**，不改动任何业务计算。

## 1. 端点

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/knowledge/ask` | 完整知识问答（答案+引用） |
| POST | `/api/knowledge/search` | 仅检索知识块 |
| GET  | `/api/knowledge/status` | 索引状态（版本/文档/块/stale） |

## 2. 请求

```http
POST /api/knowledge/ask
Content-Type: application/json

{ "question": "负荷预测使用什么模型？输入输出分别是什么？", "top_k": 5 }
```
`/search`：`{ "query": "...", "top_k": 5, "category": "预测模型" }`（`category` 可选）。

## 3. 成功响应示例

```json
{
  "status": "success",
  "query": "负荷预测使用什么模型？输入输出分别是什么？",
  "answer": "根据项目当前实现（负荷预测模型与接口）：负荷预测使用梯度提升树（LightGBM）……",
  "confidence": 0.2105,
  "kb_version": "2026.10.24.d9db4f",
  "retrieval_mode": "tfidf_char",
  "kb_status": "fresh",
  "stale_sources": [],
  "citations": [
    {"knowledge_id": "KB-FORECAST-001", "title": "负荷预测模型与接口",
     "category": "预测模型", "section": "结论", "score": 0.2105,
     "source_path": "knowledge/03_预测模型/负荷预测模型与网关.md"}
  ],
  "elapsed_seconds": 0.012
}
```

## 4. 无依据响应示例（拒答）

```json
{
  "status": "no_answer",
  "query": "某厂家内部故障码999是什么意思",
  "answer": "知识库暂无可靠依据，请检查项目文档或补充知识来源。",
  "confidence": 0.0,
  "kb_version": "2026.10.24.d9db4f",
  "retrieval_mode": "tfidf_char",
  "kb_status": "fresh",
  "stale_sources": [],
  "citations": [],
  "elapsed_seconds": 0.008
}
```

## 5. 状态示例

```json
{ "status": "success", "kb_version": "2026.10.24.d9db4f", "n_docs": 12, "n_chunks": 72,
  "categories": ["预测模型", "健康与维护", "..."], "built_at": "2026-10-24T17:xx",
  "kb_status": "fresh", "stale_sources": [], "index_ok": true }
```

## 6. 前端使用约定

- `status=no_answer` 时展示拒答文案，并提供"试试这些示例问题"；
- 展示 `citations`（知识ID/标题/章节/来源）以体现可解释性；
- `kb_status=stale` 时显示"知识库索引已过期"提示；
- 实时数值（当前健康分/预测/调度）请改调对应动态接口（`/api/health`、`/api/forecast`、`/api/dispatch` 等），不要用知识库充当实时值。
