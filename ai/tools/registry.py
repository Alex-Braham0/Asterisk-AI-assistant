import inspect
from copy import deepcopy
from typing import Callable, Dict, Type, List
from pydantic import BaseModel

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._schemas: list = []
        self._metadata: Dict[str, dict] = {}

    def register(
        self, 
        name: str, 
        description: str, 
        args_schema: Type[BaseModel] = None,
        log_execution: bool = True,
        hide_args: List[str] = None,
        log_result: bool = True,
        restricted: bool = False  # --- NEW: Security Flag ---
    ):
        def decorator(func: Callable):
            self._tools[name] = func
            self._metadata[name] = {
                "log_execution": log_execution,
                "hide_args": set(hide_args or []) | {"context"},
                "log_result": log_result,
                "restricted": restricted
            }
            
            schema = {
                "name": name,
                "description": description,
                "parameters": {"type": "OBJECT", "properties": {}}
            }
            
            if args_schema:
                pydantic_schema = args_schema.model_json_schema()
                defs = pydantic_schema.get("$defs", {})
                
                def resolve_schema(schema_node):
                    if "$ref" in schema_node:
                        ref_key = schema_node["$ref"].split("/")[-1]
                        schema_node = defs.get(ref_key, {})
                        
                    node_type = schema_node.get("type", "string").upper()
                    gemini_node = {"type": node_type}
                    
                    if "description" in schema_node:
                        gemini_node["description"] = schema_node["description"]
                        
                    if node_type == "ARRAY":
                        gemini_node["items"] = resolve_schema(schema_node.get("items", {}))
                    elif node_type == "OBJECT":
                        gemini_node["properties"] = {}
                        for k, v in schema_node.get("properties", {}).items():
                            gemini_node["properties"][k] = resolve_schema(v)
                        if "required" in schema_node:
                            gemini_node["required"] = schema_node["required"]
                            
                    return gemini_node

                gemini_props = {}
                for prop_name, prop_details in pydantic_schema.get("properties", {}).items():
                    gemini_props[prop_name] = resolve_schema(prop_details)
                    
                schema["parameters"]["properties"] = gemini_props
                if "required" in pydantic_schema:
                    schema["parameters"]["required"] = pydantic_schema["required"]

            self._schemas.append(schema)
            return func
        return decorator

    def get_declarations(self) -> list:
        return [{"functionDeclarations": self._schemas}] if self._schemas else []

    def should_log(self, name: str) -> bool:
        return self._metadata.get(name, {}).get("log_execution", True)

    def sanitize_args(self, name: str, args: dict) -> dict:
        hide_keys = self._metadata.get(name, {}).get("hide_args", {"context"})
        clean_args = {}
        for k, v in args.items():
            if k not in hide_keys:
                try:
                    clean_args[k] = deepcopy(v)
                except Exception:
                    clean_args[k] = str(v)
        return clean_args

    def sanitize_result(self, name: str, result: dict) -> dict:
        if not self._metadata.get(name, {}).get("log_result", True):
            return {"status": "logged_without_details"}
        try:
            return deepcopy(result)
        except Exception:
            return {"result": str(result)}

    async def execute(self, name: str, args: dict, context: dict = None) -> dict:
        if name not in self._tools:
            return {"error": f"Tool '{name}' not found."}
        
        context = context or {}
        
        # --- NEW: Enforce Restricted Tool Security ---
        is_restricted = self._metadata.get(name, {}).get("restricted", False)
        if is_restricted and not context.get("restricted_unlocked", False):
            print(f"[ToolRegistry] 🛑 Blocked unauthorized AI attempt to run restricted tool '{name}'.")
            return {
                "error": "SYSTEM DENIED. You are not authorized to use this tool at this time. "
                         "You must wait for a specific SYSTEM EVENT to authorize its use."
            }
        # --------------------------------------------
        
        try:
            func = self._tools[name]
            exec_args = dict(args)
            
            sig = inspect.signature(func)
            if "context" in sig.parameters:
                exec_args["context"] = context

            if inspect.iscoroutinefunction(func):
                result = await func(**exec_args)
            else:
                result = func(**exec_args)
            return result
        except Exception as e:
            print(f"[ToolRegistry] ❌ Error executing {name}: {e}")
            return {"error": f"Internal execution error: {str(e)}"}

registry = ToolRegistry()