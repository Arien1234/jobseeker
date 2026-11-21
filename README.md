# JobHunter

在本机打开智联招聘推荐页，采集当前列表，用本机 Ollama 模型按筛选条件打分，再对通过的职位点击「立即投递」。简历和登录态只留在这台电脑上。

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

浏览器打开 http://127.0.0.1:7860 。第一次运行会在 `config/profile.json` 写入一份示例筛选条件，按自己的城市、薪资和岗位改完再保存。简历在界面的「简历」里上传 PDF。

登录、短信和滑动验证都在弹出的浏览器里手动完成。程序不会代填验证码。

## 模型

筛选走 Ollama 的 `/api/chat`，要求模型直接返回 JSON。默认地址是 `http://127.0.0.1:11434`，默认模型是 `qwen3.5:9b`。

建议用 **qwen3.5:9b**。它能读中文职位描述，也能按要求输出 JSON，一台有大约 8GB 可用内存的电脑就能跑。这是程序开箱使用的模型。

机器更紧时可以换成 `qwen2.5:7b-instruct`，速度更快，但对长职位描述和 JSON 格式没那么稳。显存或内存充裕（大约 16GB 以上给模型）时可以用 `qwen2.5:14b-instruct`，打分更稳，每条职位也会更慢。不要把嵌入模型（例如 bge-m3）配成筛选模型，它不负责对话打分。

不建议把简历交给云端模型。换模型只改环境变量，不用改代码：

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

上传 PDF 时，姓名旁的手机号、邮箱、微信和 GitHub 链接会先去掉，再写入本地数据库。日志会打掉密钥和手机号，但仍然可能出现公司名和职位名，所以日志目录也不上传。

仓库里的 `config/profile.example.json` 是空模板。克隆后直接运行即可，个人配置会写到被忽略的 `config/profile.json`。

## 测试

```powershell
python -m pytest -q
```
