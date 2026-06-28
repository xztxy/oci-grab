# AI 部署引导 / AI Deployment Runbook

> **用途**:把**本仓库地址 + 本文件**丢给任意 AI 助手(Claude / GPT / Cursor / Kiro 等),它即可按本runbook**逐项引导你填参数并自动部署启动**。
>
> 参数的**确切来源位置**见 [CONFIG.md](CONFIG.md);本文件是给 AI 执行的**操作流程**。

---

## 复制给 AI 的开场白(直接粘贴)

```
请阅读本仓库的 AI_DEPLOY.md 和 CONFIG.md,然后在我的本机用 Docker 部署 oci-grab:
1) 按 runbook 逐项向我索取需要的 OCID / API 密钥 / 可选的通知 token,并告诉我每一项在 Oracle 控制台的哪里获取;
2) 帮我落地配置文件、生成 SSH 密钥、写好 .env;
3) docker compose 构建启动,并验证 /api/presets 返回 configured=true;
4) 全程不要把任何密钥提交到 Git,不要超出免费额度,面板只绑定本机。
仓库地址:<在这里填本仓库 URL>
```

---

## 给 AI 的执行准则(AGENT RULES — 必须遵守)

1. **凡是你无法自行获取的私密值(OCID、API 私钥、PushPlus/Telegram token),必须逐项向用户索取,并附上 CONFIG.md 里的获取位置;绝不编造或猜测。**
2. **绝不把 `.env` / `oci/` / `keys/` / 任何密钥提交到 Git 或打印到公开输出**(它们已在 `.gitignore`)。
3. **不要超免费额度**:ARM `A1.Flex` 累计 ≤ 4 OCPU / 24 GB(新政约 2核/12G 持续);AMD `E2.1.Micro` ≤ 2 台;块存储 ≤ 200 GB。
4. **面板无登录鉴权且能创建云资源**:只绑定 `127.0.0.1` / 可信内网,**切勿暴露公网**。
5. 每步执行后**验证结果**再进行下一步;失败先排查(见末尾)。

---

## 步骤

### 0. 前置检查
```bash
docker --version && docker compose version   # 需 Docker + compose
```
- 用户需有一个 Oracle Cloud 账号。若缺 Docker,先指引安装。

### 1. 获取代码
```bash
git clone <仓库URL> oci-grab && cd oci-grab
cp .env.example .env
mkdir -p oci keys data
```

### 2. 向用户索取并落地 OCI API 凭证(参见 CONFIG.md 第1步)
向用户索取「配置文件预览」内容与下载的私钥,然后:
- 写入 `oci/config`(把最后一行改成容器内路径):
  ```ini
  [DEFAULT]
  user=<用户提供>
  fingerprint=<用户提供>
  tenancy=<用户提供>
  region=<用户提供，如 ap-tokyo-1>
  key_file=/root/.oci/oci_api_key.pem
  ```
- 把私钥写入 `oci/oci_api_key.pem`,并 `chmod 600 oci/oci_api_key.pem`。

### 3. 生成 SSH 密钥(若用户没有现成公钥)
```bash
ssh-keygen -t rsa -b 4096 -f keys/id_rsa -N ""
```

### 4. 向用户索取**两个** OCID 并写入 `.env`(每项附 CONFIG.md 位置)
- `COMPARTMENT_ID`(=tenancy 或子区间 OCID)
- `SUBNET_ID`(**公有**子网 OCID)

> **镜像无需索取**:`IMAGE_ID` / `IMAGE_ID_AMD` 留空即可,程序运行时会按区域+架构自动获取最新官方镜像。除非用户明确要锁定某镜像,否则保持留空。

写入示例(逐项替换,**禁止保留 xxxx 占位**):
```bash
sed -i "s|^COMPARTMENT_ID=.*|COMPARTMENT_ID=<值>|" .env
sed -i "s|^SUBNET_ID=.*|SUBNET_ID=<值>|" .env
# IMAGE_ID / IMAGE_ID_AMD 保持留空(自动获取)
```

### 5.(可选)通知:向用户索取 token 后写入 `.env`
- `PUSHPLUS_TOKEN`(微信,国内直连);`TG_BOT_TOKEN`+`TG_CHAT_ID`(+ 国内 `NOTIFY_PROXY` 代理)。来源见 CONFIG.md 第5步。

### 6. 部署
```bash
docker compose up -d --build
```

### 7. 验证(全部通过才算成功)
```bash
# a) 配置已就绪(应含 "configured": true 且 region 正确)
curl -s http://127.0.0.1:8090/api/presets

# b) 容器健康
docker compose ps

# c)(配了通知才测)
curl -s -X POST http://127.0.0.1:8090/api/notify-test
```
- 打开 `http://127.0.0.1:8090`,选预设方案 → 开始抢占。

---

## 校验清单(AI 自检)
- [ ] `oci/config` 存在,`key_file=/root/.oci/oci_api_key.pem`
- [ ] `oci/oci_api_key.pem` 存在且权限 600
- [ ] `keys/id_rsa.pub` 存在
- [ ] `.env` 的 `COMPARTMENT_ID` / `SUBNET_ID` 已填、**无 `xxxx` 占位**(`IMAGE_ID` 留空=自动获取)
- [ ] `/api/presets` 返回 `configured: true`,`region` 与你的区域一致
- [ ] `docker compose ps` 显示容器 Up
- [ ] 未提交任何密钥;面板仅绑定 127.0.0.1

---

## 常见故障排查
| 现象 | 原因 / 处理 |
|---|---|
| `/api/presets` 的 `configured` 为 false | `.env` 的 OCID 没填全,或 `oci/config` 缺失 |
| 日志报凭证/认证错误 | 检查 `oci/config`(user/tenancy/fingerprint/region)与 `oci_api_key.pem` 是否匹配、key_file 路径 |
| `Out of host capacity` | **正常**,ARM 容量不足,引擎会自动换可用域并重试,耐心等 |
| `LimitExceeded` / `QuotaExceeded` | 已达免费额度上限,引擎会停止;别再手动多开 |
| 创建失败且报架构错误 | `IMAGE_ID` 必须 aarch64、`IMAGE_ID_AMD` 必须 x86_64,且与区域匹配 |
| 抢到但连不上实例 | 用 `ssh -i keys/id_rsa ubuntu@<公网IP>`(Ubuntu 镜像);确认子网安全列表放行 22 |
