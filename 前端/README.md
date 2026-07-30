# 《旅行星球》前端运行教程

本教程面向使用项目压缩包进行体验的评委老师，支持 **Windows** 和 **macOS**。请先按照后端教程启动后端，再依次完成以下步骤。

## 1. 检查并安装 Node.js 与 pnpm

前端需要 **Node.js 20.9.0 或更高版本**和 **pnpm**，推荐使用当前 Node.js **LTS（长期支持）版本**。

### 1.1 检查现有环境

打开终端，执行：

```bash
node --version
pnpm --version
```

如果两个命令都能显示版本号，并且 Node.js 版本不低于 `20.9.0`，直接进入第 2 节。

如果 Node.js 版本过低或提示命令不存在，请按第 1.2 节安装；如果只有 `pnpm` 不存在，请执行：

```bash
corepack enable
pnpm --version
```

### 1.2 安装 Node.js（推荐使用命令行）

**Windows（PowerShell）：**

```powershell
winget install OpenJS.NodeJS.LTS
```

**macOS（已安装 Homebrew）：**

```bash
brew install node
```

也可以打开 [Node.js 官网](https://nodejs.org/)，下载并运行适用于当前系统的 **LTS** 安装程序：Windows 选择 `.msi`，macOS 选择 `.pkg`。请勿选择 Docker 方式。

安装完成后，**关闭并重新打开终端**，再执行：

```bash
node --version
corepack enable
pnpm --version
```

三个命令均正常执行，且 Node.js 版本不低于 `20.9.0`，即表示环境准备完成。

## 2. 查询电脑的局域网 IPv4 地址

如果需要用手机体验，手机和电脑必须接入**同一个局域网**。电脑可以连接 Wi-Fi，也可以使用网线；使用网线时，应确保有线网络与手机 Wi-Fi 属于同一个路由器或局域网。

### 2.1 使用终端查询（推荐）

以下命令只输出当前联网接口实际使用的 IPv4 地址，不会显示大段网络信息。请复制对应系统的整段命令执行。

**Windows（PowerShell）：**

```powershell
$socket = New-Object Net.Sockets.UdpClient; $socket.Connect('8.8.8.8', 53); $socket.Client.LocalEndPoint.Address.IPAddressToString; $socket.Dispose()
```

**macOS（终端）：**

```bash
ipconfig getifaddr "$(route -n get default | awk '/interface:/{print $2}')"
```

记下输出的 IPv4 地址。常见局域网地址通常以以下内容开头：

- 普通家用 Wi-Fi：`192.168.`
- 公司、学校等大型场所网络：`10.`
- 中型企业或商用机房网络：`172.16.` 至 `172.31.`

实际地址应以命令输出为准；无论电脑使用 Wi-Fi 还是有线网络，都使用当前联网接口对应的地址。

### 2.2 通过系统设置查询

- **Windows：** 打开“设置” → “网络和 Internet” → 当前使用的“Wi-Fi”或“以太网” → “硬件属性”，找到“IPv4 地址”。
- **macOS：** 打开“系统设置” → “网络” → 当前已连接的 Wi-Fi 或有线网络 → “详细信息” → “TCP/IP”，找到“IPv4 地址”。

## 3. 修改 `.env.local` 配置文件

压缩包内已经包含 `.env.local`。用记事本、文本编辑或 VS Code 打开项目根目录中的 `.env.local`，其中有以下两行：

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api
NEXT_PUBLIC_ASSET_BASE_URL=http://localhost:8000
```

- **只在电脑上访问：** 保持以上内容不变，使用 `localhost`。
- **需要用手机访问：** 将两处 `localhost` 都替换为第 2 步查到的电脑 IPv4 地址。

例如，电脑 IPv4 地址为 `192.168.10.222`，应修改为：

```env
NEXT_PUBLIC_API_BASE_URL=http://192.168.10.222:8000/api
NEXT_PUBLIC_ASSET_BASE_URL=http://192.168.10.222:8000
```

**特别注意：只替换 `localhost`，两个地址中的 `:8000` 端口都必须保留，不能删除，也不能改成前端使用的 `:3000`。** 保存文件后，请按照第 4 节重新构建并启动前端。

当前云端后端及 Android APK 使用以下固定地址：

```env
NEXT_PUBLIC_API_BASE_URL=http://114.132.201.196:8000/api
NEXT_PUBLIC_ASSET_BASE_URL=http://114.132.201.196:8000
```

Android APP 的固定图标源文件为 `design/travelplanet-app-icon.png`。后续打包必须继续从该文件生成各分辨率图标，未经明确确认不要替换或重新设计。

Android APK 必须保留 `capacitor.config.json` 中的 `plugins.CapacitorHttp.enabled: true`，让云端 API 请求使用原生 HTTP，避免 WebView 的 CORS 限制。

云端图片目前通过 `http://114.132.201.196:8000/static/...` 提供，因此 Android 的 `server.androidScheme` 必须保持为 `http`，避免图片成为 HTTPS WebView 中的混合内容。

## 4. 安装依赖并启动前端

在前端**项目根目录**（有 `package.json` 的文件夹）打开终端，依次执行：

```bash
pnpm install                              # 会在项目根目录生成 node_modules 文件夹，可能需要较长时间
pnpm build                                # 会在项目根目录生成 .next 文件夹，修改配置、代码都需要重新 build
pnpm start --hostname 0.0.0.0             # 启动前端的命令
```

首次安装和构建可能需要几分钟，请保持网络连接。看到 `Ready` 后保持此终端窗口运行；需要停止前端时，在此终端按 `Ctrl+C`。

**修改任何内容后——包括 IPv4 地址、`.env.local` 配置或前端代码——都必须先停止当前前端，再重新执行以下两条命令，否则修改不会生效：**

```bash
pnpm build
pnpm start --hostname 0.0.0.0
```

## 5. 访问前端

推荐使用 **Chrome、Safari 或 Edge** 浏览器；其他浏览器可能出现前端请求超时等兼容性问题。

- **电脑访问：** 打开 `http://localhost:3000`
- **手机访问：** 打开 `http://电脑IPv4地址:3000`

例如，第 2 步查到的 IPv4 地址为 `192.168.10.222`，手机应打开：

```text
http://192.168.10.222:3000
```


## 6. 常见问题

**页面可以打开，但没有数据或图片**

确认后端已在 `8000` 端口启动。手机访问时，确认 `.env.local` 中两处 `localhost` 均已替换为电脑 IPv4，两个 `:8000` 均已保留；修改后重新执行 `pnpm build` 和 `pnpm start --hostname 0.0.0.0`。

**手机无法打开前端**

确认手机和电脑位于同一局域网、访问地址使用电脑当前联网接口的 IPv4，并确认前端使用了 `--hostname 0.0.0.0` 启动。还应检查电脑防火墙是否允许 `3000` 和 `8000` 端口通过。学校、公司或公共 Wi-Fi 可能启用了设备隔离，此时建议让手机和电脑连接同一个手机热点后重试。

**修改配置或代码后没有生效**

在运行前端的终端按 `Ctrl+C` 停止服务，然后重新执行：

```bash
pnpm build
pnpm start --hostname 0.0.0.0
```

**提示找不到 `pnpm`**

关闭并重新打开终端，执行 `corepack enable`，再用 `pnpm --version` 验证。

**`pnpm build` 失败**

执行 `node --version`，确认 Node.js 版本不低于 `20.9.0`；同时确认依赖已通过 `pnpm install` 完整安装。
