# JobHunter

在本机打开智联招聘推荐页，采集职位，用本机 Ollama 按筛选条件打分，再对通过的职位点击「立即投递」。简历、登录态和职位数据只留在这台电脑上。

## 环境

- Python 3.11 及以上
- 本机已安装并启动 [Ollama](https://ollama.com)
- 能打开有界面的 Chromium（由 Playwright 启动）

```powershell
pip install -r requirements.txt
python -m playwright install chromium
ollama pull qwen3.5:9b
python app.py
```

浏览器打开 http://127.0.0.1:7860 。第一次运行会按 `config/profile.example.json` 生成 `config/profile.json`。登录、短信和滑动验证都在弹出的浏览器里手动完成。

## 怎么用

界面有两页：**岗位列表**和**筛选设置**。标题和页签固定在上面，下面的内容可以滚动。

1. 点「启动浏览器」，在弹出的窗口里登录智联招聘。
2. 「开始采集」只抓当前页。采集中按钮会变成「停止采集」，再点会从上次位置继续。清空列表、刷新页面都不会自己开始采集。
3. 「筛选并投递」按设定数量投递，默认 150，最多 200。已采集的岗位会先筛选再投。之后每次只采一个新岗位，交给 AI 筛选，通过才点「立即投递」。
4. 当前页投完后，列表下方会出现「没有更多了」。程序会刷新页面，等新岗位出来，再按同样的顺序继续采集、筛选、投递，直到达到数量，或点「停止投递」。

采集时会看侧栏按钮。按钮是「继续沟通」记为筛选前已投递，是「立即投递」记为筛选前未投递。筛选说明里会写上这个结果。每条岗位前面的「查看」打开岗位详情，职位描述在最上面。

浏览器关闭后，当前职位列表会清空。关闭程序时也会清空本地简历。

## 默认筛选

保底薪资 4K，优先薪资不设上限。公司至少 20 人。HR 活跃最旧可收到「本月活跃」，更旧的不投。实习、正式、校招、社招可以多选，全选表示都投。目标岗位包含 Vibe Coding。这些都可以在「筛选设置」里改，「恢复默认筛选」会回到这套值。

## 模型

筛选走 Ollama 的 `/api/chat`，要求模型直接返回 JSON。默认地址是 `http://127.0.0.1:11434`，默认模型是 `qwen3.5:9b`。

建议用 **qwen3.5:9b**。它能读中文职位描述，也能按要求输出 JSON，一台有大约 8GB 可用内存的电脑就能跑。

机器更紧时可以换成 `qwen2.5:7b-instruct`。显存或内存充裕时可以用 `qwen2.5:14b-instruct`。不要把嵌入模型配成筛选模型。

```powershell
$env:OLLAMA_HOST = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "qwen3.5:9b"
python scripts/check_ollama.py
```

`check_ollama.py` 只发一句不含简历的测试，用来确认模型能回复。

## 隐私

这些内容只在本机，`.gitignore` 已经排除，不要加进 Git：

| 路径 | 里面有什么 |
| --- | --- |
| `config/profile.json` | 城市、薪资、学校、技能和筛选条件 |
| `data/` | 简历正文、职位和投递记录 |
| `browser_data/` | 智联登录 Cookie |
| `logs/` | 运行日志 |
| `*.pdf` | 简历原件 |
| `.env`、`config/secrets.json` | 本地密钥 |

上传 PDF 时，姓名旁的手机号、邮箱、微信和 GitHub 链接会先去掉，再写入本地数据库。日志仍然可能出现公司名和职位名，所以日志目录也不上传。

仓库里的 `config/profile.example.json` 是模板。克隆后直接运行即可，个人配置会写到被忽略的 `config/profile.json`。

## 测试

```powershell
python -m pytest -q
```
