# 配置指引 / Configuration Guide

本指引说明每一个参数**从哪里获取**(Oracle Cloud 控制台的确切位置),按顺序做完即可运行。

> 控制台语言以中文为例,括号内为英文菜单名。OCID 形如 `ocid1.xxx.oc1..aaaa....`,**全部是你自己账号的值,切勿外泄、切勿提交到 Git**(`.env`、`oci/`、`keys/` 已被 `.gitignore` 排除)。

---

## 0. 目录结构(运行前准备)

```
oci-grab/
├─ .env                # 复制 .env.example 填写(见下)
├─ oci/
│  ├─ config           # OCI API 配置(见 第1步)
│  └─ oci_api_key.pem  # OCI API 私钥(见 第1步)
└─ keys/
   ├─ id_rsa           # SSH 私钥(登录抢到的实例用)
   └─ id_rsa.pub       # SSH 公钥(注入实例)
```

```bash
cp .env.example .env
mkdir -p oci keys data
```

---

## 1. OCI API 凭证 → `oci/config` + `oci/oci_api_key.pem`

工具用 OCI CLI 调 API,需要一份 API 密钥。

1. 登录控制台,点**右上角头像** →「**我的配置文件**」(My profile)。
2. 左下「资源」里点「**API 密钥**」(API keys) →「**添加 API 密钥**」(Add API key)。
3. 选「**生成 API 密钥对**」→ 点「**下载私钥**」(保存好)→「**添加**」。
4. 弹出「**配置文件预览**」(Configuration file preview),内容形如:
   ```ini
   [DEFAULT]
   user=ocid1.user.oc1..aaaaaaaa....
   fingerprint=12:34:56:....
   tenancy=ocid1.tenancy.oc1..aaaaaaaa....
   region=ap-tokyo-1
   key_file=<path to your private keyfile>
   ```
5. 把这段**原样保存为 `oci/config`**,只把最后一行改成容器内路径:
   ```ini
   key_file=/root/.oci/oci_api_key.pem
   ```
6. 把第 3 步下载的私钥**保存为 `oci/oci_api_key.pem`**。

> 这一步同时给了你后面要用的 **tenancy OCID** 和 **region**。

---

## 2. `.env` 必填参数

| 参数 | 含义 | 控制台获取位置 |
|---|---|---|
| `COMPARTMENT_ID` | 区间 OCID | 最简单 = 直接用第1步 `config` 里的 `tenancy=ocid1.tenancy...`(根区间)。<br>或:导航菜单 →「**身份与安全**」(Identity & Security)→「**区间**」(Compartments)→ 点区间名 → 复制 **OCID**。 |
| `SUBNET_ID` | 子网 OCID(**必须公有子网**) | 导航菜单 →「**网络**」(Networking)→「**虚拟云网络**」(VCN)→ 选你的 VCN →左侧「**子网**」(Subnets)→ 点子网 → 复制 **OCID**。<br>没有 VCN 就先用控制台「**启动 VCN 向导**」一键建带公有子网的 VCN。 |
| `IMAGE_ID` | **ARM(aarch64)** 系统镜像 OCID | **留空即可** —— 程序会自动获取当前区域最新官方镜像(aarch64)。只想锁定固定镜像时才填,取法见「3.」。 |
| `IMAGE_ID_AMD` | **AMD(x86_64)** 系统镜像 OCID(给 E2.1.Micro) | **留空即可** —— 自动获取(x86_64)。同上。 |
| `IMAGE_OS` / `IMAGE_OS_VERSION` | 自动获取时的系统/版本(可选) | 默认 `Canonical Ubuntu` 最新;想用别的(如 Oracle Linux 或固定 `22.04`)才填。 |
| `SSH_KEY_FILE` | 容器内公钥路径 | 默认 `/keys/id_rsa.pub`,对应你放到 `./keys/id_rsa.pub` 的公钥(见 第4步),一般不用改。 |

---

## 3. (可选)手动锁定镜像 OCID(`IMAGE_ID` / `IMAGE_ID_AMD`)

> **默认无需做这步** —— 留空时程序会自动按「当前区域 + 形状架构」取最新官方镜像。
> 仅当你想固定某个特定镜像(版本/发行版)时,才按下面取 OCID 填入。

镜像按 **区域 + 架构** 区分,务必和你的区域、形状架构匹配。

**方法 A(推荐,所见即所得):**
1. 计算 → 实例 →「**创建实例**」(Create instance)。
2. 「**映像和形状**」(Image and shape)区块 →「**编辑**」→ 选操作系统(如 Ubuntu 22.04 / Oracle Linux)。
3. 切换「**形状**」(Shape):
   - 选 **VM.Standard.A1.Flex**(Ampere/ARM)时显示的镜像 = `IMAGE_ID`(aarch64)。
   - 选 **VM.Standard.E2.1.Micro**(AMD/x86)时显示的镜像 = `IMAGE_ID_AMD`(x86_64)。
4. 选定镜像后,在镜像名旁/详情里复制其 **OCID**。

**方法 B:** 计算 →「**自定义映像**」(Custom images)用自己的镜像;或查 Oracle 官方按区域发布的平台镜像 OCID 文档。

> 架构对不上(如 ARM 用了 x86 镜像)会导致创建失败。

---

## 4. SSH 密钥 → `keys/`

抢到的实例会注入你的**公钥**,之后用私钥登录。

```bash
ssh-keygen -t rsa -b 4096 -f ./keys/id_rsa -N ""
# 生成 ./keys/id_rsa(私钥) 与 ./keys/id_rsa.pub(公钥)
```
- 公钥默认路径(容器内)`/keys/id_rsa.pub` 已与 `SSH_KEY_FILE` 对应,无需改。
- 登录:`ssh -i ./keys/id_rsa ubuntu@<实例公网IP>`(Ubuntu 镜像用户名 `ubuntu`,Oracle Linux 用 `opc`)。

---

## 5. 通知(可选)— 抢到 / 抢满 / 出错时推送

| 参数 | 获取位置 |
|---|---|
| `PUSHPLUS_TOKEN` | 微信推送。打开 https://www.pushplus.plus → 微信扫码登录 →「**一对一推送**」页复制你的 **token**。国内直连,免代理。 |
| `PUSHPLUS_TOPIC` | 可选,PushPlus「群组」编码(一对多);留空即发给自己。 |
| `TG_BOT_TOKEN` | Telegram 里找 **@BotFather** → `/newbot` 创建机器人 → 返回的 **token**。 |
| `TG_CHAT_ID` | 找 **@userinfobot** 发一句话,它回你的 **chat id**;群组可用 @getidsbot。 |
| `NOTIFY_PROXY` | **仅 Telegram 用**。国内连 `api.telegram.org` 通常要代理,如 `http://192.168.1.1:7890`(指向你的代理混合端口);PushPlus 不走此代理。 |

配置后可测试:`curl -X POST http://127.0.0.1:8090/api/notify-test`。

---

## 6. 实例规格与免费额度

Web 面板可视化选择规格/台数/时长;命令行版(`grab.sh`)用 `.env` 里的 `DISPLAY_NAME/SHAPE/NUM_INSTANCES/OCPUS/MEMORY_GB/BOOT_VOLUME_GB/SLEEP_SECONDS/AVAILABILITY_DOMAINS`。

**Always Free 额度(别超,否则可能计费):**
- ARM `A1.Flex`:累计 **≤ 4 OCPU / 24 GB**(部分新账户为时长预算制 **1500 OCPU·时 + 9000 GB·时/月 ≈ 持续 2核/12G**)。
- AMD `E2.1.Micro`:**≤ 2 台**(每台固定 1核/1G)。
- 块存储:所有实例共享 **≤ 200 GB**。

> `A1.Flex` 是可计费弹性规格,控制台**不会**给它打"始终免费"标签;只要总量不超额度即免费。PAYG/试用账户超额会计费,请在「计费与成本管理 → 成本分析」核对。

---

## 7. 启动

```bash
docker compose up -d --build
# 打开 http://127.0.0.1:8090
```

> 面板**无登录鉴权**且能创建云资源,务必只绑定本机/可信内网,**切勿暴露公网**。
