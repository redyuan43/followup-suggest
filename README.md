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
- **ollama 后端**：[Ollama](https://ollama.com) 运行于 `127.0.0.1:11434`，任一小模型（如 `ollama pull mistral`）
- **ds-flash 后端**：无需 Ollama，但需要 SSH 可达的代理服务（见下节）

## 用法

```bash
# 简单模式
python3 followup_suggest.py --last-reply "刚才的回复内容..."

# 标准模式 (stdin 传入 JSON 上下文)
echo '{"user_input_history": ["你好"], "last_assistant_response": "..."}' | python3 followup_suggest.py

# 指定模型
python3 followup_suggest.py --last-reply "..." --model qwen3:32b

# 使用 Trae 官方同款 DeepSeek-V4-Flash 后端 (需 SSH 隧道, 见下节)
DS_FLASH_KEY=YOUR_KEY python3 followup_suggest.py --last-reply "..." --backend ds-flash
```

### ds-flash 后端（Trae 官方同款模型）

典型延迟 10-25s（首连可达 60s+），输出质量与 Trae 官方建议基本一致。链路：

```
followup_suggest.py → 127.0.0.1:19220 (SSH 隧道)
  → 代理服务 :9220 (OpenAI 兼容) → Trae 官方 API → deepseek-v4-flash
```

准备步骤：

1. 在代理服务所在机器上，进入代理服务项目目录，读取 `.env` 中的 `API_KEY` 行（这就是下文的 `YOUR_API_KEY`），然后启动 OpenAI 兼容服务（监听 `127.0.0.1:9220`）
2. 建立本地 SSH 隧道（把远端 9220 映射到本地 19220）：

   ```bash
   ssh -f -N -L 19220:127.0.0.1:9220 YOUR_SERVER
   ```

3. 验证隧道（应返回 `{"status":"ok",...}`）：

   ```bash
   curl -m 5 http://127.0.0.1:19220/v1/status -H "Authorization: Bearer YOUR_API_KEY"
   ```

4. 设置环境变量并运行：

   ```bash
   export DS_FLASH_KEY=YOUR_API_KEY          # 必需
   # 可选覆盖:
   # export DS_FLASH_URL=http://127.0.0.1:19220/v1/chat/completions
   # export DS_FLASH_MODEL=DeepSeek-V4-Flash-Official
   ```

**故障排查**（脚本会给出对应提示）：

- `无法连接 127.0.0.1:19220` → 隧道断了，重新执行步骤 2
- `key 无效` → `DS_FLASH_KEY` 与代理服务 `.env` 的 `API_KEY` 不一致
- 请求超时 → 上游慢或隧道不稳，重试一次

**注意**：API key 只通过环境变量传入，不要写入代码或提交到仓库。

**本地测试**：`python3 test_mock.py ds-flash` 会用 6 组 mock 上下文（中文/英文/文件上下文/超长回复/边界）跑通 ds-flash 后端。

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

| 维度 | Trae 官方 | 本复刻 (ollama) | 本复刻 (ds-flash) |
|---|---|---|---|
| 模型 | deepseek_v4_flash_official | 本地任意小模型 | 同官方 |
| 端点 | 云端 API (明文 JSON) | 本地 Ollama | Trae 官方 API 经代理 |
| 延迟 | ~1-2s | 10-50s (视模型) | 10-25s (首连可达 60s) |
| 输入 | 历史输入+最后回复+当前文件 | 相同 | 相同 |
| 输出 | `[{"query": ...}]` × 3 | `{"queries": [...]}` × 3 | 相同 |
| 单次成本 | ~1762 tokens | 本地推理 | ~1700 tokens |

回放真实流量验证：本地 `mistral:7b` 与官方 `deepseek_v4_flash` 输出语义基本一致
（均能抓住「继续 Phase 3 重构」等上下文核心）。

## License

MIT
