from tools.system import SubmitCallSummary, SetActiveUser, UpdateUserTimezone, MarkMissionComplete
from tools.telephony import TransferCall, SendDTMF, EndCall, ExecuteOutboundDial
from tools.directory import SearchDirectory, SearchUsers
from tools.scheduling import DelegateAutonomousTask
from tools.external import CheckWeather
from tools.identity import RegisterNewUser, UpdateEndpointContext, ResolveAndSwitchUser

class ToolRegistry:
    def __init__(self, session):
        self.session = session
        
        registered_classes = [
            SubmitCallSummary, SetActiveUser, UpdateUserTimezone, MarkMissionComplete,
            TransferCall, SendDTMF, EndCall, ExecuteOutboundDial,
            SearchDirectory, SearchUsers,
            RegisterNewUser, UpdateEndpointContext, ResolveAndSwitchUser,
            DelegateAutonomousTask,
            CheckWeather
        ]
        
        self.tools = {cls.name: cls() for cls in registered_classes}

    def _check_auth(self, tool_instance):
        return True 

    def get_declarations(self):
        return [tool.get_declaration() for tool in self.tools.values()]

    async def execute_tool(self, name, args):
        tool = self.tools.get(name)
        if not tool:
            return {"status": "failed", "message": f"Tool '{name}' not found."}
            
        if not self._check_auth(tool):
            return {"status": "failed", "message": f"Authorization denied for {name}."}

        return await tool.execute(self.session, args)