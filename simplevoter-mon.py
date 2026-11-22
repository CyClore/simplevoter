#!/usr/bin/env python3
import configparser, json, os, pty, re, socket, subprocess, threading, time
from datetime import datetime

HUB_NODE = "2340"
VOTER_NODES = ["2340", "5101", "5102"]
OUT_FILE = "/var/www/html/simplevoter/status.json"
ALLMON_INI = "/etc/allmon3/allmon3.ini"

state = {
    "winner": None,
    "nodes": {n: {"rssi": 0, "state": "IDLE", "connected": (n == HUB_NODE), "ts": 0} for n in VOTER_NODES}
}
lock = threading.Lock()

def get_ami_creds():
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(ALLMON_INI)
    sec = HUB_NODE if HUB_NODE in cfg else cfg.sections()[0]
    host = cfg.get(sec, "host", fallback="127.0.0.1").split(":")[0]
    port = int(cfg.get(sec, "port", fallback="5038"))
    user = cfg.get(sec, "user", fallback="admin")
    passwd = cfg.get(sec, "pass", fallback="")
    return host, port, user, passwd

def write_status():
    with lock:
        now = time.time()
        for n, info in state["nodes"].items():
            if info["rssi"] > 0 and (now - info["ts"]) > 1.2:
                info["rssi"] = 0
                info["state"] = "IDLE"
        active = [n for n, v in state["nodes"].items() if v["rssi"] > 0]
        if not active:
            state["winner"] = None
        elif state["winner"] not in active:
            state["winner"] = max(active, key=lambda n: state["nodes"][n]["rssi"])
        payload = {
            "winner": state["winner"],
            "nodes": state["nodes"],
            "updated": datetime.now().strftime("%H:%M:%S")
        }
        tmp = OUT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, OUT_FILE)

def cli_reader():
    for cmd in [
        "core set debug 8",
        "core set debug 4 chan_usbradio.so",
        "core set debug 4 chan_usbradio",
        "core set debug 4 app_rpt.so",
        "core set debug 4 app_rpt",
        "logger set level DEBUG on"
    ]:
        subprocess.run(["asterisk", "-rx", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    re_ansi = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
    re_local = re.compile(r"Channel\s+(\d+):\s+Count_rssi_update\s+(\d+)")
    re_remote = re.compile(r"(?:IAX2/|Node\s+)(\d+).*?RSSI[=:\s]+(\d+)", re.IGNORECASE)
    re_winner = re.compile(r"(?:voted|winner|active\s+rx)[^\d]*(\d{4,6})", re.IGNORECASE)

    while True:
        try:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                ["asterisk", "-r"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True
            )
            os.close(slave_fd)
            buf = ""
            while True:
                data = os.read(master_fd, 4096).decode(errors="ignore")
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    raw_line, buf = buf.split("\n", 1)
                    line = re_ansi.sub("", raw_line).strip()
                    now = time.time()
                    m1 = re_local.search(line)
                    if m1 and m1.group(1) in state["nodes"]:
                        nid, val = m1.group(1), int(m1.group(2))
                        with lock:
                            state["nodes"][nid]["rssi"] = val
                            state["nodes"][nid]["state"] = "RX" if val > 0 else "IDLE"
                            state["nodes"][nid]["ts"] = now
                        continue
                    m2 = re_remote.search(line)
                    if m2 and m2.group(1) in state["nodes"]:
                        nid, val = m2.group(1), int(m2.group(2))
                        with lock:
                            state["nodes"][nid]["rssi"] = val
                            state["nodes"][nid]["state"] = "RX" if val > 0 else "IDLE"
                            state["nodes"][nid]["ts"] = now
                        continue
                    m3 = re_winner.search(line)
                    if m3 and m3.group(1) in state["nodes"]:
                        with lock:
                            state["winner"] = m3.group(1)
            os.close(master_fd)
            proc.wait()
        except Exception:
            time.sleep(2)

def ami_poller():
    host, port, user, passwd = get_ami_creds()
    while True:
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.settimeout(0.5)
            s.sendall(f"Action: Login\r\nUsername: {user}\r\nSecret: {passwd}\r\n\r\n".encode())
            buf = ""
            last_poll = 0
            while True:
                now = time.time()
                if now - last_poll >= 1.0:
                    s.sendall(f"Action: RptStatus\r\nCOMMAND: XStat\r\nNODE: {HUB_NODE}\r\n\r\n".encode())
                    last_poll = now
                try:
                    chunk = s.recv(4096).decode(errors="ignore")
                    if not chunk:
                        raise ConnectionError()
                    buf += chunk
                except socket.timeout:
                    pass
                while "\r\n\r\n" in buf:
                    pkt, buf = buf.split("\r\n\r\n", 1)
                    if "Conn:" in pkt:
                        linked = {m.group(1) for m in re.finditer(r"Conn:\s*(\d+)\s+", pkt)}
                        with lock:
                            for n in VOTER_NODES:
                                if n != HUB_NODE:
                                    state["nodes"][n]["connected"] = (n in linked)
                    m_vote = re.search(r"VotedNode:\s*(\d+)", pkt)
                    if m_vote and m_vote.group(1) in state["nodes"]:
                        with lock:
                            state["winner"] = m_vote.group(1)
                    if "RSSI=" in pkt:
                        m_node = re.search(r"Channel:\s*IAX2/(\d+)", pkt)
                        m_rssi = re.search(r"RSSI=(\d+)", pkt)
                        if m_node and m_rssi and m_node.group(1) in state["nodes"]:
                            nid, val = m_node.group(1), int(m_rssi.group(1))
                            with lock:
                                state["nodes"][nid]["rssi"] = val
                                state["nodes"][nid]["state"] = "RX" if val > 0 else "IDLE"
                                state["nodes"][nid]["ts"] = now
        except Exception:
            time.sleep(2)

if __name__ == "__main__":
    threading.Thread(target=cli_reader, daemon=True).start()
    threading.Thread(target=ami_poller, daemon=True).start()
    while True:
        write_status()
        time.sleep(0.25)
