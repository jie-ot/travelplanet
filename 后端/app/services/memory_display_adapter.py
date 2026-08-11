"""Display adapter for user-facing travel memory.

The adapter formats existing `memory_json` / `memory_text` into a small set of
editable notes. It avoids fixed profile categories and never exposes raw fields.
"""

from __future__ import annotations

import re
from datetime import datetime
from hashlib import sha1

from app.models.dto import (
    MemoryDisplayItem,
    MemoryPlanningPreferenceField,
    TravelMemoryDisplay,
)
from app.models.user_memory import UserMemory

LOW_VALUE_PATTERNS = (
    "喜欢上海旅游",
    "关注城市旅游内容",
    "喜欢人文类内容",
    "喜欢人文类",
)

LOCATION_ONLY_PATTERN = re.compile(r"^[\u4e00-\u9fffA-Za-z·\-\s]{1,12}(旅游|旅行|行程|内容)?$")

PREFIXES = (
    "用户多次",
    "用户经常",
    "用户喜欢",
    "用户偏好",
    "用户倾向",
    "目的地偏好：",
    "目的地偏好:",
    "偏好：",
    "偏好:",
    "喜欢",
    "关注",
)

ICON_POOL = ("✦", "◦", "✧", "·", "✺", "✱")

PLANNING_FIELD_META = (
    ("transport", "交通偏好", "例如：更愿意高铁 / 自驾 / 少换乘"),
    ("hotel", "酒店偏好", "例如：想住市中心 / 靠近地铁 / 更安静"),
    ("attractions", "景点偏好", "例如：更喜欢老城街区 / 博物馆 / 自然景观"),
    ("food", "餐饮偏好", "例如：偏好本地菜 / 咖啡馆 / 市集"),
    ("pace", "行程节奏", "例如：不要太赶 / 留出午休 / 方便临时停留"),
    ("other", "其他特别偏好", "例如：想避开爬坡 / 带老人小孩 / 需要拍照时间"),
)

SOURCE_LABELS = {
    "generate": "照片分析",
    "plan_save": "旅行规划",
    "plan_update": "旅行规划",
    "postcard": "明信片创作",
}


class MemoryDisplayAdapter:
    """Convert stored memories into concise, editable display notes."""

    def to_display(self, memory: UserMemory) -> TravelMemoryDisplay:
        preferences = self._active_preferences(memory)
        items: list[MemoryDisplayItem] = []
        seen_topics: set[str] = set()
        for pref in preferences:
            item = self._to_item(pref, len(items))
            topic = self._topic_key(item.title, item.content)
            if topic in seen_topics:
                continue
            seen_topics.add(topic)
            items.append(item)
        is_empty = not items
        overview_title, overview_content = self._overview(memory, items)
        planning_preferences = self._planning_preferences(memory)
        return TravelMemoryDisplay(
            intro=self._intro(items),
            overview_title=overview_title,
            overview_content=overview_content,
            planning_preferences=planning_preferences,
            memories=items[:6],
            editable=True,
            updated_at=self._format_datetime(memory.updated_at),
            version=memory.version,
            is_empty=is_empty,
        )

    def source_text_for_description(self, memory: UserMemory, description_id: str) -> list[str]:
        preferences = self._active_preferences(memory)
        target_topics = {
            self._topic_key(item.title, item.content)
            for pref in preferences
            if self._item_id(pref) == description_id or self._has_source_ref(pref, description_id)
            for item in [self._to_item(pref, 0)]
        }
        if not target_topics:
            return []
        return [
            self._summary_text(pref)
            for pref in preferences
            if self._topic_key_for_pref(pref) in target_topics
        ]

    def _active_preferences(self, memory: UserMemory) -> list[dict]:
        mem_json = memory.memory_json or {}
        hidden_ids = self._hidden_display_ids(mem_json)
        raw = mem_json.get("preferences", [])
        preferences: list[dict] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict) or item.get("status") == "weakened":
                    continue
                if self._is_hidden(item, hidden_ids):
                    continue
                summary = self._summary_text(item)
                if self._is_displayable(summary):
                    preferences.append(item)

        if not preferences and not self._is_custom_overview_text(memory.memory_text):
            preferences = [
                {"summary": summary, "confidence": 0, "source_refs": []}
                for summary in self._summaries_from_text(memory.memory_text)
            ]

        visible = [pref for pref in preferences if not self._is_hidden(pref, hidden_ids)]
        return sorted(visible, key=lambda p: p.get("confidence", 0), reverse=True)

    def _to_item(self, pref: dict, index: int) -> MemoryDisplayItem:
        raw = self._summary_text(pref)
        title, content = self._split_edited_note(raw)
        if not title or not content:
            clean = self._clean_summary(raw)
            title = self._title_from_summary(clean)
            content = self._content_from_summary(clean)
        return MemoryDisplayItem(
            id=self._item_id(pref),
            icon=ICON_POOL[index % len(ICON_POOL)],
            title=title,
            content=content,
            planning_hint=self._planning_hint_from_summary(raw),
            source_labels=self._source_labels(pref, raw),
            editable=True,
        )

    def _split_edited_note(self, text: str) -> tuple[str, str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= 2:
            return self._strip_source_marker(lines[0]), "\n".join(lines[1:])
        return "", ""

    def _strip_source_marker(self, text: str) -> str:
        return text.strip(" 「」“”")

    def _clean_summary(self, text: str) -> str:
        cleaned = re.sub(r"（置信度\s*\d+(\.\d+)?）", "", text).strip()
        cleaned = cleaned.replace("旅行相关内容", "").replace("旅游相关内容", "")
        cleaned = cleaned.strip(" -，,。；;")
        for prefix in PREFIXES:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip(" ：:，,")
        return cleaned or text.strip()

    def _title_from_summary(self, summary: str) -> str:
        summary = self._abstract_summary(summary)
        if self._has_nature_and_culture(summary):
            return "偏好自然与人文兼具的目的地"
        if any(word in summary for word in ("照片", "摄影", "拍照", "记录")):
            return "偏好用画面记录旅行氛围"
        if any(word in summary for word in ("自由", "弹性", "慢", "不赶", "深度")):
            return "偏好留有余地的旅行节奏"
        if any(word in summary for word in ("历史", "博物馆", "老城", "街区", "文化", "故事")):
            return "偏好有地方故事感的旅行目的地"
        if any(word in summary for word in ("城市", "建筑", "街道", "夜景")):
            return "重视城市空间和街区细节"
        if any(word in summary for word in ("自然", "风景", "山", "海", "湖", "森林", "日落", "滨海")):
            return "重视自然环境带来的空间感"
        if any(word in summary for word in ("美食", "餐厅", "小吃", "咖啡")):
            return "重视在地味道和街区生活"
        return "形成中的稳定旅行偏好"

    def _content_from_summary(self, summary: str) -> str:
        phrase = self._abstract_summary(summary)
        if self._has_nature_and_culture(summary):
            return self._format_analysis(
                "更容易被“景观氛围 + 地方故事”同时成立的目的地打动，而不是单一景点。",
                "你在选择旅行内容时，既在意空间打开后的感受，也在意这个地方有没有可被理解的历史、街区和生活线索。",
                "优先选择老城、滨水街区、博物馆周边或能连续步行体验的片区，减少纯打卡型景点。",
            )
        if any(word in summary for word in ("照片", "摄影", "拍照", "记录", "明信片")):
            return self._format_analysis(
                "你记录旅行时更重视画面气质、光线和现场感，不只是完成“到此一游”。",
                "这说明你对一趟旅行的满意度，和街道层次、黄昏时段、停留空间这些视觉条件关系很大。",
                "规划时要给黄昏、步行街道、观景停留和拍照缓冲留时间，不适合把节奏压得过满。",
            )
        if any(word in summary for word in ("自由", "弹性", "慢", "不赶", "深度", "轻松")):
            return self._format_analysis(
                "你更在意行程是否有余地，而不是一天塞进多少个点。",
                "这类偏好通常意味着你重视停留质量、临时调整空间，以及围绕少数片区做更完整的体验。",
                "路线应减少跨城来回和高频换点，把缓冲、午休、临时停留写进计划本身。",
            )
        if any(word in summary for word in ("历史", "博物馆", "老城", "街区", "文化", "故事", "建筑")):
            return self._format_analysis(
                "你更看重一个地方长期积累下来的历史层次、建筑肌理和街区故事。",
                "这说明你对目的地的兴趣不是“著名景点数量”，而是一个片区能否持续提供文化背景和在地感。",
                "适合优先安排老城、博物馆、历史街区和步行友好的路线，少做分散跳点。",
            )
        if any(word in summary for word in ("城市", "街道", "夜景")):
            return self._format_analysis(
                "你对城市的兴趣落在街道尺度、建筑轮廓、夜间光线和人的活动痕迹上。",
                "这种偏好说明你适合通过街区连续步行去理解一座城市，而不是只靠景点清单建立印象。",
                "酒店更适合放在核心街区附近，路线优先安排可步行、细节密度高的城区。",
            )
        if any(word in summary for word in ("自然", "风景", "山", "海", "湖", "森林", "日落", "安静")):
            return self._format_analysis(
                "你重视自然场景带来的空间感、开阔感和情绪变化，不只是“景色好看”。",
                "这意味着自然环境本身就是行程价值的一部分，需要停留、观察和转换节奏，而不是匆忙经过。",
                "适合把海岸、湖面、山林、日落时段放进更舒展的路线里，并放宽交通衔接。",
            )
        if any(word in summary for word in ("美食", "餐厅", "小吃", "咖啡", "市集", "地方生活")):
            return self._format_analysis(
                "你对吃喝的兴趣更偏向在地生活体验，而不是单纯追逐热门店。",
                "这说明餐饮对你来说不仅是补给，更是理解街区气质、作息节奏和地方生活方式的一条线索。",
                "适合把市集、小店、地方菜和街区散步打包安排，让餐饮自然嵌入路线中段。",
            )
        return self._format_analysis(
            f"当前稳定偏好指向：{phrase}。",
            "这条信息说明你在目的地选择、停留方式或记录方式上已经出现重复倾向，不是一次性的偶然选择。",
            "后续规划可以把它当成筛选路线和安排行程密度的依据。",
        )

    def _format_analysis(self, judgment: str, value: str, planning: str) -> str:
        return "\n".join(
            (
                f"- 核心判断：{judgment}",
                f"- 价值线索：{value}",
                f"- 规划使用：{planning}",
            )
        )

    def _planning_hint_from_summary(self, summary: str) -> str | None:
        text = self._clean_summary(summary)
        if self._has_nature_and_culture(text):
            return "优先安排历史片区、滨水街区或博物馆周边，少放纯打卡点。"
        if any(word in text for word in ("照片", "摄影", "拍照", "记录", "明信片")):
            return "预留黄昏、街道和停留时间，保证拍照和观察空间。"
        if any(word in text for word in ("自由", "弹性", "慢", "不赶", "深度", "轻松")):
            return "酒店和景点之间要留缓冲，避免单日安排过满。"
        if any(word in text for word in ("历史", "博物馆", "老城", "街区", "文化", "故事", "建筑")):
            return "把老城、展馆和步行街区串起来，比跳点式安排更合适。"
        if any(word in text for word in ("城市", "街道", "夜景")):
            return "酒店放在核心街区附近，方便步行观察城市细节。"
        if any(word in text for word in ("自然", "风景", "山", "海", "湖", "森林", "日落", "安静")):
            return "放宽观景时间和交通衔接，别把自然场景压成匆忙经过。"
        if any(word in text for word in ("美食", "餐厅", "小吃", "咖啡", "市集", "地方生活")):
            return "把餐饮和街区散步一起设计，让吃喝成为路线的一部分。"
        return "可用于筛选目的地、住宿位置和行程密度。"

    def _planning_preferences(self, memory: UserMemory) -> list[MemoryPlanningPreferenceField]:
        mem_json = memory.memory_json or {}
        raw = mem_json.get("planning_preferences", {})
        values = raw if isinstance(raw, dict) else {}
        return [
            MemoryPlanningPreferenceField(
                key=key,
                label=label,
                value=str(values.get(key) or ""),
                placeholder=placeholder,
                helper=self._planning_field_helper(key),
            )
            for key, label, placeholder in PLANNING_FIELD_META
        ]

    def _planning_field_helper(self, key: str) -> str:
        helpers = {
            "transport": "会优先影响交通方式、换乘次数和目的地之间的连接。",
            "hotel": "会优先影响酒店位置、通勤半径和夜间活动安排。",
            "attractions": "会优先影响景点组合、停留时长和路线顺序。",
            "food": "会优先影响餐饮安排和街区的穿插方式。",
            "pace": "会优先影响每天的行程密度和缓冲时间。",
            "other": "会优先影响路线细节和特殊避让项。",
        }
        return helpers.get(key, "会优先影响后续规划细节。")

    def _natural_phrase(self, summary: str) -> str:
        phrase = summary.strip("。；;，,")
        if phrase.startswith("避免"):
            return phrase
        return phrase

    def _has_nature_and_culture(self, summary: str) -> bool:
        has_nature = any(word in summary for word in ("自然", "风景", "山", "海", "湖", "森林", "日落", "滨海"))
        has_culture = any(word in summary for word in ("人文", "历史", "文化", "建筑", "街区", "故事", "博物馆", "老城"))
        return has_nature and has_culture

    def _abstract_summary(self, summary: str) -> str:
        phrase = self._natural_phrase(summary)
        phrase = re.sub(r"^(目的地)?偏好[:：]?", "", phrase).strip()
        phrase = phrase.replace("北方滨海城市", "具有地域气质的城市空间")
        phrase = phrase.replace("海边", "自然环境和开放空间")
        phrase = re.sub(r"^[\u4e00-\u9fffA-Za-z·\-\s]{1,12}(、[\u4e00-\u9fffA-Za-z·\-\s]{1,12})+(等)?", "多个地方", phrase)
        phrase = phrase.replace("旅行相关内容", "").replace("旅游相关内容", "")
        return phrase.strip(" ：:，,。；;") or "旅行中反复出现的偏好"

    def _summary_text(self, item: dict) -> str:
        return str(item.get("summary") or item.get("key") or "").strip()

    def _summaries_from_text(self, text: str | None) -> list[str]:
        if not text:
            return []
        summaries: list[str] = []
        for line in text.splitlines():
            cleaned = self._clean_summary(line)
            if self._is_displayable(cleaned):
                summaries.append(cleaned)
        return summaries

    def _is_displayable(self, summary: str) -> bool:
        cleaned = self._clean_summary(summary)
        if not cleaned or cleaned in LOW_VALUE_PATTERNS:
            return False
        if len(cleaned) < 8:
            return False
        if LOCATION_ONLY_PATTERN.fullmatch(cleaned) and not any(
            word in cleaned
            for word in ("历史", "文化", "建筑", "街道", "夜景", "自然", "风景", "照片", "节奏", "美食")
        ):
            return False
        if re.fullmatch(r"[\w\u4e00-\u9fff]{1,8}(旅游|旅行|内容)", cleaned):
            return False
        return True

    def _item_id(self, pref: dict) -> str:
        refs = pref.get("source_refs", [])
        if isinstance(refs, list):
            edited_ref = next((str(ref) for ref in refs if str(ref).startswith("memory_note_")), None)
            if edited_ref:
                return edited_ref
        key = str(pref.get("key") or self._summary_text(pref))
        digest = sha1(key.encode("utf-8")).hexdigest()[:10]
        return f"memory_note_{digest}"

    def _has_source_ref(self, item: dict, source_ref: str) -> bool:
        refs = item.get("source_refs", [])
        return isinstance(refs, list) and source_ref in refs

    def _hidden_display_ids(self, mem_json: dict) -> set[str]:
        hidden = mem_json.get("hidden_display_ids", [])
        if not isinstance(hidden, list):
            return set()
        return {str(item) for item in hidden}

    def _is_hidden(self, item: dict, hidden_ids: set[str]) -> bool:
        if self._item_id(item) in hidden_ids:
            return True
        refs = item.get("source_refs", [])
        return isinstance(refs, list) and any(str(ref) in hidden_ids for ref in refs)

    def _topic_key_for_pref(self, pref: dict) -> str:
        item = self._to_item(pref, 0)
        return self._topic_key(item.title, item.content)

    def _topic_key(self, title: str, content: str) -> str:
        text = self._clean_summary(f"{title} {content}")
        groups = (
            ("故事", "历史", "博物馆", "老街", "建筑", "文化", "展馆", "地方生活"),
            ("照片", "摄影", "光影", "画面", "明信片", "氛围"),
            ("自由", "弹性", "慢", "节奏", "停留", "绕路"),
            ("自然", "风景", "山", "海", "湖", "森林", "日落", "安静"),
        )
        for group in groups:
            if any(word in text for word in group):
                return "|".join(group[:2])
        return text[:24]

    def _intro(self, items: list[MemoryDisplayItem]) -> str | None:
        if len(items) < 3:
            return None
        return "这些内容来自多次旅行记录中的重复线索，可直接作为后续规划依据。"

    def _source_labels(self, pref: dict, raw: str) -> list[str]:
        labels: list[str] = []
        refs = pref.get("source_refs", [])
        if isinstance(refs, list):
            if any(str(ref).startswith("plan_") for ref in refs):
                labels.append("旅行规划")
            if any(str(ref).startswith("postcard_") for ref in refs):
                labels.append("明信片创作")
            if any(str(ref).startswith("asset_") or str(ref).startswith("report_") for ref in refs):
                labels.append("照片分析")
        source_type = pref.get("source_type")
        if isinstance(source_type, str) and source_type in SOURCE_LABELS:
            labels.append(SOURCE_LABELS[source_type])
        if any(word in raw for word in ("照片", "摄影", "拍照", "画面")):
            labels.append("照片分析")
        if any(word in raw for word in ("明信片", "创作")):
            labels.append("明信片创作")
        if not labels:
            labels.append("旅行规划")
        return list(dict.fromkeys(labels))[:3]

    def _overview(self, memory: UserMemory, items: list[MemoryDisplayItem]) -> tuple[str | None, str | None]:
        mem_json = memory.memory_json or {}
        overview = mem_json.get("display_overview")
        if not isinstance(overview, dict):
            text = (memory.memory_text or "").strip()
            if self._is_custom_overview_text(text):
                return "旅行记忆概述", text
            if items:
                return "旅行记忆概述", self._overview_from_items(items)
            return "旅行记忆概述", "星球还在根据你的旅行记录学习你的长期偏好。"
        title = str(overview.get("title") or "").strip() or None
        content = str(overview.get("content") or "").strip() or None
        return title, content

    def _format_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()

    def _overview_from_items(self, items: list[MemoryDisplayItem]) -> str:
        text = " ".join(f"{item.title} {item.content}" for item in items)
        destination_trait = ""
        rhythm_trait = ""
        recording_trait = ""
        if self._has_nature_and_culture(text):
            destination_trait = "目的地选择上，偏好兼具自然层次和地方故事感的区域。"
        elif any(word in text for word in ("历史", "文化", "故事", "建筑", "街区", "博物馆", "老城")):
            destination_trait = "目的地选择上，更看重历史层次、建筑肌理和街区生活。"
        elif any(word in text for word in ("自然", "风景", "山", "海", "湖", "森林", "日落", "空间氛围")):
            destination_trait = "目的地选择上，重视自然环境带来的空间感和情绪变化。"

        if any(word in text for word in ("照片", "摄影", "画面", "光线", "明信片", "现场感", "记忆切片")):
            recording_trait = "记录方式上，偏好通过画面、光线和现场感保存旅行记忆。"
        if any(word in text for word in ("慢", "弹性", "留有", "停留", "不赶", "慢逛", "深度")):
            rhythm_trait = "行程组织上，适合留有缓冲、允许停留和临时调整的节奏。"
        if any(word in text for word in ("美食", "地方菜", "市集", "小店", "咖啡", "当地生活")):
            destination_trait = destination_trait or "体验重点上，会把在地味道和街区生活当作理解城市的重要入口。"

        lines = ["- 核心结论：你的旅行偏好已经出现较稳定的重复倾向。"]
        if destination_trait:
            lines.append(f"- 目的地偏好：{destination_trait}")
        if rhythm_trait:
            lines.append(f"- 行程偏好：{rhythm_trait}")
        if recording_trait:
            lines.append(f"- 记录偏好：{recording_trait}")
        if len(lines) == 1:
            lines.append("- 当前重点：更适合围绕氛围、节奏和停留质量来筛选路线，而不是单纯堆景点。")
        return "\n".join(lines)

    def _is_custom_overview_text(self, text: str | None) -> bool:
        value = (text or "").strip()
        return bool(value) and not value.startswith("稳定旅行偏好：") and not value.startswith("暂无稳定偏好")
