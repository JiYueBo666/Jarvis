VALID_STATUS = {"pending", "in_progress", "completed"}
VALID_PRIORITY = {"low", "normal", "high"}


class TodoLedger:
    def __init__(self, runtime):
        self.runtime = runtime
        self.runtime.session.setdefault("todos", {"next_id": 1, "items": []})

    @property
    def state(self):
        return self.runtime.session.setdefault("todos", {"next_id": 1, "items": []})
