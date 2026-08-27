#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复刻 Trae CN 的"对话完成后 3 个建议"功能 (基于 MITM 抓包逆向)

用法:
  1. 标准模式:
     echo '{"user_input_history": [...], "last_assistant_response": "..."}' | python3 followup_suggest.py
  2. 回放模式 (用抓到的真实 Trae 请求测试):
     python3 followup_suggest.py --replay /home/ai/trae-capture/mitm.flows
  3. 简单模式:
     python3 followup_suggest.py --last-reply "刚才的回复内容..."

实现还原自 POST /api/ide/v1/super_completion_query:
  输入: user_input_history + last_assistant_response + active_file_content + symbol_infos
  输出: 严格 JSON 数组 [{"query": "..."}] × 3
"""
import argparse
import json
import os
import re
import sys
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
# Trae 官方同款模型后端 (OpenAI 兼容, 经 SSH 隧道转发 AMD 上的 trae_cn 代理服务):
#   ssh -f -N -L 19220:127.0.0.1:9220 AMD
DS_FLASH_URL = os.environ.get("DS_FLASH_URL", "http://127.0.0.1:19220/v1/chat/completions")
DS_FLASH_MODEL = os.environ.get("DS_FLASH_MODEL", "DeepSeek-V4-Flash-Official")
DS_FLASH_KEY = os.environ.get("DS_FLASH_KEY", "")

BACKEND = "ollama"  # "ollama" | "ds-flash"
MODEL = "mistral:latest"  # 本地轻量模型; Trae 用的是 deepseek_v4_flash 级别
MAX_REPLY_CHARS = 3000    # Trae 对 last_assistant_response 做了截断
MAX_FILE_CHARS = 4000

PROMPT_TEMPLATE = """You are a follow-up query recommender for an AI coding assistant chat.
Based on the conversation context, generate exactly 3 short follow-up queries the user might want to ask next.

Rules:
- Each query must be concise, in the SAME language as the conversation (Chinese in, Chinese out).
- Queries must start with an action verb and point to a SPECIFIC next step from the response (like "继续 Phase 3 重构 tools/ 目录"), NOT generic open questions (avoid "如何/能否/可以吗" phrasing).
- If a file context is given, at least one query may relate to it.
- Output ONLY a single JSON object (no markdown, no extra text) with exactly 3 items:
{{"queries": ["...", "...", "..."]}}

Example (real production behavior):
{example}

User input history (newest last):
{user_input_history}

Last assistant response:
{last_assistant_response}

Active file (if any): {active_file}
"""

# 按语言切换 few-shot 示例 (固定中文示例会压过语言指令, 导致英文对话输出中文)
EXAMPLES = {
    "Chinese": """Context: assistant just finished MITM capture analysis and plans to replay captured LLM requests
Good output: {{"queries": ["继续深挖已捕获的 SSE 流量，提取完整 prompt", "帮我回放 mitm.flows 里的真实请求", "把抓包分析结果整理成文档"]}}
Bad output: {{"queries": ["如何分析流量？", "能否介绍 mitmproxy？", "抓包有什么用途？"]}}""",
    "English": """Context: assistant just finished MITM capture analysis and plans to replay captured LLM requests
Good output: {{"queries": ["Continue digging into the captured SSE streams to extract full prompts", "Replay the real requests from mitm.flows", "Turn the capture analysis into a document"]}}
Bad output: {{"queries": ["How to analyze traffic?", "Can you introduce mitmproxy?", "What is packet capture used for?"]}}""",
}


def detect_language(texts) -> str:
    """按 CJK 字符占比判定对话语言，注入 prompt 增强语言跟随。
    技术对话常混大量英文标识符，因此汉字只需达到较低比例即判中文。"""
    cjk = ascii_ = 0
    for t in texts:
        for ch in (t or ""):
            if "\u4e00" <= ch <= "\u9fff":
                cjk += 1
            elif ch.isascii() and ch.isalpha():
                ascii_ += 1
    if cjk == 0:
        return "English" if ascii_ else "unknown"
    return "Chinese" if cjk * 4 >= ascii_ else "English"


def build_prompt(ctx: dict) -> str:
    history = ctx.get("user_input_history") or []
    if isinstance(history, str):
        history = [history]
    history_str = "\n".join(f"- {h}" for h in history[-8:]) or "(none)"

    reply = (ctx.get("last_assistant_response") or "").strip()[:MAX_REPLY_CHARS] or "(none)"

    file_content = ctx.get("active_file_content") or ""
    fname = ""
    if ctx.get("symbol_infos"):
        try:
            si = json.loads(ctx["symbol_infos"]) if isinstance(ctx["symbol_infos"], str) else ctx["symbol_infos"]
            fname = si[0].get("content", "") if si else ""
        except Exception:
            pass
    active = f"{fname} (content omitted)" if fname else "(none)"
    if file_content and len(file_content) < MAX_FILE_CHARS:
        active = f"{fname}\n```\n{file_content[:MAX_FILE_CHARS]}\n```"

    language = detect_language([*history, reply])
    return PROMPT_TEMPLATE.format(
        example=EXAMPLES.get(language, EXAMPLES["Chinese"]),
        user_input_history=history_str,
        last_assistant_response=reply,
        active_file=active,
    )


def suggest(ctx: dict, _retry: bool = False) -> list:
    prompt = build_prompt(ctx)
    if BACKEND == "ds-flash":
        text = call_ds_flash(prompt, max_tokens=256, temperature=0.7 if not _retry else 0.2)
    else:
        text = call_ollama(prompt, _retry)
    return parse_queries(text, ctx, _retry)


class BackendError(Exception):
    """后端调用失败, message 含定位提示"""


def call_ds_flash(prompt: str, max_tokens: int = 256, temperature: float = 0.7) -> str:
    """调用 Trae 官方同款 DeepSeek-V4-Flash (OpenAI 兼容接口)"""
    if not DS_FLASH_KEY:
        raise BackendError(
            "未设置 DS_FLASH_KEY 环境变量 (代理服务的 API key)。\n"
            "  提示: key 位于代理服务机器的 .env 文件 (API_KEY=... 行)"
        )
    payload = {
        "model": DS_FLASH_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        DS_FLASH_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {DS_FLASH_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        hint = {
            401: "key 无效, 检查 DS_FLASH_KEY 是否与代理服务 .env 中的一致",
            404: "路径不对, 检查 DS_FLASH_URL",
            502: "代理服务无法连通上游, 检查代理服务日志",
        }.get(e.code, f"HTTP {e.code}")
        raise BackendError(f"ds-flash 请求失败: {hint}") from e
    except urllib.error.URLError as e:
        raise BackendError(
            f"无法连接 {DS_FLASH_URL}: {e.reason}\n"
            f"  排查: 1) SSH 隧道是否存活: ssh -f -N -L 19220:127.0.0.1:9220 YOUR_SERVER\n"
            f"        2) 验证: curl -m 5 http://127.0.0.1:19220/v1/status -H 'Authorization: Bearer $DS_FLASH_KEY'"
        ) from e
    except TimeoutError:
        raise BackendError("ds-flash 请求超时 (60s), 上游响应过慢或隧道不稳") from None
    try:
        resp = json.loads(raw)
        text = (resp["choices"][0]["message"].get("content") or "").strip()
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        preview = raw[:200].decode("utf-8", "replace")
        raise BackendError(f"ds-flash 响应异常 (非标准 OpenAI 格式): {preview}") from e
    if not text:
        raise BackendError("ds-flash 返回空 content, 可能被内容过滤拦截")
    return text


def call_ollama(prompt: str, _retry: bool) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",   # Ollama JSON 约束模式
        "think": False,     # 禁用 qwen3 思考模式 (避免 thinking 耗尽 token 配额)
        "options": {"temperature": 0.7 if not _retry else 0.2, "num_predict": 256},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    timeout = 120 if "mistral" in MODEL or "qwen" not in MODEL else 300
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError:
        # 老版本 Ollama 不认识 think 字段, 移除后重试
        payload.pop("think", None)
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    return (resp["message"].get("content") or "").strip()


def parse_queries(text: str, ctx: dict, _retry: bool) -> list:
    # 解析 JSON (兼容 {"queries":[...]}、数组、或 markdown 包裹)
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        out = json.loads(m.group(0)) if m else []
    if isinstance(out, dict):
        out = out.get("queries") or out.get("suggestions") or out.get("query") or []
    if isinstance(out, str) or isinstance(out, dict):
        out = [out]
    queries = []
    for item in out[:3]:
        if isinstance(item, dict):
            queries.append(item.get("query") or item.get("question") or str(item))
        elif isinstance(item, str):
            queries.append(item)
    # 解析失败或为空时降温度重试一次
    if not queries and not _retry:
        return suggest(ctx, _retry=True)
    return queries


def replay(flows_path: str):
    """从 mitm.flows 提取真实 super_completion_query 请求做回放对比"""
    from mitmproxy import io as mio
    n = 0
    with open(flows_path, "rb") as f:
        for fl in mio.FlowReader(f).stream():
            if fl.type != "http" or fl.request.path != "/api/ide/v1/super_completion_query":
                continue
            body = json.loads(fl.request.get_text())
            variables = json.loads(body["render_context"]["variables"])
            print(f"\n{'='*70}\n[回放 {n}] 真实 Trae 请求 (function_type={body['function_type']}):")
            print(f"  user_input_history: {variables.get('user_input_history', '')[:200]}")
            # Trae 原始响应 (deepseek_v4_flash 生成)
            orig = fl.response.get_text(strict=False) or ""
            orig_text = "".join(
                m.group(1) for m in re.finditer(r'"text":"((?:[^"\\]|\\.)*)"', orig)
            )
            print(f"  Trae 原始建议(deepseek_v4_flash): {orig_text[:200]}")
            # 本地模型复刻
            queries = suggest(variables)
            backend_label = "ds-flash" if BACKEND == "ds-flash" else MODEL
            print(f"  本地复刻建议({backend_label}):")
            for q in queries:
                print(f"    - {q}")
            n += 1
            if n >= 3:
                break


def main():
    ap = argparse.ArgumentParser(description="复刻 Trae 的 3-followup 建议生成")
    ap.add_argument("--replay", metavar="FLOWS", help="回放 mitm.flows 中的真实请求")
    ap.add_argument("--last-reply", help="最后一条助手回复(简单模式)")
    ap.add_argument("--model", default=MODEL, help=f"Ollama 模型 (默认 {MODEL})")
    ap.add_argument("--backend", choices=["ollama", "ds-flash"], default=BACKEND,
                    help="后端: ollama=本地模型, ds-flash=Trae 官方同款 DeepSeek-V4-Flash")
    args = ap.parse_args()

    globals()["BACKEND"] = args.backend
    if args.model != MODEL:
        globals()["MODEL"] = args.model

    if args.replay:
        replay(args.replay)
        return

    if args.last_reply:
        ctx = {"user_input_history": [], "last_assistant_response": args.last_reply}
    else:
        raw = sys.stdin.read()
        ctx = json.loads(raw) if raw.strip() else {}
    try:
        queries = suggest(ctx)
    except BackendError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(queries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
