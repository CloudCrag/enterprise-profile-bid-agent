# Windows 运行手册

## 1. 准备环境

安装：

- Python 3.11；
- Node.js 18+（包含 npm）；
- 可用的 SSH 客户端。

确认 PowerShell 可以执行本地脚本；若被策略阻止，可仅对当前进程执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 2. 建立本地配置

```powershell
Copy-Item .env.example .env.local
notepad .env.local
```

至少填写：

```text
CAPABILITY_AI_MODEL=实际可用模型名
CAPABILITY_AI_API_KEY=实际智谱Key
REAL_DATABASE_URL=mysql://用户名:URL编码密码@127.0.0.1:本地转发端口/project_recommendation
```

`.env.local` 只保存在本机，不得放入交付 ZIP。

## 3. 手工建立 SSH 隧道

按照项目已有的《Windows SSH 隧道连接数据库操作手册》建立端口转发。项目本身不会创建、保存或重连 SSH 隧道。

先确认本地转发端口可访问，再启动系统。

## 4. 启动

在项目根目录运行：

```powershell
.\start.ps1
```

首次运行会创建 `.venv` 并安装锁定 Python/Node 依赖。启动检查通过后浏览器打开 `http://127.0.0.1:5173`。

## 5. 常见错误

### 缺少配置

错误页会列出缺少的变量。补齐 `.env.local` 后重新运行。

### 数据库连接失败

检查：

1. SSH 隧道窗口是否仍在运行；
2. `REAL_DATABASE_URL` 端口、用户名、密码和库名；
3. 密码中的特殊字符是否 URL 编码；
4. 远程账号是否有只读查询权限。

### 找不到有效项目

启动检查要求真实查询读取到一条 状态为 `TENDER/PLAN` 的项目。确认远程库中存在符合条件的项目和当前系统时间正确。

### 智谱调用失败

检查 API Key、模型名、模型权限、网络和接口地址。系统不会降级到固定答案。

### 端口被占用

关闭占用 `8000`、`8001`、`3000`、`5173` 的旧进程后重试。

## 6. 停止

在运行 `start.ps1` 的窗口按 `Ctrl+C`。脚本会清理本次启动的所有子服务。
