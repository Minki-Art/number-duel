# 推送指南（只做一次）

> 这份文档是给你自己看的操作步骤。
> **我不会、也无法替你登录 GitHub** —— 推送必须由你本人在终端里完成。

---

## 第 0 步：先把密码改掉

如果你曾经在聊天、文档或任何地方贴过账号密码，**请立刻修改密码**，
并确认没有在 `git config`、脚本或代码里留下凭据。

---

## 第 1 步：在 GitHub 上建一个空仓库

1. 打开 <https://github.com/new>
2. **Repository name** 填 `number-duel`
3. **Description** 可以填：`回合制数字对战游戏 · Python 服务端权威 + 四档 AI + 133 个测试`
4. 选择 **Public**
5. ⚠️ **不要勾选** "Add a README file"、"Add .gitignore"、"Choose a license"
   （本地已经有了，勾了会产生冲突）
6. 点 **Create repository**

---

## 第 2 步：把本地仓库关联到远程

把下面的 `YOUR_NAME` 换成你的 GitHub 用户名：

```powershell
cd D:\game

git remote add origin https://github.com/YOUR_NAME/number-duel.git
git branch -M main
git push -u origin main
```

推送时会弹出浏览器让你登录 GitHub，**在弹窗里输入你自己的凭据**。

---

## 关于密码：GitHub 不再支持账号密码

从 2021 年起，GitHub 已经**不接受用账号密码做 git 操作**了。你有两种选择：

### 方案 A：Personal Access Token（简单）

1. 打开 <https://github.com/settings/tokens> → **Generate new token (classic)**
2. 勾选 **`repo`** 权限，设置有效期
3. 生成后**复制那串 token**（只显示一次）
4. `git push` 提示输入密码时，**粘贴这个 token**（不是你的账号密码）

> 💡 Windows 上凭据会被「凭据管理器」记住，之后不用反复输入。
> 想清除：控制面板 → 凭据管理器 → Windows 凭据 → 删除 `git:https://github.com`

### 方案 B：SSH 密钥（推荐，长期最省心）

```powershell
# 1. 生成密钥（一路回车即可）
ssh-keygen -t ed25519 -C "你的邮箱"

# 2. 查看公钥并复制
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub

# 3. 打开 https://github.com/settings/keys → New SSH key → 粘贴保存

# 4. 把远程地址改成 SSH 形式
git remote set-url origin git@github.com:YOUR_NAME/number-duel.git
git push -u origin main
```

### 方案 C：用 GitHub CLI（最省事）

```powershell
winget install GitHub.cli
gh auth login          # 跟着提示走，会自动配好凭据
gh repo create number-duel --public --source=. --push
```

---

## 第 3 步：推送完成后要改的两处地方

仓库推上去之后，下面两处的 `YOUR_NAME` 需要替换成你的用户名，否则徽章和图例会显示不出来：

| 文件 | 位置 |
|---|---|
| `README.md` | 顶部 CI 徽章的链接地址（有两处 `YOUR_NAME`） |
| `LICENSE` | `Copyright (c) 2025 <在此填写你的名字>` |

改完再提交一次：

```powershell
git add README.md LICENSE
git commit -m "docs: 填写仓库地址与版权信息"
git push
```

---

## 第 4 步：确认 CI 通过

推送后打开 `https://github.com/YOUR_NAME/number-duel/actions`，
应该能看到 **CI** 工作流在跑（Python 3.10 / 3.11 / 3.12 三个版本）。

全绿之后，README 顶部的徽章就会自动变绿 —— **这是招聘方第一眼看到的东西**。

---

## 常见问题

**Q：`git push` 报 `remote origin already exists`**
```powershell
git remote set-url origin https://github.com/YOUR_NAME/number-duel.git
```

**Q：误把某次提交推上去了，想撤销**
```powershell
git revert <commit-id>
git push
```

**Q：不小心把密码/密钥提交进仓库了**
1. **立刻改掉那个密码/吊销那个密钥**（这是最重要的一步）
2. 用 [`git filter-repo`](https://github.com/newren/git-filter-repo) 或 BFG 清理历史
3. 强推：`git push --force`

> ⚠️ 注意：即使清理了历史，**已经泄露的凭据也必须视为永久失效**，必须更换。
