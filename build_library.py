# -*- coding: utf-8 -*-
"""
build_library.py — 全量会话 → 回溯器对话库（索引 + 按需加载分片）

用法: python -X utf8 build_library.py
产出（在回溯器目录 lib/ 下）:
  index.js     对话索引（分组=项目域目录），页面秒开清单
  c-<id>.js    每对话一个分片 window.__TRAJ_LIB__["<id>"]=events，点选时 <script> 注入加载
标题=首个实质性 user 消息；空会话(<10事件)跳过。
"""
import os, sys, json, glob, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_pi_session import parse_events

SESS_ROOT = os.path.expanduser("~/.pi/agent/sessions")   # pi 标准会话位置，跨机器通用
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
HOME_NAME = os.path.basename(os.path.expanduser("~"))     # 当前用户名(目录过滤用)

def project_name(folder):
    # 会话目录名形如 "--D--myworkspace-subproject--"，取业务段做分组名
    core = folder.strip("-")
    parts = [p for p in core.split("-") if p]
    # 丢弃盘符、AIpi/AiCode 根段和当前用户名段，留业务段
    biz = [p for p in parts if p not in ("D", "C", "AIpi", "AiCode", HOME_NAME)
           and p not in ("Users",)]
    if not biz: return "AIpi 根目录"
    if biz[0] in ("Windows", "System32"): return "系统杂项"
    if biz[0] in ("Users", "Desktop"): return "桌面杂项"
    return "-".join(biz)

def first_title(events):
    for e in events:
        if e["action"] == "user_msg" and len(e["detail"]) >= 8:
            return e["detail"][:60]
    return "(无用户输入)"

def framework_inventory(cwds=None):
    """扫真实架构性文件（章程+全部技能），作为回放页资料架「框架资料」预置清单
    cwds: 从会话日志收集到的 {工作目录: 次数}，从中发现工作区章程"""
    import glob as _g
    home = os.path.expanduser("~").replace("\\", "/")
    items = []
    def add(path, name, desc=""):
        items.append({"path": path.replace("\\", "/"), "name": name, "desc": desc})
    add(home + "/.pi/agent/AGENTS.md", "AGENTS.md(全局章程)", "OS 章程：开机自动装入上下文")
    # 工作区章程：从会话 cwd 动态发现（不再硬编码特定机器路径）
    for cwd, _ in sorted((cwds or {}).items(), key=lambda kv: -kv[1])[:3]:
        p = cwd.replace("\\", "/").rstrip("/") + "/AGENTS.md"
        if os.path.exists(cwd) and os.path.exists(p):
            add(p, "AGENTS.md(" + (os.path.basename(cwd) or cwd) + ")", "工作区章程：该域对话开机装载")
    for pat, root in [(home + "/.agents/skills/*/SKILL.md", None),
                      (home + "/.pi/agent/skills/*/SKILL.md", None),
                      (home + "/.pi/agent/skills/*/*/SKILL.md", None)]:
        for sp in sorted(_g.glob(pat)):
            name = os.path.basename(os.path.dirname(sp))
            desc = ""
            try:
                head = io_open(sp).read(900)
                m = re.search(r"description:\s*(.{10,130})", head)
                if m: desc = m.group(1).strip()
            except Exception: pass
            add(sp, "📖 " + name, desc or "技能文件（用到才装进上下文）")
    return items

def io_open(p):
    import io as _io
    return _io.open(p, encoding="utf-8", errors="ignore")

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    groups, lib, cwd_cnt = {}, {}, {}
    files = sorted(glob.glob(os.path.join(SESS_ROOT, "*", "*.jsonl")))
    n_skip = 0
    for fp in files:
        if "_backup" in fp or ".migrated" in fp: continue
        folder = os.path.basename(os.path.dirname(fp))
        proj = project_name(folder)
        base = os.path.basename(fp)
        m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}-\d{2})", base)
        date, hhmm = (m.group(1), m.group(2)) if m else ("?", "?")  # hhmm 保持数字,勿用冒号(Windows文件名非法,会变ADS空壳)
        # 优先取用户改过的会话名(session_info.name 最后一条);无则用首个实质 user 消息
        conv_name = None
        for line in open(fp, encoding="utf-8"):
            if '"session_info"' not in line and '"type": "session"' not in line and '"type":"session"' not in line: continue
            try:
                o = json.loads(line)
                if o.get("type") == "session_info" and o.get("name"): conv_name = o["name"]
                elif o.get("type") == "session" and o.get("cwd"):
                    cwd_cnt[o["cwd"]] = cwd_cnt.get(o["cwd"], 0) + 1
            except: pass
        try: events = parse_events(fp)
        except Exception as ex:
            print(f"跳过(解析失败) {base}: {ex}"); n_skip += 1; continue
        if len(events) < 10: n_skip += 1; continue
        cid = proj.replace("/", "_") + "__" + date + "T" + hhmm
        real = sum(e["usage"]["t"] for e in events if e.get("usage"))
        cost = sum(e["usage"].get("$", 0) for e in events if e.get("usage"))
        tok = real if real > 0 else sum(e["tokens_est"] for e in events if e["tokens_est"] > 0)
        errs = sum(1 for e in events if not e["ok"])
        writes = sum(1 for e in events if e["action"] == "write")
        conv = {"id": cid, "title": (conv_name or first_title(events))[:60], "renamed": bool(conv_name), "date": date,
                "time": hhmm.replace("-", ":"),
                "n": len(events), "tok": tok, "errs": errs, "writes": writes,
                "cost": round(cost, 2), "tokReal": real > 0}
        groups.setdefault(proj, []).append(conv)
        lib[cid] = events
    # 每组按时间正序
    index = {"groups": []}
    for proj in sorted(groups, key=lambda p: -len(groups[p])):
        convs = sorted(groups[proj], key=lambda c: c["date"] + c["time"])
        index["groups"].append({"project": proj, "convs": convs})
    # 写分片
    for cid, evs in lib.items():
        with open(os.path.join(OUT_DIR, f"c-{cid}.js"), "w", encoding="utf-8") as f:
            f.write('window.__TRAJ_LIB__=window.__TRAJ_LIB__||{};')
            f.write('__TRAJ_LIB__["' + cid + '"]=' + json.dumps(evs, ensure_ascii=False, separators=(",", ":")) + ";")
    with open(os.path.join(OUT_DIR, "index.js"), "w", encoding="utf-8") as f:
        f.write("window.CONV_INDEX=" + json.dumps({"groups": index["groups"], "frame": framework_inventory(cwd_cnt)}, ensure_ascii=False, separators=(",", ":")) + ";")
    total = sum(len(g["convs"]) for g in index["groups"])
    print(f"入库: {total} 对话 / {len(index['groups'])} 项目 / 跳过 {n_skip}")
    for g in index["groups"]:
        print(f"  {g['project']}: {len(g['convs'])} 个对话")
        for c in g["convs"]:
            print(f"    {c['date']} {c['time']}  {c['n']:5d}事件 {c['tok']//1000:5d}K 失败{c['errs']:3d} 写{c['writes']:3d} | {c['title']}")

if __name__ == "__main__":
    main()
