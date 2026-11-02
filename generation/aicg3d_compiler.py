"""AICG3D 原装无限视频分段提示词编译器与导演技能库接入模块。

完整同步自 comfyui-AICG3D 原项目：
1. 《无限视频生成提示词模板V1.MD》（11 大章节 + 输出前 13 项自查规范）
2. 《H3 图生视频自动提示词·多分镜连贯系统》（同空间不瞬移、段尾未完成动作钩子咬合下段开头）
3. `aicg3d/prompt_split.py`（AICG3DPromptSegmentSplit 原装分段解析器）
4. `skills/` + `prompt_guides/`（21 个原装导演 Skills 与 H3 场景方案）
5. `workflows/13.AICG3D_MiniMax H3 无限长视频生成工作流1.2.json` 原装连续剧本
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
import urllib.error
import urllib.request

WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"
TEMPLATE_MD_PATH = WORKFLOWS_DIR / "aicg3d_prompt_template_v1.md"
SKILLS_CATALOG_PATH = WORKFLOWS_DIR / "aicg3d_skills_catalog.json"

DEFAULT_REF_ALIGN_LINE = (
    "For the target video, at 0.00 seconds into the target video, "
    "<Picture 1> (from [Shot 1]) is fully referenced."
)

# 原项目《无限视频生成提示词模板V1.MD》第 2.1 节严禁出现在最终 H3 提示词正文中的元指令词
FORBIDDEN_META_PATTERNS = (
    r"No on-screen text\.?",
    r"Do not speak\.?",
    r"No watermark\.?",
    r"Keep consistent\.?",
    r"(?:画面)?无字幕[、，,。；;\s]*",
    r"(?:不加|不要|禁止)字幕[、，,。；;\s]*",
    r"(?:无|不要|禁止)(?:文字|水印|Logo)[、，,。；;\s]*",
    r"(?:不要|禁止)(?:说话|说出提示词|复述提示词)[、，,。；;\s]*",
    r"保持沉默[、，,。；;\s]*",
)

# ---------------------------------------------------------------------------
# 原项目 aicg3d/prompt_split.py 分段解析器（原装逻辑）
# ---------------------------------------------------------------------------
_INVISIBLE = (
    "\ufeff", "\u200b", "\u200c", "\u200d", "\u2060", "\u00a0",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
)

_HEADER_RE = re.compile(
    r"^[\s>*#\-—•·]*"
    r"(?P<marker>"
    r"第\s*(?:[0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*[段幕镜场景节]"
    r"|(?:镜头|分镜|场景|画面|shot|Shot|SHOT)\s*[0-9]{1,3}"
    r"|[0-9]{1,3}\s*[.、)）](?=\s|$)"
    r")"
    r"(?P<tail>.*)$"
)
_LABEL_TAIL_CHARS = set(" \t：:、,。.-—–)）]】")
_LABEL_TAIL_PREFIXES = ("（", "(", "【", "[")
_BRACKET_HEADER_RE = re.compile(
    r"^[\s>*#\-—•·]*(?P<label>[【\[［][^】\]］]*[】\]］])(?P<rest>.*)$"
)
_SEGMENT_MARK_RE = re.compile(
    r"第\s*(?:[0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*[段幕镜场景节]"
    r"|(?:镜头|分镜|场景|画面|shot|Shot|SHOT)\s*[0-9]{1,3}"
)
_SEC_LABEL_RE = re.compile(
    r"(?<![0-9.])(?P<sec>[0-9]+(?:\.[0-9]+)?)\s*(?:秒|s|S|sec|Sec|SEC)(?![0-9A-Za-z])"
)
_SEC_BRACKET_RE = re.compile(
    r"[（(\[【]\s*(?P<sec>[0-9]+(?:\.[0-9]+)?)\s*(?:秒|s|S|sec|Sec|SEC)\s*[)）\]】]"
)
_SEC_BARE_RE = re.compile(r"(?<![0-9.])(?P<sec>[0-9]+(?:\.[0-9]+)?)\s*秒")


def normalize_text(text: str) -> str:
    value = str(text or "")
    for marker in _INVISIBLE:
        value = value.replace(marker, "")
    value = value.replace("\u2028", "\n").replace("\u2029", "\n")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value


def _strip_leading_separators(text: str) -> str:
    return re.sub(r"^[\s：:、,，.。\-—–)）|\]】]+", "", str(text or "")).strip()


def _read_seconds(line: str) -> float | None:
    text = str(line or "")
    match = _SEC_BRACKET_RE.search(text) or _SEC_BARE_RE.search(text)
    if match is None:
        return None
    try:
        return float(match.group("sec"))
    except (TypeError, ValueError):
        return None


def _line_header(line: str) -> tuple[bool, str, float | None]:
    text = str(line or "").strip()
    bracket = _BRACKET_HEADER_RE.match(text)
    if bracket is not None and _SEGMENT_MARK_RE.search(bracket.group("label") or ""):
        label = bracket.group("label") or ""
        rest = _strip_leading_separators(bracket.group("rest") or "")
        found = _SEC_BRACKET_RE.search(label) or _SEC_LABEL_RE.search(label)
        seconds = float(found.group("sec")) if found else None
        return True, rest, seconds
    match = _HEADER_RE.match(text)
    if match is None:
        return False, "", None
    tail = match.group("tail") or ""
    if tail and not (tail[0] in _LABEL_TAIL_CHARS or tail.startswith(_LABEL_TAIL_PREFIXES)):
        return False, "", None
    return True, _strip_leading_separators(tail), _read_seconds(text)


def parse_aicg3d_prompt_text(
    text: str,
    default_seconds: float = 5.0,
) -> tuple[list[str], list[float]]:
    """按 comfyui-AICG3D 的 AICG3DPromptSegmentSplit 规则切分整篇分段剧本。"""
    raw = normalize_text(text).strip()
    if not raw:
        return [], []
    lines = raw.split("\n")
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        is_hdr, rest, sec = _line_header(line)
        if is_hdr:
            if current is not None:
                blocks.append(current)
            current = {"rest": rest, "seconds": sec, "body": []}
        else:
            if current is None:
                if line.strip():
                    current = {"rest": "", "seconds": None, "body": [line]}
            else:
                current["body"].append(line)
    if current is not None:
        blocks.append(current)

    prompts: list[str] = []
    seconds: list[float] = []
    for block in blocks:
        parts = ([block["rest"]] if block["rest"] else []) + list(block["body"])
        body = "\n".join(parts).strip()
        if not body:
            continue
        sec = block["seconds"] if block["seconds"] is not None else default_seconds
        prompts.append(body)
        seconds.append(float(sec))
    return prompts, seconds


def sanitize_aicg3d_segment(segment_text: str, has_ref_image: bool = True) -> str:
    """执行《无限视频生成提示词模板V1.MD》第 2 节与第 10 节的 13 项自查清洗。"""
    cleaned = normalize_text(segment_text).strip()
    # 移除代码块围栏或段头残留
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    is_hdr, rest, _ = _line_header(cleaned.split("\n", 1)[0])
    if is_hdr:
        tail = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = (rest + "\n" + tail).strip()

    # 清除第 2.1 节禁止的元指令词（防止模型把“无字幕/不要说话/No on-screen text”当台词念出来）
    for pat in FORBIDDEN_META_PATTERNS:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)

    # 规范化说话人标签 S1 -> (S1)
    cleaned = re.sub(
        r"(?<![A-Za-z0-9_(<（])S([1-9]|1\d|20)(?![A-Za-z0-9_)>）])",
        lambda m: f"(S{m.group(1)})",
        cleaned,
    )

    # 确保包含 H3 三个标准字段，并按 13 号工作流原装规范将 non_diegetic_music 统一锁为 N/A（防止每5秒BGM硬切跳变）
    if "integrated_multimodal_description:" not in cleaned:
        cleaned = f"integrated_multimodal_description: [Shot 1] {cleaned}"
    if "overall_soundscape:" not in cleaned:
        cleaned += "\n\noverall_soundscape: 自然真实的现场环境底噪与细微动作衣料摩擦声。"
    if "non_diegetic_music:" in cleaned:
        cleaned = re.sub(r"non_diegetic_music:\s*[^\n]+", "non_diegetic_music: N/A", cleaned)
    else:
        cleaned += "\n\nnon_diegetic_music: N/A"

    # 确保参考图对齐头存在
    if has_ref_image and "Picture 1" not in cleaned.split("integrated_multimodal_description:", 1)[0]:
        cleaned = f"{DEFAULT_REF_ALIGN_LINE}\n\n{cleaned}"

    return cleaned.strip()


def format_full_aicg3d_script(segments: list[str], segment_seconds: int = 5) -> str:
    """将分段数组组装为可直接粘贴到 ComfyUI AICG3DPromptSegmentSplit 的纯文本复制区。"""
    chunks = []
    for idx, seg in enumerate(segments, start=1):
        chunks.append(f"【第{idx}段｜{segment_seconds}秒】\n{seg.strip()}")
    return "\n\n".join(chunks)


def segments_to_acts(
    segments: list[str],
    act_titles: list[str] | None = None,
    shot_summaries: list[str] | None = None,
    segment_seconds: int = 5,
) -> list[dict[str, Any]]:
    """将 12 段提示词映射到导演台 4 幕 × 3 镜结构（保持 UI 兼容）。"""
    default_titles = [
        "第一幕 · 建立空间与初始状态（0-15s）",
        "第二幕 · 核心事件与动作推进（15-30s）",
        "第三幕 · 关键转折与情绪高潮（30-45s）",
        "第四幕 · 动作落点与意境收束（45-60s）",
    ]
    titles = act_titles if act_titles and len(act_titles) >= 4 else default_titles
    acts: list[dict[str, Any]] = []
    shots_per_act = 3
    for act_idx in range(4):
        act_shots = []
        for s_idx in range(shots_per_act):
            global_idx = act_idx * shots_per_act + s_idx
            if global_idx >= len(segments):
                break
            seg_text = segments[global_idx]
            summary = ""
            if shot_summaries and global_idx < len(shot_summaries):
                summary = shot_summaries[global_idx]
            else:
                summary = _extract_shot_summary(seg_text, global_idx + 1)
            act_shots.append({
                "index": global_idx + 1,
                "duration": segment_seconds,
                "summary": summary,
                "prompt": seg_text,
            })
        if act_shots:
            acts.append({
                "actIndex": act_idx + 1,
                "title": titles[act_idx] if act_idx < len(titles) else f"第{act_idx + 1}幕",
                "shots": act_shots,
            })
    return acts


def _extract_shot_summary(seg_text: str, index: int) -> str:
    """从 H3 段落提示词中提取简短可读的分镜摘要（含对白或关键动作）。"""
    dlg = re.search(r"<d>\[[A-Za-z]+\]\s*([^<]+)</d>", seg_text)
    timeline_lines = re.findall(r"(?:0\.0|0)[～~-][0-9.]+秒[：:]\s*([^\n。]+)", seg_text)
    hook_lines = re.findall(r"[34]\.[0-9][～~-]5(?:\.0)?秒[：:]\s*([^\n。]+)", seg_text)
    parts = []
    if timeline_lines:
        parts.append(timeline_lines[0][:26])
    if dlg:
        parts.append(f"台词：「{dlg.group(1).strip()[:22]}」")
    if hook_lines:
        parts.append(f"段尾钩子：{hook_lines[-1][:24]}")
    if parts:
        return " ｜ ".join(parts)
    desc = re.search(r"integrated_multimodal_description:\s*(?:\[Shot 1\]\s*)?([^\n]+)", seg_text)
    return (desc.group(1)[:52] + "…") if desc else f"第 {index} 段连续镜头（5秒）"


# ---------------------------------------------------------------------------
# 原项目内置预设剧本库（含《无限视频生成提示词模板V1.MD》编译的 60s 同空间一镜到底剧本 + 13号工作流原装剧本）
# ---------------------------------------------------------------------------
CAMPFIRE_CONTINUOUS_12_SEGMENTS = [
    # 第 1 段（0-5s）：建立空间与人物初始姿态，段尾留“半蹲抬头凝视海面、嘴唇微启吸气”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，数字电影机浅景深成像，黄昏海滩礁石营地同一空间。中景平视机位，<Picture 1> 的年轻男性身穿深灰色户外冲锋衣，半蹲在礁石滩一簇跳跃的暖橙色篝火右侧，背景是深蓝色暮色海湾与湿润礁石，右下方暖橙火光与左上方冷蓝海暮光均匀照亮人物面部轮廓。
0.0～1.6秒：男性半蹲在篝火旁，右手将一根枯木枝轻轻添进火堆，火星随之微微跃起，海风轻拂他的额前短发。
1.6～3.6秒：男性拍掉双手手心的细碎木屑，双手自然搭在右膝上，镜头做极小幅度的平稳推进。
3.6～5.0秒：男性缓缓抬起头望向镜头右前方的海面方向，肩膀轻微起伏完成吸气，画面停在他半蹲凝视、准备开口说话的稳定瞬间。

overall_soundscape: 篝火木柴持续噼啪燃烧声，傍晚海浪轻拍礁石的规律低鸣，手掌拍落木屑的细微摩擦声。

non_diegetic_music: 低沉克制的大提琴环境铺底缓慢进入，音量轻柔。""",

    # 第 2 段（5-10s）：直接承接段1半蹲抬头姿态 -> S1台词 -> 段尾留“右手伸向身旁金属工具箱，指尖刚触碰锁扣”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视固定凝视机位，<Picture 1> 的年轻男性仍穿着深灰色户外冲锋衣半蹲在篝火右侧原位，右下方暖橙色篝火主光与背后深蓝海面环境光与前序画面完全一致，面部五官清晰锐利。
0.0～0.8秒：男性从海面方向收回目光看向镜头前方，眼神沉稳，胸口轻微吸气进入说话状态。
0.8～3.6秒：男性 (S1) says: <d>[Chinese]流落到这座荒岛第三天，篝火终于稳住了。</d>
3.6～5.0秒：男性闭上嘴唇，视线向下移向身旁平整礁石上的银灰色金属工具箱，右手缓慢伸出，画面停在右手指尖刚触碰到工具箱金属锁扣的瞬间。

overall_soundscape: 篝火持续温暖噼啪声，海风拂过冲锋衣领口的轻微声响，男性清晰的呼吸声。

non_diegetic_music: 低沉大提琴铺底平稳延续。""",

    # 第 3 段（10-15s）：承接段2指尖触碰锁扣 -> 开箱取出改装终端 -> 段尾留“双手捧终端低头注视亮起的蓝光、右手拇指悬在按键上方”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视略带俯角机位，<Picture 1> 的年轻男性半蹲在篝火与礁石旁，右手指尖正搭在金属工具箱锁扣上，暖橙色篝火与冷蓝海暮光色温曝光稳定。
0.0～1.5秒：男性右手拇指向上拨开金属锁扣，顺势轻轻掀开工具箱顶盖。
1.5～3.6秒：男性双手从箱内稳稳取出一台改装过的便携式太阳能信号终端，将其平托在胸前掌心。
3.6～5.0秒：终端屏幕亮起一抹柔和的冰蓝色微光，映在男性下颌与鼻梁上，他微微低头凝视屏幕，右手拇指悬停在终端侧边按键上方准备按下。

overall_soundscape: 金属锁扣清脆弹开声，手指托起电子设备的轻微摩擦声，篝火噼啪声与海浪声持续。

non_diegetic_music: 柔和的电子脉冲弦乐轻声汇入大提琴铺底。""",

    # 第 4 段（15-20s）：承接段3双手捧终端低头姿态 -> 按键并说S1台词 -> 段尾留“右手食指与拇指捏住顶部调频旋钮准备旋转”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视固定机位，<Picture 1> 的年轻男性半蹲在篝火旁，双手在胸前捧着亮起冰蓝微光的信号终端，暖橙火光与屏幕冷光在面部形成冷暖交融层次。
0.0～0.8秒：男性右手拇指按下侧边按键，眼神专注地抬眼平视前方，轻微吸气。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]电量只够发一次脉冲，频率得调到最准。</d>
3.7～5.0秒：男性闭上嘴巴，目光重新落回终端顶部，右手食指与拇指稳稳捏住终端顶部的金属调频旋钮，画面停在手指即将发力拧动旋钮的瞬间。

overall_soundscape: 按键轻响一声，篝火木柴轻微爆裂声，远处平稳的海浪环境音。

non_diegetic_music: 低沉弦乐保持慢速张力。""",

    # 第 5 段（20-25s）：承接段4捏住调频旋钮 -> 精细拧动旋钮锁定信号 -> 段尾留“信号灯变绿、左腿发力准备站起身一半”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视机位，<Picture 1> 的年轻男性半蹲在篝火旁，左手托着信号终端，右手食指与拇指正捏在终端顶部的金属旋钮上，面部与手部对焦清晰。
0.0～1.8秒：男性右手手指缓慢而精准地顺时针拧动调频旋钮两格，眉头微敛，眼神紧盯屏幕波形。
1.8～3.6秒：终端侧面的指示灯由冰蓝色转为稳定的翠绿色微光，男性紧绷的眉宇顿时舒展开来，嘴角浮现一丝欣慰弧度。
3.6～5.0秒：男性双手握稳终端，左脚脚掌在礁石地面发力向下蹬，身体重心向上抬起，画面停在他从半蹲向站立过渡到一半的动态瞬间。

overall_soundscape: 金属旋钮细腻的刻度咔哒声，设备锁定频率的短促轻微蜂鸣，衣料随起身动作的摩擦声。

non_diegetic_music: 弦乐音高轻微上扬，营造希望初现的氛围。""",

    # 第 6 段（25-30s）：承接段5起身一半的动作 -> 完全站直并拉出折叠天线 -> 段尾留“天线指向夜空、仰头吸气准备说话”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。中近景平视机位，镜头随人物起身做轻微向上摇移，<Picture 1> 的年轻男性正站在篝火右侧，身穿深灰色户外冲锋衣，暖橙色火光从下方映照衣摆，深蓝海湾暮色作背景。
0.0～1.5秒：男性顺着上一段起身的动量完全站直身体，双脚稳稳立在礁石滩上，双手将信号终端举至胸前高度。
1.5～3.6秒：男性右手捏住终端顶端的金属折叠天线，流畅地向上拉出完整一节，天线尖端反射出篝火微光。
3.6～5.0秒：男性抬头望向斜上方开阔的海湾夜空，胸廓微扩吸气，画面停在他双手托举终端、准备开口的稳定姿态。

overall_soundscape: 金属天线抽出的清脆滑动声，海风吹动冲锋衣下摆的呼啸声，篝火持续燃烧声。

non_diegetic_music: 温暖的钢琴单音伴随弦乐缓慢铺开。""",

    # 第 7 段（30-35s）：承接段6站立托举终端姿态 -> S1台词 -> 段尾留“右手拇指果断按向发射键、绿光映亮指尖”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视固定机位，<Picture 1> 的年轻男性站立在篝火旁，双手在胸前托着展开天线的信号终端，面部受暖橙火光与翠绿指示灯双重映照，神情笃定。
0.0～0.8秒：男性目光从夜空收回，坚定地平视镜头前方的终端屏幕，呼吸平稳。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]中继信标已经锁定轨道，现在开始上传坐标。</d>
3.7～5.0秒：男性闭上嘴巴，右手大拇指移至终端中央的发射按钮上并果断向下按压，画面停在拇指按住发射键、翠绿光芒映亮指尖的瞬间。

overall_soundscape: 篝火噼啪声，海浪拍岸声，拇指按下机械发射键的清脆咔哒声。

non_diegetic_music: 钢琴与弦乐平稳推进，情绪凝聚。""",

    # 第 8 段（35-40s）：承接段7拇指按住发射键 -> 脉冲发送成功提示 -> 段尾留“长舒一口气、双臂准备缓缓放下终端”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视机位，<Picture 1> 的年轻男性站立在篝火旁，双手托着信号终端，右手拇指仍按在发射键上，光影色温与前序镜头一致。
0.0～1.8秒：终端顶部的翠绿色指示灯连续规律闪烁三次，男性屏住呼吸，目光紧紧锁住屏幕上传进度。
1.8～3.6秒：终端指示灯转为常亮绿光，男性紧绷的双肩瞬间自然下沉，胸口起伏长长舒出一口气，眼中浮现笑意。
3.6～5.0秒：男性右手拇指松开发射键，双臂开始将胸前的终端缓缓向下收拢，画面停在手臂下放一半的放松瞬间。

overall_soundscape: 电子终端清脆柔和的发送成功双音提示，男性长舒一口气的真实呼吸声，海风与篝火声持续。

non_diegetic_music: 温暖舒展的大提琴与钢琴和弦自然绽放。""",

    # 第 9 段（40-45s）：承接段8手臂收拢终端 -> S1台词 -> 段尾留“重心下移准备重新坐回篝火旁礁石的半蹲瞬间”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视机位，<Picture 1> 的年轻男性站立在篝火右侧，双手将信号终端自然垂捧在腰腹前，暖橙色篝火映亮他舒展温和的面庞。
0.0～0.8秒：男性眼神温和地看向镜头前方，嘴角带着笃定的微笑，轻轻吸气。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]信号发出去了，天亮之前一定会有回应。</d>
3.7～5.0秒：男性闭上嘴巴微微点头，双膝弯曲，身体重心平稳向下沉，画面停在他准备坐回篝火旁平整礁石的半蹲瞬间。

overall_soundscape: 篝火温暖的噼啪声，海浪轻柔拍岸声，冲锋衣随屈膝动作的轻微摩擦声。

non_diegetic_music: 舒缓温暖的钢琴旋律持续流淌。""",

    # 第 10 段（45-50s）：承接段9半蹲坐下动作 -> 坐回礁石放好终端伸手烤火 -> 段尾留“转头望向海面侧颜沉思”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。中景平视机位，镜头随人物落座轻微下移，<Picture 1> 的年轻男性正坐在篝火右侧的平整礁石上，暖橙色篝火在画面左前方跳跃，背景是静谧的深蓝海湾。
0.0～1.6秒：男性稳稳坐定在礁石上，右手将信号终端轻轻放回身旁的金属工具箱内。
1.6～3.6秒：男性将双手自然伸向篝火上方轻轻烘烤取暖，跳跃的火光在他掌心与脸庞之间映出温暖的明暗变化。
3.6～5.0秒：男性双手收回搭在膝盖上，头部缓慢转向右侧深蓝色的海湾远方，画面停在他安静侧颜望海的瞬间。

overall_soundscape: 设备放入箱内的轻响，双手靠近火堆的细微摩擦声，篝火燃烧与海浪环境音。

non_diegetic_music: 大提琴与钢琴旋律温暖交织。""",

    # 第 11 段（50-55s）：承接段10坐姿望海姿态 -> 转回视线说S1收官台词 -> 段尾留“右手拿起细木枝准备拨动篝火”钩子
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视固定机位，<Picture 1> 的年轻男性坐在篝火右侧的礁石上，暖橙火光稳定勾勒出他的面部轮廓，服装与环境光影与前序镜头一致。
0.0～0.8秒：男性从右侧海面方向缓缓转回頭看向镜头前方，眼神清澈从容，轻微吸气。
0.8～3.8秒：男性 (S1) says: <d>[Chinese]不管在代码里还是荒野里，活下去靠的都是耐心。</d>
3.8～5.0秒：男性闭上嘴巴，嘴角保留浅浅笑意，右手俯身拾起脚边一根细枯木枝，画面停在木枝尖端刚靠近篝火边缘的瞬间。

overall_soundscape: 篝火持续噼啪作响，远处柔和的海浪声，手指拾起枯枝的轻微声响。

non_diegetic_music: 钢琴旋律缓慢进入尾声前的温暖铺陈。""",

    # 第 12 段（55-60s）：承接段11右手拿木枝靠近篝火 -> 轻拨篝火升起火星，镜头缓拉收束全片
    """For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。中景平视机位，镜头从近景开始极其缓慢平稳地向后拉远，<Picture 1> 的年轻男性坐在篝火右侧的礁石上，右手握着细木枝靠近火堆。
0.0～1.8秒：男性用手中的木枝轻轻拨动了一下篝火中心的柴薪，几簇明亮的暖金色火星顺势向深蓝色夜空轻盈升腾。
1.8～3.8秒：男性将木枝放回火堆旁，双手自然交叠在膝头，安静凝望着跳动的温暖火光，镜头持续平滑向后拉升展现礁石营地与海湾全景。
3.8～5.0秒：海风轻拂男性发梢，暖橙色篝火与辽阔深蓝暮色海面在画面中形成静谧平衡，画面平稳停留在他守候黎明的坐姿定格中。

overall_soundscape: 木枝拨动炭火的清脆噼啪声，火星升腾的细微声响，辽阔海湾的轻柔海浪与夜风声。

non_diegetic_music: 温暖的大提琴与钢琴尾音缓缓渐弱收束。""",
]

# 原项目 13 号工作流（13.AICG3D_MiniMax H3 无限长视频生成工作流1.2.json）原装剧本 + 同场景双人同框连续延展
WORKFLOW13_TUSHAN_SEGMENTS = [
    # 第 1-4 段为 13 号工作流原装提示词（双参考图：<Picture 1>白衣女子师妹 + <Picture 2>蓝衣男子大师兄，全程双人同框不脱画）
    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，场景为云雾缭绕的涂山仙境白玉平台。开场中远景平视，<Picture 1>白衣年轻女子站在画面左侧，<Picture 2>蓝衣青年男子站在画面右侧，两人面对面站立于白玉平台之上。镜头做连续平滑推进至双人近景，两人面部五官均保持清晰锐利。主光来自斜上方，均匀打亮两人面部轮廓，环境薄雾缓慢流动。
0.0～1.8秒：中远景平视完整展现涂山仙境白玉平台，左侧<Picture 1>白衣女子与右侧<Picture 2>蓝衣男子相对而立，山间薄雾缓缓飘动。
1.8～3.4秒：镜头持续向前平滑推进至双人同框近景，左侧<Picture 1>白衣女子眉眼带着疑惑神态望向右侧<Picture 2>蓝衣男子，发丝随微风轻微晃动。
3.4～5.0秒：镜头保持双人同框近景固定，两人身体站位保持不变，画面停留在左侧<Picture 1>白衣女子嘴唇微启准备发问的稳定状态。

overall_soundscape: 山间持续轻柔风声，远处隐约传来仙灵环境回响，布料被微风轻轻拂动摩擦声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景镜头，左侧<Picture 1>白衣年轻女子正对右侧<Picture 2>蓝衣青年男子，二人站在平台原地不动，服装样貌与上一段一致，两人面部持续清晰对焦。镜头为固定凝视机位不做位移。主光依旧斜上方照射，面部光影、色温和曝光保持统一。
0.0～0.7秒：左侧<Picture 1>白衣女子眉头微微蹙起，嘴唇微张，身体小幅前倾，眼神充满疑惑望向右侧<Picture 2>蓝衣男子，肩膀轻微起伏完成吸气。
0.7～3.5秒：清脆年轻女子 (S1) says: <d>[Chinese]大师兄，柳如烟到底是谁？</d>
3.5～5.0秒：左侧<Picture 1>白衣女子闭上嘴巴，头部姿态不变，依旧带着疑惑看向右侧<Picture 2>蓝衣男子，等待对方作答，两人维持稳定的双人同框姿态。

overall_soundscape: 山间轻柔风声持续，衣物布料轻微摩擦，女子细微呼吸声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子面对面站立，两人站位不变，面部全程清晰对焦，镜头保持固定凝视机位。主光斜上方，光影色调曝光延续前序段落。
0.0～0.6秒：右侧<Picture 2>蓝衣男子眉毛向上扬起，露出略带诧异的神情，肩膀微抬吸气，左侧<Picture 1>白衣女子闭口专注聆听。
0.6～2.8秒：沉稳青年男子 (S2) says: <d>[Chinese]连如烟大帝你都不知道，看样子你都不看红果的吗？</d>
2.8～3.6秒：右侧<Picture 2>蓝衣男子闭合嘴唇，目光注视左侧<Picture 1>白衣女子，身体保持不动。
3.6～5.0秒：左侧<Picture 1>白衣女子头部微微歪向一侧，脸上疑惑感加重，嘴唇轻启准备回话，画面停留在双人同框、女子即将开口的稳定姿态。

overall_soundscape: 山间轻柔风声持续，衣料轻微晃动，两人平缓呼吸声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框中近景镜头，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子保持平台原地对峙站位，两人全身与面部均在画面内清晰可见，镜头固定凝视。主光斜上方，光影色温曝光不变。
0.0～0.8秒：左侧<Picture 1>白衣女子眼神困惑，眉头紧锁，身体轻微向前倾。
0.8～2.4秒：清脆年轻女子 (S1) says: <d>[Chinese]是贵州六盘水的那个红果吗？</d>
2.4～3.6秒：左侧<Picture 1>白衣女子闭上嘴，右侧<Picture 2>蓝衣男子神色瞬间转为哭笑不得，右手抬起食指指向女子额前虚点一下。
3.6～5.0秒：右侧<Picture 2>蓝衣男子右手悬在半空准备放下，左侧<Picture 1>白衣女子缩了缩脖子露出无辜神情，画面停留在双人同框对视的衔接瞬间。

overall_soundscape: 山间风声持续，衣袖抬起时的布料摩擦声，轻柔呼吸声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子站在平台原位，严禁任何一人移出画面，主光斜上方均匀照亮两人面部。
0.0～0.7秒：右侧<Picture 2>蓝衣男子放下右手，无奈叹了一口气看向左侧<Picture 1>白衣女子，肩膀微抬吸气。
0.7～3.6秒：沉稳青年男子 (S2) says: <d>[Chinese]那是山下人间最火的短剧阁，连掌门都在熬夜看。</d>
3.6～5.0秒：右侧<Picture 2>蓝衣男子闭上嘴巴，右手探入左袖准备取出一枚玉简，左侧<Picture 1>白衣女子目光好奇地盯向他的袖口，停在双人同框的衔接瞬间。

overall_soundscape: 山间清风拂过，衣袖摩擦声，男子清晰的叹息与呼吸声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子面对面站在原位，平台云雾与斜上方柔和主光保持不变。
0.0～1.5秒：右侧<Picture 2>蓝衣男子右手从袖中取出一枚泛着温润白光的玉简，递向左侧<Picture 1>白衣女子面前。
1.5～3.6秒：左侧<Picture 1>白衣女子双手接过发光玉简捧在胸前，低头端详玉简表面流转的光纹，右侧<Picture 2>蓝衣男子负手站立注视她。
3.6～5.0秒：左侧<Picture 1>白衣女子抬起头看向右侧<Picture 2>蓝衣男子，嘴角露出一丝恍然笑意，停在吸气准备发问的双人同框瞬间。

overall_soundscape: 手指交接玉简的温润轻响，山谷微风声，衣料轻微摩擦声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子手持玉简看向右侧<Picture 2>蓝衣青年男子，两人全程保持在画面左右两侧，面部清晰对焦。
0.0～0.8秒：左侧<Picture 1>白衣女子轻轻晃了晃手中的玉简，眼神带着几分促狭笑意，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S1) says: <d>[Chinese]原来师兄闭关三天，就是在偷偷刷短剧？</d>
3.6～5.0秒：左侧<Picture 1>白衣女子闭上嘴，眉眼弯起看着右侧<Picture 2>蓝衣男子，画面停在双人同框等待师兄辩解的稳定瞬间。

overall_soundscape: 轻柔风声，衣袂微动声，女子清浅笑意呼吸声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子站在原位不变，主光斜上方柔和稳定。
0.0～0.7秒：右侧<Picture 2>蓝衣男子脸上闪过一丝窘迫，轻咳一声挺直腰板，左侧<Picture 1>白衣女子闭口微笑着注视他。
0.7～3.6秒：沉稳青年男子 (S2) says: <d>[Chinese]此言差矣，我这是在参悟人间的叙事之道。</d>
3.6～5.0秒：右侧<Picture 2>蓝衣男子闭上嘴，双手背在身后微微昂头，左侧<Picture 1>白衣女子把玉简妥帖收入袖中，画面停在双人同框的衔接姿态。

overall_soundscape: 男子轻微干咳声，衣袍拂动声，山间持续清风。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子面对面站在白玉栏杆旁，两人面部光影清晰自然。
0.0～0.8秒：左侧<Picture 1>白衣女子收好玉简后双手轻轻搭在身前，眼神期待地看向右侧<Picture 2>蓝衣男子，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S1) says: <d>[Chinese]那师兄还不快给我讲讲，如烟大帝后来怎么了？</d>
3.6～5.0秒：左侧<Picture 1>白衣女子闭上嘴，身体微微前倾做出认真倾听的姿态，右侧<Picture 2>蓝衣男子展颜一笑准备开口，保持双人同框构图。

overall_soundscape: 玉简入袖的布料摩擦声，山谷轻柔风声，平缓呼吸声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子同框而立，背景云海翻涌。
0.0～0.8秒：右侧<Picture 2>蓝衣男子看着左侧<Picture 1>白衣女子好奇的模样，微微一笑，抬起右手做了个保密的手势。
0.8～3.6秒：沉稳青年男子 (S2) says: <d>[Chinese]天机不可泄露，等你今晚筑基成功再借你看。</d>
3.6～5.0秒：右侧<Picture 2>蓝衣男子放下右手，左侧<Picture 1>白衣女子轻轻嘟嘴随后展颜微笑点头，画面停在两人同框相视的稳定瞬间。

overall_soundscape: 山间轻柔风声，衣袖摆动声，温和呼吸声。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台栏杆前。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子并肩站在白玉栏杆前，两人始终完整留在画面中，斜上方仙境柔光均匀打亮两人面庞。
0.0～0.8秒：左侧<Picture 1>白衣女子微笑着向右侧<Picture 2>蓝衣男子拱手行了一个轻巧的同门礼，吸气准备说话。
0.8～3.6秒：清脆年轻女子 (S1) says: <d>[Chinese]一言为定，师兄可不许反悔。</d>
3.6～5.0秒：左侧<Picture 1>白衣女子礼毕放下双手，与右侧<Picture 2>蓝衣男子一同扶住身前的雕花白玉栏杆，画面停在两人同框远眺云海的瞬间。

overall_soundscape: 山风吹拂发丝与衣带的轻响，手掌轻触白玉栏杆的细微声响。

non_diegetic_music: N/A""",

    """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台栏杆前。双人同框中景至远景平滑缓拉机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子并肩立于白玉栏杆前，两人全程保持在画面中央偏下位置同框不分离。
0.0～1.6秒：左侧<Picture 1>白衣女子与右侧<Picture 2>蓝衣男子并肩扶着白玉栏杆，微笑着望向云海深处的仙山楼阁，衣袂随微风轻轻飘动。
1.6～3.8秒：镜头从两人正前方极其缓慢平稳地向后拉远，完整保留两人同框身影的同时，展现涂山仙境层峦叠嶂的浮空仙岛与漫天霞光。
3.8～5.0秒：山间薄雾与金色霞光在两人周围缓缓流淌，画面平稳收束在<Picture 1>白衣女子与<Picture 2>蓝衣男子并肩同框的定格画面中。

overall_soundscape: 辽阔山谷的柔和风声，远处仙钟悠远回荡，衣袂随风轻摆声。

non_diegetic_music: N/A""",
]


PRESET_SCRIPTS: dict[str, dict[str, Any]] = {
    "aicg3d_campfire_continuous_60s": {
        "id": "aicg3d_campfire_continuous_60s",
        "name": "🎬 AICG3D 原装编译器：极客荒岛·篝火守夜一镜到底（60秒 12段同空间强连贯）",
        "skill_id": "short-scene-director-lite",
        "description": (
            "严格按原项目《无限视频生成提示词模板V1.MD》+《多分镜强连贯系统》编译："
            "全片 12 段×5 秒保持在「黄昏礁石海滩篝火旁」同一空间与光影基准，严禁跨段瞬移换场；"
            "每段段尾（3.6～5.0秒）留未完成动作钩子，下一段开头（0.0～0.8秒）从同一姿态无缝接力。"
        ),
        "characterPrompt": (
            "A cinematic medium close-up portrait of a young Chinese tech founder in his late 20s, "
            "short neat dark hair, wearing a dark grey outdoor tactical jacket, warm campfire light on his face "
            "against a deep blue twilight ocean reef background, photorealistic, 8k film grain, sharp focus on eyes."
        ),
        "actTitles": [
            "第一幕 · 篝火添柴与开箱取终端（0-15s｜篝火旁同空间）",
            "第二幕 · 精准调频与起身展天线（15-30s｜动作钩子无缝接力）",
            "第三幕 · 锁定信标与发射坐标脉冲（30-45s｜单说话人标准对白）",
            "第四幕 · 落座烘火与星空火星收束（45-60s｜同机位缓拉收尾）",
        ],
        "segments": [
            re.sub(r"non_diegetic_music:\s*[^\n]+", "non_diegetic_music: N/A", s)
            for s in CAMPFIRE_CONTINUOUS_12_SEGMENTS
        ],
    },
    "aicg3d_original_workflow13_tushan": {
        "id": "aicg3d_original_workflow13_tushan",
        "name": "🏛️ 原项目 13 号工作流原装剧本：涂山仙境·如烟大帝与红果（60秒 12段连续剧）",
        "skill_id": "chinese-celestial-palace",
        "description": (
            "100% 源自 comfyui-AICG3D/workflows/13.AICG3D_MiniMax H3 无限长视频生成工作流1.2.json 内置原装剧本"
            "（「大师兄，柳如烟到底是谁？」「连如烟大帝你都不知道，看样子你都不看红果的吗？」），"
            "绑定 <Picture 1>白衣女师妹 + <Picture 2>蓝衣男大师兄 双参考图，全程双人同框不脱画。"
        ),
        "characterPrompt": (
            "16:9古风奇幻电影定妆照，云雾缭绕的涂山仙境白玉平台，二十岁出头的古风仙侠清丽女弟子，"
            "身穿素白飘逸古风仙门长袍，发髻佩戴银色仙鹤发冠，斜上方柔和绚丽的仙境霞光均匀打亮面部轮廓，五官精致清晰锐利。"
        ),
        "characterPrompts": [
            "16:9古风奇幻电影定妆照，云雾缭绕的涂山仙境白玉平台，二十岁出头的古风仙侠清丽女弟子，身穿素白飘逸古风仙门长袍，发髻佩戴银色发冠，斜上方柔和绚丽的仙境霞光均匀打亮面部轮廓，五官精致清晰锐利。",
            "16:9古风奇幻电影定妆照，云雾缭绕的涂山仙境白玉平台，二十五岁的古风仙侠俊朗男大师兄，身穿淡青蓝色古风仙门长袍，束发银冠，神情温和带笑意，斜上方柔和绚丽的仙境霞光均匀打亮面部轮廓，五官清晰锐利。",
        ],
        "actTitles": [
            "第一幕 · 涂山仙境俯冲与柳如烟之问（0-15s｜13号工作流原装段1-3）",
            "第二幕 · 六盘水红果乌龙与递交玉简（15-30s｜13号工作流原装段4+接力）",
            "第三幕 · 闭关刷剧调侃与天机筑基（30-45s｜双人同框固定机位）",
            "第四幕 · 同门拱手约定与云海收束（45-60s｜双人同框缓拉远景）",
        ],
        "segments": WORKFLOW13_TUSHAN_SEGMENTS,
    },
    "aicg3d_wuxia_stone_steps_60s": {
        "id": "aicg3d_wuxia_stone_steps_60s",
        "name": "⚔️ 古风石阶·师兄师妹夜探 (无限视频模板V1原装延展60s·双采精修版)",
        "skill_id": "zh_dialogue",
        "description": (
            "源自《无限视频生成提示词模板V1.MD》第 8 节原装高处石阶夜探示例并延展至 60 秒 12 段："
            "绑定 <Picture 1>玄青劲装男师兄 + <Picture 2>淡碧衣裙女师妹 双参考图，"
            "全程锁定双人同框不脱画、明确男女音色标签、全部 non_diegetic_music: N/A。"
        ),
        "characterPrompt": (
            "16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十五岁俊朗沉稳的古风仙门男师兄，"
            "束发佩戴古铜发冠，身穿深玄青色修身古风剑客劲装长袍，腰悬长剑，斜上方清冷月光与远处暖色山门灯火均匀打亮立体五官，眼神温和锐利，数字电影摄影机成像，8k超清"
        ),
        "characterPrompts": [
            "16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十五岁俊朗沉稳的古风仙门男师兄，束发佩戴古铜发冠，身穿深玄青色修身古风剑客劲装长袍，腰悬长剑，斜上方清冷月光与远处暖色山门灯火均匀打亮立体五官，眼神温和锐利，数字电影摄影机成像，8k超清",
            "16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十岁出头清丽灵动的古风仙门女师妹，乌黑长发绾成灵动仙侠发髻并饰以碧玉发簪，身穿淡碧青色古风刺绣衣裙，斜上方清冷月光与暖色灯火柔和打亮精致五官，眼神机敏清澈，数字电影摄影机成像，8k超清",
        ],
        "actTitles": [
            "第一幕 · 石阶夜语与暗处异响（0-15s｜模板V1原装段1-3·双人同框）",
            "第二幕 · 按剑凝神与掌珠照阶（15-30s｜模板V1原装段4+接力·双人同框）",
            "第三幕 · 灵鹿虚惊与同门相视（30-45s｜双人同框固定机位）",
            "第四幕 · 巡山记毕与并肩归门（45-60s｜双人同框缓拉远景）",
        ],
        "segments": [
            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷环境光与远处温暖山门灯火。开场中景平视缓推至双人同框近景，场景固定在高处青石台阶平台前，<Picture 1>玄青劲装青年男师兄站在画面左侧，<Picture 2>淡碧衣裙年轻女师妹站在画面右侧，两人并肩相对而立，面部五官均保持清晰锐利。
0.0～1.8秒：左侧<Picture 1>玄青劲装男师兄与右侧<Picture 2>淡碧衣裙女师妹并肩站在高处石阶前，山间夜风轻轻吹拂两人衣袖与发丝。
1.8～3.6秒：镜头平滑推进至双人同框近景，左侧<Picture 1>玄青劲装男师兄缓慢转头看向右侧<Picture 2>淡碧衣裙女师妹，右手轻轻抬起停在半空。
3.6～5.0秒：镜头保持双人同框近景固定，画面停在左侧<Picture 1>男师兄转头吸气准备开口、右侧<Picture 2>女师妹安静聆听的稳定瞬间。

overall_soundscape: 高处山谷夜风声持续，远处山门灯火方向隐约风铃声，两人衣料轻微摩擦声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定凝视机位，左侧<Picture 1>玄青劲装青年男师兄看向右侧<Picture 2>淡碧衣裙年轻女师妹，两人站在石阶原位不动，服装样貌与清冷月光保持一致，面部清晰对焦。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄轻轻吸了一口气，目光温和地落在右侧<Picture 2>淡碧衣裙女师妹脸上，女师妹闭口专注倾听。
0.8～3.5秒：沉稳青年男子 (S1) says: <d>[Chinese]师妹，夜深风急，我们先回山门吧。</d>
3.5～5.0秒：左侧<Picture 1>男师兄闭上嘴巴放下右手，右侧<Picture 2>女师妹神色微动似听见竹林方向传来声响，停在双人同框等待回应的瞬间。

overall_soundscape: 高处山风持续吹拂，衣料在夜风中轻轻摩擦，男子平缓呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定凝视机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹站在石阶原位保持双人同框，两人面部全程清晰对焦。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹抬起敏锐的眼睛看向左侧<Picture 1>玄青劲装男师兄，右手在身侧微微收紧，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]前面石阶有声音，我们先别动。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴，与左侧<Picture 1>男师兄同时将目光警觉地投向画面右前方的石阶竹林暗处，停在双人同框戒备瞬间。

overall_soundscape: 高处山谷风声持续，前方竹林深处传来一声很轻的铜铃晃动声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框中近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶原位，严禁任何一人移出画面，光影色温保持统一。
0.0～1.5秒：左侧<Picture 1>玄青劲装男师兄与右侧<Picture 2>淡碧衣裙女师妹同时屏住呼吸，目光紧锁右前方石阶竹林。
1.5～3.6秒：左侧<Picture 1>男师兄左脚向前稳踏半步，右手微微横在右侧<Picture 2>女师妹身前做出守护姿态，女师妹紧贴他身侧驻足。
3.6～5.0秒：左侧<Picture 1>男师兄左手稳稳按上腰间长剑剑柄，画面停在双人同框按剑凝神的衔接瞬间。

overall_soundscape: 靴底踏在青石台阶上的轻微回响，手掌按住剑柄的皮革摩擦声，夜风吹动竹叶声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄左手按住剑柄，右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在他身侧，两人始终完整留在画面内。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄微微侧过半张脸示意右侧<Picture 2>淡碧衣裙女师妹安心，胸口轻微吸气。
0.8～3.6秒：沉稳青年男子 (S1) says: <d>[Chinese]跟在我身旁别离开，我们照亮看看。</d>
3.6～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，右手探入衣襟怀中准备取出一颗夜明珠，右侧<Picture 2>女师妹目光专注地看向他的右手，保持双人同框。

overall_soundscape: 山林夜风吹拂竹叶的沙沙声，衣襟布料摩擦声，沉稳呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷环境光叠加夜明珠温润白光。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶原位，两人面部清晰锐利。
0.0～1.6秒：左侧<Picture 1>玄青劲装男师兄右手从怀中取出一颗泛着温润白光的夜明珠，平托在两人胸前中央照亮前方青石台阶。
1.6～3.6秒：伴随竹林方向传来一声清脆的灵鹿轻鸣，左侧<Picture 1>男师兄与右侧<Picture 2>女师妹紧绷的眉宇同时舒展开来，男师兄左手松开腰间剑柄。
3.6～5.0秒：左侧<Picture 1>男师兄嘴角浮现温和笑意转头看向右侧<Picture 2>女师妹，停在吸气准备开口解释的双人同框瞬间。

overall_soundscape: 竹叶轻微晃动声，林间一声清脆柔和的灵鹿轻鸣，手掌松开剑柄的轻响。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷月光与掌心夜明珠柔光交融。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄右手托着发光夜明珠，右侧<Picture 2>淡碧衣裙年轻女师妹站在身旁，两人全程保持在画面左右两侧。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄面带温和笑意看向右侧<Picture 2>淡碧衣裙女师妹，轻轻吸气。
0.8～3.6秒：沉稳青年男子 (S1) says: <d>[Chinese]原来是山里的灵鹿碰响了竹林铜铃。</d>
3.6～5.0秒：左侧<Picture 1>男师兄闭上嘴巴微笑注视她，右侧<Picture 2>女师妹长舒一口气展露浅笑，画面停在两人相视放松的双人同框瞬间。

overall_soundscape: 山间轻柔夜风声，远处灵鹿轻快跃入竹林的细微蹄声，轻柔呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷月光与夜明珠柔光。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹站在石阶原位，夜明珠微光柔和映亮两人面庞。
0.0～0.7秒：右侧<Picture 2>淡碧衣裙女师妹紧握的右手自然垂落，脸上露出几分不好意思的浅笑看向左侧<Picture 1>男师兄，轻微吸气。
0.7～3.5秒：清脆年轻女子 (S2) says: <d>[Chinese]害师兄白白按剑紧张一场，让你见笑了。</d>
3.5～5.0秒：右侧<Picture 2>女师妹闭上嘴巴微微颔首浅笑，左侧<Picture 1>男师兄温和摇了摇头准备开口宽慰，保持双人同框构图。

overall_soundscape: 女子轻柔的呼气声，衣袖自然垂落的布料摩擦声，山谷夜风声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹面对面站在青石台阶旁，两人面部光影清晰自然。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄眼神温和笃定地注视右侧<Picture 2>淡碧衣裙女师妹，轻微吸气。
0.8～3.7秒：沉稳青年男子 (S1) says: <d>[Chinese]夜探山道小心为上，师妹这份警觉正是好事。</d>
3.7～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，右手将夜明珠妥帖收回怀中，右侧<Picture 2>女师妹欣然点头微笑，停在双人同框的衔接姿态。

overall_soundscape: 夜明珠收入怀中的衣料摩擦声，山间清风声，平和的呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷高处石阶平台，背景远处山门灯火温暖闪烁。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶栏杆旁。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹拍了拍腰间收纳袋，眼神清亮地看向左侧<Picture 1>玄青劲装男师兄，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]巡山记录已经记好了，师兄我们回山门复命吧。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴面带微笑，左侧<Picture 1>男师兄颔首赞许并望向山门灯火方向，画面停在两人同框相视的稳定瞬间。

overall_soundscape: 手掌轻拍收纳袋的细微声响，山谷轻柔夜风声，两人温和呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶前，两人始终完整留在画面中。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄从山门方向收回视线看向右侧<Picture 2>淡碧衣裙女师妹，神情从容温和，轻微吸气。
0.8～3.6秒：沉稳青年男子 (S1) says: <d>[Chinese]好，趁着夜雾还没起，我们这就上去。</d>
3.6～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，右手做出请行的温和手势，右侧<Picture 2>女师妹微笑拱手回礼，画面停在两人同框并肩的瞬间。

overall_soundscape: 山顶传来的悠远夜钟声，轻柔山风吹拂发丝与衣带的声响。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框中景至远景平滑缓拉机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩立于高处青石台阶之上，两人全程保持在画面中央同框不分离。
0.0～1.6秒：左侧<Picture 1>玄青劲装男师兄与右侧<Picture 2>淡碧衣裙女师妹并肩站在高处石阶平台，微笑着望向远处灯火通明的古风山门，衣袂随清风轻轻飘动。
1.6～3.8秒：镜头从两人正前方极其缓慢平稳地向后拉远，完整保留两人同框身影的同时，展现高处青石台阶、幽深竹林山谷与山巅璀璨灯火。
3.8～5.0秒：山谷薄雾与清冷月光在两人周围缓缓流淌，画面平稳收束在<Picture 1>男师兄与<Picture 2>女师妹并肩同框的定格画卷中。

overall_soundscape: 辽阔山谷的柔和夜风声，远处古寺山门钟声悠远回荡，衣袂随风轻摆声。

non_diegetic_music: N/A""",
        ],
    },
    "aicg3d_wuxia_stone_steps_sequel_60s": {
        "id": "aicg3d_wuxia_stone_steps_sequel_60s",
        "name": "🦌 古风石阶·续集：灵鹿衔玉示警 (60s双采精修版)",
        "skill_id": "zh_dialogue",
        "description": (
            "承接第一集《古风石阶·师兄师妹夜探》60秒结尾，两人刚踏上山门前青石平台，"
            "方才的灵鹿去而复返，在石阶上留下一枚刻有玄鸦暗纹的陌生古玉。"
            "全程锁定 <Picture 1>男师兄 + <Picture 2>女师妹 双人同框、明确男女音色标签、全部 non_diegetic_music: N/A。"
        ),
        "characterPrompt": (
            "16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十五岁俊朗沉稳的古风仙门男师兄，"
            "束发佩戴古铜发冠，身穿深玄青色修身古风剑客劲装长袍，腰悬长剑，斜上方清冷月光与远处暖色山门灯火均匀打亮立体五官，眼神温和锐利，数字电影摄影机成像，8k超清"
        ),
        "characterPrompts": [
            "16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十五岁俊朗沉稳的古风仙门男师兄，束发佩戴古铜发冠，身穿深玄青色修身古风剑客劲装长袍，腰悬长剑，斜上方清冷月光与远处暖色山门灯火均匀打亮立体五官，眼神温和锐利，数字电影摄影机成像，8k超清",
            "16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十岁出头清丽灵动的古风仙门女师妹，乌黑长发绾成灵动仙侠发髻并饰以碧玉发簪，身穿淡碧青色古风刺绣衣裙，斜上方清冷月光与暖色灯火柔和打亮精致五官，眼神机敏清澈，数字电影摄影机成像，8k超清",
        ],
        "actTitles": [
            "第一幕 · 鹿鸣落玉与掌心端详（0-15s｜山门石阶·双人同框）",
            "第二幕 · 玄鸦暗纹与名册核查（15-30s｜古玉特写接力·双人同框）",
            "第三幕 · 尚存余温与按剑戒备（30-45s｜双人同框固定机位）",
            "第四幕 · 携玉直赴主殿与远景收束（45-60s｜双人同框缓拉远景）",
        ],
        "segments": [
            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷夜色与山门前温暖灯笼光交融。开场中景平视缓推至双人同框近景，场景固定在山门外高处青石台阶平台前，<Picture 1>玄青劲装青年男师兄站在画面左侧，<Picture 2>淡碧衣裙年轻女师妹站在画面右侧，两人并肩而立，面部五官清晰锐利。
0.0～1.8秒：左侧<Picture 1>玄青劲装男师兄与右侧<Picture 2>淡碧衣裙女师妹刚踏上山门前的青石平台，竹林暗处忽然传来一声清越的灵鹿轻鸣与玉石落地清脆声。
1.8～3.6秒：镜头平滑推进至双人同框近景，左侧<Picture 1>男师兄与右侧<Picture 2>女师妹同时停下脚步，目光讶异地低头看向两人前方石阶地面。
3.6～5.0秒：右侧<Picture 2>女师妹抬起头望向左侧<Picture 1>男师兄，微张嘴吸气准备开口，停在双人同框警觉对视的稳定瞬间。

overall_soundscape: 竹林方向传来一声清脆灵鹿轻鸣，紧接着一声温润玉器落在青石板上的清脆轻响，山谷夜风声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台与温暖山门灯火。双人同框近景固定凝视机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶原位，两人服装样貌与光影保持一致，面部清晰对焦。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹右手轻轻指向前方石阶地面，眼神惊讶地看向左侧<Picture 1>玄青劲装男师兄，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]师兄你看，方才那只灵鹿在石阶上留下了一块玉佩。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴收回右手，左侧<Picture 1>男师兄神色凝重地颔首，停在双人同框准备拾取玉佩的衔接瞬间。

overall_soundscape: 远处灵鹿轻快跃入竹林的细碎蹄声，高处山风吹拂衣袂声，女子轻柔呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹站在石阶原位保持双人同框，两人面部全程清晰对焦。
0.0～1.0秒：左侧<Picture 1>玄青劲装男师兄右手向上抬起，掌心托着一枚泛着温润青白色微光的古朴雕花玉佩停在两人胸前中央，轻微吸气。
1.0～3.7秒：沉稳青年男子 (S1) says: <d>[Chinese]灵鹿通灵去而复返，莫非是特意来向我们示警？</d>
3.7～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，与右侧<Picture 2>女师妹同时低头凝视他右手掌心中的发光古玉，停在双人同框端详玉佩的瞬间。

overall_soundscape: 手指拾起温润玉佩的轻微摩擦声，山门方向檐角风铃轻响，山谷夜风声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷月光叠加掌心古玉温润青光。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄右手平托发光古玉，右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在身侧，严禁任何一人移出画面。
0.0～1.6秒：左侧<Picture 1>玄青劲装男师兄右手手指将掌心泛着青光的古玉轻轻翻转，借着月光与灯火细看玉佩背面的刻印。
1.6～3.6秒：随着古玉背面图腾显露，左侧<Picture 1>男师兄眉头微微蹙起，右侧<Picture 2>女师妹也凑近半寸专注端详，两人神情渐趋严肃。
3.6～5.0秒：左侧<Picture 1>男师兄抬起目光看向右侧<Picture 2>女师妹，停在吸气准备开口说出发现的双人同框瞬间。

overall_soundscape: 指尖翻转玉佩的细腻摩擦声，夜风拂过竹林的沙沙声，两人屏息凝神的轻微呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄右手托着古玉，右侧<Picture 2>淡碧衣裙年轻女师妹站在他身旁，两人始终完整留在画面内。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄眼神深邃笃定地注视右侧<Picture 2>淡碧衣裙女师妹，胸口轻微吸气。
0.8～3.7秒：沉稳青年男子 (S1) says: <d>[Chinese]玉佩背面刻着玄鸦暗纹，绝非我们本门弟子的信物。</d>
3.7～5.0秒：左侧<Picture 1>男师兄闭上嘴巴稳托古玉，右侧<Picture 2>女师妹神色一凛，双手从腰间取出宗门名册卷轴，保持双人同框。

overall_soundscape: 山谷夜风持续吹拂，女子从腰间取出纸质卷轴的轻微摩擦声，沉稳呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷月光与温暖山门灯火。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄右手托玉，右侧<Picture 2>淡碧衣裙年轻女师妹双手展开一卷古风纸质名册，两人面部清晰锐利。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹快速扫视手中展开的宗门名册卷轴，随后抬眼看向左侧<Picture 1>男师兄，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]我查过访客名册，今夜并未登记有外门来客入山。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴，目光再次落向左侧<Picture 1>男师兄手中的古玉，画面停在两人同框思索的瞬间。

overall_soundscape: 纸质名册卷轴展开与翻动的沙沙声，高处山谷清冷风声，平缓呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶原位，两人全程保持在画面左右两侧。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹指尖轻轻触碰了一下左侧<Picture 1>男师兄掌心的古玉边缘，眼神敏锐地抬起看向他，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]玉佩尚有余温，说明那人刚刚潜入山门外围不久。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴收回手指，左侧<Picture 1>男师兄左手迅速按上腰间剑柄，停在双人同框警觉戒备的瞬间。

overall_soundscape: 指尖轻触玉石的细微声响，手掌按住长剑剑柄的皮革摩擦声，山林夜风声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹面对面站在青石台阶旁，两人面部光影清晰自然。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄眼神凌厉而沉着地看向右侧<Picture 2>淡碧衣裙女师妹，轻微吸气。
0.8～3.6秒：沉稳青年男子 (S1) says: <d>[Chinese]看来方才铜铃作响，正是有人暗中惊动了林中灵鹿。</d>
3.6～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，右手将古玉稳妥收入衣襟怀中，右侧<Picture 2>女师妹将手中名册卷轴合拢，保持双人同框构图。

overall_soundscape: 玉佩收入怀中的衣料摩擦声，纸质名册卷轴合拢声，山谷夜风吹动竹叶声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷高处石阶平台，背景山门灯火温暖明亮。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶前，两人始终完整留在画面中。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹将名册卷轴收回腰间，神情郑重地看向左侧<Picture 1>玄青劲装男师兄，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]事关护山大阵安危，我们必须立刻带玉佩禀报掌门。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴眼神坚定地注视师兄，左侧<Picture 1>男师兄郑重颔首准备下令，停在双人同框的衔接姿态。

overall_soundscape: 卷轴收入腰间收纳袋的轻响，山间清风声，两人沉稳的呼吸声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷高处石阶平台与温暖山门灯火。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶栏杆旁，面部清晰对焦。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄目光坚定地注视右侧<Picture 2>淡碧衣裙女师妹，轻微吸气。
0.8～3.7秒：沉稳青年男子 (S1) says: <d>[Chinese]好，你随我直赴主殿呈交古玉，沿途留心暗处动静。</d>
3.7～5.0秒：左侧<Picture 1>男师兄闭上嘴巴左手按剑蓄势，右侧<Picture 2>女师妹双手抱拳准备领命，画面停在两人同框相视的稳定瞬间。

overall_soundscape: 衣袖抬起的布料摩擦声，山谷轻柔夜风声，远处山门传来的低沉钟鸣声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶前，两人始终完整留在画面中。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹向左侧<Picture 1>玄青劲装男师兄行了一个干脆利落的古风抱拳礼，眼神清亮笃定，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]明白，有师兄在身旁，今晚定叫来人无所遁形。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴放下双手，左侧<Picture 1>男师兄露出一丝赞许微笑并肩转向山门方向，停在两人同框并肩的瞬间。

overall_soundscape: 抱拳行礼时衣袖清脆的摩擦声，山顶传来的悠远夜钟声，轻柔山风声。

non_diegetic_music: N/A""",

            """How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台与辉煌山门灯火。双人同框中景至远景平滑缓拉机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩立于山门青石台阶之上，两人全程保持在画面中央同框不分离。
0.0～1.6秒：左侧<Picture 1>玄青劲装男师兄左手按剑，与右侧<Picture 2>淡碧衣裙女师妹并肩站在山门石阶平台，神情坚定地望向灯火辉煌的仙门主殿。
1.6～3.8秒：镜头从两人正前方极其缓慢平稳地向后拉远，完整保留两人同框身影的同时，展现高耸古风山门、幽深竹林石阶与漫天清冷月色。
3.8～5.0秒：山谷云雾与暖色灯火在两人周围交织流淌，画面平稳收束在<Picture 1>男师兄与<Picture 2>女师妹并肩守卫山门的史诗画卷中。

overall_soundscape: 辽阔山谷的柔和夜风声，古寺山门浑厚钟声悠远回荡，衣袂随风轻摆声。

non_diegetic_music: N/A""",
        ],
    },
}


def load_skills_catalog() -> dict[str, Any]:
    if SKILLS_CATALOG_PATH.is_file():
        try:
            return json.loads(SKILLS_CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"skills": [], "h3_continuity_guide": ""}


def load_template_v1_markdown() -> str:
    if TEMPLATE_MD_PATH.is_file():
        return TEMPLATE_MD_PATH.read_text(encoding="utf-8")
    return ""


def get_presets_and_skills_payload() -> dict[str, Any]:
    """返回供前端测试台展示的原项目导演 Skills 清单与原装预设剧本列表。"""
    catalog = load_skills_catalog()
    skills_list = []
    for item in catalog.get("skills", []):
        skills_list.append({
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "name_en": item.get("name_en", ""),
            "tag": item.get("tag", "") or "导演 Skill",
            "kind": item.get("kind", "skill"),
            "summary": (item.get("summary", "") or "")[:180],
        })

    presets_list = []
    for pid, preset in PRESET_SCRIPTS.items():
        segs = preset["segments"]
        presets_list.append({
            "id": pid,
            "name": preset["name"],
            "skill_id": preset["skill_id"],
            "description": preset["description"],
            "characterPrompt": preset["characterPrompt"],
            "characterPrompts": preset.get("characterPrompts", [preset["characterPrompt"]]),
            "segmentCount": len(segs),
            "segmentSeconds": 5,
            "rawScript": format_full_aicg3d_script(segs, 5),
            "acts": segments_to_acts(segs, preset.get("actTitles"), segment_seconds=5),
        })

    return {
        "skills": skills_list,
        "presets": presets_list,
        "templateVersion": "无限视频生成提示词模板V1.MD (comfyui-AICG3D 1.2)",
    }


def _build_compiler_system_prompt(
    skill_id: str,
    segment_count: int = 12,
    segment_seconds: int = 5,
    has_ref_image: bool = True,
) -> str:
    """构建原装《无限视频生成提示词模板V1.MD》+ 多分镜强连贯规则 + 所选导演 Skill 的系统提示词。"""
    template_md = load_template_v1_markdown()
    catalog = load_skills_catalog()
    skill_section = ""
    if skill_id and skill_id != "auto":
        matched = next((s for s in catalog.get("skills", []) if s.get("id") == skill_id), None)
        if matched and matched.get("body"):
            skill_body = str(matched["body"])[:3500]
            skill_section = (
                f"\n\n## 附加导演风格技能：{matched.get('name')} ({skill_id})\n"
                f"在严格遵守上方《无限视频生成提示词模板V1.MD》输出格式的前提下，融入以下导演风格与镜头调度原则：\n"
                f"{skill_body}\n"
            )

    ref_rule = (
        f"单人戏在 `【第N段｜{segment_seconds}秒】` 下一行写标准参考图对齐指令：\n"
        f"`{DEFAULT_REF_ALIGN_LINE}`\n"
        "双人戏如果涉及两个固定角色（如师妹与大师兄），必须写双参考图对齐指令：\n"
        "`How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.`\n"
        "并在正文中严格用 `<Picture 1>白衣年轻女子`、`<Picture 2>蓝衣青年男子` 明确区分两个角色的性别、服装和左右站位！"
        if has_ref_image else
        "不使用 `<Picture 1>` 标签，但每段必须写清一致的人物性别、服装、外貌与左右站位。"
    )

    continuity_mandate = f"""
## 12. 针对 {segment_count} 段 × {segment_seconds} 秒（共 {segment_count * segment_seconds} 秒）长视频的【同空间强连贯与防崩坏硬性铁律】

1. **严禁跨段瞬移换场（最高优先级）**：
   - 全片 {segment_count} 段必须发生在**同一个连续的物理空间、同一时间段、同一主光源方向与色温基准**下。
2. **双人戏严禁角色脱画与李代桃僵（极其关键）**：
   - 如果场景中有两个角色对话（例如左侧 `<Picture 1>年轻女子` 与右侧 `<Picture 2>青年男子`），**全片 {segment_count} 段必须全程保持双人同框（左侧始终是 `<Picture 1>女子`，右侧始终是 `<Picture 2>男子`），采用固定凝视机位或小幅推拉**！
   - **绝对禁止在中间某一段切单人镜头或移动镜头把其中一人甩出画面外**！因为在尾帧 Latent 续写模式下，一旦某一段尾帧只剩一个人，下一段模型会把留在画面里的人错误替换成另一个角色，甚至凭空刷出第三个人！
3. **说话人标签必须带明确性别与音色，严禁写模糊的“主角 (S1)”或“人物 (S1)”**：
   - 说话行必须明确写出角色性别，例如：`清脆年轻女子 (S1) says: <d>[Chinese]...</d>` 或 `沉稳青年男子 (S2) says: <d>[Chinese]...</d>`。
   - **绝对禁止写 `主角 (S1) says:` 或 `人物 (S1) says:`**！不写性别会导致 H3 模型给女角色配男声或给男角色配女声！
4. **`non_diegetic_music` 必须全部固定写 `N/A`**：
   - 每一段的 `non_diegetic_music:` 必须严格写 `non_diegetic_music: N/A`（与原项目 13 号工作流完全一致），严禁在每段写钢琴、笛子等配乐描述，否则每 5 秒生成的独立配乐会在段落接缝处发生刺耳的断音和跳变！
5. **段尾未完成动作钩子（Action Hook）与下段 0.0 秒无缝咬合**：
   - 第 N 段的最后时间段（`3.6～{segment_seconds}.0秒`）必须停在一个具体、可见、可接住下一段的姿态或动作钩子。
   - 第 N+1 段的开头（`0.0～0.8秒`）必须直接从第 N 段结束时的精确身体姿态、手部位置和视线方向接着往下演，严禁凭空变出或凭空消失道具！
6. **段数与时长严格对齐**：
   - 必须精确输出 **{segment_count} 段**，从 `【第1段｜{segment_seconds}秒】` 一直到 `【第{segment_count}段｜{segment_seconds}秒】`。
   - {ref_rule}
   - 严禁出现“无字幕、不要说话、禁止、保持一致、No on-screen text”等任何元指令词。
   - 第一个字符必须是 `【第1段｜{segment_seconds}秒】` 的 `【`，不输出任何前缀、解释或代码块围栏。
"""
    return f"{template_md}\n{continuity_mandate}{skill_section}"


def compile_aicg3d_script(
    story: str,
    skill_id: str = "short-scene-director-lite",
    segment_count: int = 12,
    segment_seconds: int = 5,
    has_ref_image: bool = True,
    character_desc: str = "",
    model_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调用工作台配置的 LLM，按 comfyui-AICG3D 原装编译器规范生成 N 段连续提示词。"""
    story_text = str(story or "").strip()
    if not story_text:
        raise ValueError("请输入故事构想或剧情梗概")
    segment_count = max(1, min(int(segment_count or 12), 24))
    segment_seconds = max(3, min(int(segment_seconds or 5), 10))

    if not model_config or not model_config.get("endpoint"):
        raise ValueError("工作台尚未配置可用的 LLM 模型接口")

    system_prompt = _build_compiler_system_prompt(
        skill_id=skill_id,
        segment_count=segment_count,
        segment_seconds=segment_seconds,
        has_ref_image=has_ref_image,
    )
    user_prompt = (
        f"/no_think\n"
        f"请将以下故事创意编译为精确的 {segment_count} 段（每段 {segment_seconds} 秒，总长 {segment_count * segment_seconds} 秒）"
        f"MiniMax H3 无限分段视频提示词：\n\n"
        f"【故事创意与剧情】\n{story_text}\n\n"
    )
    if character_desc.strip():
        user_prompt += f"【角色外观与参考图说明】\n{character_desc.strip()}\n\n"
    user_prompt += (
        f"【硬性要求】\n"
        f"1. 必须完整输出 `【第1段｜{segment_seconds}秒】` 到 `【第{segment_count}段｜{segment_seconds}秒】` 共 {segment_count} 段。\n"
        f"2. 全片 {segment_count} 段必须保持在同一具体场景空间与同一光线基准下连续推进，严禁跨段瞬移换场。\n"
        f"3. 若为双人戏，必须全程保持双人同框（左 `<Picture 1>` 右 `<Picture 2>` 固定站位），严禁单人特写出画导致后半段角色丢失或错位！\n"
        f"4. 说话人前缀必须带明确性别（如 `清脆年轻女子 (S1) says: <d>[Chinese]...</d>`、`沉稳青年男子 (S2) says: <d>[Chinese]...</d>`），严禁写模糊的 `主角 (S1)` 或 `人物 (S1)`！\n"
        f"5. 每一段的 `non_diegetic_music:` 必须固定写 `non_diegetic_music: N/A`。\n"
        f"6. 直接以 `【第1段｜{segment_seconds}秒】` 开头输出纯文本，不要任何寒暄或 Markdown 代码围栏。"
    )

    payload = {
        "model": model_config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": min(int(model_config.get("max_output_tokens", 16384)), 16384),
        "temperature": 0.3,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Content-Type": "application/json"}
    if model_config.get("api_key"):
        headers["Authorization"] = f"Bearer {model_config['api_key']}"

    request = urllib.request.Request(
        model_config["endpoint"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    timeout = max(int(model_config.get("timeout_seconds", 120)), 90)
    try:
        with opener.open(request, timeout=timeout) as resp:
            result = json.loads(resp.read(8 * 1024 * 1024).decode("utf-8"))
        content = str(result["choices"][0]["message"].get("content") or "").strip()
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError(f"AICG3D 提示词编译器调用模型返回 HTTP {code}") from None
    except Exception as exc:
        raise ValueError(f"AICG3D 提示词编译器调用模型失败：{exc}") from None

    content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()

    raw_segments, _ = parse_aicg3d_prompt_text(content, default_seconds=float(segment_seconds))
    if not raw_segments:
        raise ValueError("模型未返回可解析的分段提示词，请重试")

    cleaned_segments = [
        sanitize_aicg3d_segment(seg, has_ref_image=has_ref_image)
        for seg in raw_segments[:segment_count]
    ]
    while len(cleaned_segments) < segment_count:
        cleaned_segments.append(
            sanitize_aicg3d_segment(
                f"{DEFAULT_REF_ALIGN_LINE}\n\n"
                f"integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，保持同一场景与主光色温。"
                f"中景平视机位，<Picture 1> 承接上一镜头结束时的站位与姿态。\n"
                f"0.0～1.6秒：<Picture 1> 呼吸平稳，目光沿着当前方向自然延展。\n"
                f"1.6～3.6秒：镜头做极其轻微的平稳推进，环境光影柔和流淌。\n"
                f"3.6～{segment_seconds}.0秒：画面停留在人物神情笃定、姿态稳定的衔接瞬间。\n\n"
                f"overall_soundscape: 持续的现场自然环境声与轻微衣料摩擦声。\n\n"
                f"non_diegetic_music: N/A",
                has_ref_image=has_ref_image,
            )
        )

    formatted_raw = format_full_aicg3d_script(cleaned_segments, segment_seconds)
    return {
        "ok": True,
        "skill_id": skill_id,
        "segmentCount": len(cleaned_segments),
        "segmentSeconds": segment_seconds,
        "rawScript": formatted_raw,
        "segments": cleaned_segments,
        "acts": segments_to_acts(cleaned_segments, segment_seconds=segment_seconds),
        "model": result.get("model", model_config.get("model", "")),
    }


def parse_raw_script_payload(
    raw_script: str,
    segment_seconds: int = 5,
    has_ref_image: bool = True,
) -> dict[str, Any]:
    """将用户粘贴或编辑的整篇【第1段｜5秒】剧本解析为导演台 4 幕 / N 镜结构。"""
    raw_segments, secs = parse_aicg3d_prompt_text(raw_script, default_seconds=float(segment_seconds))
    if not raw_segments:
        raise ValueError("未能从文本中解析出分段，请使用【第1段｜5秒】…【第N段｜5秒】格式")
    cleaned = [sanitize_aicg3d_segment(seg, has_ref_image=has_ref_image) for seg in raw_segments]
    return {
        "ok": True,
        "segmentCount": len(cleaned),
        "segmentSeconds": int(secs[0]) if secs else segment_seconds,
        "rawScript": format_full_aicg3d_script(cleaned, segment_seconds),
        "segments": cleaned,
        "acts": segments_to_acts(cleaned, segment_seconds=segment_seconds),
    }
