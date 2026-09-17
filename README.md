# Neptune 断电自动续充

每天北京时间 05:55 预检查昨夜 23:45–00:30 因断电结束的充电记录；设备重新供电且原端口空闲时，在同一设备和端口续充。失败会在 06:05、06:15、06:25 重试。

## 安全设计

- 默认 `NEPTUNE_DRY_RUN=true`，只读账户、历史和设备状态，不发送真实开电请求。
- 只支持已核对的平台按电量计费模式（`measure=1`）；遇到其他模式会退出，不猜测参数。
- 通过 HTTPS 访问平台，避免 `openId` 和账户信息明文传输。
- `employeeId` 从账户接口自动读取，无需抓包或长期保存。
- 充电最长 480 分钟，或采用设备更短的上限。
- 工作流权限仅为只读，并禁止同一时间重复运行。
- 05:55 使用非整点附近的调度分钟，降低 GitHub Actions 排队导致约 07:00 才启动的概率。

## GitHub Actions 部署（推荐）

### 1. 配置 Secret

进入仓库的 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，添加：

| Name | Value |
|---|---|
| `NEPTUNE_OPEN_ID` | 微信充电账户的 openId |

`openId` 相当于账户凭证。不要写入代码、Issue、Actions 日志或聊天消息。

### 2. 配置 Variables

在同一页面切换到 **Variables**，添加：

| Name | 初始值 | 说明 |
|---|---:|---|
| `NEPTUNE_AREA_ID` | `6` | 以实际小程序 URL/请求为准 |
| `NEPTUNE_DRY_RUN` | `true` | 初次必须保持演练模式 |
| `NEPTUNE_MAX_CHARGE_TIME` | `480` | 最长续充分钟数 |

### 3. 首次演练

进入 **Actions → Neptune Auto Resume → Run workflow**。普通白天没有昨夜断电记录时，显示“未找到符合条件的断电记录”是正常结果；要完整验证设备和端口，需在真实断电记录产生后的早晨运行。

确认日志包含以下信息且设备、端口、时长都正确：

```text
运行模式: 演练（不会开电）
续充计划: 设备=..., 端口=..., 最长=480 分钟, 按电量计费, 余额支付
演练检查通过，未发送开电请求
```

### 4. 启用真实续充

只有在演练日志正确后，才把变量 `NEPTUNE_DRY_RUN` 改为 `false`。如需立即停用，把它改回 `true`，或在 Actions 中禁用工作流；这不会停止已经开始的订单，已开订单仍需在充电平台内结束。

## 本地演练

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，保持 NEPTUNE_DRY_RUN=true
python -m unittest discover -s tests -v
python main.py
```

## 必要配置

| 变量 | 必需 | 默认值 | 说明 |
|---|---|---:|---|
| `NEPTUNE_OPEN_ID` | 是 | — | 微信充电账户标识/凭证 |
| `NEPTUNE_AREA_ID` | 否 | `6` | 平台区域 ID |
| `NEPTUNE_DRY_RUN` | 否 | `true` | 是否只演练 |
| `NEPTUNE_MAX_CHARGE_TIME` | 否 | `480` | 最长续充分钟数 |
| `NEPTUNE_EMPLOYEE_ID` | 否 | — | 旧版兼容；若填写则校验一致性 |

## 使用边界

本项目调用的是充电平台的非公开接口，平台升级后字段可能变化。每次修改充电策略或长期停用后重新启用时，应先恢复演练模式核对日志。GitHub Actions 的定时任务不是实时调度，06:05 是目标时间而非严格保证。
