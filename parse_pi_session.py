# -*- coding: utf-8 -*-
"""
parse_pi_session.py — pi 会话日志 → 通用事件 JSONL（回溯器 v2.0 解析层）

用法:
  python -X utf8 parse_pi_session.py <session.jsonl> <output.jsonl>            # 整会话一份
  python -X utf8 parse_pi_session.py <session.jsonl> <outdir>/ --split-by-task # 按任务切分 + manifest.json

设计: 本脚本唯一绑定 pi 日志格式；输出 schema 与工具链无关——
      action ∈ {boot, user_msg, answer, think, read, write, exec, web, skill, spawn, report, ask, compress, inject}
      v2.0: +think(模型思考块) +inject(harness注入:系统提醒/用户装料/剪除/权限档/模型切换)
      answer/think 事件带 usage={i输入,o输出,r思考,t总tok,$成本}——真实计量优先于估算
      回放页面只认 schema，换任何工作流/工具链只要映射表对，界面通用。
任务切分: 每条实质性 user 消息（≥6字且非确认语）= 一次任务运行起点。
"""
import sys, json, os, re
from collections import Counter

TOOL_MAP = {
    "read": "read", "grep": "read", "find": "read", "ls": "read",
    "conversation_read": "read",            # 读别的对话=取上下文
    "write": "write", "edit": "write", "edit_soft": "write",
    "bash": "exec", "powershell": "exec",
    "skill": "skill",
    "subagent_spawn": "spawn", "subagent_steer": "spawn", "delegate_task": "spawn",
    "subagent_wait_all": "report", "subagent_get_result": "report",
    "present_files": "report",
    "ask_user_question": "ask",
    "web_search": "web", "fetch_content": "web", "get_search_content": "web",
    "web_enable": "web", "browser_page": "web",
    "compact_context": "compress",          # 手动压缩=type:compaction 之外的另一条路
    "plan_update": "exec", "todo_list": "exec", "claim_files": "exec",
    "db_query": "exec", "db_rows": "exec", "db_connections": "exec",
    "lsp": "exec", "eval": "exec",
}
BASIS = {"read": "读", "write": "写", "exec": "工具回路", "skill": "技能(用到才装)",
         "web": "上网取料", "spawn": "派活", "report": "结论回包", "ask": "人工闸门",
         "answer": "回答进包", "user_msg": "用户原话", "compress": "压缩", "boot": "开机自动",
           "think": "思考进包", "inject": "harness注入"}
CONFIRM = {"继续", "好的", "可以", "嗯", "嗯嗯", "ok", "OK", "是的", "对", "好",
           "行", "go", "谢谢", "辛苦了", "收到", "没问题", "是", "对的对的", "嗯好"}

def est(text): return int(len(text or "") * 0.7)

def short(s, n=60): return re.sub(r"\s+", " ", s or "")[:n]

def parse_events(src, state=None):
    """src: 文件路径 或 行列表(live增量); state: {"pending":{},"seq":N} 跨调用续接(工具调用/结果配对不丢)"""
    if state is None: state = {"pending": {}, "seq": 0}
    events, pending = [], state["pending"]
    def emit(t, action, target, detail="", tokens=0, ok=True, actor="main", path=None, usage=None):
        state["seq"] += 1
        cap = 1500 if action in ("answer", "user_msg") else (200 if action in ("web", "inject") else 90)
        ev = {"seq": state["seq"], "t": t, "actor": actor, "action": action,
              "target": short(target, 70), "detail": short(detail, cap),
              "tokens_est": tokens, "basis": BASIS.get(action, ""), "ok": ok}
        if path: ev["path"] = path
        if usage: ev["usage"] = usage
        events.append(ev)

    def pack_usage(m):
        """真实计量: usage→紧凑dict; 无则 None(回退估算)"""
        u = m.get("usage") or {}
        if not u.get("totalTokens"): return None
        c = (u.get("cost") or {}).get("total", 0) or 0
        return {"i": u.get("input", 0), "o": u.get("output", 0), "r": u.get("reasoning", 0),
                "t": u["totalTokens"], "$": round(c, 6)}
    lines = src if isinstance(src, list) else open(src, encoding="utf-8")
    for line in lines:
        line = line.strip()
        if not line: continue
        try: o = json.loads(line)
        except: continue
        ty, ts = o.get("type"), o.get("timestamp", "")
        if ty == "custom_message":
            ct = o.get("customType", "")
            parts_c = o.get("content") or []
            if ct == "file":
                txt = " ".join(p.get("text", "") for p in parts_c if isinstance(p, dict) and p.get("type") == "text")
                has_img = any(isinstance(p, dict) and p.get("type") == "image" for p in parts_c)
                emit(ts, "inject", "用户装料", (txt or "图片/文件(拖入)") + ("+图片" if txt and has_img else ""), est(txt) if txt else (400 if has_img else 0))
            else:
                txt = " ".join(p.get("text", "") for p in parts_c if isinstance(p, dict) and p.get("type") == "text")
                lbl = {"parallel-work-reminder": "系统提醒", "manual-retry": "重试注入"}.get(ct, ct or "系统注入")
                emit(ts, "inject", lbl, txt or ct, est(txt))
            continue
        if ty == "custom":
            ct = o.get("customType", "")
            if ct == "permission/preset":
                emit(ts, "inject", "权限档", str((o.get("data") or {}).get("preset", "")), 0)
            # plan/update 与工具重复、web-search-results 与 web 工具重复、compaction-done 与 compaction 行重复 → 不重复建模
            continue
        if ty == "context_edit":
            emit(ts, "inject", "✂️ 剪除上下文", "手动编辑: 剪除条目 " + str(o.get("targetId", ""))[:8], 0)
            continue
        if ty == "model_change":
            emit(ts, "inject", "模型切换", (o.get("provider", "") + "/" + o.get("modelId", "")).strip("/"), 0)
            continue
        if ty == "session":
            emit(ts, "boot", "开机装载：章程/工具表/记忆", "cwd=" + o.get("cwd", ""), 7000)
        elif ty == "compaction":
            emit(ts, "compress", "对话历史→结构化摘要", "章程自动重载", -1)
        elif ty == "message":
            m = o.get("message") or {}
            role, c = m.get("role"), m.get("content")
            if role == "toolResult":
                info = pending.pop(m.get("toolCallId"), None)
                if not info: continue
                action, target, _ = info
                if action in ("spawn", "ask"): continue  # 发起时已 emit，结果端不再重复(审查P1-2)
                out = "".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))
                ok = not m.get("isError")
                sk_path = None
                if action == "skill":
                    # skill 加载结果自带 location 与 description —— 供资料架悬停/跳转用
                    mloc = re.search(r'location="([^"]+)"', out)
                    if mloc: sk_path = mloc.group(1)
                    mdesc = re.search(r'description="([^"]{10,160})', out)
                    if mdesc: out = mdesc.group(1)
                emit(ts, action, target, ("失败: " if not ok else "") + short(out, 60),
                     est(out) if action in ("read", "exec", "web") else 0, ok, path=sk_path)
                continue
            if isinstance(c, str):
                if role == "user": emit(ts, "user_msg", "用户原话", c)
                elif role == "assistant": emit(ts, "answer", "大脑回答", c, est(c))
                continue
            if not isinstance(c, list): continue
            usg = pack_usage(m) if role == "assistant" else None
            for part in c:
                pt = part.get("type")
                if pt == "text":
                    txt = part.get("text", "")
                    if role == "user": emit(ts, "user_msg", "用户原话", txt)
                    elif role == "assistant" and txt.strip(): emit(ts, "answer", "大脑回答", txt, est(txt), usage=usg)
                elif pt == "thinking":
                    if role == "assistant": emit(ts, "think", "思考块", part.get("thinking", ""), est(part.get("thinking", "")), usage=usg)
                elif pt == "toolCall":
                    name = part.get("name", "")
                    action = TOOL_MAP.get(name, "exec")
                    args = part.get("arguments") or {}
                    if action == "read":
                        target = args.get("path") or args.get("file_path") or args.get("pattern") or name
                    elif action == "write":
                        target = args.get("path") or args.get("file_path") or name
                    elif action == "exec":
                        target = short(args.get("command") or args.get("cmd") or name, 50)
                    elif action == "skill":
                        target = args.get("name") or "skill"
                    elif action == "spawn":
                        target = short(args.get("prompt") or args.get("task") or "子agent", 40)
                    elif action == "ask":
                        target = short(json.dumps(args, ensure_ascii=False), 50)
                    elif action == "web":
                        # 上网：target=查询词/URL/检索id
                        target = short(args.get("query") or (args.get("queries") or [""])[0]
                                       or args.get("url") or args.get("path") or name, 60)
                    else:
                        target = name
                    pending[part.get("id")] = (action, target, ts)
                    if action in ("spawn", "ask"):
                        emit(ts, action, target, "发起")
    return events

def split_tasks(events, outdir, src):
    os.makedirs(outdir, exist_ok=True)
    tasks, cur = [], None
    for e in events:
        is_new = (e["action"] == "user_msg" and len(e["detail"]) >= 6
                  and e["detail"][:6] not in CONFIRM and e["detail"][:4] not in CONFIRM)
        if is_new:
            if cur: tasks.append(cur)
            cur = {"events": [], "first_user": e["detail"], "start": e["t"]}
        if cur is None:
            cur = {"events": [], "first_user": "(开机/无用户输入)", "start": e["t"]}
        cur["events"].append(e)
    if cur: tasks.append(cur)
    sid = os.path.basename(src).split("_")[0][:10]
    manifest = []
    for i, t in enumerate(tasks, 1):
        fname = f"task{i:03d}.jsonl"
        with open(os.path.join(outdir, fname), "w", encoding="utf-8") as f:
            for e in t["events"]: f.write(json.dumps(e, ensure_ascii=False) + chr(10))
        tok = sum(e["tokens_est"] for e in t["events"] if e["tokens_est"] > 0)
        errs = sum(1 for e in t["events"] if not e["ok"])
        acts = Counter(e["action"] for e in t["events"])
        end = t["events"][-1]["t"] if t["events"] else t["start"]
        manifest.append({"id": i, "file": fname, "first_user": t["first_user"][:70],
                         "start": t["start"], "end": end, "n_events": len(t["events"]),
                         "tokens": tok, "errors": errs,
                         "writes": acts.get("write", 0), "spawns": acts.get("spawn", 0),
                         "web": acts.get("web", 0),
                         "compress": acts.get("compress", 0)})
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"session": os.path.basename(src), "tool": "parse_pi_session.py v2.0",
                   "n_tasks": len(tasks), "tasks": manifest}, f, ensure_ascii=False, indent=1)
    print(f"任务数: {len(tasks)}  →  {outdir}")
    for m in manifest[:15]:
        print(f"  #{m['id']:03d} {m['n_events']:4d}事件 {m['tokens']//1000:4d}K tok 失败{m['errors']:3d} 写{m['writes']:2d} 上网{m['web']:2d} 压缩{m['compress']} | {m['first_user']}")
    if len(manifest) > 15: print(f"  ... 共 {len(manifest)} 条")

def main(src, dst):
    events = parse_events(src)
    with open(dst, "w", encoding="utf-8") as f:
        for e in events: f.write(json.dumps(e, ensure_ascii=False) + chr(10))
    act = Counter(e["action"] for e in events)
    tok = sum(e["tokens_est"] for e in events if e["tokens_est"] > 0)
    print(f"事件总数: {len(events)}")
    print(f"动作分布: {dict(act.most_common())}")
    print(f"装包内容累计(估): {tok//1000}K（含结果输出，未扣压缩）")
    print(f"失败事件: {sum(1 for e in events if not e['ok'])}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    if "--split-by-task" in sys.argv:
        split_tasks(parse_events(sys.argv[1]), sys.argv[2], sys.argv[1])
    else:
        main(sys.argv[1], sys.argv[2])
