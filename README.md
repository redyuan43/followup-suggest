# followup-suggest

复刻 Trae CN「对话完成后生成 3 条建议」功能的独立实现。

基于对 Trae 客户端 `POST /api/ide/v1/super_completion_query` 请求的 MITM 抓包分析还原，
使用本地 Ollama 小模型（Trae 官方使用 `deepseek_v4_flash_official`）。

## 工作原理

```
触发时机: 助手回复结束
     ↓
拼装上下文:
  - user_input_history       最近几条用户输入
  - last_assistant_response  最后一条助手回复 (截断)
  - active_file_content      当前打开的文件内容 (增强建议相关性)
  - symbol_infos             文件符号信息
     ↓
调用轻量 LLM (单次 ~1700 prompt tokens)
     ↓
输出严格 JSON: {"queries": ["...", "...", "..."]} × 3
```

## 依赖

- Python 3.8+（仅标准库）
- [Ollama](https://ollama.com) 运行于 `127.0.0.1:11434`
- 任一小模型，如 `ollama pull mistral`

## 用法

```bash
# 简单模式
python3 followup_suggest.py --last-reply "刚才的回复内容..."

# 标准模式 (stdin 传入 JSON 上下文)
echo '{"user_input_history": ["你好"], "last_assistant_response": "..."}' | python3 followup_suggest.py

# 指定模型
python3 followup_suggest.py --last-reply "..." --model qwen3:32b
```

输出：

```json
[
  "帮我继续实现下一步",
  "提交本次改动到 git",
  "查看完整的优化方案"
]
```

## 集成到自己的应用

```python
from followup_suggest import suggest

queries = suggest({
    "user_input_history": ["帮我写个解析器", "继续"],
    "last_assistant_response": "已完成解析器主体，包含词法分析...",
    "active_file_content": "def parse(tokens): ...",
})
```

## 与官方实现的对比

| 维度 | Trae 官方 | 本复刻 |
|---|---|---|
| 模型 | deepseek_v4_flash_official | 本地任意小模型 |
| 端点 | 云端 API (明文 JSON) | 本地 Ollama |
| 输入 | 历史输入+最后回复+当前文件 | 相同 |
| 输出 | `[{"query": ...}]` × 3 | `{"queries": [...]}` × 3 |
| 单次成本 | ~1762 tokens | 本地推理 |

回放真实流量验证：本地 `mistral:7b` 与官方 `deepseek_v4_flash` 输出语义基本一致
（均能抓住「继续 Phase 3 重构」等上下文核心）。

## License

MIT
