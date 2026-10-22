#!/usr/bin/env python3
"""claude-usage-tracker: per-answer Claude Code usage, as % of the 5-hour and weekly limits.

  tracker.py [report] [N]   current limits, the last N answers (default 15), per-session totals
  tracker.py on | off       add / remove the Stop hook and status line in Claude Code's settings
  tracker.py stop           Stop hook: logs the answer that just finished and shows its %
  tracker.py statusline     status line: last answer, this session, current limits

How it works: every API request in the transcripts (<config>/projects/**/*.jsonl) is weighted by
API price (model + input / output / cache-write / cache-read tokens). The exact utilisation of each
window comes from the endpoint Claude Code's /usage uses. A window's % is divided across its
requests by weight, so an answer's % = its weight x (window % / window total weight).
Everything runs locally and costs no tokens.
"""
import fcntl
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

CONFIG = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
PROJECTS = os.path.join(CONFIG, "projects")
SETTINGS = os.path.join(CONFIG, "settings.json")
DIR = os.path.join(CONFIG, "usage-tracker")
CACHE = os.path.join(DIR, "cache.json")        # per-transcript parsed requests
STATE = os.path.join(DIR, "state.json")        # usage-endpoint cache, calibration, per-session answers
LEDGER = os.path.join(DIR, "ledger.jsonl")     # one line per answer
PREV_STATUSLINE = os.path.join(DIR, "previous-statusline.json")
LOCK = os.path.join(DIR, ".lock")
SELF = os.path.abspath(__file__)
MARK = "usage-tracker/tracker.py"              # identifies our entries in settings.json

KEEP_DAYS = 8
USAGE_TTL = 45  # seconds between usage-endpoint calls

# $ per MTok: input, output, cache read. Cache writes: 1.25x input (5m), 2x input (1h).
# Only the ratios matter; the scale comes from the live utilisation.
PRICES = [
    ("fable", (10, 50, 0.25)), ("mythos", (10, 50, 0.25)),
    ("opus-5-5", (4, 20, 0.20)), ("opus", (5, 25, 0.50)),
    ("sonnet-5-5", (2, 10, 0.20)), ("sonnet-5", (2, 10, 0.20)), ("sonnet", (3, 15, 0.30)),
    ("haiku-5-5", (0.10, 0.50, 0.01)), ("haiku", (1, 5, 0.10)),
]


def price(model):
    for key, p in PRICES:
        if key in model:
            return p
    return 4, 20, 0.20


def weight(model, u):
    inp, out, read = price(model)
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens") or 0
    w5m = cc.get("ephemeral_5m_input_tokens")
    if w5m is None:
        w5m = (u.get("cache_creation_input_tokens") or 0) - w1h
    c = ((u.get("input_tokens") or 0) * inp + (u.get("output_tokens") or 0) * out
         + w5m * inp * 1.25 + w1h * inp * 2 + (u.get("cache_read_input_tokens") or 0) * read) / 1e6
    return c * 2 if u.get("speed") == "fast" else c


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def locked(fn):
    os.makedirs(DIR, exist_ok=True)
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        return fn()


# ---------- transcripts ----------

def scan(cache):
    """Incrementally parse every transcript; cache[path] = {"off": bytes read, "reqs": {rid: [ts, weight, tokens]}}."""
    cutoff = time.time() - KEEP_DAYS * 86400
    seen = set()
    for path in glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True):
        path = os.path.realpath(path)  # transcript_path from the hook is a real path
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime < cutoff:
            continue
        seen.add(path)
        ent = cache.get(path)
        if not ent or st.st_size < ent["off"]:
            ent = cache[path] = {"off": 0, "reqs": {}}
        if st.st_size == ent["off"]:
            continue
        with open(path, "rb") as f:
            f.seek(ent["off"])
            data = f.read()
        end = data.rfind(b"\n") + 1  # only complete lines
        for line in data[:end].splitlines():
            if b'"usage"' not in line or b'"requestId"' not in line:
                continue
            try:
                d = json.loads(line)
                rid, m = d["requestId"], d["message"]
                u, model = m["usage"], m.get("model", "")
            except Exception:
                continue
            # one request is written as several lines (one per content block) with the same usage
            if rid in ent["reqs"] or model.startswith("<"):
                continue
            w = weight(model, u)
            if w > 0:
                toks = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0) \
                    + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                ent["reqs"][rid] = [ts(d["timestamp"]), w, toks]
        ent["off"] += end
        ent["reqs"] = {k: v for k, v in ent["reqs"].items() if v[0] >= cutoff}
    for path in list(cache):
        if path not in seen:
            del cache[path]


def total_since(cache, start):
    return sum(r[1] for e in cache.values() for r in e["reqs"].values() if r[0] >= start)


def session_meta(path):
    """The session's title and its latest typed prompt (text, timestamp)."""
    title, prompt, prompt_ts = None, None, None
    try:
        with open(path) as f:
            for line in f:
                if '"ai-title"' in line:
                    try:
                        title = json.loads(line).get("aiTitle") or title
                    except Exception:
                        pass
                elif '"type":"user"' in line:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    c = d.get("message", {}).get("content")
                    if isinstance(c, str) and not c.startswith("<") and not d.get("isMeta"):
                        prompt, prompt_ts = c, ts(d["timestamp"])
    except OSError:
        pass
    return title, prompt, prompt_ts


# ---------- usage endpoint ----------

def oauth_token():
    """Claude Code's login token: macOS Keychain, else the credentials file (Linux)."""
    if sys.platform == "darwin":
        try:
            raw = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                                 capture_output=True, text=True, timeout=5).stdout
            return json.loads(raw)["claudeAiOauth"]["accessToken"]
        except Exception:
            pass
    return load(os.path.join(CONFIG, ".credentials.json"), {}).get("claudeAiOauth", {}).get("accessToken")


def fetch_usage(state):
    u = state.get("usage")
    if u and time.time() - u["at"] < USAGE_TTL:
        return u
    try:
        req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
            "Authorization": "Bearer " + oauth_token(), "anthropic-beta": "oauth-2025-04-20"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.load(r)
    except Exception:
        return u  # keep last known values
    u = {"at": time.time()}
    for key, span in (("five_hour", 5 * 3600), ("seven_day", 7 * 86400)):
        w = d.get(key) or {}
        if w.get("utilization") is not None and w.get("resets_at"):
            u[key] = {"util": float(w["utilization"]), "start": ts(w["resets_at"]) - span,
                      "resets": ts(w["resets_at"])}
    state["usage"] = u
    return u


def scales(cache, state, usage):
    """% per weight unit for each window. Low readings are coarse (whole %), so lean on past windows."""
    out, calib = {}, state.setdefault("calib", {})
    for key, min_util in (("five_hour", 8), ("seven_day", 3)):
        w = (usage or {}).get(key)
        if not w:
            continue
        tot = total_since(cache, w["start"])
        hist = calib.get(key, {})
        cur = w["util"] / tot if tot > 0 and w["util"] > 0 else None
        if cur and w["util"] >= min_util:
            hist[str(int(w["start"]))] = cur  # latest reading per window
            hist = dict(sorted(hist.items())[-10:])
            calib[key] = hist
        past = sorted(v for k, v in hist.items() if k != str(int(w["start"])))
        if cur and w["util"] >= min_util:
            out[key] = cur
        elif past:
            out[key] = past[len(past) // 2]
        elif cur:
            out[key] = cur
    return out


def session_pcts(state, sid):
    """A session's share of the current 5-hour window and week."""
    usage, k, out = state.get("usage") or {}, state.get("k") or {}, []
    for key in ("five_hour", "seven_day"):
        w = usage.get(key)
        if not w or key not in k:
            out.append(None)
            continue
        out.append(sum(wt for t, wt in state.get("answers", {}).get(sid, []) if t >= w["start"]) * k[key])
    return out


def fmt_pct(p):
    if p is None:
        return "?"
    return f"{p:.2f}%" if p < 10 else f"{p:.1f}%"


# ---------- hook commands ----------

def stop():
    hook = json.load(sys.stdin)
    if hook.get("stop_hook_active"):
        return
    sid, tpath = hook.get("session_id"), hook.get("transcript_path")
    if not sid or not tpath:
        return

    def run():
        cache, state = load(CACHE, {}), load(STATE, {})
        scan(cache)
        usage = fetch_usage(state)
        k = state["k"] = scales(cache, state, usage)
        title, prompt, prompt_ts = session_meta(tpath)
        # this answer = every request since the session's previous answer (or its latest prompt)
        last_stop = state.setdefault("last_stop", {})
        start = last_stop.get(sid) or (prompt_ts or 0) - 1
        last_stop[sid] = time.time()
        state["last_stop"] = {s: t for s, t in last_stop.items() if t > time.time() - KEEP_DAYS * 86400}
        mine = [os.path.realpath(p) for p in
                [tpath] + glob.glob(os.path.join(os.path.dirname(tpath), sid, "subagents", "*.jsonl"))]
        reqs = [r for p in mine for r in cache.get(p, {}).get("reqs", {}).values() if r[0] > start]
        w, toks = sum(r[1] for r in reqs), sum(r[2] for r in reqs)
        if not reqs:
            save(CACHE, cache)
            save(STATE, state)
            return None
        rec = {"ts": time.time(), "session": sid, "cwd": hook.get("cwd"), "title": title,
               "prompt": (prompt or "")[:200], "requests": len(reqs), "tokens": toks, "weight": w,
               "pct5": w * k["five_hour"] if "five_hour" in k else None,
               "pct7": w * k["seven_day"] if "seven_day" in k else None,
               "util5": (usage or {}).get("five_hour", {}).get("util"),
               "util7": (usage or {}).get("seven_day", {}).get("util")}
        # per-session answers, read by the status line (which must stay cheap: no scan, no network)
        answers = state.setdefault("answers", {})
        answers[sid] = (answers.get(sid, []) + [[rec["ts"], w]])[-2000:]
        state["answers"] = {s: a for s, a in answers.items() if a[-1][0] > time.time() - KEEP_DAYS * 86400}
        save(CACHE, cache)
        save(STATE, state)
        with open(LEDGER, "a") as f:
            f.write(json.dumps(rec) + "\n")
        rec["sess5"], rec["sess7"] = session_pcts(state, sid)
        return rec

    rec = locked(run)
    if rec:
        msg = f"Tracker · this answer: {fmt_pct(rec['pct5'])} of 5h, {fmt_pct(rec['pct7'])} of week"
        msg += f" · this session: {fmt_pct(rec['sess5'])} of 5h, {fmt_pct(rec['sess7'])} of week"
        if rec["util5"] is not None and rec["util7"] is not None:
            msg += f" · now {rec['util5']:.0f}% / {rec['util7']:.0f}%"
        msg += f" · {rec['tokens'] / 1000:.0f}k tokens"
        print(json.dumps({"systemMessage": msg}))  # shown to the user, not sent to the model


def statusline():
    sid = json.load(sys.stdin).get("session_id")
    state = load(STATE, {})
    usage, k = state.get("usage") or {}, state.get("k") or {}
    answers = state.get("answers", {}).get(sid, [])
    parts = []
    if answers and "five_hour" in k:
        s5, _ = session_pcts(state, sid)
        parts.append(f"Last answer {fmt_pct(answers[-1][1] * k['five_hour'])} · Session {fmt_pct(s5)} of 5h")
    else:
        parts.append("Last answer – · Session –")
    w5, w7 = usage.get("five_hour"), usage.get("seven_day")
    if w5:
        if time.time() > w5["resets"]:
            parts.append("5h new window")
        else:
            parts.append(f"5h {w5['util']:.0f}% (resets {datetime.fromtimestamp(w5['resets']).strftime('%H:%M')})")
    if w7:
        parts.append(f"week {w7['util']:.0f}%")
    print("  │  ".join(parts))


# ---------- report ----------

def report(n=15):
    def run():
        cache, state = load(CACHE, {}), load(STATE, {})
        scan(cache)
        usage = fetch_usage(state)
        k = state["k"] = scales(cache, state, usage)
        save(CACHE, cache)
        save(STATE, state)
        return usage, k

    usage, k = locked(run)
    rows = []
    try:
        with open(LEDGER) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except OSError:
        pass

    w5, w7 = (usage or {}).get("five_hour"), (usage or {}).get("seven_day")
    # answers inside the current windows use the current scale so they add up to the live %
    for r in rows:
        if w5 and "five_hour" in k and r["ts"] >= w5["start"]:
            r["pct5"] = r["weight"] * k["five_hour"]
        if w7 and "seven_day" in k and r["ts"] >= w7["start"]:
            r["pct7"] = r["weight"] * k["seven_day"]

    def when(t, fmt="%a %H:%M"):
        return datetime.fromtimestamp(t).strftime(fmt)

    def label(r):
        return (r.get("title") or os.path.basename(r.get("cwd") or "") or r["session"][:8])[:34]

    print()
    if w5:
        print(f"  5-hour window: {w5['util']:.0f}% used · resets {when(w5['resets'], '%a %d %b %H:%M')}")
    if w7:
        print(f"  Weekly:        {w7['util']:.0f}% used · resets {when(w7['resets'], '%a %d %b %H:%M')}")
    if not usage:
        print("  (usage endpoint unreachable: % figures use the last calibration)")
    if not rows:
        print("\n  No answers logged yet: they appear here after Claude Code finishes its next answer.\n")
        return

    print(f"\n  Last {min(n, len(rows))} answers")
    print(f"  {'time':<10} {'5h':>7} {'week':>7} {'tokens':>7}  {'session':<34}  prompt")
    for r in rows[-n:]:
        prompt = " ".join(r.get("prompt", "").split())[:50]
        print(f"  {when(r['ts']):<10} {fmt_pct(r.get('pct5')):>7} {fmt_pct(r.get('pct7')):>7} "
              f"{r['tokens'] / 1000:>6.0f}k  {label(r):<34}  {prompt}")

    for name, w, key in (("current 5-hour window", w5, "pct5"), ("current week", w7, "pct7")):
        if not w:
            continue
        sessions = {}
        for r in rows:
            if r["ts"] >= w["start"] and r.get(key) is not None:
                s = sessions.setdefault(r["session"], [label(r), 0.0, 0])
                s[0], s[1], s[2] = label(r), s[1] + r[key], s[2] + 1
        if not sessions:
            continue
        print(f"\n  Sessions, {name}")
        for lbl, pct, cnt in sorted(sessions.values(), key=lambda s: -s[1])[:12]:
            print(f"  {fmt_pct(pct):>7}  {cnt:>3} answers  {lbl}")
    print()


# ---------- settings ----------

def read_settings():
    if not os.path.exists(SETTINGS):
        return {}
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except ValueError:
        sys.exit(f"{SETTINGS} is not valid JSON; fix it first, nothing was changed.")


def write_settings(s):
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, SETTINGS + ".bak-usage-tracker")
    os.makedirs(CONFIG, exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")


def ours(entry):
    return MARK in json.dumps(entry)


def remove_hook(s):
    hooks = s.get("hooks", {})
    stop_hooks = [h for h in hooks.get("Stop", []) if not ours(h)]
    if stop_hooks:
        hooks["Stop"] = stop_hooks
    else:
        hooks.pop("Stop", None)
    if not hooks:
        s.pop("hooks", None)


def turn_on():
    s = read_settings()
    remove_hook(s)
    run = "python3 " + shlex.quote(SELF)
    s.setdefault("hooks", {}).setdefault("Stop", []).append(
        {"hooks": [{"type": "command", "command": run + " stop", "timeout": 30}]})
    current = s.get("statusLine")
    if current and not ours(current):
        answer = "n"
        if sys.stdin.isatty():
            print(f"You already have a status line: {json.dumps(current)}")
            answer = input("Replace it? It is saved and restored when you turn the tracker off. [y/N] ").strip().lower()
        if answer.startswith("y"):
            os.makedirs(DIR, exist_ok=True)
            save(PREV_STATUSLINE, current)
            current = None
        else:
            print("Kept your status line; per-answer messages and cc-usage still work.")
    if not current or ours(current):
        s["statusLine"] = {"type": "command", "command": run + " statusline"}
    write_settings(s)
    print("Usage tracker on. Restart open Claude Code sessions to pick it up.")


def turn_off():
    s = read_settings()
    remove_hook(s)
    if ours(s.get("statusLine")):
        previous = load(PREV_STATUSLINE, None)
        if previous:
            s["statusLine"] = previous
            os.remove(PREV_STATUSLINE)
        else:
            s.pop("statusLine", None)
    write_settings(s)
    print(f"Usage tracker off. Its files and history stay in {DIR}.")


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args and not args[0].isdigit() else "report"
    try:
        if cmd == "stop":
            stop()
        elif cmd == "statusline":
            statusline()
        elif cmd == "on":
            turn_on()
        elif cmd == "off":
            turn_off()
        elif cmd == "report":
            nums = [a for a in args if a.isdigit()]
            report(int(nums[0]) if nums else 15)
        else:
            print(__doc__)
    except Exception:
        if cmd in ("stop", "statusline"):
            sys.exit(0)  # never get in the way of Claude Code
        raise
