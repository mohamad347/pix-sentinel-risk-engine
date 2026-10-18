from errors import QIException


class InvalidPixPayload(QIException):
    code = "QIT002001"

    def __init__(self, _description=None) -> None:
        super().__init__(
            "Invalid Pix payload",
            self.code,
            422,
            "The Pix payload is invalid.",
            "Os dados da transação Pix são inválidos.",
        )


class PixAnalysisNotFound(QIException):
    code = "QIT002002"

    def __init__(self) -> None:
        super().__init__(
            "Pix analysis not found",
            self.code,
            404,
            "The requested Pix analysis was not found.",
            "A análise Pix solicitada não foi encontrada.",
        )


class BootstrapPolicyUnavailable(QIException):
    code = "QIT002003"

    def __init__(self) -> None:
        super().__init__(
            "Risk policy unavailable",
            self.code,
            503,
            "No antifraud policy is enabled for this environment.",
            "Nenhuma política antifraude está habilitada para este ambiente.",
        )


class PixTransactionConflict(QIException):
    code = "QIT002004"

    def __init__(self) -> None:
        super().__init__(
            "Pix transaction conflict",
            self.code,
            409,
            "The Pix identifier is already associated with different transaction data.",
            "O identificador Pix já está associado a dados de transação diferentes.",
        )
