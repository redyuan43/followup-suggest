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
import re
import sys
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "mistral:latest"  # 本地轻量模型; Trae 用的是 deepseek_v4_flash 级别
MAX_REPLY_CHARS = 3000    # Trae 对 last_assistant_response 做了截断
MAX_FILE_CHARS = 4000

PROMPT_TEMPLATE = """You are a follow-up query recommender for an AI coding assistant chat.
Based on the conversation context, generate exactly 3 short follow-up queries the user might want to ask next.

Rules:
- Each query must be concise, in the SAME language as the conversation.
- Queries should be directly actionable next steps related to the last assistant response.
- If a file context is given, at least one query may relate to it.
- Output ONLY a single JSON object (no markdown, no extra text) with exactly 3 items:
{{"queries": ["...", "...", "..."]}}

User input history (newest last):
{user_input_history}

Last assistant response:
{last_assistant_response}

Active file (if any): {active_file}
"""


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

    return PROMPT_TEMPLATE.format(
        user_input_history=history_str,
        last_assistant_response=reply,
        active_file=active,
    )


def suggest(ctx: dict) -> list:
    prompt = build_prompt(ctx)
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",   # Ollama JSON 约束模式
        "options": {"temperature": 0.7, "num_predict": 256},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    text = resp["message"]["content"].strip()
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
            print(f"  本地复刻建议({MODEL}):")
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
    args = ap.parse_args()

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
    queries = suggest(ctx)
    print(json.dumps(queries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
