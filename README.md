# JobHunter

在本机采集智联招聘职位，用规则和本地模型筛选，再对通过的职位点击「立即投递」。

界面是 Gradio，浏览器自动化是 Playwright，职位存在 SQLite，打分走本机 Ollama。

## 技术

| 部分 | 实现 |
| --- | --- |
| 界面 | Gradio，主题 Soft |
| 浏览器 | Playwright 持久化 Chromium，有界面 |
| 采集 | 读取当前列表和侧栏详情，解析职位名称、公司、薪资、地点、学历、经验、规模、HR 活跃和职位描述 |
| 硬性筛选 | `core/rules.py`，不调用模型 |
| 打分 | Ollama `/api/chat`，`format` 为 JSON |
| 投递 | 在侧栏点击「立即投递」。按钮已是「继续沟通」的记为筛选前已投递 |
| 存储 | SQLite，WAL |

```mermaid
flowchart LR
  UI["Gradio :7860"] --> Worker[任务线程]
  Worker --> Browser[Playwright]
  Browser --> Site[智联招聘]
  Worker --> Rules[本地规则]
  Rules --> Model["Ollama :11434"]
  Worker --> DB[(SQLite)]
```

## 端口

| 地址 | 用途 |
| --- | --- |
| http://127.0.0.1:7860 | 操作界面 |
| http://127.0.0.1:11434 | Ollama。可用环境变量 `OLLAMA_HOST` 改 |
| 模型名 | 默认 `qwen3.5:9b`，用 `OLLAMA_MODEL` 改 |

## 项目结构

```text
app.py                      Gradio 页面和按钮
requirements.txt
config/profile.example.json 第一次运行用的筛选模板
core/
  browser.py                启动、关闭、重新加载浏览器
  collector.py              翻页采集，当前页没有新职位就停
  job_parser.py             解析智联页面
  rules.py                  薪资、城市、经验、公司规模、HR 活跃、届别、岗位类型
  ai_filter.py              调用 Ollama，整理 JSON 分数
  application.py            分数是否达到自动投递线
  auto_apply.py             点击投递按钮
  database.py               职位、投递记录、简历正文
  task_manager.py           采集、筛选、投递的任务循环
  resume_parser.py          从 PDF 抽出正文
  defaults.py               恢复默认筛选时用的值
  profile_store.py          读写 config/profile.json
scripts/check_ollama.py     检查模型能否返回 JSON
tests/                      pytest
```

## 环境

Python 3.11 及以上，本机已启动 Ollama，并且能弹出 Chromium。

```powershell
pip install -r requirements.txt
python -m playwright install chromium
ollama pull qwen3.5:9b
python app.py
```

浏览器打开 http://127.0.0.1:7860 。第一次运行会按 `config/profile.example.json` 写出 `config/profile.json`。智联的登录、短信和验证在弹出的窗口里手动完成。

检查模型是否在线：

```powershell
python scripts/check_ollama.py
```

这个脚本只发一句测试，不带简历。模型若跑在 CPU 上，生成一条评分大约要一分钟。界面会写明正在看哪个职位，这条结束再继续下一条。点「停止投递」会立刻断开这次模型调用。

## 使用

界面分「岗位列表」和「筛选设置」。标题和页签固定在上面。

![岗位列表](docs/images/home.png)

浏览器控制在第一行：「启动浏览器」「重新加载」「关闭浏览器」。

第二行：

| 按钮 | 作用 |
| --- | --- |
| 筛选并投递 | 主操作。先筛已经采集的职位，再从当前智联页面继续采集。达到分数才投递。运行中变成灰色「停止投递」，当前这条处理完才变回来。 |
| 仅采集 | 只抓当前页，不投递。运行中变成「停止采集」。 |
| 暂停 | 暂停当前任务。 |
| 清空列表 | 清空职位表，不会接着自动采集。 |

「本批投递数量」是这一轮的目标，默认 150，最大 200。「进度」按这一轮计算。「已采集」和「已投递」是这次进程启动以来的累计，新开一轮不会把这两个数清零。

当前页没有新职位时，投递停止，浏览器保持打开。在智联页面上改条件、刷出新职位后，再点「筛选并投递」。

关闭浏览器会清空当前职位列表。退出程序会清空简历正文，筛选条件仍留在 `config/profile.json`。

### 搜索和详情

搜索框只匹配职位名称和岗位详情。命中的词会标黄。名称下面会带出详情里的一小段。勾选「只看已投递」只留已经投出的行。两个条件可以一起用。

点「查看」打开详情，职位描述在最上面。点弹窗外面关闭。

![搜索职位](docs/images/search.png)

侧栏按钮是「继续沟通」时，这条记为筛选前已投递；是「立即投递」时，记为筛选前未投递。筛选说明里会带上这个结果。

### 筛选设置

在这一页改条件，点「保存筛选条件」。点「恢复默认筛选」会回到 `core/defaults.py`。

硬性条件在本地判断，不通过的不交给模型。通过的由 Ollama 打分，分数达到「自动投递最低分」（默认 80）才自动点击投递。

薪资看两档。上限低于保底则排除。没有勾选「优先薪资不设上限」时，下限高于优先薪资则排除；勾选后不按上限排除。

岗位类型可以多选：实习、正式、校招、社招。全选表示四种都允许。

![岗位类型](docs/images/job-types.png)

勾选了校招或实习时，用简历或画像里的大学毕业年份核对届别。职位写明只招某一届、而年份对不上，就排除。写了「优先」或「也可」的届别不是硬性条件。正式和社招默认要已毕业；还没毕业时，只有职位写明可以发 offer、提前实习并提供转正才保留。筛选说明开头会写岗位属性。

公司人数、HR 活跃、城市、经验年限、目标岗位和屏蔽词也在这一页。HR 活跃比所选下限更旧的不投。简历 PDF 只用于打分，程序退出时正文会被清掉。

## 测试

```powershell
python -m pytest -q
```
