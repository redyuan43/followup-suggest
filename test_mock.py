#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ds-flash 后端 mock 数据测试: 覆盖中文/英文/文件上下文/超长回复/边界场景"""
import json
import time
import sys
from followup_suggest import suggest, build_prompt, detect_language, BACKEND

# ---------- mock 数据 ----------
MOCK_CASES = [
    {
        "name": "中文编码场景",
        "ctx": {
            "user_input_history": ["帮我写一个解析 YAML 的 Python 脚本", "继续，加上错误处理"],
            "last_assistant_response": "已完成 YAML 解析脚本主体：使用 pyyaml 库，包含 load_file() 和 validate_schema() 两个函数，错误处理覆盖了文件不存在、格式非法两类异常。下一步计划添加 CLI 入口。",
        },
    },
    {
        "name": "英文场景(语言跟随)",
        "ctx": {
            "user_input_history": ["write a rust http server", "add graceful shutdown"],
            "last_assistant_response": "Implemented the HTTP server with tokio and axum, including graceful shutdown via SIGTERM handler. Connection draining takes up to 30s. Next: add health check endpoint.",
        },
    },
    {
        "name": "带文件上下文",
        "ctx": {
            "user_input_history": ["优化这个函数的性能"],
            "last_assistant_response": "分析发现 parse_tokens() 存在 O(n²) 复杂度，主因是嵌套循环里的重复 split。已改为预编译正则一次遍历，实测快 40 倍。建议补充基准测试。",
            "active_file_content": "def parse_tokens(s):\n    tokens = []\n    for part in s.split(','):\n        for t in part.split(':'):\n            tokens.append(t.strip())\n    return tokens",
            "symbol_infos": '[{"content": "parse_tokens.py"}]',
        },
    },
    {
        "name": "超长回复(触发截断)",
        "ctx": {
            "user_input_history": ["总结一下"],
            "last_assistant_response": "重构报告：\n" + "阶段进展详细描述。" * 300 + "\n最终全部完成。",
        },
    },
    {
        "name": "空历史(仅回复)",
        "ctx": {
            "user_input_history": [],
            "last_assistant_response": "已初始化项目结构，包含 src/、test/、README。下一步可以开始实现核心模块。",
        },
    },
    {
        "name": "极简上下文",
        "ctx": {
            "user_input_history": ["继续"],
            "last_assistant_response": "好的。",
        },
    },
]


def main():
    backend = sys.argv[1] if len(sys.argv) > 1 else "ds-flash"
    globals()["BACKEND"] = backend
    print(f"后端: {backend}\n")
    ok = fail = 0
    for case in MOCK_CASES:
        name, ctx = case["name"], case["ctx"]
        lang = detect_language([*ctx["user_input_history"], ctx["last_assistant_response"]])
        t0 = time.time()
        try:
            queries = suggest(ctx)
            dt = time.time() - t0
            status = "PASS" if queries else "EMPTY"
            ok += bool(queries)
            print(f"[{status}] {name}  lang={lang}  {dt:.1f}s")
            for q in queries:
                print(f"       - {q}")
        except Exception as e:
            dt = time.time() - t0
            fail += 1
            print(f"[FAIL] {name}  lang={lang}  {dt:.1f}s  {type(e).__name__}: {e}")
        print()
    print(f"结果: {ok} pass / {fail} fail  (共 {len(MOCK_CASES)} 例)")


if __name__ == "__main__":
    main()
