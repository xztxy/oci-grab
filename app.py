#!/usr/bin/env python3
"""
OCI 免费 ARM 实例抢占 - Web 后端 (FastAPI)
- 可视化配置 + 自定义组合(不超免费额度)+ 3 种默认方案 + 自定义运行时长
- 抢占引擎在后台线程运行,shell 调用已验证的 oci CLI
"""
import os
import json
import time
import threading
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime
from collections import deque
from typing import List, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------- 免费额度上限(两个独立池 + 共享存储) ----------------
# 注:2025年甲骨文将 A1 免费额度降为 1500 OCPU时+9000 GB时/月 ≈ 持续 2核/12G。
FREE_LIMITS = {
    "arm_ocpus": 2,      # ARM A1.Flex 总核数(新政策:持续运行约 2 核内免费)
    "arm_memory_gb": 12, # ARM A1.Flex 总内存(约 12GB 内免费)
    "micro_count": 2,    # AMD E2.1.Micro 最多 2 台(每台固定 1核/1GB)
    "boot_gb": 200,      # 所有实例共享的块存储总额
}
MIN_BOOT_GB = 50          # 单台启动盘最小 50GB

# 规格注册表
SHAPES = {
    "arm":   {"shape": "VM.Standard.A1.Flex",   "flex": True,  "label": "ARM A1.Flex",
              "fixed_ocpus": None, "fixed_mem": None},
    "micro": {"shape": "VM.Standard.E2.1.Micro", "flex": False, "label": "AMD E2.1.Micro",
              "fixed_ocpus": 1,    "fixed_mem": 1},
}

# ---------------- 默认方案(均在 2核/12G ARM + 2 AMD + 200G 存储 内)----------------
PRESETS = [
    {"id": "A", "name": "单台 ARM", "desc": "1 台 ARM 2核/12G",
     "items": [{"shape": "arm", "ocpus": 2, "memory_gb": 12, "boot_gb": 150, "count": 1}]},
    {"id": "B", "name": "双 ARM(更易抢)", "desc": "2 台 ARM 1核/6G",
     "items": [{"shape": "arm", "ocpus": 1, "memory_gb": 6, "boot_gb": 75, "count": 2}]},
    {"id": "C", "name": "全家桶", "desc": "2 台 AMD 1核/1G + 1 台 ARM 2核/12G",
     "items": [{"shape": "micro", "ocpus": 1, "memory_gb": 1, "boot_gb": 50, "count": 2},
               {"shape": "arm", "ocpus": 2, "memory_gb": 12, "boot_gb": 100, "count": 1}]},
    {"id": "D", "name": "双 AMD + 双 ARM", "desc": "2 台 AMD 1核/1G + 2 台 ARM 1核/6G",
     "items": [{"shape": "micro", "ocpus": 1, "memory_gb": 1, "boot_gb": 50, "count": 2},
               {"shape": "arm", "ocpus": 1, "memory_gb": 6, "boot_gb": 50, "count": 2}]},
]

COMPARTMENT_ID = os.environ.get("COMPARTMENT_ID", "")
SUBNET_ID = os.environ.get("SUBNET_ID", "")
IMAGE_ID = os.environ.get("IMAGE_ID", "").strip()            # ARM aarch64;留空=自动获取
IMAGE_ID_AMD = os.environ.get("IMAGE_ID_AMD", "").strip()    # AMD x86;留空=自动获取
# 镜像自动获取(IMAGE_ID/IMAGE_ID_AMD 留空时):按区域+形状架构取最新官方镜像
IMAGE_OS = (os.environ.get("IMAGE_OS", "").strip() or "Canonical Ubuntu")
IMAGE_OS_VERSION = os.environ.get("IMAGE_OS_VERSION", "").strip()   # 可选,如 22.04
SSH_KEY_FILE = os.environ.get("SSH_KEY_FILE", "/keys/id_rsa.pub")
OCI_CONFIG = "/root/.oci/config"
STATE_FILE = "/data/job.json"   # 任务持久化(挂载的可写卷),容器重启后自动恢复

# ---------------- 通知配置(PushPlus 微信 + Telegram) ----------------
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")
PUSHPLUS_TOPIC = os.environ.get("PUSHPLUS_TOPIC", "")   # 可选:群组编码(一对多推送)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
# 可选:仅给 Telegram 用的代理(国内连 api.telegram.org 一般需要),如 http://192.168.15.1:7890
NOTIFY_PROXY = os.environ.get("NOTIFY_PROXY", "")


def notify_enabled() -> bool:
    return bool(PUSHPLUS_TOKEN or (TG_BOT_TOKEN and TG_CHAT_ID))


def send_notification(title: str, content: str = ""):
    """同步发送到已配置的渠道,返回 [(渠道, 是否成功, 备注)]。best-effort,不抛异常。"""
    content = content or title
    results = []

    # --- PushPlus(微信)---  国内直连,显式不走代理
    if PUSHPLUS_TOKEN:
        try:
            payload = {"token": PUSHPLUS_TOKEN, "title": title,
                       "content": content, "template": "txt"}
            if PUSHPLUS_TOPIC:
                payload["topic"] = PUSHPLUS_TOPIC
            data = json.dumps(payload).encode("utf-8")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(
                "https://www.pushplus.plus/send", data=data,
                headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", "ignore")
            ok = '"code":200' in body.replace(" ", "")
            results.append(("PushPlus", ok, "" if ok else body[:160]))
        except Exception as e:
            results.append(("PushPlus", False, str(e)[:160]))

    # --- Telegram ---  国内多半要走代理(NOTIFY_PROXY)
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            data = urllib.parse.urlencode(
                {"chat_id": TG_CHAT_ID, "text": f"{title}\n{content}"}).encode("utf-8")
            proxies = {"http": NOTIFY_PROXY, "https": NOTIFY_PROXY} if NOTIFY_PROXY else {}
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", data=data)
            with opener.open(req, timeout=15) as resp:
                resp.read()
            results.append(("Telegram", True, ""))
        except Exception as e:
            results.append(("Telegram", False, str(e)[:160]))

    return results

app = FastAPI(title="OCI Free ARM Grabber")


# ======================================================================
#  抢占引擎
# ======================================================================
class GrabManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self.logs = deque(maxlen=500)
        self.state = "idle"          # idle | running | done | stopped | error
        self.items: List[Dict] = []  # 原始组合(用于持久化恢复)
        self.plan: List[Dict] = []   # 展开后的目标实例列表
        self.created: List[Dict] = []
        self.started_at: Optional[float] = None
        self.deadline: Optional[float] = None   # None = 一直跑到抢满
        self.ads: List[str] = []
        self.region = ""
        self.images: Dict[str, str] = {}   # shape_key -> 解析出的镜像 OCID(缓存)

    # ---------- 日志 ----------
    def log(self, msg: str):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self.logs.append(line)
        print(line, flush=True)

    # ---------- 通知(后台线程发送,不阻塞抢占循环) ----------
    def notify(self, title: str, content: str = ""):
        if not notify_enabled():
            return

        def _worker():
            for ch, ok, info in send_notification(title, content):
                self.log(f"📨 通知 {ch}: {'已发送' if ok else '失败 ' + info}")

        threading.Thread(target=_worker, daemon=True).start()

    # ---------- 任务持久化(容器重启后自动恢复) ----------
    def _save_job(self):
        try:
            os.makedirs("/data", exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump({"items": self.items, "plan": self.plan,
                           "deadline": self.deadline, "started_at": self.started_at}, f)
        except Exception as e:
            self.log(f"保存任务失败: {e}")

    def _clear_job(self):
        try:
            os.remove(STATE_FILE)
        except Exception:
            pass

    def resume_if_needed(self):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
        except Exception:
            return
        if data.get("deadline") and time.time() >= data["deadline"]:
            self._clear_job()
            return
        if not data.get("plan"):
            return
        self.items = data.get("items", [])
        self.plan = data["plan"]
        self.deadline = data.get("deadline")
        self.started_at = data.get("started_at") or time.time()
        self.stop_flag.clear()
        self.state = "running"
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # ---------- 读取区域 ----------
    def detect_region(self) -> str:
        try:
            with open(OCI_CONFIG) as f:
                for ln in f:
                    if ln.strip().startswith("region"):
                        return ln.split("=", 1)[1].strip()
        except Exception:
            pass
        return ""

    # ---------- 读取可用域 ----------
    def detect_ads(self) -> List[str]:
        try:
            out = subprocess.run(
                ["oci", "iam", "availability-domain", "list",
                 "--compartment-id", COMPARTMENT_ID,
                 "--query", "data[].name", "--output", "json"],
                capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
            )
            data = json.loads(out.stdout or "[]")
            return [x for x in data if isinstance(x, str)]
        except Exception as e:
            self.log(f"获取可用域失败: {e}")
            return []

    # ---------- 镜像自动解析(留空时按区域+架构取最新官方镜像) ----------
    def _resolve_image(self, shape_key: str) -> str:
        if self.images.get(shape_key):
            return self.images[shape_key]
        # 1) .env 显式指定优先(非空且非 "auto")
        env_override = IMAGE_ID if shape_key == "arm" else IMAGE_ID_AMD
        if env_override and env_override.lower() != "auto":
            self.images[shape_key] = env_override
            return env_override
        # 2) 自动:按形状(决定架构)查最新官方镜像
        sp = SHAPES[shape_key]
        args = ["oci", "compute", "image", "list",
                "--compartment-id", COMPARTMENT_ID,
                "--shape", sp["shape"],
                "--operating-system", IMAGE_OS,
                "--sort-by", "TIMECREATED", "--sort-order", "DESC",
                "--output", "json"]
        if IMAGE_OS_VERSION:
            args += ["--operating-system-version", IMAGE_OS_VERSION]
        try:
            out = subprocess.run(args, capture_output=True, text=True,
                                 timeout=60, stdin=subprocess.DEVNULL)
            data = (json.loads(out.stdout or "{}") or {}).get("data", []) or []
            if data:
                img = data[0]
                self.images[shape_key] = img.get("id", "")
                self.log(f"  🧩 自动选用镜像[{sp['label']}]: {img.get('display-name')}")
                return self.images[shape_key]
            self.log(f"  ⚠️ 未找到 {IMAGE_OS} 适配 {sp['shape']} 的镜像")
        except Exception as e:
            self.log(f"  自动获取镜像失败[{shape_key}]: {e}")
        return ""

    # ---------- 已存在(未终止)实例名 ----------
    def existing_names(self) -> List[str]:
        try:
            out = subprocess.run(
                ["oci", "compute", "instance", "list",
                 "--compartment-id", COMPARTMENT_ID, "--output", "json"],
                capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
            )
            data = (json.loads(out.stdout or "{}") or {}).get("data", []) or []
            names = []
            for x in data:
                st = (x.get("lifecycle-state") or "").upper()
                if st not in ("TERMINATED", "TERMINATING"):
                    names.append(x.get("display-name", ""))
            return names
        except Exception:
            return []

    # ---------- 启动 ----------
    def start(self, items: List[Dict], duration_minutes: Optional[int]) -> Dict:
        with self.lock:
            if self.state == "running":
                return {"ok": False, "error": "已在运行中"}

            ok, err = validate_items(items)
            if not ok:
                return {"ok": False, "error": err}

            # 展开成具体目标实例
            plan = []
            counters = {"arm": 0, "micro": 0}
            for it in items:
                shape = it.get("shape", "arm")
                sp = SHAPES[shape]
                ocpus = sp["fixed_ocpus"] if not sp["flex"] else int(it["ocpus"])
                mem = sp["fixed_mem"] if not sp["flex"] else int(it["memory_gb"])
                for _ in range(int(it["count"])):
                    counters[shape] += 1
                    plan.append({
                        "name": f"free-{shape}-{counters[shape]}",
                        "shape": shape,
                        "shape_name": sp["shape"],
                        "ocpus": ocpus,
                        "memory_gb": mem,
                        "boot_gb": int(it["boot_gb"]),
                        "status": "pending",
                    })
            self.plan = plan
            self.created = []
            self.items = items
            self.logs.clear()
            self.stop_flag.clear()
            self.state = "running"
            self.started_at = time.time()
            self.deadline = (time.time() + duration_minutes * 60) if duration_minutes else None
            self._save_job()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return {"ok": True}

    def stop(self):
        self.stop_flag.set()
        self.log("收到停止指令,正在停止...")
        return {"ok": True}

    # ---------- 主循环 ----------
    def _run(self):
        try:
            self.region = self.detect_region()
            self.ads = self.detect_ads()
            if not self.ads:
                self.state = "error"
                self.log("❌ 无法获取可用域,请检查凭证配置。")
                self.notify("❌ OCI 抢占出错", "无法获取可用域,请检查凭证配置。")
                self._clear_job()
                return
            self.log(f"区域 {self.region} | 可用域 {', '.join(self.ads)}")
            self.log(f"目标 {len(self.plan)} 台: " +
                     ", ".join(f"{p['name']}({p['ocpus']}核/{p['memory_gb']}GB)" for p in self.plan))
            if self.deadline:
                self.log(f"运行时长上限: {int((self.deadline - time.time())/60)} 分钟")
            else:
                self.log("运行时长: 一直跑到抢满")

            # 预解析镜像:IMAGE_ID/IMAGE_ID_AMD 留空时按区域+架构自动取最新官方镜像
            for sk in {p["shape"] for p in self.plan}:
                if not self._resolve_image(sk):
                    self.state = "error"
                    self.log(f"❌ 无法确定 {SHAPES[sk]['label']} 镜像。可在 .env 手动指定 IMAGE_ID/IMAGE_ID_AMD,或检查 IMAGE_OS/凭证。")
                    self.notify("❌ OCI 抢占出错", f"无法自动获取 {SHAPES[sk]['label']} 镜像")
                    self._clear_job()
                    return

            self.notify("🚀 OCI 抢占启动",
                        f"区域 {self.region}\n目标 {len(self.plan)} 台: " +
                        ", ".join(f"{p['name']}({p['ocpus']}核/{p['memory_gb']}GB)" for p in self.plan))

            rnd = 0
            while not self.stop_flag.is_set():
                rnd += 1
                exist = set(self.existing_names())
                todo = [p for p in self.plan if p["name"] not in exist]
                for p in self.plan:
                    if p["name"] in exist:
                        p["status"] = "created"
                        if not any(c.get("name") == p["name"] for c in self.created):
                            self.created.append({"name": p["name"], "id": "", "ad": ""})
                if not todo:
                    self.state = "done"
                    self.log(f"🎉 已抢满 {len(self.plan)} 台,全部完成!")
                    self.notify("🎉 OCI 全部抢满",
                                f"已抢满 {len(self.plan)} 台: " +
                                ", ".join(p["name"] for p in self.plan))
                    self._clear_job()
                    return

                self.log(f"第 {rnd} 轮 | 已有 {len(self.plan)-len(todo)}/{len(self.plan)} 台,"
                         f"待抢 {', '.join(p['name'] for p in todo)}")

                for p in todo:
                    if self._expired() or self.stop_flag.is_set():
                        break
                    for ad in self.ads:
                        if self._expired() or self.stop_flag.is_set():
                            break
                        p["status"] = "trying"
                        self.log(f"  [{p['name']}] 尝试创建于 {ad} ...(约1-2分钟)")
                        rc, out = self._launch(p, ad)
                        if rc == 0:
                            p["status"] = "created"
                            iid = ""
                            try:
                                iid = json.loads(out)["data"]["id"]
                            except Exception:
                                pass
                            self.created.append({"name": p["name"], "id": iid, "ad": ad})
                            self.log(f"  ✅ [{p['name']}] 抢占成功!{ad}")
                            self.notify(f"✅ 抢占成功 {p['name']}",
                                        f"{p['ocpus']}核/{p['memory_gb']}GB @ {ad}\n"
                                        f"区域 {self.region}")
                            break
                        if "Out of host capacity" in out:
                            self.log(f"  → [{p['name']}] {ad} 容量不足")
                        elif "LimitExceeded" in out or "QuotaExceeded" in out:
                            self.state = "error"
                            self.log("  → ⚠️ 已达配额上限,停止。")
                            self.notify("⚠️ OCI 抢占停止",
                                        "已达配额上限(LimitExceeded / QuotaExceeded),已停止。")
                            self._clear_job()
                            return
                        elif "TooManyRequests" in out or "429" in out:
                            self.log("  → 被限流(429),延长等待")
                            self._sleep(120)
                        else:
                            msg = out.strip().replace("\n", " ")[:200]
                            self.log(f"  → [{p['name']}] {ad} 其他错误: {msg}")

                if self._expired():
                    self.state = "stopped"
                    self.log("⏱ 已达运行时长上限,停止。")
                    self._clear_job()
                    return
                if self.stop_flag.is_set():
                    break
                self.log("本轮结束,等待 60s 后重试...")
                self._sleep(60)

            self.state = "stopped"
            self.log("已停止。")
            self._clear_job()
        except Exception as e:
            self.state = "error"
            self.log(f"引擎异常: {e}")
            self.notify("❌ OCI 抢占引擎异常", str(e)[:300])
            self._clear_job()

    def _expired(self) -> bool:
        return self.deadline is not None and time.time() >= self.deadline

    def _sleep(self, secs: int):
        # 可被停止/超时打断的 sleep
        end = time.time() + secs
        while time.time() < end:
            if self.stop_flag.is_set() or self._expired():
                return
            time.sleep(1)

    def _launch(self, p: Dict, ad: str):
        shape = p.get("shape", "arm")
        sp = SHAPES[shape]
        image = self._resolve_image(shape)
        if not image:
            return 1, f"无可用镜像({sp['label']});请检查 IMAGE_OS 或在 .env 指定 IMAGE_ID"
        cmd = [
            "oci", "compute", "instance", "launch",
            "--availability-domain", ad,
            "--compartment-id", COMPARTMENT_ID,
            "--shape", sp["shape"],
            "--image-id", image,
            "--subnet-id", SUBNET_ID,
            "--assign-public-ip", "true",
            "--display-name", p["name"],
            "--boot-volume-size-in-gbs", str(p["boot_gb"]),
            "--ssh-authorized-keys-file", SSH_KEY_FILE,
        ]
        # 仅 flex 规格(A1.Flex)需要 shape-config;E2.1.Micro 是固定规格
        if sp["flex"]:
            cmd += ["--shape-config",
                    json.dumps({"ocpus": p["ocpus"], "memoryInGBs": p["memory_gb"]})]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=300, stdin=subprocess.DEVNULL)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return 1, "launch 调用超时"
        except Exception as e:
            return 1, str(e)

    def status(self) -> Dict:
        remaining = None
        if self.deadline:
            remaining = max(0, int(self.deadline - time.time()))
        return {
            "state": self.state,
            "region": self.region,
            "ads": self.ads,
            "items": self.items,
            "plan": self.plan,
            "created": self.created,
            "remaining_seconds": remaining,
            "duration_minutes": int((self.deadline - self.started_at) / 60) if (self.deadline and self.started_at) else None,
            "logs": list(self.logs),
        }


MANAGER = GrabManager()
# 容器(重)启动时,若存在未完成的任务则自动恢复抢占
MANAGER.resume_if_needed()


# ======================================================================
#  额度校验
# ======================================================================
def validate_items(items: List[Dict]):
    if not items:
        return False, "组合为空"
    arm_o = arm_m = 0
    micro_c = 0
    tot_b = 0
    for it in items:
        shape = it.get("shape", "arm")
        if shape not in SHAPES:
            return False, f"未知规格 {shape}"
        try:
            b, c = int(it["boot_gb"]), int(it["count"])
        except Exception:
            return False, "参数格式错误"
        if c < 1:
            return False, "台数必须 ≥ 1"
        if b < MIN_BOOT_GB:
            return False, f"单台启动盘不能小于 {MIN_BOOT_GB}GB"
        if shape == "micro":
            micro_c += c
            tot_b += b * c
        else:  # arm flex
            try:
                o, m = int(it["ocpus"]), int(it["memory_gb"])
            except Exception:
                return False, "参数格式错误"
            if o < 1 or m < 1:
                return False, "核数/内存必须 ≥ 1"
            arm_o += o * c
            arm_m += m * c
            tot_b += b * c
    if arm_o > FREE_LIMITS["arm_ocpus"]:
        return False, f"ARM 总核数 {arm_o} 超过免费额度 {FREE_LIMITS['arm_ocpus']}"
    if arm_m > FREE_LIMITS["arm_memory_gb"]:
        return False, f"ARM 总内存 {arm_m}GB 超过免费额度 {FREE_LIMITS['arm_memory_gb']}GB"
    if micro_c > FREE_LIMITS["micro_count"]:
        return False, f"AMD E2.1.Micro {micro_c} 台超过免费上限 {FREE_LIMITS['micro_count']} 台"
    if tot_b > FREE_LIMITS["boot_gb"]:
        return False, f"总存储 {tot_b}GB 超过免费额度 {FREE_LIMITS['boot_gb']}GB"
    return True, ""


def query_actual_usage():
    """查询 OCI 当前真实占用,区分本工具管理(free-arm-/free-micro-)与外部(orphan)实例"""
    r = {"arm_ocpus": 0, "arm_memory_gb": 0, "micro_count": 0, "boot_gb": 0,
         "orphan_arm_ocpus": 0, "orphan_arm_memory_gb": 0, "orphan_micro_count": 0,
         "orphan": False, "instances": [], "error": ""}
    try:
        out = subprocess.run(
            ["oci", "compute", "instance", "list", "--compartment-id", COMPARTMENT_ID, "--output", "json"],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        data = (json.loads(out.stdout or "{}") or {}).get("data", []) or []
    except Exception as e:
        r["error"] = str(e)
        return r
    for x in data:
        st = (x.get("lifecycle-state") or "").upper()
        if st in ("TERMINATED", "TERMINATING"):
            continue
        name = x.get("display-name", "")
        shp = x.get("shape", "")
        sc = x.get("shape-config") or {}
        o = sc.get("ocpus") or 0
        m = sc.get("memory-in-gbs") or 0
        managed = name.startswith("free-arm-") or name.startswith("free-micro-")
        is_arm = "A1.Flex" in shp
        is_micro = "e2.1.micro" in shp.lower()
        if is_arm:
            r["arm_ocpus"] += o
            r["arm_memory_gb"] += m
            if not managed:
                r["orphan_arm_ocpus"] += o
                r["orphan_arm_memory_gb"] += m
                r["orphan"] = True
        elif is_micro:
            r["micro_count"] += 1
            if not managed:
                r["orphan_micro_count"] += 1
                r["orphan"] = True
        elif not managed:
            r["orphan"] = True
        r["instances"].append({"name": name, "shape": shp, "ocpus": o,
                               "memory_gb": m, "state": st, "managed": managed})
    try:
        out = subprocess.run(
            ["oci", "bv", "boot-volume", "list", "--compartment-id", COMPARTMENT_ID, "--output", "json"],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        for v in (json.loads(out.stdout or "{}") or {}).get("data", []) or []:
            st = (v.get("lifecycle-state") or "").upper()
            if st not in ("TERMINATED", "TERMINATING"):
                r["boot_gb"] += int(v.get("size-in-gbs") or 0)
    except Exception:
        pass
    return r


# ======================================================================
#  API
# ======================================================================
class StartReq(BaseModel):
    items: List[Dict]
    duration_minutes: Optional[int] = None


@app.get("/api/presets")
def get_presets():
    return {
        "presets": PRESETS,
        "limits": FREE_LIMITS,
        "min_boot_gb": MIN_BOOT_GB,
        "shapes": {k: {"label": v["label"], "flex": v["flex"],
                       "fixed_ocpus": v["fixed_ocpus"], "fixed_mem": v["fixed_mem"]}
                   for k, v in SHAPES.items()},
        "region": MANAGER.detect_region(),
        "configured": all([COMPARTMENT_ID, SUBNET_ID]),
        "amd_ready": True,
        "image_auto": not (IMAGE_ID and IMAGE_ID_AMD),
        "notify": {
            "enabled": notify_enabled(),
            "pushplus": bool(PUSHPLUS_TOKEN),
            "telegram": bool(TG_BOT_TOKEN and TG_CHAT_ID),
        },
    }


@app.post("/api/notify-test")
def api_notify_test():
    """发送一条测试通知,用于验证 PushPlus / Telegram 配置是否生效。"""
    if not notify_enabled():
        return JSONResponse(
            {"ok": False, "error": "未配置任何通知渠道(需 PUSHPLUS_TOKEN,或 TG_BOT_TOKEN + TG_CHAT_ID)"},
            status_code=400)
    results = send_notification("🔔 OCI 抢占器测试通知",
                                "收到这条消息说明通知配置成功 ✅")
    return {"ok": all(ok for _, ok, _ in results) if results else False,
            "results": [{"channel": c, "ok": o, "info": i} for c, o, i in results]}


@app.post("/api/validate")
def api_validate(req: StartReq):
    ok, err = validate_items(req.items)
    return {"ok": ok, "error": err}


@app.post("/api/start")
def api_start(req: StartReq):
    res = MANAGER.start(req.items, req.duration_minutes)
    code = 200 if res.get("ok") else 400
    return JSONResponse(res, status_code=code)


@app.post("/api/stop")
def api_stop():
    return MANAGER.stop()


@app.get("/api/status")
def api_status():
    return MANAGER.status()


@app.get("/api/usage")
def api_usage():
    return query_actual_usage()


app.mount("/", StaticFiles(directory="static", html=True), name="static")
