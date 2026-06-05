import json
from collections import OrderedDict


def tail_clip(text, limit):
    text = str(text)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


class TurnHistoryBuilder:
    def __init__(self, agent):
        self.agent = agent

    def enrich(self, item):
        """
        给每一条消息自动打上唯一 ID：turn_id、run_id、event_id，确保每条消息都能被追踪、溯源、不混乱。
        """
        item = dict(item)
        """
        同一个用户提问 + AI 回答，共用同一个 turn_id
        比如：
        用户：你好
        AI：你好呀
        这两条 turn_id 相同，表示是同一回合。
        """
        if not item.get("turn_id"):
            current_turn = str(getattr(self.agent, "current_turn_id", "") or "")
            if not current_turn:
                if item.get(
                    "role"
                ) == "user" or not self.agent.session.get(  # “还没有生成过手动回合 ID”
                    "_manual_turn_id"
                ):
                    self.agent.session["_manual_turn_seq"] = (
                        int(self.agent.session.get("_manual_turn_seq", 0)) + 1
                    )
                    self.agent.session["_manual_turn_id"] = (
                        f"manual_{self.agent.session['_manual_turn_seq']:06d}"
                    )
                current_turn = str(self.agent.session.get("_manual_turn_id", "legacy"))
            item["turn_id"] = current_turn
        if not item.get("run_id"):
            item["run_id"] = str(getattr(self.agent, "current_run_id", "") or "")
        if not item.get("event_id"):
            self.agent.session["_event_seq"] = (
                int(self.agent.session.get("_event_seq", 0)) + 1
            )
            item["event_id"] = f"event_{self.agent.session['_event_seq']:06d}"
        item.setdefault("source", "runtime")
        return item
