from tools.system import SubmitCallSummary, SetActiveUser, UpdateUserTimezone
from tools.telephony import TransferCall, SendDTMF
from tools.directory import SearchDirectory, SearchUsers
from tools.scheduling import ScheduleOutboundCall, CancelScheduledCall
from tools.external import CheckWeather
from tools.identity import RegisterNewUser, UpdateEndpointContext

class ToolRegistry:
    def __init__(self, session):
        self.session = session
        
        # Instantiate all registered tools
        registered_classes = [
            SubmitCallSummary, SetActiveUser, UpdateUserTimezone,
            TransferCall, SendDTMF,
            SearchDirectory, SearchUsers,
            RegisterNewUser, UpdateEndpointContext,
            ScheduleOutboundCall, CancelScheduledCall,
            CheckWeather
        ]
        
        self.tools = {cls.name: cls() for cls in registered_classes}

    def _check_auth(self, tool_instance):
        # user_level = getattr(self.session, 'active_user_level', 10)
        # return user_level >= tool_instance.auth_level
        return True # Forced bypass for current development phase

    def get_declarations(self):
        return [tool.get_declaration() for tool in self.tools.values()]

    async def execute_tool(self, name, args):
        tool = self.tools.get(name)
        if not tool:
            return {"status": "failed", "message": f"Tool '{name}' not found."}
            
        if not self._check_auth(tool):
            return {"status": "failed", "message": f"Authorization denied for {name}."}

        return await tool.execute(self.session, args)