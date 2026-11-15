# Agent-shaped benchmark: single decode, N-stream decode, shared-prefix sessions with follow-up turns.
# usage: agentbench.py BASE TAG [streams csv]
import json, sys, time, random, threading, urllib.request, hashlib
BASE, TAG = sys.argv[1], sys.argv[2]
NS = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "4,8").split(",")]
MODEL = json.load(urllib.request.urlopen(BASE + "/v1/models"))["data"][0]["id"]
URL = BASE + "/v1/chat/completions"; OUT = open(f"{TAG}.jsonl", "a")
import os
seed = int(os.environ.get("SEED") or time.time_ns()); rnd = random.Random(seed)
VOC = "the of and to in is it for on with as by at from that this which these those many some other first last next".split() + [f"w{rnd.randrange(10**6)}" for _ in range(600)]
def words(n): return " ".join(rnd.choice(VOC) for _ in range(n))
def run(label, messages, max_tokens, temperature=0):
    body = {"model": MODEL, "stream": True, "stream_options": {"include_usage": True}, "temperature": temperature, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}, "messages": messages}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0 = time.time(); ev = []; usage = None; sha = None; text = []
    try:
        r = urllib.request.urlopen(req, timeout=1800)
        for line in r:
            if not line.startswith(b"data: ") or b"[DONE]" in line: continue
            d = json.loads(line[6:]); t = time.time()
            for c in d.get("choices", []):
                s = c["delta"].get("content") or c["delta"].get("reasoning_content") or ""
                if s: ev.append(t); text.append(s)
            if d.get("usage"): usage = d["usage"]
            tf = d.get("tensorfold") or {}; sha = tf.get("token_sha") or sha
    except Exception as e:
        rec = {"label": label, "error": repr(e)[:200], "total": time.time()-t0}; OUT.write(json.dumps(rec)+"\n"); OUT.flush(); return rec
    t1 = time.time(); u = usage or {}; n = u.get("completion_tokens"); p = u.get("prompt_tokens")
    txt = "".join(text)
    rec = {"label": label, "prompt_tokens": p, "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens"), "completion_tokens": n,
           "ttft": ev[0]-t0 if ev else None, "total": t1-t0, "t0": t0, "t1": t1, "tfirst": ev[0] if ev else None, "tlast": ev[-1] if ev else None,
           "decode_tps": (n-1)/(ev[-1]-ev[0]) if n and len(ev) > 1 and ev[-1] > ev[0] else None,
           "sha": sha or hashlib.sha1(txt.encode()).hexdigest()[:16], "text": txt}
    OUT.write(json.dumps({k: v for k, v in rec.items() if k != "text"}) + "\n"); OUT.flush(); return rec
def par(fns):
    out = [None]*len(fns)
    def w(i): out[i] = fns[i]()
    th = [threading.Thread(target=w, args=(i,)) for i in range(len(fns))]
    [t.start() for t in th]; [t.join() for t in th]; return out
def U(c): return [{"role": "user", "content": c}]
def agg(rs):
    ok = [r for r in rs if r.get("decode_tps")]
    if not ok: return {"errors": len(rs)}
    toks = sum(r["completion_tokens"] for r in ok); wall = max(r["t1"] for r in ok) - min(r["t0"] for r in ok)
    tt = sorted(r["ttft"] for r in ok)
    return {"n_ok": len(ok), "sum_tps": round(sum(r["decode_tps"] for r in ok), 1), "wall_tps": round(toks/wall, 1),
            "ttft_p50": round(tt[len(tt)//2], 2), "ttft_max": round(tt[-1], 2), "wall": round(wall, 1), "errors": len(rs)-len(ok)}
PROSE = "Write a long essay about the history of lighthouses and their keepers."
CODE = "Write a complete Python module implementing an LRU cache with TTL, type hints and docstrings, plus unit tests."
TOPICS = ["tide tables", "bridges", "clockmaking", "canals", "printing presses", "railways", "windmills", "telegraphs", "cartography", "glassblowing", "beekeeping", "salt mines", "aqueducts", "typewriters", "observatories", "ferries"]
R = {"seed": seed}
print(f"[{TAG}] {BASE} {MODEL}", flush=True)
run("warm", U("Reply with exactly: ready"), 8)
if not os.environ.get("SESS_ONLY"):
    for name, prompt in (("prose", PROSE), ("code", CODE)):
        rs = [run(f"single_{name}_{i}", U(prompt), 500) for i in range(3)]
        R[f"single_{name}"] = {"decode_tps": [round(r["decode_tps"], 2) for r in rs], "ttft": round(min(r["ttft"] for r in rs), 3), "sha": rs[0]["sha"], "same": len({r["sha"] for r in rs}) == 1}
        print(f"  single {name}: {R[f'single_{name}']}", flush=True)
    rs = [run(f"single_sampled_{i}", U(PROSE), 500, 0.7) for i in range(2)]
    R["single_sampled"] = {"decode_tps": [round(r["decode_tps"], 2) for r in rs]}; print(f"  single sampled: {R['single_sampled']}", flush=True)
    # fixed-prompt concurrent decode (deterministic prompts: shas comparable across builds)
    for n in NS:
        rs = par([(lambda i=i: run(f"conc{n}_{i}", U(f"Write a long essay about the history of {TOPICS[i % len(TOPICS)]}." + ("" if i < len(TOPICS) else f" Part {i}.")), 400)) for i in range(n)])
        R[f"conc{n}"] = {**agg(rs), "shas": hashlib.sha1("".join(sorted(str(r.get("sha")) for r in rs)).encode()).hexdigest()[:12]}
        print(f"  conc{n}: {R[f'conc{n}']}", flush=True)

    for n in NS:
        rs = par([(lambda i=i: run(f"samp{n}_{i}", U(f"Write a long essay about the history of {TOPICS[i % len(TOPICS)]}."), 400, 0.7)) for i in range(n)])
        R[f"samp{n}"] = agg(rs); print(f"  samp{n}: {R[f'samp{n}']}", flush=True)
    crnd = random.Random(7)
    BLOCKS = ["\n".join(f"def f{crnd.randrange(10**6)}(a, b): return a * {crnd.randrange(999)} + b - {crnd.randrange(999)}" for _ in range(28)) for _ in range(16)]
    def copy(i): return U("Repeat the following code exactly, with no changes and no commentary:\n\n" + BLOCKS[i])
    rs = [run("copy1", copy(0), 700)]; R["copy1"] = {**agg(rs), "sha": rs[0].get("sha")}; print(f"  copy1: {R['copy1']}", flush=True)
    for n in NS:
        rs = par([(lambda i=i: run(f"copy{n}_{i}", copy(i), 700)) for i in range(n)])
        R[f"copy{n}"] = {**agg(rs), "shas": hashlib.sha1("".join(sorted(str(r.get("sha")) for r in rs)).encode()).hexdigest()[:12]}; print(f"  copy{n}: {R[f'copy{n}']}", flush=True)
    rs = par([(lambda: run("mixcopy_c", copy(1), 700))] + [(lambda i=i: run(f"mixcopy_p{i}", U(f"Write a long essay about the history of {TOPICS[i]}."), 400)) for i in range(3)])
    R["mix1copy3prose"] = {"copy_tps": round(rs[0].get("decode_tps") or 0, 1), "prose_tps": [round(r.get("decode_tps") or 0, 1) for r in rs[1:]], "wall_tps": agg(rs).get("wall_tps")}
    print(f"  mix1copy3prose: {R['mix1copy3prose']}", flush=True)
# agent sessions: a shared ~6K-token system prefix (fresh each run), distinct tasks, 3 turns each with a ~1.5K-token "tool result" between turns
SYS = "You are a coding agent. Reference notes follow.\n" + words(4300)
def session(i, turns=5):
    lr = random.Random(seed * 1000 + i); lw = lambda n: " ".join(lr.choice(VOC) for _ in range(n))
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": f"Task {i}: explain {TOPICS[i % len(TOPICS)]} in about 120 words."}]
    out = []
    for t in range(turns):
        r = run(f"sess{i}_t{t}", msgs, 160); out.append(r)
        if r.get("error"): break
        msgs = msgs + [{"role": "assistant", "content": r["text"]}, {"role": "user", "content": f"Tool output {t}: " + lw(int(os.environ.get("TOOLW") or 130)) + "\nContinue with the next 120 words."}]
    return out
r0 = session(99, 1)[0]   # one session primes the shared prefix
R["prefix_prime"] = {"prompt_tokens": r0.get("prompt_tokens"), "ttft": round(r0.get("ttft") or -1, 2), "cached": r0.get("cached")}
print(f"  prefix prime: {R['prefix_prime']}", flush=True)
for n in NS:
    t0 = time.time(); ss = par([(lambda i=i: session(i + 100*n)) for i in range(n)]); wall = time.time() - t0
    flat = [r for s in ss for r in s]; ok = [r for r in flat if r.get("ttft") is not None]
    byturn = {}
    for t in range(5):
        tt = sorted(s[t]["ttft"] for s in ss if len(s) > t and s[t].get("ttft") is not None)
        if tt: byturn[f"t{t}_ttft_p50/max"] = [round(tt[len(tt)//2], 2), round(tt[-1], 2)]
    R[f"sess{n}"] = {"wall": round(wall, 1), "out_tokens": sum(r["completion_tokens"] or 0 for r in ok), "out_tps_wall": round(sum(r["completion_tokens"] or 0 for r in ok)/wall, 1),
                     "prompt_tokens_t2": ok[-1]["prompt_tokens"] if ok else None, "cached_ratio": round(sum(r["cached"] or 0 for r in ok)/max(1, sum(r["prompt_tokens"] or 0 for r in ok)), 3),
                     "errors": len(flat)-len(ok), "shas": hashlib.sha1("".join(str(r.get("sha")) for s_ in ss for r in s_).encode()).hexdigest()[:12], **byturn}
    print(f"  sess{n}: {R[f'sess{n}']}", flush=True)
json.dump(R, open(f"{TAG}.summary.json", "w"), indent=1); print(f"[{TAG}] done", flush=True)
