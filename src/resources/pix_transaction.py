from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from controllers import PixTransactionController
from errors import InvalidPixPayload
from utils.schema_handler import SchemaHandler


class PixTransactionResource:
    @SchemaHandler.validate("post_pix_transaction.json", InvalidPixPayload)
    def on_post(self, payload: dict) -> JSONResponse:
        analysis = PixTransactionController().create(payload)
        return JSONResponse(content=jsonable_encoder(analysis), status_code=http_status.HTTP_200_OK)

    def on_get_by_external_id(self, id: str) -> JSONResponse:
        analysis = PixTransactionController().get_by_external_id(id)
        return JSONResponse(content=jsonable_encoder(analysis), status_code=http_status.HTTP_200_OK)
