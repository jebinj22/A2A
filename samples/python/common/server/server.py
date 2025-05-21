from starlette.applications import Starlette
from starlette.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from common.types import (
    A2ARequest,
    JSONRPCResponse,
    InvalidRequestError,
    JSONParseError,
    GetTaskRequest,
    CancelTaskRequest,
    SendTaskRequest,
    SetTaskPushNotificationRequest,
    GetTaskPushNotificationRequest,
    InternalError,
    AgentCard,
    TaskResubscriptionRequest,
    SendTaskStreamingRequest,
)
from pydantic import ValidationError
import json
from typing import AsyncIterable, Any
from common.server.task_manager import TaskManager

import logging

logger = logging.getLogger(__name__)


class A2AServer:
    def __init__(
        self,
        host="0.0.0.0",
        port=5000,
    ):
        self.host = host
        self.port = port
        self.agents = {}  # Dictionary to store agents and their endpoints
        self.app = Starlette()

    def register_agent(self, endpoint: str, agent_card: AgentCard, task_manager: TaskManager):
        """Register a new agent or update existing one."""
        print("Registering agent============:", endpoint)
        self.agents[endpoint] = {
            "agent_card": agent_card,
            "task_manager": task_manager,
        }
        
        # Remove existing routes for this endpoint if they exist
        self.app.router.routes = [route for route in self.app.router.routes 
                                if not (hasattr(route, 'path') and 
                                      (route.path == endpoint or 
                                       route.path == f"{endpoint}/.well-known/agent.json"))]
        
        # Add/update routes
        self.app.add_route(endpoint, self._process_request(endpoint), methods=["POST"])
        self.app.add_route(
            f"{endpoint}/.well-known/agent.json", self._get_agent_card(endpoint), methods=["GET"]
        )

    def start(self):
        if not self.agents:
            raise ValueError("No agents registered.")

        import uvicorn

        uvicorn.run(self.app, host=self.host, port=self.port)

    def _get_agent_card(self, endpoint: str):
        async def handler(request: Request) -> JSONResponse:
            agent_card = self.agents[endpoint]["agent_card"]
            return JSONResponse(agent_card.model_dump(exclude_none=True))

        return handler

    def _process_request(self, endpoint: str):
        async def handler(request: Request):
            try:
                body = await request.json()
                json_rpc_request = A2ARequest.validate_python(body)
                task_manager = self.agents[endpoint]["task_manager"]

                if isinstance(json_rpc_request, GetTaskRequest):
                    result = await task_manager.on_get_task(json_rpc_request)
                elif isinstance(json_rpc_request, SendTaskRequest):
                    result = await task_manager.on_send_task(json_rpc_request)
                elif isinstance(json_rpc_request, SendTaskStreamingRequest):
                    result = await task_manager.on_send_task_subscribe(json_rpc_request)
                elif isinstance(json_rpc_request, CancelTaskRequest):
                    result = await task_manager.on_cancel_task(json_rpc_request)
                elif isinstance(json_rpc_request, SetTaskPushNotificationRequest):
                    result = await task_manager.on_set_task_push_notification(json_rpc_request)
                elif isinstance(json_rpc_request, GetTaskPushNotificationRequest):
                    result = await task_manager.on_get_task_push_notification(json_rpc_request)
                elif isinstance(json_rpc_request, TaskResubscriptionRequest):
                    result = await task_manager.on_resubscribe_to_task(json_rpc_request)
                else:
                    logger.warning(f"Unexpected request type: {type(json_rpc_request)}")
                    raise ValueError(f"Unexpected request type: {type(request)}")

                return self._create_response(result)

            except Exception as e:
                return self._handle_exception(e)

        return handler

    def _handle_exception(self, e: Exception) -> JSONResponse:
        if isinstance(e, json.decoder.JSONDecodeError):
            json_rpc_error = JSONParseError()
        elif isinstance(e, ValidationError):
            json_rpc_error = InvalidRequestError(data=json.loads(e.json()))
        else:
            logger.error(f"Unhandled exception: {e}")
            json_rpc_error = InternalError()

        response = JSONRPCResponse(id=None, error=json_rpc_error)
        return JSONResponse(response.model_dump(exclude_none=True), status_code=400)

    def _create_response(self, result: Any) -> JSONResponse | EventSourceResponse:
        if isinstance(result, AsyncIterable):

            async def event_generator(result) -> AsyncIterable[dict[str, str]]:
                async for item in result:
                    yield {"data": item.model_dump_json(exclude_none=True)}

            return EventSourceResponse(event_generator(result))
        elif isinstance(result, JSONRPCResponse):
            return JSONResponse(result.model_dump(exclude_none=True))
        else:
            logger.error(f"Unexpected result type: {type(result)}")
            raise ValueError(f"Unexpected result type: {type(result)}")
