"""Explicit interview specimen; never installed by the production API."""
from pathlib import Path

from adaptive_learning.api.app import store
from adaptive_learning.api.store import BookRecord
from adaptive_learning.assessment.models import DiagnosticItem, ResponseType


def install_demo_book() -> None:
    if "demo_book" in store.books:
        return
    items = [
        DiagnosticItem(
            item_id="diag_1",
            chapter_id="ch_1",
            knowledge_point_ids=["system_definition"],
            prompt="下面哪项最符合“系统”的含义？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                "若干事物的简单集合",
                "相互作用并形成整体行为的要素集合",
                "只有机器才是系统",
                "任何复杂对象",
            ],
            correct_option_ids=["1"],
            difficulty=-0.7,
            discrimination=1.2,
        ),
        DiagnosticItem(
            item_id="diag_2",
            chapter_id="ch_2",
            knowledge_point_ids=["feedback"],
            prompt="一个变化会进一步强化自身，这通常属于哪类反馈？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=["负反馈", "正反馈", "无反馈", "随机反馈"],
            correct_option_ids=["1"],
            difficulty=-0.2,
            discrimination=1.4,
        ),
        DiagnosticItem(
            item_id="diag_3",
            chapter_id="ch_3",
            knowledge_point_ids=["delay", "emergence"],
            prompt="系统中的时间延迟为什么容易导致判断失误？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                "行动与结果存在间隔，容易造成错误归因和过度调整",
                "所有结果都会立即出现，因此人们通常反应不足",
                "时间延迟会消除反馈，所以系统不再发生变化",
                "时间延迟只改变记录方式，不影响人的判断过程",
            ],
            correct_option_ids=["0"],
            difficulty=0.6,
            discrimination=1.5,
        ),
        DiagnosticItem(
            item_id="diag_4",
            chapter_id="ch_4",
            knowledge_point_ids=["intervention"],
            prompt="解决复杂问题时，为什么不应只处理最明显的症状？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                "症状可能源于深层结构，局部处理还可能转移问题或产生反作用",
                "最明显的症状通常与系统结构完全无关，也不需要继续观察",
                "复杂问题没有可以分析的原因，所以只能等待症状自行消失",
                "处理症状一定会解决所有结构问题，不需要检查后续影响",
            ],
            correct_option_ids=["0"],
            difficulty=0.9,
            discrimination=1.4,
        ),
        DiagnosticItem(
            item_id="diag_5",
            chapter_id="ch_1",
            knowledge_point_ids=["boundary"],
            prompt="分析一个问题时，系统边界的主要作用是什么？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                "证明问题很简单",
                "决定暂时纳入哪些要素和关系",
                "排除所有外部影响",
                "找到唯一正确答案",
            ],
            correct_option_ids=["1"],
            difficulty=0.1,
            discrimination=1.3,
        ),
    ]
    store.books["demo_book"] = BookRecord(
        book_id="demo_book",
        original_name="理解复杂系统.pdf",
        file_path=Path("demo.pdf"),
        status="ready",
        progress=1,
        current_step="已生成章节结构与诊断题",
        diagnostic_items=items,
    )
