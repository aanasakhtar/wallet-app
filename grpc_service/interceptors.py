"""
Server interceptor: request-id propagation + one log line per RPC.

This is what keeps Assignment 1's log correlation working across the gRPC
boundary. The caller sends its request id as the `x-request-id` metadata key
(the gRPC counterpart of the HTTP `X-Request-ID` header). The interceptor puts
it into the same ContextVar the HTTP middleware uses, so every log line the
shared business code writes - `transfer_completed`, `transfer_failed`, ... - carries
it, exactly as it does for an HTTP request. The id is echoed back to the caller in
the initial metadata so a client that did not supply one can still learn it.
"""
import time
import uuid

import grpc

from app import metrics
from app.logging_config import app_logger
from app.middleware.request_context import reset_request_id, set_request_id


class RequestContextInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        method = handler_call_details.method
        incoming = dict(handler_call_details.invocation_metadata or ()).get("x-request-id")
        inner = handler.unary_unary

        def wrapped(request, context):
            request_id = incoming or str(uuid.uuid4())
            token = set_request_id(request_id)
            context.send_initial_metadata((("x-request-id", request_id),))
            metrics.grpc_server_in_flight.labels(grpc_method=method).inc()
            start = time.perf_counter()
            crashed = False
            try:
                return inner(request, context)
            except BaseException:
                crashed = True  # not an abort(): the handler blew up; gRPC will answer UNKNOWN
                raise
            finally:
                elapsed = time.perf_counter() - start
                code = context.code()
                if code is not None:
                    status = code.name
                elif crashed:
                    status = "UNKNOWN"
                elif not context.is_active():
                    status = "CANCELLED"  # the client gave up (e.g. its deadline passed) before we answered
                else:
                    status = "OK"
                # Duration is measured HERE, on the server, around the handler only: it excludes
                # network time and protobuf (de)serialisation, which a client-side timer would include.
                metrics.grpc_server_handled_total.labels(grpc_method=method, grpc_code=status).inc()
                metrics.grpc_server_handling_seconds.labels(grpc_method=method).observe(elapsed)
                metrics.grpc_server_in_flight.labels(grpc_method=method).dec()
                app_logger.info(
                    "gRPC call finished",
                    extra={"event": "grpc_call", "grpc_method": method, "grpc_status": status,
                           "duration_ms": round(elapsed * 1000, 2)},
                )
                reset_request_id(token)

        return grpc.unary_unary_rpc_method_handler(
            wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
