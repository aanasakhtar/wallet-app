"""
The gRPC handler for CreateTransfer.

It does NOT call the HTTP endpoint. It calls the same service-layer function the
HTTP route calls (transfer_service.create_transfer) against the same database:

    HTTP : router -> Pydantic schema -> transfer_service.create_transfer -> DB
    gRPC : stub -> THIS handler -> shared validation -> transfer_service.create_transfer -> DB

so the business rules (balance check, locking, idempotency, cashback, metrics,
logs) exist once.
"""
import grpc

from app import validation
from app.database import SessionLocal
from app.errors import DomainError, Unauthenticated
from app.logging_config import app_logger
from app.services import auth_service, transfer_service
from wallet.v1 import wallet_pb2, wallet_pb2_grpc


def _bearer_token(context) -> str:
    for key, value in context.invocation_metadata():
        if key == "authorization":
            scheme, _, token = value.partition(" ")
            if scheme.lower() == "bearer" and token:
                return token
    raise Unauthenticated()


def _abort(context, err: DomainError):
    """Map a DomainError to a gRPC status using the same table the HTTP handlers use."""
    context.set_trailing_metadata((("x-error-code", err.code),))
    context.abort(grpc.StatusCode[err.grpc_status], err.message)


class TransferServicer(wallet_pb2_grpc.TransferServiceServicer):
    def CreateTransfer(self, request, context):
        db = SessionLocal()
        try:
            user = auth_service.authenticate_token(db, _bearer_token(context))
            validation.validate_transfer_inputs(request.recipient, request.amount, request.idempotency_key)
            txn, replayed = transfer_service.create_transfer(
                db, user, request.recipient.strip(), request.amount, request.idempotency_key)
            return wallet_pb2.CreateTransferResponse(
                transaction_id=txn.id, status=txn.status.value, amount=txn.amount,
                from_wallet_id=txn.from_wallet_id or "", to_wallet_id=txn.to_wallet_id or "",
                created_at=txn.created_at.isoformat() + "Z", replayed=replayed,
            )
        except DomainError as err:
            _abort(context, err)
        except Exception:  # a bug, not a business rule: log it, hide the details
            app_logger.exception("Unhandled error in CreateTransfer", extra={"event": "unhandled_exception"})
            context.set_trailing_metadata((("x-error-code", "internal_error"),))
            context.abort(grpc.StatusCode.INTERNAL, "Internal server error")
        finally:
            db.close()
