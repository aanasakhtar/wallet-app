from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class CreateTransferRequest(_message.Message):
    __slots__ = ("recipient", "amount", "idempotency_key")
    RECIPIENT_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    recipient: str
    amount: float
    idempotency_key: str
    def __init__(self, recipient: _Optional[str] = ..., amount: _Optional[float] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CreateTransferResponse(_message.Message):
    __slots__ = ("transaction_id", "status", "amount", "from_wallet_id", "to_wallet_id", "created_at", "replayed")
    TRANSACTION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    FROM_WALLET_ID_FIELD_NUMBER: _ClassVar[int]
    TO_WALLET_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    REPLAYED_FIELD_NUMBER: _ClassVar[int]
    transaction_id: str
    status: str
    amount: float
    from_wallet_id: str
    to_wallet_id: str
    created_at: str
    replayed: bool
    def __init__(self, transaction_id: _Optional[str] = ..., status: _Optional[str] = ..., amount: _Optional[float] = ..., from_wallet_id: _Optional[str] = ..., to_wallet_id: _Optional[str] = ..., created_at: _Optional[str] = ..., replayed: _Optional[bool] = ...) -> None: ...
