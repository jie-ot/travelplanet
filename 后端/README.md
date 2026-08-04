# 《旅行星球》后端运行教程

本教程面向使用项目压缩包进行体验的评委老师，支持 **Windows** 和 **macOS**。请依次完成以下步骤。

## 1. 检查并安装运行环境

后端需要 **uv** 和 **Node.js**。Python 由 uv 根据项目要求（Python 3.11 或更高版本）自动准备，无需单独安装。

### 1.1 uv

先打开终端并检查是否已经安装：

```bash
uv --version
```

如果能看到版本号，直接进入第 1.2 节；如果提示命令不存在，推荐在终端执行对应命令安装。

**Windows（PowerShell）：**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

也可以使用 WinGet 安装：

```powershell
winget install --id=astral-sh.uv -e
```

**macOS（终端）：**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

如果电脑已安装 Homebrew，也可以执行：

```bash
brew install uv
```

安装后关闭并重新打开终端，再验证一次：

```bash
uv --version
```



### 1.2 Node.js

打开终端，执行：

```bash
node --version
npx --version
```

两个命令都能显示版本号即可。建议使用当前 **LTS（长期支持）版本**，最低要求为 Node.js 20.9.0。

如果尚未安装，推荐在终端执行：

**Windows（PowerShell）：**

```powershell
winget install OpenJS.NodeJS.LTS
```

**macOS（已安装 Homebrew）：**

```bash
brew install node
```

也可以打开 [Node.js 官网](https://nodejs.org/)，下载并运行适用于当前系统的 **LTS** 安装程序，注意不要选择上方的“用 Docker 运行 Node.js”，而应该选择下面的适用于 Windows（.msi）或适用于 macOS（.pkg）安装。

安装后关闭并重新打开终端，然后验证：

```bash
node --version
npx --version
```



## 2. 安装项目依赖

在**项目根目录**（有 `pyproject.toml` 的文件夹）打开终端，执行：

```bash
uv sync
```

uv 会自动准备合适的 Python、创建 `.venv` 虚拟环境并安装全部依赖。首次执行可能需要几分钟，请保持网络连接。

## 3. 查询电脑的局域网 IPv4 地址

手机和电脑必须接入**同一个局域网**，也可以是电脑连接手机的热点。电脑可以使用 Wi-Fi，也可以使用网线；使用网线时，应确保电脑连接的有线网络与手机 Wi-Fi 属于同一个路由器或局域网。

### 3.1 使用终端查询（推荐）

以下命令只输出当前联网接口的实际 IPv4 地址，不会显示大段网络信息。请复制对应系统的整段命令执行。

**Windows（PowerShell）：**

```powershell
$socket = New-Object Net.Sockets.UdpClient; $socket.Connect('8.8.8.8', 53); $socket.Client.LocalEndPoint.Address.IPAddressToString; $socket.Dispose()
```

**macOS（终端）：**

```bash
ipconfig getifaddr "$(route -n get default | awk '/interface:/{print $2}')"
```

记下输出的 IPv4 地址。常见的局域网地址通常以以下内容开头：

- 普通家用 Wi-Fi：`192.168.`
- 公司或学校等大型网络：`10.`
- 中型企业或商用网络：`172.16.` 至 `172.31.`

实际地址可能不同，应以命令输出为准。如果连接了 VPN 并得到多个地址，请使用当前 Wi-Fi 或有线网卡的地址。

### 3.2 通过系统设置查询

- **Windows：** 打开“设置” → “网络和 Internet” → 当前使用的“Wi-Fi”或“以太网” → “硬件属性”，找到“IPv4 地址”。
- **macOS：** 打开“系统设置” → “网络” → 当前已连接的 Wi-Fi 或有线网络 → “详细信息” → “TCP/IP”，找到“IPv4 地址”。



## 4. 修改 `.env` 配置文件（第8行 FRONTEND_ORIGINS）

压缩包内已经包含 `.env`。用记事本、文本编辑或 VS Code 打开项目根目录中的 `.env`，找到第8行的：

```env
FRONTEND_ORIGINS=http://localhost:3000
```

不要删除原有地址，只需在这一行末尾**添加**手机访问所用的新地址。不同地址之间必须使用**英文逗号** `,` 分隔，逗号前后不要加空格；端口必须是前端使用的 `3000`，不是后端的 `8000`。

例如，刚才查到的电脑 IPv4 地址是 `192.168.10.222`，修改为：

```env
FRONTEND_ORIGINS=http://localhost:3000,http://192.168.10.222:3000
```

请把示例中的 `192.168.10.222` 替换为电脑实际的 IPv4 地址。保存 `.env` 后，如果后端已经启动，需要先停止再重新启动，配置才会生效。

### 4.1 配置可选的 DeepSeek 规划模型

规划页默认继续使用当前的 `Doubao-Seed-2.0-pro`，无需增加配置。若要选择 DeepSeek V4 Flash 或 V4 Pro，请在 `.env` 中填写：

```env
DEEPSEEK_API_KEY=请填写你的DeepSeek_API_Key
DEEPSEEK_CHAT_BASE_URL=https://api.deepseek.com
DEEPSEEK_FLASH_MODEL=deepseek-v4-flash
DEEPSEEK_PRO_MODEL=deepseek-v4-pro
```

只填写本地 `.env`，不要把真实密钥写入 `.env.example`、代码、日志或提交到 Git。修改后需重启后端。

## 5. 启动后端

在后端项目根目录的终端执行：

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后端通常需要 10-20 秒，看到以下内容表示后端启动成功：`Uvicorn running on http://0.0.0.0:8000`

保持此终端窗口运行，然后按照前端运行教程启动前端。需要停止后端时，在此终端按 `Ctrl+C`。

## 6. 常见问题

**执行** `uv`**、**`node` **或** `npx` **时提示命令不存在**

关闭当前终端并重新打开后再试；如果仍然无效，按照第 1 节重新安装对应环境。

**手机可以打开前端，但接口请求失败或提示跨域错误**

确认手机和电脑位于同一局域网；后端启动命令包含 `--host 0.0.0.0`；`.env` 的 `FRONTEND_ORIGINS` 已添加 `http://电脑实际IPv4:3000`。修改 `.env` 后必须重启后端。

**手机无法打开前端**

确认使用的是电脑当前联网网卡的 IPv4，并检查电脑防火墙是否允许前端的 `3000` 端口和后端的 `8000` 端口通过。学校、公司或公共 Wi-Fi 可能启用了设备隔离；此时建议让手机和电脑连接同一个手机热点后重试。

**行程规划中的火车票查询不可用**

再次执行 `node --version` 和 `npx --version`，确认两者都有版本输出。首次查询需要联网下载相关组件，可能会稍慢。
