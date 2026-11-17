# 演示前检查清单（网络 / 代理 / 沙箱）

记录于 2026-11-17，全部数字为当天本机实测，不是估算。

## 一、问题：校园网直连 GitHub 必失败

```
PS> git clone --depth 1 https://github.com/dynamicslab/pysindy
Cloning into ...
fatal: unable to access '...': Recv failure: Connection was reset
```

`Connection was reset` 的意思是链路上有设备发了 RST，**连接在 TLS 握手阶段就作废**，
不是地址写错、不是没网、不是 git 配置问题。

同一台机器、同一个仓库，换成走本机代理后立刻成功：

```
remote: Enumerating objects: 303, done.
Receiving objects: 100% (303/303), 94.19 MiB | 3.83 MiB/s, done.
```

所以 **演示机的出口决定一切**，与仓库大小无关——TLS 层被重置时，连 84 KB 的靶场小仓库也拉不下来。

对照实验（沙箱环境，非校园网）：PyPI 下 12.5 MB wheel 完整通过校验，
GitHub codeload 同一 URL 连下 5 次，断点分别是 599405 / 888949 / 1188135 / 1485843 / 1573023 字节，
每次都不一样。据此判断为传输层截断，非 DNS、非证书、非认证。

## 二、必须知道的坑：git 优先读小写代理变量

后端 `git_clone` 用 `subprocess.run` 且不传 `env=`，**直接继承后端进程的环境变量**。
而 git/curl 读取代理时**小写优先**（`https_proxy` 先于 `HTTPS_PROXY`）。

实测后果：只设大写 `HTTPS_PROXY` 时，clone 落到环境里另一个低速代理上，
45 秒只涨 0.8 MB（约 18 KB/s）；四个变量都指向同一个可用代理后，
212 MB 工作区 **32 秒**拉完。

所以启动后端时必须成对设置：

```powershell
$env:http_proxy  = "http://127.0.0.1:10808"
$env:https_proxy = "http://127.0.0.1:10808"
$env:HTTP_PROXY  = "http://127.0.0.1:10808"
$env:HTTPS_PROXY = "http://127.0.0.1:10808"
```

反过来也成立：**代理已经失效但变量还留着，clone 会挂死到 240 秒超时**，
最后只得到一句 `clone_failed`。启动前先把变量清干净再按需设置。

## 三、当前环境的能力边界

| 能力 | 状态 | 后果 |
|---|---|---|
| GitHub 直连 | ❌ 校园网 RST | 必须挂代理或用镜像 |
| GitHub 走代理 | ✅ 3.83 MB/s | 可演示真实仓库 |
| Docker | ❌ 未安装（非未启动） | **无法跑真实沙箱** |
| fake 沙箱 | ✅ | 只到 `smoke_test_failed` |

没有 Docker 时，fake 沙箱不会真正执行 `check.py`，因此工作区里没有 `out.txt`，
`verified` 恒为 false。这条与代码无关，是沙箱替身的行为差异。

## 四、演示前逐条确认

1. 代理客户端在运行，`Test-NetConnection 127.0.0.1 -Port 10808` 为 True
2. 四个代理环境变量（大小写各两个）都指向同一个可用端口
3. 后端已重启，且启动日志里能看到它继承到的代理端口
4. `docker ps` 有输出（要演示真实沙箱这一步不能省）
5. 浏览器打开页面后顶栏是 `state` 而非 `mode replay`

## 五、验收信号（真跑成功时应看到的 6 条事件）

以前端"动作流"里出现的顺序为准：

| # | state | message 关键片段 |
|---|---|---|
| 1 | preparing | `cloned at c4421fc...` |
| 2 | preparing | `matched target paper-pysindy: injected check.py, requirements.txt` |
| 3 | planning | `run plan resolved`（command 必须是 `python -I check.py`，来自 manifest） |
| 4 | installing | `dependency install finished` |
| 5 | testing | `smoke test finished` |
| 6 | succeeded | `run succeeded` |

关键判据只有两个：`resolved_revision` 等于 `c4421fcec275c8f4cc5c1e93bebb961b212067ae`，
以及工作区里真的存在 `out.txt`。

## 六、仍然待办（不属于前端）

- `runner.git_clone` 在没有 `revision` 时走全历史 clone（pysindy 工作区 212 MB，pack 94 MB），
  建议默认 `--depth 1`。
- `CLONE_TIMEOUT_SECONDS = 240` 不可配置，对外服务建议缩短。
- 失败时只存 `clone failed: <异常>`，`git` 的 stderr 摘要没有进入事件，
  前端只能显示类别，无法告诉用户是网络、仓库不存在还是私有仓库。
- `_match_target` 用 `repo_url` 字符串精确相等匹配，使用镜像地址会匹配不到内置靶场，
  计划会退回模型生成。若要支持镜像，需要"匹配用原地址、clone 用拼接地址"。
