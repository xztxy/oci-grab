# oci-grab

> Web-based grabber for Oracle Cloud **Always Free** instances (ARM A1.Flex + AMD E2.1.Micro), with live free-tier quota validation, mixed-shape combos, auto-resume, and a one-click web UI.
>
> 甲骨文云**免费实例抢占器**(ARM A1.Flex + AMD E2.1.Micro),带 Web 界面、实时免费额度校验、多规格混合组合、容器重启自动续抢。

---

## English

Oracle's Always Free ARM (A1.Flex) capacity is frequently exhausted (`Out of host capacity`). This tool repeatedly calls the OCI `LaunchInstance` API until capacity is available, then stops. It runs as a small FastAPI app in Docker with a web dashboard.

### Features
- **Web UI**: pick a preset or build a custom combo; live validation against the free-tier limits.
- **Two free pools handled separately**: ARM A1.Flex (OCPU/RAM) and AMD E2.1.Micro (instance count), plus shared 200 GB block storage.
- **Mixed shapes & multi-instance**: e.g. 2× AMD micro + 1× ARM 4c/24G in one job.
- **Idempotent & resumable**: tracks which named instances already exist; a container restart auto-resumes the job from `data/job.json` and never double-creates.
- **Runtime limit**: run until grabbed, or for N minutes.
- **Push notifications** (PushPlus / WeChat + Telegram) on each instance grabbed, all-done, and errors/quota-exceeded; PushPlus works in mainland China without a proxy.
- Engine retries on `Out of host capacity`, backs off on `429`, and stops on `LimitExceeded`.

### Architecture
- `app.py` — FastAPI backend + background grab engine (`GrabManager`), free-tier validation, REST API (`/api/presets|validate|start|stop|status|usage|notify-test`), auto-resume.
- `static/index.html` — single-page dashboard (presets, custom builder, live quota meters, real-time log, real-account usage panel).
- `grab.sh` — standalone shell grabber (CLI alternative, single-shape).
- `Dockerfile` / `docker-compose.yml` — deployment.

### Quick start
```bash
cp .env.example .env       # fill in COMPARTMENT_ID / SUBNET_ID  (image OCID auto-resolved)
mkdir -p oci keys data
# put your OCI API config+key in ./oci  (oci/config + oci_api_key.pem)
# put the SSH public key to inject into instances in ./keys/id_rsa.pub
docker compose up -d --build
# open http://127.0.0.1:8090
```

### Configuration (`.env`)
| Key | Meaning |
|-----|---------|
| `COMPARTMENT_ID` | Tenancy/Compartment OCID |
| `SUBNET_ID` | Subnet OCID |
| `IMAGE_ID` | ARM (aarch64) image OCID — **leave empty to auto-resolve** the latest official image for your region |
| `IMAGE_ID_AMD` | AMD (x86) image OCID for E2.1.Micro — **leave empty to auto-resolve** |
| `IMAGE_OS` / `IMAGE_OS_VERSION` | optional — OS used for auto-resolve (default `Canonical Ubuntu`, latest) |
| `SSH_KEY_FILE` | in-container path to the public key (default `/keys/id_rsa.pub`) |
| `PUSHPLUS_TOKEN` | optional — PushPlus token for WeChat push (https://www.pushplus.plus, no proxy needed in China) |
| `PUSHPLUS_TOPIC` | optional — PushPlus group code for one-to-many push |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | optional — Telegram bot push |
| `NOTIFY_PROXY` | optional — proxy for **Telegram only** (usually required in mainland China), e.g. `http://192.168.1.1:7890` |

> **Notifications** fire on: 🚀 start, ✅ each instance grabbed, 🎉 all done, ⚠️ quota exceeded, ❌ errors. Configure any/both channels and test with `curl -X POST http://127.0.0.1:8090/api/notify-test`.

> 📖 **Where does each value come from?** Step-by-step guide with the exact Oracle Cloud console location for every OCID / token: **[CONFIG.md](CONFIG.md)**.

> 🤖 **Deploying with an AI agent?** Hand it the repo URL + **[AI_DEPLOY.md](AI_DEPLOY.md)** — it will collect the parameters from you and deploy automatically.

### Security
- The dashboard can create cloud resources and has **no authentication** — bind it to `127.0.0.1` or a trusted LAN only, never expose it to the internet.
- Your `oci/`, `keys/`, `.env` hold private keys/credentials — they are gitignored and must never be committed.
- On a **Pay-As-You-Go** account, anything beyond the free limits is billed; the tool caps combos at the free tier, but don't create extra resources manually.

---

## 中文

甲骨文免费 ARM(A1.Flex)经常没有容量(`Out of host capacity`)。本工具循环调用 OCI `LaunchInstance` API 直到抢到为止,以 Docker 里的小型 FastAPI 应用 + Web 面板运行。

### 功能
- **Web 界面**:选预设方案或自定义组合,实时按免费额度校验。
- **两个免费池分别处理**:ARM A1.Flex(核数/内存)与 AMD E2.1.Micro(台数),外加共享 200GB 块存储。
- **多规格混合 / 多实例**:例如一个任务同时抢 2 台 AMD 小鸡 + 1 台 ARM 4核/24G。
- **幂等可续跑**:记录已存在的实例;容器重启后从 `data/job.json` **自动恢复**,不会重复创建。
- **运行时长**:一直跑到抢满,或限时 N 分钟。
- **消息推送**(PushPlus 微信 + Telegram):每抢到一台 / 全部抢满 / 出错或配额上限时推送;PushPlus 国内直连免代理。
- 遇 `容量不足` 重试、`429` 退避、`LimitExceeded` 停止。

### 架构
- `app.py` — FastAPI 后端 + 后台抢占引擎(`GrabManager`)、额度校验、REST API、自动恢复。
- `static/index.html` — 单页面板(方案/自定义构建器/实时额度表/实时日志/账号真实占用)。
- `grab.sh` — 独立 shell 抢占脚本(命令行备选)。
- `Dockerfile` / `docker-compose.yml` — 部署。

### 快速开始
```bash
cp .env.example .env       # 填 COMPARTMENT_ID / SUBNET_ID(镜像 OCID 自动获取)
mkdir -p oci keys data
# 把 OCI API 配置和私钥放到 ./oci(oci/config + oci_api_key.pem)
# 把要注入实例的 SSH 公钥放到 ./keys/id_rsa.pub
docker compose up -d --build
# 打开 http://127.0.0.1:8090
```

### 通知配置(可选)
在 `.env` 里填以下任意一个/两个渠道,抢到/抢满/出错时会推送:

| 配置项 | 说明 |
|-----|---------|
| `PUSHPLUS_TOKEN` | PushPlus 微信推送 token(https://www.pushplus.plus,国内直连免代理)|
| `PUSHPLUS_TOPIC` | 可选,PushPlus 群组编码(一对多)|
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | Telegram 机器人推送 |
| `NOTIFY_PROXY` | 仅 Telegram 用的代理(国内一般必填),如 `http://192.168.1.1:7890` |

推送时机:🚀 启动、✅ 每抢到一台、🎉 全部抢满、⚠️ 配额上限、❌ 出错。配置后可用 `curl -X POST http://127.0.0.1:8090/api/notify-test` 测试。

> 📖 **每个参数(OCID / token)在 Oracle 控制台的确切获取位置**,见详细配置指引:**[CONFIG.md](CONFIG.md)**。

> 🤖 **想让 AI 帮你部署?** 把仓库地址 + **[AI_DEPLOY.md](AI_DEPLOY.md)** 丢给 AI,它会逐项向你索取参数并自动部署启动。

### 安全提醒
- 面板**无登录鉴权**且能创建云资源——只绑定本机或可信内网,切勿暴露公网。
- `oci/`、`keys/`、`.env` 含私钥/凭证,已被 gitignore,**切勿提交**。
- PAYG 账号超出免费额度会扣费;工具已锁定免费上限,但别在控制台手动多开。

---

*Built iteratively with an AI assistant. Personal tooling — use at your own risk.*
