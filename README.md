# 生图轮椅

在自己电脑上用 [Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) 生图、改图的一键工具：一个像聊天一样的网页，放几张参考图、写一句话就出图；后台是一个本项目自己管理的 [ComfyUI](https://github.com/Comfy-Org/ComfyUI)，不用碰节点图。

偏向二次元插画，下面的写法和默认参数都是照官方资料调过、实测对比过的（记录在 `CLAUDE.md` 和 `studio/config.toml` 的注释里）。

> 本仓库只有本项目自己的代码。ComfyUI、Python 环境和模型都由安装脚本从官方来源下载到你的电脑上；**Qwen-Image-2.1 模型的许可证只允许研究、评估等非商业用途**，详见文末「第三方组件和许可证」。

## 功能

- **文生图 / 图片编辑**：有参考图且比例选「自动」时直接在图1 上改；最多 10 张参考图，提示词里用「图1」「<image1>」指代。
- **人物设定**：给每个人物填身高、头身比、体型、姿势、左右手、腿、表情、视线……按官方提示词写法渲染成一段描述，多人画面人数、高矮、动作更稳。
- **预设模式**：换角色（可同步换服装，含丝袜鞋子）、扩图（选目标比例和原图位置）、四视图（正面 / 左侧 / 背面 / 右侧，可加脸部特写）、草图生图、圈选 / 涂抹编辑。
- **画板**：网页里直接画草图，或在参考图上圈出、涂抹要改的地方；不同颜色代表不同的东西 / 不同的修改。
- **反推提示词**：看图写出适合本模型的描述（先核实镜头和姿势，再放大局部补细节，自动去掉签名水印）。
- **画风统一**：先分析某张参考图的画法，要求人物和背景用同一种画风。
- **超分**：原图之外可以选超分到 2048 / 3072 / 4096，可选用生图模型低幅度重绘补细节。
- 透明背景、批量、种子、步数、画质（1K / 1.5K / 2K）、历史记录、一键复用之前的设置。
- 命令行 `studio/cli.py` 和网页同一套逻辑，方便脚本或 AI 助手（Claude Code 等）调用和修改；`CLAUDE.md` 是给 AI 助手看的项目说明。

## 需要什么

- Windows 10 / 11（64 位）
- NVIDIA 显卡，驱动支持 CUDA 13.0（580 版以上）；显存 16GB 左右（在 RTX 4070 Ti SUPER 16GB 上测试），更小也能跑但会慢
- 内存建议 64GB 以上：文本编码器（16.7GB）和生图模型（13.5GB）放不进显存时会在内存和显存之间搬运
- 硬盘剩余 60GB 以上：模型约 32GB，安装时另需约 17GB 临时空间，Python 环境约 6GB
- 网络能访问 GitHub、PyTorch 官网，以及 ModelScope（魔搭）或 Hugging Face 之一（安装脚本会自动测速选最快的下载源）

## 安装

1. 下载本仓库（`git clone https://github.com/sanciyuandehundan/image-wheelchair`，或在 GitHub 页面点 Code → Download ZIP 解压）。放在空间够的盘上，路径里有中文没关系。
2. 双击 **`安装.cmd`**。它会依次：
   - 下载 [uv](https://github.com/astral-sh/uv)（Python 安装管理工具，核对 SHA-256），用它装一份独立的 Python 3.13 和 `.venv` 环境（不影响电脑上已有的 Python）；
   - 下载固定版本的 ComfyUI 源码，装 PyTorch（CUDA 13.0）和依赖；
   - 下载模型并逐个核对 SHA-256，把文本编码器转成 ComfyUI 能读的格式。
   全部下完要一段时间（主要是 32GB 模型）。中途关掉或断网都没关系，**重新双击 `安装.cmd` 会接着装**，已经装好的会跳过。
   - 网络慢或连不上国外网站也能装：每个文件都会在官方源和镜像之间测速，用最快的那个，并核对 SHA-256。用到的镜像有魔搭和 hf-mirror.com（模型）、阿里云 / 上海交大 / 南京大学（PyTorch）、清华 / 阿里云 / 中科大（PyPI），以及 ghfast.top 等 GitHub 代理（只用于有固定 SHA-256 的文件）。
   - 想指定 PyPI 源，可以先设置环境变量 `IMAGE_WHEELCHAIR_PIP_INDEX`（例如公司内网源）再运行 `安装.cmd`。
3. 看到「安装完成」后，双击 **`生图轮椅.cmd`**，浏览器会打开 http://127.0.0.1:8190 。第一次生成要先把模型读进内存，多等一两分钟。

关闭：关掉「生图轮椅」的命令行窗口即可；后台的 ComfyUI 会留着（占显存），在网页右上角菜单里点「关闭 ComfyUI」释放。

## 用法提示

- 速度参考（RTX 4070 Ti SUPER 16GB，默认 50 步）：1K 一张约 75 秒，2K 约 5 分钟。画质选 2K 手脚、脸和细节明显更好。
- 网页底部的灰字会随模式提示怎么用；各模式的写法、实测效果和已知问题写在 `studio/config.toml` 的注释里。
- 改默认参数（步数、模板、颜色含义……）直接改 `studio/config.toml`，保存后下一次生成就生效。
- 命令行示例见 `studio/cli.py` 开头的说明，例如：
  ```
  .venv\Scripts\python.exe -X utf8 -s studio\cli.py "海边日落，日系动漫插画" --aspect 16:9 --quality 2k
  ```

## 第三方组件和许可证

本仓库只包含本项目自己写的代码（Apache-2.0，见 `LICENSE`）。下面这些都**不在本仓库里**，由安装脚本在你的电脑上直接从官方来源下载，使用时请遵守它们各自的许可证：

| 组件 | 来源 | 许可证 |
|---|---|---|
| Qwen-Image-2.1 生图模型和 VAE（ComfyUI 打包版） | [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1)，文件来自 [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) / [魔搭](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1) | **[Qwen RESEARCH LICENSE](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)（仅限研究、评估等非商业用途；商用要另向阿里申请授权）** |
| 文本编码器 Qwen3-VL-8B-Instruct Heretic 版 | [pottokao/Qwen-Image-2.1-Text-Encoder-Heretic](https://huggingface.co/pottokao/Qwen-Image-2.1-Text-Encoder-Heretic)，基于 [Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) | Apache-2.0 |
| 超分模型 4x-AnimeSharp | [Kim2091/AnimeSharp](https://huggingface.co/Kim2091/AnimeSharp) | **CC BY-NC-SA 4.0（仅限非商业用途）** |
| ComfyUI | [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 |
| PyTorch | [pytorch.org](https://pytorch.org) | BSD-3-Clause |
| uv / Python | [astral-sh/uv](https://github.com/astral-sh/uv)、[python.org](https://www.python.org) | MIT / Apache-2.0、PSF |

说明：

- **生图模型（Qwen-Image-2.1）只许非商业使用**，超分模型同样是非商业许可；安装脚本下载模型前会列出这些许可证，输入 Y 同意后才会下载。本项目代码本身是 Apache-2.0，但用它跑这些模型时，要遵守模型的许可证。
- 文本编码器用的是社区做的 Heretic（去除拒答）版本，不是阿里官方发布的权重，与阿里 / 通义千问无关。安装时只是把它的 4 个分片按官方 ComfyUI 版的张量结构重新打包成一个文件，权重字节不做任何修改。
- 本项目与阿里巴巴、通义千问、Comfy-Org、上述模型作者均无关联。生成内容请遵守当地法律和各模型的使用条款，责任由使用者自负。
- 超分模型是非商业许可：要商用的话，请在 `studio/config.toml` 的 `[upscale]` 换成允许商用的超分模型，或只用「原图」。
