from uuid import uuid4

class PayloadGenerator:
    @staticmethod
    def create_pix_transaction_payload(external_id: str = None, include_geolocation: bool = True) -> dict:
        if external_id is None:
            external_id = f"pix-{uuid4()}"

        payload = {
            "id": external_id,
            "amount": 150000,
            "transaction_date": "2026-10-17T22:15:00-03:00",
            "source_account": {
                "branch": "0001",
                "account_number": "00088213",
                "opening_date": "2025-03-02T10:00:00-03:00",
                "owner": {"document_number": "00ABC12345000199"},
            },
            "destination_account": {
                "participant": "060701190",
                "branch": "03675",
                "account_number": "00010442",
                "opening_date": "2026-10-10T09:00:00-03:00",
                "owner": {"document_number": "000.000.001-91"},
            },
            "source": {
                "device_id": "dvc-test",
                "channel": "app",
                "ip": "198.51.100.23",
            },
        }
        if include_geolocation:
            payload["source"]["geolocation"] = {"latitude": -23.56, "longitude": -46.65}
        return payload
