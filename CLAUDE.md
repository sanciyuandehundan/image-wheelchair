# 生图轮椅

Qwen-Image-2.1（Heretic BF16）+ 独立的 ComfyUI 实例（端口 8189），外面套了一层「生图轮椅」网页（端口 8190）：给参考图 + 一句话就出图，像 ChatGPT 生图那样用。仓库：https://github.com/sanciyuandehundan/image-wheelchair （Apache-2.0）。

## 怎么跑

- 安装：双击 `安装.cmd` → `scripts/bootstrap.ps1`（下载 uv、装 Python 3.13 到 `.runtime/python`、建 `.venv`）→ `scripts/install.py`（按 `scripts/models.json` 下载指定提交的 ComfyUI 到 `.runtime/ComfyUI`、往 `.venv` 装 PyTorch cu130 和依赖、下载模型并核对 SHA-256、把 Heretic 文本编码器转成 ComfyUI 格式）。可以重复运行，装好的会跳过，下载断了会接着下（断点在 `models/.partial/`，校验过的文件记在 `models/.verified.json`）。`--skip-models` 只装程序，`--only upscale` 只下路径含 upscale 的模型。镜像（做法参照用户另一个项目的安装器）：每个文件的几个来源先测速用最快的（`install.pick_url`）；模型有魔搭 / 抱抱脸 / hf-mirror，PyTorch 轮子有官方和阿里云 / 上交 / 南大镜像（自己断点续传下载、核对 SHA-256 后再装），PyPI 按速度排序、镜像当主源 pypi.org 兜底（镜像会缺新包），GitHub 文件（uv、Python、ComfyUI 源码包）可走 `github_proxies` 里的代理，但只限有固定 SHA-256 的文件。环境变量 `IMAGE_WHEELCHAIR_PIP_INDEX` 可指定 PyPI 源。2026-10-12 用全新克隆测过：uv / Python / ComfyUI / 依赖都能从网上装上，模型（硬链接现成文件）全部校验通过，装好后文生图 + 超分、反推提示词、网页都正常。
- 用户入口：双击 `生图轮椅.cmd` → 打开 http://127.0.0.1:8190 ，需要时自动启动 ComfyUI。
- 仓库只放自己写的代码：`.gitignore` 是白名单（`/*` 全忽略，再逐个放行），ComfyUI、Python 环境、模型、input/output/logs 都不进仓库，也不要加进去；新加的顶层文件要在 `.gitignore` 里放行。改了依赖的版本或模型，同步改 `scripts/models.json`（每个文件都要有 size 和 sha256）。
- 许可证：本项目代码 Apache-2.0；Qwen-Image-2.1 模型和 VAE 是 Qwen RESEARCH LICENSE（只许研究/评估等非商业用途，产品主名不能叫 Qwen），超分模型 AnimeSharp 是 CC BY-NC-SA 4.0，Heretic 文本编码器是 Apache-2.0。安装脚本下载模型前会列出并要求输入 Y 同意（`install.py --yes` 跳过询问），README 末尾有第三方组件表，换模型或加依赖时要同步更新。
- Python 一律用 `.venv\Scripts\python.exe -X utf8 -s`（下面简写为 `PY`）。studio 的模块名别和 ComfyUI 顶层模块重名（如 `comfy`、`app`、`server`、`nodes`），以免以后把 ComfyUI 放进 sys.path 时冲突。
- 命令行生图（和网页同一套逻辑，结果也进网页历史）：
  - `PY studio/cli.py "把<image1>的背景换成海边日落" -i 照片.jpg` → 最后几行打印输出图片的绝对路径，可以直接 Read 查看效果
  - `PY studio/cli.py "……" --aspect 16:9 --quality 2k -n 2 --seed 7 --transparent`
  - `PY studio/cli.py "" -i 底图.png -i 角色.png --mode swap_character` → 「换角色」模式：图1 的身体/服装/动作 + 图2 的发型发色饰品；第一个参数变成可留空的补充要求；加 `--toggle` = 「同步换服装」，图2 的服装也换上
  - `PY studio/cli.py "公园里，写实摄影" --aspect 4:3 --people people.json` → 「人物设定」：JSON 是 `{"scene": {...}, "persons": [{...}, ...]}`（或只写 persons 列表），字段名是 `config.toml` `[people]` 里的 key，`"ref": 2` = 长相参照图2
  - `PY studio/cli.py "……" -i a.png --dry-run` → 只打印要提交给 ComfyUI 的节点图，不生成；`--show-prompt` → 只打印实际送进模型的提示词（模板、人物描述、透明背景都展开后的）
  - `PY studio/cli.py --describe 图.png` → 「反推提示词」：看图写一段适合本模型的描述（最后一段是结果，前面一行是原图最接近的比例）；加 `--dry-run` 只打印节点图
  - `PY studio/cli.py "……" --upscale 4096 [--repaint]` → 生成后超分到长边 4096；`--repaint` 再用生图模型重绘补细节
  - `PY studio/cli.py "扩出部分的描述（可空）" -i 原图.png --mode outpaint --aspect 16:9 [--anchor left|right|top|bottom]` → 扩图
  - `PY studio/cli.py "……" -i 立绘.png -i 带背景的插画.png --style 2` → 画风统一：先看图2 写出画法，要求人物和背景都用这种画法（结果里会打印【画风】）
  - `PY studio/cli.py "傍晚的街道，动漫插画" -i 草图.png --mode sketch --marks '[{"color":"红色","text":"戴眼镜的男生，坐着看书"}]'` → 草图生图；`--marks` 也可以是 JSON 文件路径，color 是 `[board]` 里的颜色名
  - `PY studio/cli.py "" -i 标注图.png --mode mark --marks '[{"color":"红色","kind":"circle","text":"把头发改成黑色"}]'` → 圈选/涂抹编辑（kind：circle 圈 / paint 涂抹）；`-i 原图 -i 黑底标注图 --mode mark --toggle` = 独立掩码
  - `PY studio/cli.py "补充要求（可空）" -i 角色.png --mode turnaround [--toggle] --quality 2k` → 四视图（正面/朝左侧面/背面/朝右侧面全身像，默认 4:3）；`--toggle` = 加脸部特写（16:9）
- ComfyUI 本身由 `scripts/launch.py start|stop|status` 管理；日志在 `logs/comfyui-*.error.log`。
- Git Bash 里用 curl 发中文 JSON 会被转成系统代码页，测试 API 时把请求体写进 UTF-8 文件再 `--data-binary @file`。

## studio/ 文件分工（改什么就去哪）

| 文件 | 负责 | 改完 |
|---|---|---|
| `config.toml` | 默认参数：模型文件名、步数、采样器、画质档位、比例列表、透明背景提示词、人物设定的字段和句式（`[people]`）、反推提示词的系统提示词和采样参数（`[describe]`）、超分（`[upscale]`）、画风统一（`[style]`）、画板颜色（`[board]`）、预设模式（`[modes.*]`，含扩图 `outpaint`、四视图 `turnaround`、草图生图 `sketch`、圈选/涂抹编辑 `mark`） | 立即生效（新增/删除模式、增删人物字段要刷新网页） |
| `pipeline.py` | 请求 → ComfyUI API 节点图。换模型、加 LoRA、加放大、改尺寸规则都在这里；`full_prompt` 拼最终提示词，`people_text` 渲染人物描述，`build_describe` 是反推提示词的节点图，`outpaint_layout` / `add_outpaint_inpaint` 管扩图，`add_upscale` 管超分 | 立即生效（每次生成前 reload） |
| `web/index.html` | 整个前端（原生 JS，无构建） | 刷新网页 |
| `jobs.py` | 参考图保存、提交、进度跟踪、历史记录 | 重启网页服务 |
| `comfy_client.py` | ComfyUI HTTP/WebSocket 客户端、启动/关闭 | 重启网页服务 |
| `webui.py` | aiohttp 网页服务和 /api/* 接口 | 重启网页服务 |
| `cli.py` | 命令行入口 | — |

重启网页服务：关掉「生图轮椅」的命令行窗口再双击 `生图轮椅.cmd`。

scripts/ 里：`bootstrap.ps1`（只用 ASCII，Windows PowerShell 5.1 读不带 BOM 的中文 .ps1 会乱码）、`install.py`、`models.json`（安装清单）、`convert_heretic.py`（文本编码器重新打包，权重字节不变）、`launch.py` / `run_comfy.py`（启停 ComfyUI）、`merge_pe_t2i.py`（可选的提示词扩写模型，见下）。

## 关键约定

- 参考图上传后转成 PNG 存到 `input/studio/<sha1前16位>.png`；生成结果在 `output/studio/<日期>/qwen_*.png`；历史记录是 `output/studio/history.jsonl`（一行一个任务）。缩略图缓存在 `temp/studio_thumbs/`，可随时删。
- 尺寸规则（`pipeline.build`）：有参考图且比例为 `auto` = 编辑模式，画布用 `TextEncodeQwenImage21` 的 latent 输出（跟随图1，总像素≈画质档位²）；否则用 `EmptyLatentImage` 按比例新建画布，参考图缩到 `reference_resolution`²。
- 预设模式（如「换角色」）：`config.toml` 的 `[modes.<名字>]` 一节就是一个模式，网页「模式」下拉框和 `cli.py --mode` 都从这里读。提交时提示词 = `template` + `extra`（用户写的补充）；`aspect = "auto"` 强制编辑模式；`default_aspect` 用于要新画布的模式（扩图、四视图）：比例不能选「自动」，切到这个模式时换成它（`jobs.make_request` 和网页 `applyMode` 各处理一遍）；`min_refs`/`need` 控制参考图张数。调效果只改 `template` 文字即可，加新模式照抄一节。模板里别列举具体物件（「发箍、兽耳、角」「腰带」等），也别写条件句（「是像素画就……」），模型会把它们当成要画的内容；用「只照搬图2上实际画出来的东西」这类通用说法。改完至少拿一组两张图都没有该物件的图验证。模式下的 `[modes.<名字>.toggle]` 子表是一个分支（网页上是模式旁的勾选框，文字取 `checkbox`；命令行 `--toggle`），勾上后用子表里的项覆盖模式的同名项，覆盖逻辑在 `pipeline.preset`。子表必须写在该模式所有键的后面，之后再加的键会被 TOML 归进子表。历史记录里存的是 `mode`、`toggle` 和用户的补充文字，不是展开后的模板。
  - 「换角色」2026-10-12 按用户反馈（同步换服装时丝袜、鞋子换得不稳）改过，实测数据在 `config.toml` 该节上方的注释里。要点：贴腿的丝袜会被当成身体保留，所以「整套服装」后面要明说「（包括腿上和脚上的穿着）」；「原来的头发整体换掉」去掉了飘到画面边缘的原发梢；但一提腿和脚，图2 是Q版且图1 没画到脚时会整张重画成Q版全身像，越详细（「两条腿各穿着什么」）越明显，所以只用括号里一句。左右不一样的袜子模板解决不了，靠补充要求写明（「右腿黑色、左腿白色过膝袜」实测有效）。测这个模式的固定几组图：底图光腿/过膝袜/一黑一白 × 图2 光腿/粉色过膝袜/一黑一白/Q版，至少 2 个种子，看鞋袜、构图、发梢残留三项。
- 人物设定（网页选项栏的「人物」按钮 → 输入框上方的面板；`cli.py --people`）：每个人物一张卡片，填身体数据（性别年龄、身高、头身比、体型）、外貌、服装和动作（姿势、朝向、视线、左右手、腿部、表情），外加整个画面的取景、镜头角度、人物互动。提交时 `pipeline.people_text` 按 `config.toml` `[people]` 渲染成一段描述，接在提示词（或模式模板）后面。规则写在 `[people]` 的注释里：每个字段的 `render` 句式、同 `group` 的字段连成一句。网页表单也按这份配置生成，增删字段只改配置。人物数据存在请求和历史记录的 `people` 里（结构化的，不是渲染后的文字），校验在 `jobs.make_people`。空白卡片也算一个人。网页面板里能预览渲染结果（`/api/people-preview`，和生成用同一段代码）。
  - 2026-10-11 实测（1K、同种子对比普通一句话提示词）：两组都没画错人数、没把「人物1」之类字样画进图里，人物设定组的身高和头身比差异更明显、不同种子之间更一致，动作吻合度略高。用「长相参照」从参考图搬人物换动作，长相和服装都保持住了。三个人一字排开时，构图会比普通提示词更像整齐站成一排。
  - 同一个人的字段别互相矛盾（如姿势写「蹲下」、腿部写「单膝跪地」），模型只会照做其中一个。左右手要符合空间关系：两人并排面朝镜头时，左边那人靠近右边那人的是她的左手。
  - 写法（2026-10-11 改）：照官方改写模型 system_prompt 的原则，用旁观者口吻只写看得见的东西（「位于画面左侧，是一位成年女性。约7头身，身材苗条。……面带微笑。」），不写「不多不少」「结构正确、五根手指」这类指令句，`anatomy` 留空；身高不写厘米数，由 `pipeline.compare_heights` 按 `height_compare` 换成「人物2站直时头顶大约到人物1的腰部」。改写前的配置是：count「画面中一共有N个人物，不多不少。」，`取景：`/`表情：` 这类标签式句子，`身高约N厘米`，`anatomy` =「每个人物的身体比例协调、结构正确，四肢和手指数量正常，关节弯曲方向自然。每只手都是五根手指，手指的长短、指节和弯曲方向自然；手和脚的大小、形状与身体相称。」。新旧写法实测（1K、40 步，双人栈桥和三人动漫海边各 4 个种子）：手脚质量和动作吻合度打平，人数全对，高矮差距同样明显，所以换成更干净的官方写法。
  - 手脚（2026-10-11 实测：双人光脚坐栈桥、十指相扣、剪刀手、拿甜筒，同种子对比）：手基本都没问题，出错的主要是交叉、叠在一起的脚。影响最大的是步数和分辨率，不是提示词：1K 25 步（约 30 秒一张）会出现脚趾融合、三只脚叠在一起；同构图换 40 步（约 60 秒）这些都消失了；2K 40 步（约 280 秒）手指、脚趾、趾甲都清清楚楚，画面也更自然。当时 `anatomy` 里的手脚句子只让糊掉的脚稍好一点。负面提示词 + CFG 3（面板上的「严格手脚」，`[people] negative`/`strict_cfg`，逻辑在 `pipeline.guidance`）让脚全部正常，但主要是因为双脚都规规矩矩下垂了；代价是慢一倍、画面更饱和、人脸更像图库照、种子之间长相差异变小，也不是官方用法，所以默认不勾。注意 `cfg = 1` 时负面提示词完全不生效。
- 官方资料（2026-10-11 查过；调参、加功能前先看这些）：
  - 抱抱脸模型卡（https://huggingface.co/Qwen/Qwen-Image-2.1 ，魔搭上的 README 和它一样；本机在 `reference/Qwen-Image-2.1/README.md` 有一份副本，不进仓库）：示例全是 40 步、2048×2048，不传 CFG 和负面提示词；原生分辨率 2K。
  - GitHub（https://github.com/QwenLM/Qwen-Image-2.1 ）：写明默认 CFG=1；还有 8 步的 Turbo 版；提示词改写模型 `Qwen/Qwen-Image-2.1-PE-T2I` / `-PE-I2I`（Qwen3.5-VL 9B，把短需求扩写成约 400–500 词的英文描述），见仓库 `prompt_rewrite/`，其输出的 `negative_prompt` 永远为空。
  - 改写模型的 `system_prompt.txt`：用旁观者口吻描述成品画面，禁止「make sure」这类指令句和「masterpiece / highly detailed」这类质量词；人物只写看得见的东西（体型、姿态、视线、表情、发型、肤色、每件衣服的颜色和材质），年龄写人生阶段不写岁数；没有任何关于手指、四肢数量的句子。
  - 官方 Demo（https://huggingface.co/spaces/Qwen/Qwen-Image-2.1 的 app.py）：`TRUE_CFG_SCALE = 1.0`，负面提示词默认为空，默认开启提示词扩写（Prompt Extend）。
  - 千问博客（https://qwen.ai/blog?id=qwen-image-2.1 ）：没讲参数；介绍了圈选、涂抹、独立掩码三种局部编辑方式（可用来单独重画坏掉的手脚），以及用人物三视图生成分镜、用 6 张人像合成多人合照。
  - 阿里云百炼的 API 文档（https://help.aliyun.com/zh/model-studio/qwen-image-api ）管的是线上的 qwen-image-2.0、3.0，不是这个开源 2.1：`negative_prompt` 的示例值是「低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲。」，默认 2048×2048，`prompt_extend` 默认开。
- 反推提示词（参考图缩略图上的「词」、生成结果上的「反推」、右上角菜单「从图片反推提示词…」；`cli.py --describe`）：用生成时本来就要加载的文本编码器（Qwen3-VL 8B Instruct，heretic 版带 `lm_head` 和视觉部分）跑 ComfyUI 的 `TextGenerate` 节点看图写字，`PreviewAny` 节点把文字带回历史结果，`jobs.collect_text` 取出。系统提示词在 `config.toml` `[describe] system`，是照官方 PE-T2I 的 system_prompt 改的看图版（英文、旁观者口吻、约 400–500 词、先总述再按方位走一遍画面、光线单独一句、最后一句总结构图），另加了逐个写清人物四肢和手的动作、彼此高矮。对话格式在 `pipeline.build_describe` 里手工拼（`use_default_template=False`），因为 `TextGenerate` 默认会套上 Qwen-Image 为编码准备的系统提示词。图片先缩到约 `megapixels` 百万像素再送进去（不缩的话上限是 1280 万像素）。结果写进输入框时切到「自由描述」，比例设成原图最接近的比例（`pipeline.nearest_aspect`）。反推任务是 `jobs.DescribeJob`，不进历史记录，`/api/jobs` 列表里也不出现（网页只轮询自己的那个 id）。
  - 速度：文本编码器 16.7GB 放不进 `--reserve-vram 5` 之后的约 11.7GB，每写一个词元都要从内存搬一部分权重，实测约 2.5 词元/秒（显卡功耗只有 90W 左右，在等传输），一段描述要 2–3 分钟。
  - 流程（`jobs.run_describe`）：⓪ `check = true` 时先用选择题单独问镜头（俯视/平视/仰视）和每个人的姿势（站/坐椅/坐地/跪坐/侧坐/蹲/躺……），`pipeline.check_answers` 把字母换回选项原文，作为「已核实的事实」放进下一遍；① 只看整图写整体描述；② `zoom > 0` 时再看「整图 + 四块重叠的放大局部」只列细节清单（眼睛、痣、发型、发饰、首饰、花纹、每只手在做什么、鞋袜）；③ `[expand] enabled`（默认关）且模型文件在时，把「描述 + 细节清单」（`details_brief`）交给官方提示词改写模型 PE-T2I，否则用 `details_join` 把细节接在描述后面。每一步的结果都过一遍 `[describe] drop` 正则，整句删掉签名、水印、@账号、网址。一张图总共约 4–5 分钟。
  - 选择题核实：在长描述里顺带写姿势和视角，8B 模型会把跪坐写成坐着、俯拍写成平视；单独自由提问也一样错；改成选择题后 4 张测试图的镜头和姿势全对。问「画进了多少身体」选择题也错 3/4，而且错成「腰部以上」会让重画把腿裁掉，所以不问。系统提示词的开头也改成固定三句：媒介风格主体（明显是知名角色就写名字）、镜头、画风（线条/上色/配色明暗），人物要写具体发型（双马尾/马尾/丸子头……）。用户确认：重画出来和大多数画师画的樱花初音一样，就算认出来了，不必非写出名字。改完后（选择题核实 + 三句开头）初音图的双马尾、视角、明亮粉彩画风、跪坐/腾空姿势都对上了，模型也会直接写出「Hatsune Miku」。还没解决：相近颜色会摇摆（青绿发写成蓝）、头戴式耳机认成猫耳；俯拍画画那张重画时画面角落多出一只不属于任何人的手（描述里写了「左手掀起画纸一角」），出现过一次。
  - 为什么这样（2026-10-11 用 pixiv 人画作品和用户的初音图实测）：模型不听「别写签名」，签名会被原样画进重画结果，所以用程序删；只看 1MP 的整图会漏掉泪痣、手环、刀柄宝石、裙子绑带这类细节，给它看放大局部就能写出来；但整图和局部放在同一遍里，8B 模型会把局部当成画面的一部分来写（「左下角露出一截裙摆和一条腿」），重画时凭空多出一条腿，所以拆成两遍、局部那遍只列清单。还剩下的毛病：颜色偶尔判断错（白披风写成深紫）、把藏在袖子里的手编成在做某个动作。画面里的字它读得不错（白骑士图上挤在一起的毛笔字「勇者」读对了）；曾加过「看不准的字别猜、只描述字形」，结果它连读对的字也不写了，重画出一个乱写的字，已撤掉，字照官方做法原样抄。用户的要求：画风不必死守原图，把粗糙的同人画重画得更精致是可以的。
  - PE-T2I（`[expand]`）：从魔搭下载到 `models/pe_t2i/`（官方 `system_prompt.txt` 原样使用；4 个权重分片合并后已按用户要求移到回收站，要重新合并得先重新下载），`scripts/merge_pe_t2i.py` 把 4 个分片原样合并成 `models/text_encoders/qwen_image_2.1_pe_t2i_bf16.safetensors`（不压缩，用户要求原精度 BF16 + 分块加载）。ComfyUI 原生认 Qwen3.5 9B，`CLIPLoader` 的 `type` 不影响它。官方要求开启思考（助手回复以 `<think>\n` 开头），采样参数照官方 t2i 推荐；输出是思考 + 一行 JSON，`pipeline.parse_expand` 取 `rewritten_prompt`，取不到就退回第一步的结果并在 `note` 里说明。实测（2026-10-11，初音画画图）：BF16 分块加载约 1.8 词元/秒，思考 2000+ 词元，一张 28 分钟；结果 1189 词，凭空加了挂轴书法和庭院，动漫风变半写实，青色双马尾变蓝色盘发——它的任务是把短需求补全成完整画面，和反推要的忠实还原相反，所以默认关。这台机器的沙箱网络代理很慢（约 90KB/s），下载大文件要在沙箱外跑 curl。
- 清晰度 / 超分（选项栏「清晰度」：原图 = 生图模型直接出的图，超分到 2048/3072/4096 长边；`[upscale]`，`pipeline.add_upscale`）：超分模型是 `models/upscale_models/4x-AnimeSharp.pth`（Kim2091/AnimeSharp，从抱抱脸下载；`extra_model_paths.yaml` 加了 `upscale_models`）。只超分：模型放大 4 倍再缩到目标，几秒，线条和轮廓明显更锐，但不会凭空多出细节。勾「重绘细节」：放大后缩到不超过 `repaint_max_side`（2048，模型原生分辨率）用生图模型按原提示词以 `repaint_denoise`（0.35）重画，再放大到目标；2026-10-11 实测房间场景背景里的屏幕图标、墙板线条明显更清楚，但脸会被改一点（眼睛形状、表情），多花约 4 分钟。注意：VAE 解码出来总是 RGBA（模型原生支持透明），超分模型和图像合成只吃 RGB，所以先用 `SplitImageWithAlpha` 拆掉透明通道，透明背景时最后 `JoinImageWithAlpha` 合回去（它会自动把透明通道缩放到最终尺寸）；重绘后再次解码也要再拆一次。
- 扩图（模式「扩图」，`[modes.outpaint]`）：选目标比例（不能是「自动」）和原图位置（居中/靠左/靠右/靠上/靠下），`pipeline.outpaint_layout` 算出往哪边扩，填进模板的 `{direction}`。官方编辑指令改写模型 PE-I2I 的 system_prompt 规定：往外扩画布要明确写成 outpainting，输出用新比例——正好对应「有参考图 + 选了比例」的新画布生成方式，所以默认 `method = "instruction"`（整张重新生成）。另有 `method = "inpaint"`（蒙版重绘：原图贴进画布只重画四周，最后贴回原图像素）。2026-10-11 用 3 张初音图实测，instruction 3 张都无缝、人物和文字保留完好，还会把原图自带的白色卡片边框去掉、自然接上背景；inpaint 有看得见的竖接缝和色差，原图的白边也被原样留成两条白带。
- 四视图（模式「四视图」，`[modes.turnaround]`；用户先要三视图，后来改成四视图）：图1 的角色画成白底上并排的正面、朝左侧面、背面、朝右侧面四个全身像，新画布（默认 4:3；勾「加脸部特写」= 千问博客样式，最左边加一张脸部特写，16:9）。写法和比例都照官方 PE-I2I 的 system_prompt（它专门有一节「三视图/多宫格」：比例按 每格宽×列数 : 每格高×行数 算，三个站立全身像用 1:1）。2026-10-12 用 pixiv 勇者、Q版小女孩和初音文件夹 3 张实测（1K，一张约 77 秒；2K 约 5 分钟，眼睛线条手指清楚得多）：服装配饰、跪坐改站姿、只有上半身时补全下半身都好，原图的字和签名不画进来，强烈侧光下被阴影压暗的半边头发也正确画回本来的颜色（用户确认）。已知问题和处理见 `config.toml` 该节注释：Q版比例可能被拉长（补充里写「Q版，约2.5头身」）；左右不对称的服装在侧面/背面容易左右搞反（加了「转身一圈左右不变」那句后好一些，仍不稳）；脸部特写 + 四个全身像要明确写「左边约四分之一宽 / 右边约四分之三宽」，否则模型不画特写。补充要求会被严格执行，只写确定的事实——曾把侧光误判成分色头发写进补充，结果真画成了分色头发。
- 画风统一（选项栏「画风」：不统一 / 照图N；`cli.py --style N`；`[style]`）：用户反馈「人物照参考图画、背景却是另一种画风」（白底立绘当长相参考，背景让模型自己补，背景会变成偏写实的楼房）。做法：生成前先用文本编码器看图N，只写画法（`pipeline.build_style`，`jobs.style_text`，按图缓存在 `input/studio/<图>.png.style.json`），再把「整张画面统一用这种画风：……人物、背景……用完全相同的画法」放进提示词（`pipeline.full_prompt`），分析结果存进历史的 `style_text`，「再来一次」直接沿用。实测只是改善（配色明暗更接近），最有效的是选一张**带背景的插画**当画风来源；只写「照图1的画风」几乎没用；用编辑把已有图的背景重画会糊。详细数据在 `config.toml [style]` 的注释里。
- 画板（网页右上角菜单「画草图…」= 空白画布；参考图缩略图上的「标」= 在这张图上画；输入框上方「打开画板修改」= 接着上次画）：前端在 `web/index.html` 的「画板」一节，画线/圈、涂抹、橡皮、撤销，颜色取 `config.toml [board]`。下面一栏列出用到的颜色，每种写一句含义（草图）或要怎么改（圈选/涂抹），作为请求的 `marks`（`[{color, kind, text}]`，`jobs.make_marks` 校验）提交并存进历史；`pipeline.marks_text` 按模式的 `mark_line` 把它们拼进模板的 `{marks}`。点「完成」后画好的图上传成参考图放到图1，并自动切到对应模式。
  - 圈选/涂抹编辑（`[modes.mark]`）：照官方博客的局部编辑写法（不同颜色的圈 +「把红色圈的……」，白色涂抹 +「在白色标注的区域……」，原图 + 黑底掩码两张图）。2026-10-12 实测圈选三处同时改、涂抹加猫、独立掩码加猫都是 2/2，圈线自动去掉，圈外不变。画板里勾「标注单独成图」= 独立掩码（图1 原图、图2 黑底标注图，模式分支自动勾上）。
  - 草图生图（`[modes.sketch]`，`sketch_lines = true`）：**模型只看黑线稿**。彩色线条直接给模型（写颜色含义、换新画布、另给黑底颜色标注图都试过）都会被照抄成彩色简笔画、火柴人还是火柴人、背景留白；所以 `jobs.sketch_lines` 把图1 转成黑线稿（涂满的色块只留轮廓）存成 `req['sketch_line']`，生成时代替图1，并算出每种颜色的笔画在画面哪里（`position_phrase`），含义写成「画面中间偏右、从上部延伸到下部的线条是……」。人物动作要写进含义（「抬起一只手臂指向天空」），只靠火柴人的手臂方向画不出来，模板里再怎么强调也没用。约 1/3 会留一部分白底，在输入框写清场景和天空会好些。可以和参考图一起用：含义写「<image2>的角色……」，长相照参考图、姿势照草图（实测樱初音 2/2）。测试草图和对比在 `config.toml` 两个模式的注释里。
- 参考图在模型里按顺序标成 `<image1>`、`<image2>`…（见 `.runtime/ComfyUI/comfy/text_encoders/qwen_image21.py`），提示词里写 `<image1>` 最准确，写「图1」通常也能理解。
- 节点图的节点 id 是可读名字（`unet`、`encode`、`sample`…），`_meta.title` 会显示为网页进度条上的阶段名。所有 `type == "output"` 的图片输出都会被收集为结果。
- 查节点有哪些参数：`logs/object-info.json`（ComfyUI `/object_info` 快照）或 `.runtime/ComfyUI/comfy_extras/nodes_qwen.py`。不要改 `.runtime/ComfyUI/` 里的代码。
- 显卡是 16GB 的 RTX 4070 Ti SUPER，1K「换角色」（两张参考图、25 步）一张约 52–60 秒，首次加载模型另需一两分钟。文生图（无参考图）实测：1K 25 步约 30 秒，1K 40 步约 60 秒，2K 40 步约 280 秒（官方默认就是 2K 40 步）。
- 已知问题：显存超额 → 变慢或卡死（2026-10-10 查明并缓解）。
  - 现象：轻则一张 1K 图要 90–680 秒（显卡 100% 但功耗只有 80–140W，在换页不是在算）；重则卡死在下一张的文本编码器加载——日志最后一行是 `QwenImage21TEModel_ prepared for dynamic VRAM loading`，8189 端口先超时后拒绝连接，进程 15 分钟以上都不会自己恢复。
  - 原因：文本编码器 16.7GB、DiT 13.5GB 都靠 dynamic VRAM（comfy_aimdo）按需进出显存，默认会把 16GB 占到只剩几百 MB。桌面程序（Chrome、微信、QQ、Steam、Claude 等平时占 1–2GB，会涨落）一抢，总量超过显存，WDDM 就来回换页。卡死时抓到的原生栈：模型线程在 aimdo 的显存分配钩子里调 `cuCtxSynchronize`，一直空转不返回；网页服务线程卡在 torch 的显存分配锁上，所以端口没响应。
  - 缓解：`scripts/launch.py` 给 ComfyUI 加了 `--reserve-vram 5 --vram-headroom 1`。前者限制 ComfyUI 自己最多用约 11.7GB；后者在算上其他程序的占用后，再保持 1GB 空闲。实测另开进程额外占 2GB 显存、每 20 秒放掉再抢回：默认参数 8 张里 3 张变慢到 88–149 秒；加了这两个参数后同条件 8 张都是 52–54 秒，没变慢，反而比默认参数不加压时（约 60 秒）还快。只给 `--reserve-vram 3`（8 张 2 张慢）或只给 `--vram-headroom 1.5`（8 张 1 张慢）都不够。改 `launch.py` 后要先 `PY scripts/launch.py stop`，新参数在下次启动时生效。
  - 测过不好用的：`config.toml` 的 `clip_device = "cpu"`（文本编码器放 CPU）一张 147–155 秒；`--disable-dynamic-vram` 一张图 20 分钟都没理解完提示词；`--lowvram` 在 dynamic VRAM 下不起作用。fp8 文本编码器、`[kv_cache]` 放 CPU、`--novram` 没测完。
  - 万一还卡：`jobs.wait_prompt` 发现进程还在但连续 `STUCK_AFTER`（300）秒不响应，就报「ComfyUI 卡住了」并自动 `launch.py stop`，用户直接重新生成即可；进程真没了才报「任务丢失」。会恢复的慢任务实测最长 91 秒不响应，所以这个阈值别调到 2 分钟以下。网页右上角的状态在这时显示「ComfyUI 没有响应」。手动处理：`PY scripts/launch.py stop`（卡死的进程也能在几秒内关掉）。
