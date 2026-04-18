class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, name: str, func: callable, description: str, input_schema: dict) -> None:
        self._tools[name] = {
            "func": func,
            "schema": {
                "name": name,
                "description": description,
                "input_schema": input_schema
            }
        }

    def get_func(self, name: str) -> callable:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Tool '{name}' not found")
        return tool["func"]

    def get_schemas(self) -> list[dict]:
        return [tool["schema"] for tool in self._tools.values()]
