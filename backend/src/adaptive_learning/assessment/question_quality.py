import re

from .models import DiagnosticItem


def has_answer_polarity_conflict(item: DiagnosticItem) -> bool:
    """The generated bank uses a true source proposition as its answer.

    A negative stem contradicts that contract. Reject only this known generated
    form; independently authored negative questions may have valid answer keys.
    """
    negative = re.search(r"不正确|不属于|错误的是|错误的说法|不符合|不包括", item.prompt)
    if not negative or not item.knowledge_point_labels:
        return False
    return any(
        option_id.isdigit()
        and int(option_id) < len(item.options)
        and item.options[int(option_id)] in item.knowledge_point_labels
        for option_id in item.correct_option_ids
    )
